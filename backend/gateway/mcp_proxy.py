import json
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from gateway.crypto import decrypt_secret, encrypt_secret
from gateway.guardrails import PII_PATTERN, SECRET_PATTERN
from gateway.models import McpGrant, McpServer, Span, Trace
from gateway.pipeline import append_audit, new_ids
from gateway.security import Identity


class McpDenied(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def valid_server_url(url: str) -> bool:
    parsed = urlsplit(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username


async def post_upstream(url: str, headers: dict[str, str], payload: dict[str, object]) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        raise McpDenied(502, f"MCP server returned HTTP {response.status_code}.")
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise McpDenied(502, "MCP server returned a non-JSON body.") from exc
    if not isinstance(body, dict):
        raise McpDenied(502, "MCP server returned an unexpected payload.")
    return body


async def invoke(
    db: Session,
    identity: Identity,
    server_id: str,
    payload: dict[str, object],
    *,
    require_grant: bool,
    trace_id: str | None = None,
) -> dict[str, object]:
    server = db.get(McpServer, server_id)
    if server is None or server.tenant_id != identity.tenant_id or server.enabled != 1:
        raise McpDenied(404, "MCP server was not found.")
    grant = db.scalar(
        select(McpGrant).where(McpGrant.server_id == server.id, McpGrant.user_id == identity.user_id)
    )
    if require_grant and grant is None:
        raise McpDenied(403, "This user is not allowed to call that MCP server.")
    method = str(payload.get("method") or "")
    tool_name = _tool_name(payload)
    if method == "tools/call" and grant is not None:
        allowed = _allowed_tools(grant)
        if allowed and tool_name not in allowed:
            raise McpDenied(403, f'Tool "{tool_name}" is not allowed for this user.')
    headers = await upstream_headers(db, server)
    body = await post_upstream(server.url, headers, payload)
    if method == "tools/call":
        request_id, generated = new_ids()
        trace_id = trace_id or generated
        arguments = ""
        params = payload.get("params")
        if isinstance(params, dict):
            arguments = json.dumps(params.get("arguments") or {}, default=str)
        result = json.dumps(body.get("result") or body.get("error") or {}, default=str)
        trace_key = trace_id
        if db.get(Trace, trace_key) is None:
            db.add(
                Trace(
                    trace_id=trace_key,
                    request_id=request_id,
                    tenant_id=identity.tenant_id,
                    application_id=identity.application_id,
                    user_id=identity.user_id,
                    model=server.id,
                    provider="mcp",
                    status="ok",
                    error_code="",
                    started_at=now(),
                    duration_ms=0,
                )
            )
            db.flush()
        db.add(
            Span(
                trace_id=trace_key,
                name="mcp.tool",
                offset_ms=0,
                duration_ms=0,
                status="ok",
                detail=tool_name[:240],
            )
        )
        append_audit(
            db,
            event="AI_MCP_TOOL",
            requestId=request_id,
            traceId=trace_id,
            tenantId=identity.tenant_id,
            applicationId=identity.application_id,
            userId=identity.user_id,
            requestedModel=server.id,
            provider="mcp",
            providerModel=tool_name,
            prompt=_redact(arguments),
            sent=_redact(arguments),
            response=_redact(result),
        )
        db.commit()
    return body


def new_grant_id() -> str:
    return f"grant_{uuid.uuid4().hex[:12]}"


def server_view(row: McpServer) -> dict[str, object]:
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "authType": row.auth_type,
        "hasSecret": bool(row.secret_ciphertext or row.headers_ciphertext),
        "headerNames": _header_names(row),
        "oauthConnected": bool(row.refresh_ciphertext or (row.auth_type == "oauth" and row.secret_ciphertext)),
        "enabled": row.enabled == 1,
        "createdAt": row.created_at,
    }


def now() -> str:
    return datetime.now(UTC).isoformat()


def _tool_name(payload: dict[str, object]) -> str:
    params = payload.get("params")
    if isinstance(params, dict):
        return str(params.get("name") or "")
    return ""


def _allowed_tools(grant: McpGrant) -> list[str]:
    try:
        parsed = json.loads(grant.tools_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _redact(value: str) -> str:
    return SECRET_PATTERN.sub("[secret]", PII_PATTERN.sub("[pii]", value))[:4000]


async def post_form(url: str, data: dict[str, str]) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
        response = await client.post(url, data=data)
    if response.status_code >= 400:
        raise McpDenied(502, f"OAuth token endpoint returned HTTP {response.status_code}.")
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise McpDenied(502, "OAuth token endpoint returned a non-JSON body.") from exc
    if not isinstance(body, dict):
        raise McpDenied(502, "OAuth token endpoint returned an unexpected payload.")
    return body


def encode_headers(pairs: list[tuple[str, str]]) -> str | None:
    cleaned: dict[str, str] = {}
    for name, value in pairs:
        key = name.strip()
        stored = value.strip()
        if not key and not stored:
            continue
        if not key or not stored:
            raise ValueError("Each custom header needs a name and a value.")
        if not re.fullmatch(r"[!#$%&'*+\-.0-9A-Z^_`a-z|~]{1,64}", key):
            raise ValueError(f'Header name "{key}" is not valid.')
        if key.lower() in {"content-type", "content-length", "host", "transfer-encoding"}:
            raise ValueError(f'Header "{key}" is set by the gateway.')
        if key in cleaned:
            raise ValueError(f'Header "{key}" is listed twice.')
        cleaned[key] = stored
    if len(cleaned) > 5:
        raise ValueError("Use at most 5 custom headers.")
    if not cleaned:
        return None
    return encrypt_secret(json.dumps(cleaned))


async def upstream_headers(db: Session, server: McpServer) -> dict[str, str]:
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if server.auth_type in {"bearer", "oauth"}:
        headers["authorization"] = f"Bearer {await access_token(db, server)}"
    extra = _header_names(server, values=True)
    for name, value in extra.items():
        headers[name] = value
    return headers


def _header_names(row: McpServer, values: bool = False) -> list[str] | dict[str, str]:
    if not row.headers_ciphertext:
        return {} if values else []
    raw = decrypt_secret(row.headers_ciphertext)
    if not raw:
        return {} if values else []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {} if values else []
    if not isinstance(parsed, dict):
        return {} if values else []
    cleaned = {str(key): str(value) for key, value in parsed.items() if str(key).strip() and str(value)}
    if values:
        return cleaned
    return list(cleaned)


async def access_token(db: Session, server: McpServer) -> str:
    if server.auth_type == "bearer":
        secret = decrypt_secret(server.secret_ciphertext)
        if not secret:
            raise McpDenied(502, "MCP server secret is not configured.")
        return secret
    current = decrypt_secret(server.secret_ciphertext) or ""
    if current and (server.token_expires_at or "") > datetime.now(UTC).isoformat():
        return current
    refresh = decrypt_secret(server.refresh_ciphertext)
    client_secret = decrypt_secret(server.oauth_client_secret_ciphertext)
    if not refresh or not server.token_url or not server.oauth_client_id or not client_secret:
        raise McpDenied(401, "Connect this MCP server with OAuth before calling it.")
    body = await post_form(
        server.token_url,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": server.oauth_client_id,
            "client_secret": client_secret,
        },
    )
    return _store_token(db, server, body)


def _store_token(db: Session, server: McpServer, body: dict[str, object]) -> str:
    access = str(body.get("access_token") or "")
    if not access:
        raise McpDenied(502, "OAuth token response did not include an access token.")
    server.secret_ciphertext = encrypt_secret(access)
    refresh = body.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        server.refresh_ciphertext = encrypt_secret(refresh)
    expires_in = int(body.get("expires_in") or 3600)
    server.token_expires_at = datetime.fromtimestamp(datetime.now(UTC).timestamp() + expires_in, UTC).isoformat()
    db.commit()
    return access


_oauth_states: dict[str, tuple[str, str, float]] = {}


def save_state(state: str, server_id: str, user_id: str) -> None:
    _oauth_states[state] = (server_id, user_id, datetime.now(UTC).timestamp() + 600)


def pop_state(state: str) -> tuple[str, str] | None:
    item = _oauth_states.pop(state, None)
    if item is None or item[2] < datetime.now(UTC).timestamp():
        return None
    return item[0], item[1]
