import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from gateway.agent_run import run_agent
from gateway.crypto import decrypt_secret, encrypt_secret
from gateway.directory import now
from gateway.http import get_db, require
from gateway.listing import clamp, contains, fetch, meta
from gateway.mcp_proxy import McpDenied, pop_state, post_form, save_state
from gateway.models import Application, LoginSession, McpServer, Prompt, Setting, Tenant, User
from gateway.oidc import discovery, exchange_code, new_state, take_state
from gateway import security
from gateway.security import Identity

router = APIRouter()


class WorkspaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)


class PromptBody(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1)


class AgentBody(BaseModel):
    model: str
    task: str = Field(min_length=1)
    serverIds: list[str] = Field(default_factory=list)
    maxSteps: int = Field(default=4, ge=1, le=8)


class RegionBody(BaseModel):
    region: str = ""


class SemanticBody(BaseModel):
    threshold: float = Field(ge=0, le=1)
    model: str = ""


class CertBody(BaseModel):
    required: bool


class SecondsBody(BaseModel):
    seconds: int = Field(ge=0, le=3600)


class OidcBody(BaseModel):
    clientId: str = ""
    clientSecret: str = ""
    redirectUrl: str = ""


def _setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def _read(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    return row.value if row is not None else ""


@router.get("/api/workspaces")
def list_workspaces(
    identity: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = []
    if identity.role != "admin":
        filters.append(Tenant.id == identity.tenant_id)
    if q.strip():
        filters.append(or_(contains(Tenant.name, q), contains(Tenant.id, q)))
    statement = select(Tenant).order_by(Tenant.name)
    if filters:
        statement = statement.where(*filters)
    rows, total = fetch(db, statement, size, start)
    ids = [row.id for row in rows]
    people: dict[str, int] = {}
    if ids:
        people = {
            tenant_id: int(count)
            for tenant_id, count in db.execute(
                select(User.tenant_id, func.count()).where(User.tenant_id.in_(ids)).group_by(User.tenant_id)
            ).all()
        }
    user = db.get(User, identity.user_id)
    home = db.get(Tenant, user.tenant_id) if user is not None else None
    return {
        "workspaces": [{"id": row.id, "name": row.name, "people": people.get(row.id, 0)} for row in rows],
        "homeId": user.tenant_id if user is not None else identity.tenant_id,
        "homeName": home.name if home is not None else identity.tenant_id,
        "page": meta(total, size, start),
    }


@router.post("/api/workspaces")
def create_workspace(
    body: WorkspaceBody,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    del identity
    workspace_id = f"ws_{uuid.uuid4().hex[:8]}"
    db.add(Tenant(id=workspace_id, name=body.name.strip(), created_at=now()))
    db.add(Application(id=f"{workspace_id}-app", tenant_id=workspace_id, name=body.name.strip(), created_at=now()))
    from gateway.seed import ensure_workspace_samples

    ensure_workspace_samples(db, workspace_id)
    db.commit()
    return {"id": workspace_id, "name": body.name.strip()}


@router.get("/api/prompts")
def list_prompts(
    identity: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = [Prompt.tenant_id == identity.tenant_id]
    if q.strip():
        filters.append(or_(contains(Prompt.name, q), contains(Prompt.body, q)))
    rows, total = fetch(db, select(Prompt).where(*filters).order_by(Prompt.name, Prompt.version.desc()), size, start)
    return {
        "prompts": [
            {"id": row.id, "name": row.name, "version": row.version, "body": row.body, "createdAt": row.created_at}
            for row in rows
        ],
        "page": meta(total, size, start),
    }


@router.post("/api/prompts")
def create_prompt(
    body: PromptBody,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    version = db.scalar(
        select(func.max(Prompt.version)).where(Prompt.tenant_id == identity.tenant_id, Prompt.name == body.name.strip())
    )
    row = Prompt(
        id=f"prompt_{uuid.uuid4().hex[:12]}",
        tenant_id=identity.tenant_id,
        name=body.name.strip(),
        version=int(version or 0) + 1,
        body=body.body,
        created_at=now(),
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name, "version": row.version}


@router.post("/api/agents/run")
async def agent_run(
    body: AgentBody,
    identity: Identity = Depends(require("inference:invoke")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return await run_agent(
        db,
        identity,
        model=body.model,
        task=body.task,
        server_ids=body.serverIds,
        max_steps=body.maxSteps,
    )


@router.put("/api/settings/region")
def put_region(
    body: RegionBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _setting(db, "gateway_region", body.region.strip())
    db.commit()
    return {"region": body.region.strip()}


@router.get("/api/settings/region")
def get_region(_: Identity = Depends(require("admin:read")), db: Session = Depends(get_db)) -> dict[str, str]:
    return {"region": _read(db, "gateway_region")}


@router.put("/api/settings/semantic")
def put_semantic(
    body: SemanticBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _setting(db, "semantic_threshold", str(body.threshold))
    _setting(db, "semantic_model", body.model.strip())
    db.commit()
    return {"threshold": body.threshold, "model": body.model.strip()}


@router.get("/api/settings/semantic")
def get_semantic(_: Identity = Depends(require("admin:read")), db: Session = Depends(get_db)) -> dict[str, object]:
    raw = _read(db, "semantic_threshold")
    try:
        threshold = float(raw) if raw else 0
    except ValueError:
        threshold = 0
    return {"threshold": threshold, "model": _read(db, "semantic_model")}


@router.get("/api/settings/client-cert")
def get_cert(_: Identity = Depends(require("admin:read")), db: Session = Depends(get_db)) -> dict[str, bool]:
    return {"required": _read(db, "require_client_cert") == "true"}


@router.put("/api/settings/client-cert")
def put_cert(
    body: CertBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    _setting(db, "require_client_cert", "true" if body.required else "false")
    db.commit()
    return {"required": body.required}


@router.put("/api/settings/oidc")
def put_oidc(
    body: OidcBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _setting(db, "oidc_client_id", body.clientId.strip())
    if body.clientSecret.strip():
        _setting(db, "oidc_client_secret", encrypt_secret(body.clientSecret.strip()) or "")
    _setting(db, "oidc_redirect_url", body.redirectUrl.strip())
    db.commit()
    return {"clientId": body.clientId.strip(), "redirectUrl": body.redirectUrl.strip(), "hasSecret": bool(_read(db, "oidc_client_secret"))}


@router.get("/api/settings/oidc")
def get_oidc(_: Identity = Depends(require("admin:read")), db: Session = Depends(get_db)) -> dict[str, object]:
    return {
        "clientId": _read(db, "oidc_client_id"),
        "redirectUrl": _read(db, "oidc_redirect_url"),
        "hasSecret": bool(_read(db, "oidc_client_secret")),
    }


@router.get("/api/settings/auth-public")
def auth_public(db: Session = Depends(get_db)) -> dict[str, bool]:
    ready = bool(_read(db, "jwt_issuer") and _read(db, "oidc_client_id") and _read(db, "oidc_client_secret"))
    return {"oidc": ready}


@router.put("/api/settings/config-cache")
def put_config_cache(
    body: SecondsBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    _setting(db, "config_cache_seconds", str(body.seconds))
    db.commit()
    return {"seconds": body.seconds}


@router.get("/api/settings/config-cache")
def get_config_cache(_: Identity = Depends(require("admin:read")), db: Session = Depends(get_db)) -> dict[str, int]:
    raw = _read(db, "config_cache_seconds")
    try:
        seconds = int(raw) if raw else 0
    except ValueError:
        seconds = 0
    return {"seconds": seconds}


@router.post("/auth/logout")
def auth_logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, bool]:
    session_id = request.cookies.get("aigw_session")
    if session_id:
        row = db.get(LoginSession, session_id)
        if row is not None:
            db.delete(row)
            db.commit()
    response.delete_cookie("aigw_session")
    return {"ok": True}


@router.get("/auth/login")
async def auth_login(db: Session = Depends(get_db)) -> RedirectResponse:
    issuer = _read(db, "jwt_issuer")
    client_id = _read(db, "oidc_client_id")
    redirect_url = _read(db, "oidc_redirect_url")
    if not issuer or not client_id or not redirect_url:
        raise HTTPException(status_code=400, detail={"message": "Directory sign-in is not configured."})
    try:
        document = await discovery(issuer)
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"message": "The identity provider discovery document could not be read."}) from exc
    endpoint = str(document.get("authorization_endpoint") or "")
    if not endpoint:
        raise HTTPException(status_code=502, detail={"message": "The identity provider did not publish an authorization endpoint."})
    state = new_state()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_url,
            "scope": "openid email",
            "state": state,
        }
    )
    return RedirectResponse(f"{endpoint}?{query}")


@router.get("/auth/callback")
async def auth_callback(
    code: str = "",
    state: str = "",
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not code or not take_state(state):
        raise HTTPException(status_code=400, detail={"message": "The sign-in state was missing or expired."})
    issuer = _read(db, "jwt_issuer")
    audience = _read(db, "jwt_audience") or _read(db, "oidc_client_id")
    jwks = _read(db, "jwt_jwks_url")
    client_secret = decrypt_secret(_read(db, "oidc_client_secret"))
    redirect_url = _read(db, "oidc_redirect_url")
    if not issuer or not client_secret or not jwks:
        raise HTTPException(status_code=400, detail={"message": "Directory sign-in is not configured."})
    try:
        document = await discovery(issuer)
        token = await exchange_code(
            str(document.get("token_endpoint") or ""),
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_url,
                "client_id": _read(db, "oidc_client_id"),
                "client_secret": client_secret,
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"message": "The identity provider rejected the sign-in code."}) from exc
    id_token = str(token.get("id_token") or "")
    try:
        claims = security._claims(id_token, issuer, audience, jwks)
    except Exception as exc:
        raise HTTPException(status_code=401, detail={"message": "The identity token could not be verified."}) from exc
    email = str(claims.get("email") or "").strip().lower()
    subject = str(claims.get("sub") or "").strip()
    user = db.scalar(select(User).where(User.email == email, User.status == "active")) if email else None
    if user is None and subject:
        user = db.scalar(select(User).where(User.external_subject == subject, User.status == "active"))
    if user is None:
        raise HTTPException(status_code=403, detail={"message": "This directory account is not a gateway user."})
    session = LoginSession(
        id=secrets.token_urlsafe(24),
        user_id=user.id,
        expires_at=(datetime.now(UTC) + timedelta(hours=12)).isoformat(),
        created_at=now(),
    )
    db.add(session)
    db.commit()
    response = RedirectResponse("/")
    response.set_cookie("aigw_session", session.id, httponly=True, samesite="lax", max_age=12 * 3600)
    return response


@router.get("/api/mcp/servers/{server_id}/oauth/start")
def oauth_start(
    server_id: str,
    request: Request,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    server = db.get(McpServer, server_id)
    if server is None or server.tenant_id != identity.tenant_id or server.auth_type != "oauth":
        raise HTTPException(status_code=404, detail={"message": "OAuth MCP server was not found."})
    state = secrets.token_urlsafe(18)
    save_state(state, server.id, identity.user_id)
    redirect_uri = str(request.base_url).rstrip("/") + "/api/mcp/oauth/callback"
    query = urlencode(
        {
            "response_type": "code",
            "client_id": server.oauth_client_id or "",
            "redirect_uri": redirect_uri,
            "scope": server.oauth_scopes or "",
            "state": state,
        }
    )
    return {"url": f"{server.authorize_url}?{query}"}


@router.get("/api/mcp/oauth/callback")
async def oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    db: Session = Depends(get_db),
) -> dict[str, str]:
    found = pop_state(state)
    if not code or found is None:
        raise HTTPException(status_code=400, detail={"message": "The OAuth state was missing or expired."})
    server_id, _user_id = found
    server = db.get(McpServer, server_id)
    if server is None or not server.token_url:
        raise HTTPException(status_code=404, detail={"message": "MCP server was not found."})
    client_secret = decrypt_secret(server.oauth_client_secret_ciphertext)
    if not client_secret or not server.oauth_client_id:
        raise HTTPException(status_code=400, detail={"message": "OAuth client credentials are missing."})
    redirect_uri = str(request.base_url).rstrip("/") + "/api/mcp/oauth/callback"
    try:
        token = await post_form(
            server.token_url,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": server.oauth_client_id,
                "client_secret": client_secret,
            },
        )
    except McpDenied as error:
        raise HTTPException(status_code=error.status, detail={"message": error.message}) from error
    access = str(token.get("access_token") or "")
    if not access:
        raise HTTPException(status_code=502, detail={"message": "OAuth token response did not include an access token."})
    server.secret_ciphertext = encrypt_secret(access)
    refresh = token.get("refresh_token")
    if isinstance(refresh, str) and refresh:
        server.refresh_ciphertext = encrypt_secret(refresh)
    expires_in = int(token.get("expires_in") or 3600)
    server.token_expires_at = (datetime.now(UTC) + timedelta(seconds=expires_in)).isoformat()
    db.commit()
    return {"connected": server.id}
