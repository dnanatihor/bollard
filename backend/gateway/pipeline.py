import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from gateway import circuit, limits, metrics, prompt_cache
from gateway import providers as provider_client
from gateway.budgets import ensure_user_budget, record_usage, workspace_blocks
from gateway.crypto import decrypt_secret
from gateway.guardrails import Decision, GuardrailDenied, evaluate
from gateway.models import AuditEvent, CatalogModel, GuardrailHit, Provider, Setting, Span, Trace, VirtualModel
from gateway.providers import Completion, ProviderError, message_text
from gateway.routing import record_latency, region_alternates, resolve_target
from gateway.security import Identity


class GatewayFailure(Exception):
    def __init__(self, code: str, status: int, message: str, request_id: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.request_id = request_id


@dataclass
class TraceRecorder:
    trace_id: str
    request_id: str
    identity: Identity
    model: str
    started: float = field(default_factory=time.perf_counter)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    spans: list[dict[str, object]] = field(default_factory=list)

    @contextmanager
    def span(self, name: str):
        start = time.perf_counter()
        record: dict[str, object] = {
            "name": name,
            "offset": int((start - self.started) * 1000),
            "status": "ok",
            "detail": "",
        }
        try:
            yield record
        except Exception:
            record["status"] = "error"
            raise
        finally:
            record["duration"] = int((time.perf_counter() - start) * 1000)
            self.spans.append(record)

    def save(self, db: Session, status: str, error_code: str, provider: str) -> None:
        db.add(
            Trace(
                trace_id=self.trace_id,
                request_id=self.request_id,
                tenant_id=self.identity.tenant_id,
                application_id=self.identity.application_id,
                user_id=self.identity.user_id,
                model=self.model,
                provider=provider,
                status=status,
                error_code=error_code,
                started_at=self.started_at,
                duration_ms=int((time.perf_counter() - self.started) * 1000),
            )
        )
        db.flush()
        for span in self.spans:
            db.add(
                Span(
                    trace_id=self.trace_id,
                    name=str(span["name"]),
                    offset_ms=int(span["offset"]),
                    duration_ms=int(span["duration"]),
                    status=str(span["status"]),
                    detail=str(span["detail"]),
                )
            )


def _money(value: float) -> str:
    return f"{value:.8f}"


def render_messages(messages: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        text = content if isinstance(content, str) else message_text([message])
        lines.append(f"{role}: {text}")
    return "\n\n".join(lines)


def _audit_body(
    *,
    event: str,
    request_id: str,
    trace_id: str,
    tenant_id: str,
    application_id: str,
    user_id: str,
    requested_model: str,
    provider: str,
    provider_model: str,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: str,
    timestamp: str,
    prev_hash: str,
    prompt: str = "",
    sent: str = "",
    response: str = "",
    include_content: bool = True,
) -> dict[str, object]:
    body: dict[str, object] = {
        "applicationId": application_id,
        "estimatedCost": estimated_cost,
        "event": event,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "prevHash": prev_hash,
        "provider": provider,
        "providerModel": provider_model,
        "requestId": request_id,
        "requestedModel": requested_model,
        "tenantId": tenant_id,
        "timestamp": timestamp,
        "traceId": trace_id,
        "userId": user_id,
    }
    if include_content:
        body["prompt"] = prompt
        body["response"] = response
        body["sent"] = sent
    return body


def append_audit(db: Session, **fields: object) -> None:
    import hashlib

    last = db.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()))
    prev = last.hash if last is not None else "0"
    timestamp = datetime.now(UTC).isoformat()
    cost = _money(float(fields.get("estimatedCost") or 0))
    prompt = str(fields.get("prompt") or "")
    sent = str(fields.get("sent") or "")
    response = str(fields.get("response") or "")
    body = _audit_body(
        event=str(fields.get("event", "")),
        request_id=str(fields.get("requestId", "")),
        trace_id=str(fields.get("traceId", "")),
        tenant_id=str(fields.get("tenantId", "")),
        application_id=str(fields.get("applicationId", "")),
        user_id=str(fields.get("userId", "")),
        requested_model=str(fields.get("requestedModel", "")),
        provider=str(fields.get("provider", "")),
        provider_model=str(fields.get("providerModel", "")),
        input_tokens=int(fields.get("inputTokens") or 0),
        output_tokens=int(fields.get("outputTokens") or 0),
        estimated_cost=cost,
        timestamp=timestamp,
        prev_hash=prev,
        prompt=prompt,
        sent=sent,
        response=response,
    )
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    db.add(
        AuditEvent(
            event=str(body["event"]),
            request_id=str(body["requestId"]),
            trace_id=str(body["traceId"]),
            tenant_id=str(body["tenantId"]),
            application_id=str(body["applicationId"]),
            user_id=str(body["userId"]),
            requested_model=str(body["requestedModel"]),
            provider=str(body["provider"]),
            provider_model=str(body["providerModel"]),
            input_tokens=int(body["inputTokens"]),
            output_tokens=int(body["outputTokens"]),
            estimated_cost=float(cost),
            timestamp=timestamp,
            prev_hash=prev,
            hash=digest,
            prompt_text=prompt,
            sent_text=sent,
            response_text=response,
        )
    )


def _digest(event: AuditEvent, *, include_content: bool) -> str:
    import hashlib

    body = _audit_body(
        event=event.event,
        request_id=event.request_id,
        trace_id=event.trace_id,
        tenant_id=event.tenant_id,
        application_id=event.application_id,
        user_id=event.user_id,
        requested_model=event.requested_model,
        provider=event.provider,
        provider_model=event.provider_model,
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        estimated_cost=_money(event.estimated_cost),
        timestamp=event.timestamp,
        prev_hash=event.prev_hash,
        prompt=event.prompt_text or "",
        sent=event.sent_text or "",
        response=event.response_text or "",
        include_content=include_content,
    )
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_audit(db: Session) -> bool:
    prev = "0"
    statement = select(AuditEvent).order_by(AuditEvent.id).execution_options(yield_per=400)
    for event in db.scalars(statement):
        if event.prev_hash != prev:
            return False
        digest = _digest(event, include_content=True)
        legacy = not (event.prompt_text or event.sent_text or event.response_text)
        if digest != event.hash and not (legacy and _digest(event, include_content=False) == event.hash):
            return False
        prev = event.hash
    return True


def _reject_embedding(catalog: CatalogModel, request_id: str) -> None:
    if (catalog.kind or "chat") == "embedding":
        raise GatewayFailure(
            "AI_GW_MODEL_NOT_FOUND",
            400,
            "This model serves embeddings. Call POST /v1/embeddings.",
            request_id,
        )


def _rate_ok(tenant_id: str, rpm: int) -> bool:
    return limits.allow(tenant_id, rpm)


def _record_hits(db: Session, recorder: TraceRecorder, phase: str, decisions: list) -> None:
    now = datetime.now(UTC).isoformat()
    for decision in decisions:
        db.add(
            GuardrailHit(
                trace_id=recorder.trace_id,
                request_id=recorder.request_id,
                phase=phase,
                rule=decision.rule,
                action=decision.action,
                reason=decision.reason,
                created_at=now,
            )
        )


def _summary(decisions: list) -> str:
    if not decisions:
        return "all rules allowed"
    return "; ".join(f"{decision.rule} {decision.action}" for decision in decisions)


async def execute_chat(
    db: Session,
    identity: Identity,
    *,
    model: str,
    messages: list[dict[str, object]],
    temperature: float | None,
    max_tokens: int | None,
    request_id: str,
    trace_id: str,
    tools: list[dict[str, object]] | None = None,
    tool_choice: object | None = None,
    headers: dict[str, str] | None = None,
    prompt_id: str | None = None,
) -> dict[str, object]:
    if prompt_id:
        from gateway.models import Prompt

        stored = db.get(Prompt, prompt_id)
        if stored is None or stored.tenant_id != identity.tenant_id:
            raise GatewayFailure("AI_GW_MODEL_NOT_FOUND", 404, "Prompt was not found.", request_id)
        messages = [{"role": "system", "content": stored.body}, *messages]
    recorder = TraceRecorder(trace_id=trace_id, request_id=request_id, identity=identity, model=model)
    provider_name = ""
    virtual = None
    prompt_text = render_messages(messages)
    sent_text = ""
    response_text = ""
    try:
        guarded_messages: list[dict[str, object]] = []
        with recorder.span("guardrail.input") as span:
            decisions = []
            for message in messages:
                content = message.get("content")
                text = content if isinstance(content, str) else message_text([message])
                try:
                    updated, found = evaluate(db, "input", text, model)
                except GuardrailDenied as denied:
                    _record_hits(db, recorder, "input", [Decision(denied.rule, "deny", denied.reason, "")])
                    span["detail"] = f"{denied.rule} deny"
                    raise
                decisions.extend(found)
                guarded_messages.append({**message, "content": updated})
            span["detail"] = _summary(decisions)
            _record_hits(db, recorder, "input", decisions)

        with recorder.span("resolve") as span:
            virtual, catalog, provider = resolve_target(db, model, headers)
            if catalog is None or provider is None or provider.enabled != 1:
                span["detail"] = "not found"
                raise GatewayFailure("AI_GW_MODEL_NOT_FOUND", 404, f'Model "{model}" was not found.', request_id)
            _reject_embedding(catalog, request_id)
            provider_name = provider.id
            strategy = virtual.strategy if virtual is not None else "catalog"
            span["detail"] = f"{strategy} {provider.id} / {catalog.provider_model}"

        rpm_row = db.get(Setting, "requests_per_minute")
        rpm = int(rpm_row.value) if rpm_row is not None else 60
        with recorder.span("rate_limit") as span:
            span["detail"] = f"{rpm} requests per minute"
            if not _rate_ok(identity.tenant_id, rpm):
                raise GatewayFailure("AI_GW_RATE_LIMITED", 429, "Rate limit exceeded.", request_id)

        estimated = (max(len(render_messages(guarded_messages)), 1) / 4) / 1_000_000 * catalog.input_price_per_million
        user_budget = ensure_user_budget(db, identity.user_id, identity.tenant_id)
        with recorder.span("budget") as span:
            if user_budget.spent_usd + estimated > user_budget.monthly_usd:
                span["detail"] = "blocked"
                raise GatewayFailure("AI_GW_BUDGET_EXCEEDED", 402, "This user's monthly budget is spent.", request_id)
            if workspace_blocks(db, identity.tenant_id, estimated):
                span["detail"] = "workspace blocked"
                raise GatewayFailure("AI_GW_BUDGET_EXCEEDED", 402, "The workspace budget for this month is spent.", request_id)
            span["detail"] = f"${user_budget.spent_usd:.4f} of ${user_budget.monthly_usd:.2f}"

        secret = decrypt_secret(provider.secret_ciphertext)
        if secret is None:
            raise GatewayFailure("AI_GW_PROVIDER_ERROR", 502, "Provider secret is not configured.", request_id)
        try:
            circuit.before_call(provider.id)
        except RuntimeError:
            raise GatewayFailure("AI_GW_PROVIDER_ERROR", 503, "Provider circuit is open.", request_id)

        sent_text = render_messages(guarded_messages)
        ttl = 0 if tools else prompt_cache.ttl_seconds(db)
        cache_key = prompt_cache.make_key(identity.tenant_id, model, guarded_messages, temperature) if ttl else ""
        cached = prompt_cache.read(cache_key) if cache_key else None
        if cached is None and not tools:
            cached = await prompt_cache.read_semantic(db, identity.tenant_id, model, sent_text)
        cache_hit = cached is not None
        if cache_hit and cached is not None:
            with recorder.span("cache") as span:
                span["detail"] = "hit"
            completion = Completion(
                text=str(cached.get("text") or ""),
                input_tokens=int(cached.get("input_tokens") or 0),
                output_tokens=int(cached.get("output_tokens") or 0),
            )
        else:
            with recorder.span("provider") as span:
                span["detail"] = provider.id
                try:
                    started = time.perf_counter()
                    completion = await provider_client.complete(
                        provider,
                        secret,
                        catalog.provider_model,
                        guarded_messages,
                        temperature,
                        max_tokens,
                        tools,
                        tool_choice,
                    )
                    record_latency(catalog.id, (time.perf_counter() - started) * 1000)
                    circuit.record_success(provider.id)
                except ProviderError:
                    circuit.record_failure(provider.id)
                    completion = None
                    for alternate in region_alternates(db, virtual, catalog.id):
                        alternate_provider = db.get(Provider, alternate.provider_id)
                        alternate_secret = (
                            decrypt_secret(alternate_provider.secret_ciphertext) if alternate_provider is not None else None
                        )
                        if alternate_provider is None or alternate_provider.enabled != 1 or alternate_secret is None:
                            continue
                        try:
                            started = time.perf_counter()
                            completion = await provider_client.complete(
                                alternate_provider,
                                alternate_secret,
                                alternate.provider_model,
                                guarded_messages,
                                temperature,
                                max_tokens,
                                tools,
                                tool_choice,
                            )
                            record_latency(alternate.id, (time.perf_counter() - started) * 1000)
                            provider = alternate_provider
                            catalog = alternate
                            provider_name = provider.id
                            secret = alternate_secret
                            span["detail"] = f"region {provider.id} / {catalog.provider_model}"
                            circuit.record_success(provider.id)
                            break
                        except ProviderError:
                            circuit.record_failure(alternate_provider.id)
                    if completion is None:
                        fallback = (
                            db.get(CatalogModel, virtual.fallback_model_id)
                            if virtual is not None and virtual.fallback_model_id
                            else None
                        )
                        fallback_provider = db.get(Provider, fallback.provider_id) if fallback is not None else None
                        fallback_secret = (
                            decrypt_secret(fallback_provider.secret_ciphertext) if fallback_provider is not None else None
                        )
                        if (
                            fallback is None
                            or fallback_provider is None
                            or fallback_provider.enabled != 1
                            or fallback_secret is None
                        ):
                            raise
                        span["detail"] = f"fallback {fallback_provider.id} / {fallback.provider_model}"
                        started = time.perf_counter()
                        completion = await provider_client.complete(
                            fallback_provider,
                            fallback_secret,
                            fallback.provider_model,
                            guarded_messages,
                            temperature,
                            max_tokens,
                            tools,
                            tool_choice,
                        )
                        record_latency(fallback.id, (time.perf_counter() - started) * 1000)
                        provider = fallback_provider
                        catalog = fallback
                        provider_name = provider.id
                        circuit.record_success(provider.id)
            if ttl and cache_key and not completion.tool_calls:
                prompt_cache.write(
                    cache_key,
                    {
                        "text": completion.text,
                        "input_tokens": completion.input_tokens,
                        "output_tokens": completion.output_tokens,
                    },
                    ttl,
                )
            if not completion.tool_calls:
                await prompt_cache.write_semantic(
                    db,
                    identity.tenant_id,
                    model,
                    sent_text,
                    completion.text,
                    completion.input_tokens,
                    completion.output_tokens,
                )

        with recorder.span("guardrail.output") as span:
            response_text = completion.text
            try:
                output, decisions = evaluate(db, "output", completion.text, model)
            except GuardrailDenied as denied:
                _record_hits(db, recorder, "output", [Decision(denied.rule, "deny", denied.reason, "")])
                span["detail"] = f"{denied.rule} deny"
                raise
            span["detail"] = _summary(decisions)
            _record_hits(db, recorder, "output", decisions)
            response_text = output
            completion_text = output

        cost = 0.0 if cache_hit else (
            completion.input_tokens / 1_000_000 * catalog.input_price_per_million
            + completion.output_tokens / 1_000_000 * catalog.output_price_per_million
        )
        record_usage(
            db,
            identity,
            request_id=request_id,
            trace_id=trace_id,
            model=model,
            provider=provider.id,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost=cost,
            kind="chat",
        )
        metrics.inc("gateway_events_total", event="invocation", provider=provider.id)
        append_audit(
            db,
            event="AI_MODEL_INVOCATION",
            requestId=request_id,
            traceId=trace_id,
            tenantId=identity.tenant_id,
            applicationId=identity.application_id,
            userId=identity.user_id,
            requestedModel=model,
            provider=provider.id,
            providerModel=catalog.provider_model,
            inputTokens=completion.input_tokens,
            outputTokens=completion.output_tokens,
            estimatedCost=round(cost, 8),
            prompt=prompt_text,
            sent=sent_text,
            response=response_text,
        )
        recorder.save(db, "ok", "", provider.id)
        db.commit()
        assistant: dict[str, object] = {"role": "assistant", "content": completion_text}
        if completion.tool_calls:
            assistant["tool_calls"] = completion.tool_calls
        return {
            "id": f"chatcmpl_{request_id}",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": assistant,
                    "finish_reason": completion.finish_reason or "stop",
                }
            ],
            "usage": {
                "prompt_tokens": completion.input_tokens,
                "completion_tokens": completion.output_tokens,
                "total_tokens": completion.input_tokens + completion.output_tokens,
            },
            "gateway": {
                "request_id": request_id,
                "trace_id": trace_id,
                "provider": provider.id,
                "cache_hit": cache_hit,
            },
        }
    except GuardrailDenied as denied:
        append_audit(
            db,
            event="AI_GUARDRAIL_BLOCKED",
            requestId=request_id,
            traceId=trace_id,
            tenantId=identity.tenant_id,
            applicationId=identity.application_id,
            userId=identity.user_id,
            requestedModel=model,
            provider=provider_name,
            prompt=prompt_text,
            sent=sent_text,
            response=response_text,
        )
        code = "AI_GW_OUTPUT_BLOCKED" if denied.phase == "output" else "AI_GW_INPUT_BLOCKED"
        recorder.save(db, "blocked", code, provider_name)
        db.commit()
        metrics.inc("gateway_events_total", event="blocked")
        raise GatewayFailure(code, 400, denied.reason, request_id) from denied
    except ProviderError as error:
        append_audit(
            db,
            event="AI_PROVIDER_FAILED",
            requestId=request_id,
            traceId=trace_id,
            tenantId=identity.tenant_id,
            applicationId=identity.application_id,
            userId=identity.user_id,
            requestedModel=model,
            provider=provider_name,
            prompt=prompt_text,
            sent=sent_text,
            response=response_text,
        )
        recorder.save(db, "error", "AI_GW_PROVIDER_ERROR", provider_name)
        db.commit()
        metrics.inc("gateway_events_total", event="provider_error", provider=provider_name or "unknown")
        raise GatewayFailure(
            "AI_GW_PROVIDER_ERROR",
            502,
            f"Provider returned HTTP {error.status}.",
            request_id,
        ) from error
    except GatewayFailure as error:
        append_audit(
            db,
            event="AI_REQUEST_REJECTED",
            requestId=request_id,
            traceId=trace_id,
            tenantId=identity.tenant_id,
            applicationId=identity.application_id,
            userId=identity.user_id,
            requestedModel=model,
            provider=provider_name,
            prompt=prompt_text,
            sent=sent_text,
            response=response_text,
        )
        recorder.save(db, "error", error.code, provider_name)
        db.commit()
        metrics.inc("gateway_events_total", event="rejected", code=error.code)
        raise


def new_ids() -> tuple[str, str]:
    return f"gw_{uuid.uuid4()}", f"tr_{uuid.uuid4()}"


async def iter_live_sse(
    db: Session,
    identity: Identity,
    *,
    model: str,
    messages: list[dict[str, object]],
    temperature: float | None,
    max_tokens: int | None,
    request_id: str,
    trace_id: str,
    headers: dict[str, str] | None = None,
):
    """Yield provider tokens as SSE. Refusal before the first token raises GatewayFailure."""
    recorder = TraceRecorder(trace_id=trace_id, request_id=request_id, identity=identity, model=model)
    provider_name = ""
    prompt_text = render_messages(messages)
    sent_text = ""
    response_text = ""
    try:
        guarded_messages: list[dict[str, object]] = []
        with recorder.span("guardrail.input") as span:
            decisions = []
            for message in messages:
                content = message.get("content")
                text = content if isinstance(content, str) else message_text([message])
                updated, found = evaluate(db, "input", text, model)
                decisions.extend(found)
                guarded_messages.append({**message, "content": updated})
            span["detail"] = _summary(decisions)
            _record_hits(db, recorder, "input", decisions)
        with recorder.span("resolve") as span:
            virtual, catalog, provider = resolve_target(db, model, headers)
            if catalog is None or provider is None or provider.enabled != 1:
                span["detail"] = "not found"
                raise GatewayFailure("AI_GW_MODEL_NOT_FOUND", 404, f'Model "{model}" was not found.', request_id)
            _reject_embedding(catalog, request_id)
            if provider.type not in {"openai", "openai-compatible", "azure-openai"}:
                raise GatewayFailure("AI_GW_PROVIDER_ERROR", 400, "This provider does not stream tokens.", request_id)
            provider_name = provider.id
            span["detail"] = f"stream {provider.id} / {catalog.provider_model}"
        rpm_row = db.get(Setting, "requests_per_minute")
        rpm = int(rpm_row.value) if rpm_row is not None else 60
        if not _rate_ok(identity.tenant_id, rpm):
            raise GatewayFailure("AI_GW_RATE_LIMITED", 429, "Rate limit exceeded.", request_id)
        estimated = (max(len(render_messages(guarded_messages)), 1) / 4) / 1_000_000 * catalog.input_price_per_million
        user_budget = ensure_user_budget(db, identity.user_id, identity.tenant_id)
        if user_budget.spent_usd + estimated > user_budget.monthly_usd:
            raise GatewayFailure("AI_GW_BUDGET_EXCEEDED", 402, "This user's monthly budget is spent.", request_id)
        if workspace_blocks(db, identity.tenant_id, estimated):
            raise GatewayFailure("AI_GW_BUDGET_EXCEEDED", 402, "The workspace budget for this month is spent.", request_id)
        secret = decrypt_secret(provider.secret_ciphertext)
        if secret is None:
            raise GatewayFailure("AI_GW_PROVIDER_ERROR", 502, "Provider secret is not configured.", request_id)
        try:
            circuit.before_call(provider.id)
        except RuntimeError:
            raise GatewayFailure("AI_GW_PROVIDER_ERROR", 503, "Provider circuit is open.", request_id)
        sent_text = render_messages(guarded_messages)
        pieces: list[str] = []
        input_tokens = 0
        output_tokens = 0
        completion_id = f"chatcmpl_{request_id}"
        async for chunk in provider_client.iter_openai_chunks(
            provider,
            secret,
            catalog.provider_model,
            guarded_messages,
            temperature,
            max_tokens,
        ):
            delta: dict[str, object] = {}
            finish = None
            choices = chunk.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                raw_delta = choices[0].get("delta")
                if isinstance(raw_delta, dict):
                    delta = raw_delta
                    if isinstance(raw_delta.get("content"), str):
                        pieces.append(raw_delta["content"])
                finish = choices[0].get("finish_reason")
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                input_tokens = int(usage.get("prompt_tokens") or input_tokens)
                output_tokens = int(usage.get("completion_tokens") or output_tokens)
        circuit.record_success(provider.id)
        response_text, decisions = evaluate(db, "output", "".join(pieces), model)
        _record_hits(db, recorder, "output", decisions)
        step = 32
        slices = [response_text[index : index + step] for index in range(0, max(len(response_text), 1), step)] or [""]
        for index, piece in enumerate(slices):
            delta = {"content": piece}
            if index == 0:
                delta = {"role": "assistant", "content": piece}
            finish = "stop" if index == len(slices) - 1 else None
            yield _sse_chunk(completion_id, model, delta, finish)
        cost = (
            input_tokens / 1_000_000 * catalog.input_price_per_million
            + output_tokens / 1_000_000 * catalog.output_price_per_million
        )
        record_usage(
            db,
            identity,
            request_id=request_id,
            trace_id=trace_id,
            model=model,
            provider=provider.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            kind="chat",
        )
        append_audit(
            db,
            event="AI_MODEL_INVOCATION",
            requestId=request_id,
            traceId=trace_id,
            tenantId=identity.tenant_id,
            applicationId=identity.application_id,
            userId=identity.user_id,
            requestedModel=model,
            provider=provider.id,
            providerModel=catalog.provider_model,
            inputTokens=input_tokens,
            outputTokens=output_tokens,
            estimatedCost=round(cost, 8),
            prompt=prompt_text,
            sent=sent_text,
            response=response_text,
        )
        recorder.save(db, "ok", "", provider.id)
        db.commit()
        yield "data: [DONE]\n\n"
    except GuardrailDenied as denied:
        code = "AI_GW_OUTPUT_BLOCKED" if denied.phase == "output" else "AI_GW_INPUT_BLOCKED"
        recorder.save(db, "blocked", code, provider_name)
        db.commit()
        raise GatewayFailure(code, 400, denied.reason, request_id) from denied
    except ProviderError as error:
        circuit.record_failure(provider_name or "unknown")
        recorder.save(db, "error", "AI_GW_PROVIDER_ERROR", provider_name)
        db.commit()
        raise GatewayFailure("AI_GW_PROVIDER_ERROR", 502, f"Provider returned HTTP {error.status}.", request_id) from error


def _sse_chunk(completion_id: object, model: str, delta: dict[str, object], finish: str | None) -> str:
    body = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n"
