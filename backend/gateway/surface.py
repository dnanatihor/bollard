import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from gateway.budgets import ensure_user_budget, record_usage, workspace_blocks
from gateway.crypto import decrypt_secret, encrypt_secret
from gateway.db import SessionLocal
from gateway.http import get_db, require
from gateway.listing import clamp, contains, fetch, meta
from gateway.mcp_proxy import McpDenied, encode_headers, invoke, new_grant_id, now, server_view, valid_server_url
from gateway.models import CatalogModel, GuardrailRule, McpGrant, McpServer, Provider, Setting, Trace, User, VirtualModel
from gateway.pipeline import GatewayFailure, append_audit, execute_chat, new_ids
from gateway.providers import ProviderError, embed, rerank
from gateway.routing import resolve_target
from gateway.security import Identity, allows, authenticate

router = APIRouter()


class EmbedBody(BaseModel):
    model: str
    input: str | list[str]


class ResponsesBody(BaseModel):
    model: str
    input: str | list[object]
    temperature: float | None = None
    max_output_tokens: int | None = None


class RerankBody(BaseModel):
    model: str
    query: str
    documents: list[str] = Field(min_length=1)


class JwtBody(BaseModel):
    issuer: str = ""
    audience: str = ""
    jwksUrl: str = ""


class HeaderField(BaseModel):
    name: str = ""
    value: str = ""


class McpBody(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    url: str
    authType: str = "none"
    secret: str | None = None
    headers: list[HeaderField] = Field(default_factory=list)
    authorizeUrl: str | None = None
    tokenUrl: str | None = None
    oauthClientId: str | None = None
    oauthClientSecret: str | None = None
    oauthScopes: str | None = None


class GrantBody(BaseModel):
    userId: str
    tools: list[str] = Field(default_factory=list)


def _setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def _read_setting(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row is not None else ""


@router.get("/api/settings/jwt")
def get_jwt(_: Identity = Depends(require("admin:read")), db: Session = Depends(get_db)) -> dict[str, str]:
    return {
        "issuer": _read_setting(db, "jwt_issuer"),
        "audience": _read_setting(db, "jwt_audience"),
        "jwksUrl": _read_setting(db, "jwt_jwks_url"),
    }


@router.put("/api/settings/jwt")
def put_jwt(
    body: JwtBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _setting(db, "jwt_issuer", body.issuer.strip())
    _setting(db, "jwt_audience", body.audience.strip())
    _setting(db, "jwt_jwks_url", body.jwksUrl.strip())
    db.commit()
    return {"issuer": body.issuer.strip(), "audience": body.audience.strip(), "jwksUrl": body.jwksUrl.strip()}


@router.get("/api/mcp/servers")
def list_servers(
    identity: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = [McpServer.tenant_id == identity.tenant_id]
    if q.strip():
        filters.append(or_(contains(McpServer.name, q), contains(McpServer.url, q)))
    rows, total = fetch(db, select(McpServer).where(*filters).order_by(McpServer.name), size, start)
    ids = [row.id for row in rows]
    grants = []
    grant_total = 0
    if ids:
        grant_stmt = select(McpGrant).where(McpGrant.server_id.in_(ids)).order_by(McpGrant.id)
        grants, grant_total = fetch(db, grant_stmt, size, 0)
    return {
        "servers": [server_view(row) for row in rows],
        "grants": [
            {"id": grant.id, "serverId": grant.server_id, "userId": grant.user_id, "tools": json.loads(grant.tools_json or "[]")}
            for grant in grants
        ],
        "page": meta(total, size, start),
        "grantsPage": meta(grant_total, size, 0),
    }


@router.post("/api/mcp/servers")
def create_server(
    body: McpBody,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if body.authType not in {"none", "bearer", "oauth", "custom"}:
        raise HTTPException(status_code=400, detail={"message": "Auth type must be none, bearer, oauth, or custom."})
    if not valid_server_url(body.url):
        raise HTTPException(status_code=400, detail={"message": "MCP server URL must be an http or https URL without embedded credentials."})
    if db.get(McpServer, body.id) is not None:
        raise HTTPException(status_code=409, detail={"message": "That MCP server id already exists."})
    if body.authType == "bearer" and not (body.secret or "").strip():
        raise HTTPException(status_code=400, detail={"message": "A bearer MCP server needs a secret."})
    if body.authType == "oauth" and not (body.authorizeUrl and body.tokenUrl and body.oauthClientId and body.oauthClientSecret):
        raise HTTPException(status_code=400, detail={"message": "OAuth needs an authorize URL, token URL, client id, and client secret."})
    header_blob = None
    if body.authType == "custom":
        try:
            header_blob = encode_headers([(item.name, item.value) for item in body.headers])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc
        if header_blob is None:
            raise HTTPException(status_code=400, detail={"message": "Custom auth needs at least one header."})
    db.add(
        McpServer(
            id=body.id,
            tenant_id=identity.tenant_id,
            name=body.name.strip(),
            url=body.url.strip(),
            auth_type=body.authType,
            secret_ciphertext=encrypt_secret(body.secret.strip()) if body.authType == "bearer" and body.secret else None,
            headers_ciphertext=header_blob,
            authorize_url=(body.authorizeUrl or "").strip() or None if body.authType == "oauth" else None,
            token_url=(body.tokenUrl or "").strip() or None if body.authType == "oauth" else None,
            oauth_client_id=(body.oauthClientId or "").strip() or None if body.authType == "oauth" else None,
            oauth_client_secret_ciphertext=encrypt_secret(body.oauthClientSecret.strip()) if body.authType == "oauth" and body.oauthClientSecret else None,
            oauth_scopes=(body.oauthScopes or "").strip() or None if body.authType == "oauth" else None,
            enabled=1,
            created_at=now(),
        )
    )
    db.commit()
    return {"id": body.id}


@router.delete("/api/mcp/servers/{server_id}")
def delete_server(
    server_id: str,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    row = db.get(McpServer, server_id)
    if row is None or row.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail={"message": "MCP server was not found."})
    for grant in db.scalars(select(McpGrant).where(McpGrant.server_id == server_id)).all():
        db.delete(grant)
    db.delete(row)
    db.commit()
    return {"id": server_id}


@router.post("/api/mcp/servers/{server_id}/grants")
def grant_server(
    server_id: str,
    body: GrantBody,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    server = db.get(McpServer, server_id)
    user = db.get(User, body.userId)
    if server is None or server.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail={"message": "MCP server was not found."})
    if user is None or user.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail={"message": "User was not found."})
    existing = db.scalar(select(McpGrant).where(McpGrant.server_id == server_id, McpGrant.user_id == user.id))
    encoded = json.dumps(body.tools)
    if existing is None:
        existing = McpGrant(id=new_grant_id(), server_id=server_id, user_id=user.id, tools_json=encoded)
        db.add(existing)
    else:
        existing.tools_json = encoded
    db.commit()
    return {"id": existing.id, "serverId": server_id, "userId": user.id, "tools": body.tools}


@router.post("/api/mcp/servers/{server_id}/tools")
async def list_tools(
    server_id: str,
    identity: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        body = await invoke(
            db,
            identity,
            server_id,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            require_grant=False,
        )
    except McpDenied as error:
        raise HTTPException(status_code=error.status, detail={"message": error.message}) from error
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    tools = result.get("tools") if isinstance(result.get("tools"), list) else []
    names = [str(tool.get("name")) for tool in tools if isinstance(tool, dict) and tool.get("name")]
    return {"tools": names}


@router.post("/mcp/{server_id}")
async def mcp_proxy(
    server_id: str,
    request: Request,
    identity: Identity = Depends(require("inference:invoke")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail={"message": "MCP calls must be JSON-RPC."}) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail={"message": "MCP calls must be a JSON object."})
    try:
        return await invoke(
            db,
            identity,
            server_id,
            payload,
            require_grant=True,
            trace_id=request.headers.get("x-trace-id", "").strip() or None,
        )
    except McpDenied as error:
        raise HTTPException(status_code=error.status, detail={"message": error.message}) from error


@router.post("/v1/embeddings")
async def embeddings(
    body: EmbedBody,
    identity: Identity = Depends(require("inference:invoke")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _virtual, catalog, provider = resolve_target(db, body.model, None)
    if catalog is None or provider is None or provider.enabled != 1:
        raise HTTPException(status_code=404, detail={"message": f'Model "{body.model}" was not found.'})
    if (catalog.kind or "chat") != "embedding":
        raise HTTPException(
            status_code=400,
            detail={"message": "This model serves chat. Call POST /v1/chat/completions."},
        )
    secret = decrypt_secret(provider.secret_ciphertext)
    if secret is None:
        raise HTTPException(status_code=502, detail={"message": "Provider secret is not configured."})
    inputs = [body.input] if isinstance(body.input, str) else list(body.input)
    budget = ensure_user_budget(db, identity.user_id, identity.tenant_id)
    estimated = (max(sum(len(item) for item in inputs), 1) / 4) / 1_000_000 * catalog.input_price_per_million
    if budget.spent_usd + estimated > budget.monthly_usd:
        raise HTTPException(
            status_code=402,
            detail={"code": "AI_GW_BUDGET_EXCEEDED", "message": "This user's monthly budget is spent."},
        )
    if workspace_blocks(db, identity.tenant_id, estimated):
        raise HTTPException(
            status_code=402,
            detail={"code": "AI_GW_BUDGET_EXCEEDED", "message": "The workspace budget for this month is spent."},
        )
    try:
        vectors, tokens = await embed(provider, secret, catalog.provider_model, inputs)
    except ProviderError as error:
        raise HTTPException(status_code=502, detail={"message": f"Provider returned HTTP {error.status}."}) from error
    request_id, trace_id = new_ids()
    preview = (inputs[0] if inputs else "")[:500]
    cost = tokens / 1_000_000 * catalog.input_price_per_million
    db.add(
        Trace(
            trace_id=trace_id,
            request_id=request_id,
            tenant_id=identity.tenant_id,
            application_id=identity.application_id,
            user_id=identity.user_id,
            model=body.model,
            provider=provider.id,
            status="ok",
            error_code="",
            started_at=now(),
            duration_ms=0,
        )
    )
    record_usage(
        db,
        identity,
        request_id=request_id,
        trace_id=trace_id,
        model=body.model,
        provider=provider.id,
        input_tokens=tokens,
        output_tokens=0,
        cost=cost,
        kind="embedding",
    )
    append_audit(
        db,
        event="AI_EMBEDDING",
        requestId=request_id,
        traceId=trace_id,
        tenantId=identity.tenant_id,
        applicationId=identity.application_id,
        userId=identity.user_id,
        requestedModel=body.model,
        provider=provider.id,
        providerModel=catalog.provider_model,
        inputTokens=tokens,
        outputTokens=0,
        estimatedCost=round(cost, 8),
        prompt=preview,
        sent=preview,
        response=f"{len(vectors)} vectors",
    )
    db.commit()
    return {
        "object": "list",
        "model": body.model,
        "data": [{"object": "embedding", "index": index, "embedding": vector} for index, vector in enumerate(vectors)],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


@router.post("/v1/responses")
async def responses(
    body: ResponsesBody,
    identity: Identity = Depends(require("inference:invoke")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    request_id, trace_id = new_ids()
    try:
        payload = await execute_chat(
            db,
            identity,
            model=body.model,
            messages=_response_messages(body.input),
            temperature=body.temperature,
            max_tokens=body.max_output_tokens,
            request_id=request_id,
            trace_id=trace_id,
        )
    except GatewayFailure as error:
        raise HTTPException(status_code=error.status, detail={"code": error.code, "message": error.message}) from error
    text = ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            text = message["content"]
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return {
        "id": f"resp_{request_id}",
        "object": "response",
        "model": body.model,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": usage,
    }


@router.post("/v1/rerank")
async def rerank_route(
    body: RerankBody,
    identity: Identity = Depends(require("inference:invoke")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    del identity
    _virtual, catalog, provider = resolve_target(db, body.model, None)
    if catalog is None or provider is None or provider.enabled != 1:
        raise HTTPException(status_code=404, detail={"message": f'Model "{body.model}" was not found.'})
    secret = decrypt_secret(provider.secret_ciphertext)
    if secret is None:
        raise HTTPException(status_code=502, detail={"message": "Provider secret is not configured."})
    try:
        ranked = await rerank(provider, secret, catalog.provider_model, body.query, body.documents)
    except ProviderError:
        from gateway.providers import lexical_rerank

        ranked = lexical_rerank(body.query, body.documents)
    ranked["model"] = body.model
    return ranked


@router.get("/api/config")
def export_config(_: Identity = Depends(require("admin:read")), db: Session = Depends(get_db)) -> dict[str, object]:
    return {
        "models": [
            {
                "id": row.id,
                "providerId": row.provider_id,
                "providerModel": row.provider_model,
                "displayName": row.display_name,
                "inputPricePerMillion": row.input_price_per_million,
                "outputPricePerMillion": row.output_price_per_million,
                "kind": row.kind or "chat",
            }
            for row in db.scalars(select(CatalogModel).order_by(CatalogModel.id)).all()
        ],
        "routes": [
            {
                "id": row.id,
                "displayName": row.display_name,
                "providerId": row.provider_id,
                "modelId": row.model_id,
                "fallbackModelId": row.fallback_model_id,
                "strategy": row.strategy or "direct",
                "targets": json.loads(row.targets_json or "[]"),
            }
            for row in db.scalars(select(VirtualModel).order_by(VirtualModel.id)).all()
        ],
        "guardrails": [
            {
                "id": row.id,
                "name": row.name,
                "kind": row.kind,
                "phase": row.phase,
                "enabled": row.enabled == 1,
                "routeId": row.route_id,
                "config": json.loads(row.config_json or "{}"),
            }
            for row in db.scalars(select(GuardrailRule).order_by(GuardrailRule.id)).all()
        ],
        "mcpServers": [
            {"id": row.id, "name": row.name, "url": row.url, "authType": row.auth_type, "enabled": row.enabled == 1}
            for row in db.scalars(select(McpServer).order_by(McpServer.id)).all()
        ],
    }


@router.post("/api/config")
def import_config(
    body: dict[str, object],
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    models = body.get("models") if isinstance(body.get("models"), list) else []
    routes = body.get("routes") if isinstance(body.get("routes"), list) else []
    guardrails = body.get("guardrails") if isinstance(body.get("guardrails"), list) else []
    servers = body.get("mcpServers") if isinstance(body.get("mcpServers"), list) else []
    written = 0
    for item in models:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if db.get(Provider, str(item.get("providerId") or "")) is None:
            continue
        row = db.get(CatalogModel, str(item["id"]))
        if row is None:
            db.add(
                CatalogModel(
                    id=str(item["id"]),
                    provider_id=str(item.get("providerId")),
                    provider_model=str(item.get("providerModel") or item["id"]),
                    display_name=str(item.get("displayName") or item["id"]),
                    input_price_per_million=float(item.get("inputPricePerMillion") or 0),
                    output_price_per_million=float(item.get("outputPricePerMillion") or 0),
                    kind=str(item.get("kind") or "chat") if str(item.get("kind") or "chat") in {"chat", "embedding"} else "chat",
                )
            )
        written += 1
    db.flush()
    for item in routes:
        if not isinstance(item, dict) or not item.get("id") or db.get(VirtualModel, str(item["id"])) is not None:
            continue
        if db.get(CatalogModel, str(item.get("modelId") or "")) is None:
            continue
        db.add(
            VirtualModel(
                id=str(item["id"]),
                display_name=str(item.get("displayName") or item["id"]),
                provider_id=str(item.get("providerId")),
                model_id=str(item.get("modelId")),
                fallback_model_id=item.get("fallbackModelId") if isinstance(item.get("fallbackModelId"), str) else None,
                strategy=str(item.get("strategy") or "direct"),
                targets_json=json.dumps(item.get("targets") or []),
            )
        )
        written += 1
    for item in guardrails:
        if not isinstance(item, dict) or not item.get("id") or db.get(GuardrailRule, str(item["id"])) is not None:
            continue
        db.add(
            GuardrailRule(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                kind=str(item.get("kind") or "blocked-words"),
                phase=str(item.get("phase") or "input"),
                enabled=1 if item.get("enabled", True) else 0,
                config_json=json.dumps(item.get("config") or {}),
                route_id=item.get("routeId") if isinstance(item.get("routeId"), str) else None,
            )
        )
        written += 1
    for item in servers:
        if not isinstance(item, dict) or not item.get("id") or db.get(McpServer, str(item["id"])) is not None:
            continue
        url = str(item.get("url") or "")
        if not valid_server_url(url):
            continue
        db.add(
            McpServer(
                id=str(item["id"]),
                tenant_id=identity.tenant_id,
                name=str(item.get("name") or item["id"]),
                url=url,
                auth_type="none",
                secret_ciphertext=None,
                enabled=1 if item.get("enabled", True) else 0,
                created_at=now(),
            )
        )
        written += 1
    db.commit()
    return {"written": written}


@router.websocket("/v1/realtime")
async def realtime(websocket: WebSocket) -> None:
    await websocket.accept()
    token = websocket.query_params.get("access_token", "")
    model = websocket.query_params.get("model", "")
    db = SessionLocal()
    try:
        identity = authenticate(db, f"Bearer {token}")
        if identity is None or not allows(identity, "inference:invoke"):
            await websocket.close(code=1008)
            return
        _virtual, catalog, provider = resolve_target(db, model, None)
        if catalog is None or provider is None or provider.enabled != 1 or provider.type not in {"openai", "openai-compatible"}:
            await websocket.close(code=1008)
            return
        secret = decrypt_secret(provider.secret_ciphertext)
        if not secret:
            await websocket.close(code=1011)
            return
        request_id, trace_id = new_ids()
        db.add(
            Trace(
                trace_id=trace_id,
                request_id=request_id,
                tenant_id=identity.tenant_id,
                application_id=identity.application_id,
                user_id=identity.user_id,
                model=model,
                provider=provider.id,
                status="ok",
                error_code="",
                started_at=now(),
                duration_ms=0,
            )
        )
        append_audit(
            db,
            event="AI_REALTIME",
            requestId=request_id,
            traceId=trace_id,
            tenantId=identity.tenant_id,
            applicationId=identity.application_id,
            userId=identity.user_id,
            requestedModel=model,
            provider=provider.id,
            providerModel=catalog.provider_model,
        )
        db.commit()
        import websockets

        base = provider.base_url or "https://api.openai.com/v1"
        upstream_url = base.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
        upstream_url = f"{upstream_url}/realtime?model={catalog.provider_model}"
        async with websockets.connect(
            upstream_url,
            additional_headers={"Authorization": f"Bearer {secret}", "OpenAI-Beta": "realtime=v1"},
        ) as upstream:
            async def client_to_upstream() -> None:
                while True:
                    incoming = await websocket.receive()
                    if incoming["type"] == "websocket.disconnect":
                        break
                    if incoming.get("text") is not None:
                        await upstream.send(incoming["text"])
                    elif incoming.get("bytes") is not None:
                        await upstream.send(incoming["bytes"])

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except Exception:
        await websocket.close(code=1011)
    finally:
        db.close()


def _response_messages(value: str | list[object]) -> list[dict[str, object]]:
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    messages: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
        elif isinstance(item, dict):
            content = item.get("content", item.get("text", ""))
            messages.append({"role": str(item.get("role") or "user"), "content": content})
    return messages or [{"role": "user", "content": ""}]
