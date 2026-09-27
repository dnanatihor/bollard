import json
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gateway.budgets import default_monthly, ensure_user_budget, month_spent, month_start, period_label, workspace_ceiling
from gateway.crypto import encrypt_secret
from gateway.db import session_scope
from gateway.directory import create_user
from gateway.listing import clamp, contains, fetch, meta
from gateway.metrics import render as render_metrics
from gateway.models import (
    ApiKey,
    Application,
    AuditEvent,
    Budget,
    CatalogModel,
    GuardrailHit,
    GuardrailRule,
    Provider,
    Setting,
    Span,
    Tenant,
    Trace,
    UsageEvent,
    User,
    UserBudget,
    VirtualModel,
)
from gateway.pipeline import GatewayFailure, execute_chat, iter_live_sse, new_ids, verify_audit
from gateway.routing import invalidate, resolve_target
from gateway.security import Identity, allows, authenticate, generate_key, hash_key, session_identity
from gateway.seed import create_admin, needs_setup

router = APIRouter()
ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]{1,128}$")
PROVIDER_TYPES = {"openai", "openai-compatible", "azure-openai", "anthropic", "gemini", "bedrock"}


def get_db():
    yield from session_scope()


def require(permission: str):
    def dependency(request: Request, db: Session = Depends(get_db)) -> Identity:
        identity = authenticate(db, request.headers.get("authorization"))
        if identity is None:
            identity = session_identity(db, request.cookies.get("aigw_session"))
        if identity is None:
            raise HTTPException(status_code=401, detail={"code": "AI_GW_UNAUTHORIZED", "message": "API key required."})
        cert = db.get(Setting, "require_client_cert")
        if cert is not None and cert.value == "true" and request.headers.get("x-client-verify", "").upper() != "SUCCESS":
            raise HTTPException(status_code=401, detail={"code": "AI_GW_UNAUTHORIZED", "message": "A client certificate is required."})
        workspace = request.headers.get("x-workspace", "").strip()
        if workspace and workspace != identity.tenant_id:
            if identity.role != "admin":
                raise HTTPException(status_code=403, detail={"message": "Only an admin can switch workspace."})
            if db.get(Tenant, workspace) is not None:
                identity = Identity(
                    key_id=identity.key_id,
                    prefix=identity.prefix,
                    tenant_id=workspace,
                    application_id=identity.application_id,
                    user_id=identity.user_id,
                    role=identity.role,
                )
        if not allows(identity, permission):
            raise HTTPException(
                status_code=403,
                detail={"code": "AI_GW_PERMISSION_DENIED", "message": "This key cannot perform that action."},
            )
        return identity

    return dependency


def _check_id(value: str) -> str:
    if not ID_PATTERN.match(value):
        raise HTTPException(status_code=400, detail={"message": "Use letters, numbers, and . _ : / - in ids."})
    return value


def _workspace_name(db: Session, tenant_id: str) -> str:
    row = db.get(Tenant, tenant_id)
    return row.name if row is not None else tenant_id


def _workspace_application(db: Session, tenant_id: str) -> str:
    row = db.scalars(select(Application).where(Application.tenant_id == tenant_id).order_by(Application.created_at)).first()
    if row is None:
        raise HTTPException(status_code=400, detail={"message": "This workspace has no application."})
    return row.id


def _allowed_until(value: str) -> str:
    try:
        day = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"message": "Choose the date the key is allowed until."}) from exc
    if day < datetime.now(UTC).date():
        raise HTTPException(status_code=400, detail={"message": "The allowed-until date has to be today or later."})
    return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=UTC).isoformat()


class KeyBody(BaseModel):
    userId: str = Field(min_length=1, max_length=64)
    expiresOn: str = Field(min_length=10, max_length=10)


class BootstrapBody(BaseModel):
    applicationName: str = Field(min_length=1, max_length=128)


class UserBody(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    displayName: str = ""
    role: str = "inference"
    applicationId: str = Field(default="", max_length=128)
    monthlyUsd: float | None = Field(default=None, ge=0)
    externalSubject: str | None = None


class UserPatch(BaseModel):
    status: str


class ProviderBody(BaseModel):
    id: str
    type: str
    baseUrl: str | None = None
    region: str | None = None
    apiVersion: str | None = None
    secret: str | None = None


class ProviderPatch(BaseModel):
    enabled: bool | None = None
    baseUrl: str | None = None
    region: str | None = None
    apiVersion: str | None = None
    secret: str | None = None


class ModelBody(BaseModel):
    id: str
    providerId: str
    providerModel: str
    displayName: str
    inputPricePerMillion: float = 0
    outputPricePerMillion: float = 0
    kind: str = "chat"


class RouteBody(BaseModel):
    id: str
    displayName: str
    providerId: str = ""
    modelId: str = ""
    fallbackModelId: str | None = None
    strategy: str = "direct"
    targets: list[dict[str, object]] = Field(default_factory=list)


class GuardrailPatch(BaseModel):
    enabled: bool | None = None
    config: dict[str, object] | None = None


class BudgetBody(BaseModel):
    monthlyUsd: float = Field(ge=0)


class WorkspaceBudgetBody(BaseModel):
    monthlyUsd: float = Field(ge=0)
    overallUsd: float = Field(ge=0)


class RateBody(BaseModel):
    requestsPerMinute: int = Field(ge=1, le=10000)


class CacheBody(BaseModel):
    ttlSeconds: int = Field(ge=0, le=86400)


class ChatMessage(BaseModel):
    role: str
    content: str | list[object] | None = None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, object]] | None = None


class ChatBody(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    tools: list[dict[str, object]] | None = None
    tool_choice: str | dict[str, object] | None = None
    promptId: str | None = None


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"message": "Database is unavailable."}) from exc
    return {"status": "ok"}


@router.get("/metrics")
def metrics_page() -> PlainTextResponse:
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")


@router.get("/api/bootstrap")
def bootstrap_status(db: Session = Depends(get_db)) -> dict[str, bool]:
    return {"needsSetup": needs_setup(db)}


@router.post("/api/bootstrap")
def bootstrap(body: BootstrapBody, db: Session = Depends(get_db)) -> dict[str, str]:
    if not needs_setup(db):
        raise HTTPException(status_code=409, detail={"message": "Setup is already complete. Sign in with an admin key."})
    name = body.applicationName.strip()
    if not name:
        raise HTTPException(status_code=400, detail={"message": "Name the application."})
    plaintext = create_admin(db, name)
    return {"plaintext": plaintext, "prefix": plaintext[:16], "role": "admin", "applicationName": name}


@router.get("/api/me")
def me(identity: Identity = Depends(require("self:read")), db: Session = Depends(get_db)) -> dict[str, object]:
    user = db.get(User, identity.user_id)
    workspace = db.get(Tenant, identity.tenant_id)
    home = db.get(Tenant, user.tenant_id) if user is not None else None
    budget = ensure_user_budget(db, identity.user_id, identity.tenant_id)
    db.commit()
    return {
        "role": identity.role,
        "email": "" if user is None else user.email,
        "displayName": "" if user is None else user.display_name,
        "userId": identity.user_id,
        "workspaceId": identity.tenant_id,
        "workspaceName": workspace.name if workspace is not None else identity.tenant_id,
        "homeId": user.tenant_id if user is not None else identity.tenant_id,
        "homeName": home.name if home is not None else identity.tenant_id,
        "monthlyUsd": budget.monthly_usd,
        "spentUsd": budget.spent_usd,
        "period": period_label(),
    }


@router.get("/api/overview")
def overview(identity: Identity = Depends(require("admin:read")), db: Session = Depends(get_db)) -> dict[str, object]:
    tenant = identity.tenant_id
    traces = db.scalars(select(Trace).where(Trace.tenant_id == tenant).order_by(Trace.started_at.desc()).limit(8)).all()
    blocked = db.scalar(select(func.count()).select_from(GuardrailHit).where(GuardrailHit.action == "deny"))
    spent = month_spent(db, tenant)
    ceiling = workspace_ceiling(db, tenant)
    return {
        "requests": db.scalar(select(func.count()).select_from(Trace).where(Trace.tenant_id == tenant)) or 0,
        "blocked": blocked or 0,
        "auditVerified": verify_audit(db),
        "budget": {"monthlyUsd": ceiling, "spentUsd": spent},
        "providers": db.scalar(select(func.count()).select_from(Provider)) or 0,
        "routes": db.scalar(select(func.count()).select_from(VirtualModel)) or 0,
        "inferenceKeys": db.scalar(
            select(func.count()).select_from(ApiKey).where(
                ApiKey.tenant_id == tenant, ApiKey.role == "inference", ApiKey.status == "active"
            )
        )
        or 0,
        "traces": [_trace(row) for row in traces],
    }


@router.get("/api/users")
def list_users(
    identity: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    role: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = [User.tenant_id == identity.tenant_id]
    if role in {"inference", "auditor", "admin"}:
        filters.append(User.role == role)
    if status in {"active", "disabled"}:
        filters.append(User.status == status)
    if q.strip():
        filters.append(or_(contains(User.email, q), contains(User.display_name, q), contains(User.application_id, q)))
    rows, total = fetch(db, select(User).where(*filters).order_by(User.email), size, start)
    ids = [row.id for row in rows]
    budgets = {}
    if ids:
        budgets = {row.user_id: row for row in db.scalars(select(UserBudget).where(UserBudget.user_id.in_(ids))).all()}
    workspace_name = _workspace_name(db, identity.tenant_id)
    return {
        "workspaceId": identity.tenant_id,
        "workspaceName": workspace_name,
        "users": [
            {
                "id": row.id,
                "email": row.email,
                "displayName": row.display_name,
                "applicationId": row.application_id,
                "role": row.role,
                "status": row.status,
                "createdAt": row.created_at,
                "workspaceId": identity.tenant_id,
                "workspaceName": workspace_name,
                "monthlyUsd": budgets[row.id].monthly_usd if row.id in budgets else default_monthly(db, identity.tenant_id),
                "spentUsd": budgets[row.id].spent_usd if row.id in budgets else 0,
            }
            for row in rows
        ],
        "page": meta(total, size, start),
    }


@router.post("/api/users")
def post_user(
    body: UserBody,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if body.role not in {"inference", "auditor", "admin"}:
        raise HTTPException(status_code=400, detail={"message": "Role must be inference, auditor, or admin."})
    try:
        user = create_user(
            db,
            tenant_id=identity.tenant_id,
            email=body.email,
            display_name=body.displayName,
            role=body.role,
            application_id=body.applicationId.strip() or _workspace_application(db, identity.tenant_id),
            external_subject=body.externalSubject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc)}) from exc
    row = ensure_user_budget(db, user.id, identity.tenant_id)
    if body.monthlyUsd is not None:
        row.monthly_usd = body.monthlyUsd
    db.commit()
    return {"id": user.id, "email": user.email, "role": user.role, "applicationId": user.application_id, "monthlyUsd": row.monthly_usd}


@router.patch("/api/users/{user_id}")
def patch_user(
    user_id: str,
    body: UserPatch,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if body.status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail={"message": "Status must be active or disabled."})
    user = db.get(User, user_id)
    if user is None or user.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail={"message": "User was not found."})
    if user.id == identity.user_id and body.status == "disabled":
        raise HTTPException(status_code=400, detail={"message": "You cannot disable the signed-in user."})
    user.status = body.status
    db.commit()
    return {"id": user.id, "status": user.status}


@router.get("/api/keys")
def list_keys(
    identity: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    status: str = "",
    role: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = [ApiKey.tenant_id == identity.tenant_id]
    if status in {"active", "revoked"}:
        filters.append(ApiKey.status == status)
    if role in {"inference", "auditor", "admin"}:
        filters.append(ApiKey.role == role)
    if q.strip():
        matched_users = select(User.id).where(
            User.tenant_id == identity.tenant_id,
            or_(contains(User.email, q), contains(User.display_name, q)),
        )
        filters.append(or_(contains(ApiKey.prefix, q), contains(ApiKey.application_id, q), ApiKey.user_id.in_(matched_users)))
    rows, total = fetch(db, select(ApiKey).where(*filters).order_by(ApiKey.created_at.desc()), size, start)
    ids = [row.user_id for row in rows]
    users = {}
    if ids:
        users = {row.id: row for row in db.scalars(select(User).where(User.id.in_(ids))).all()}
    workspace_name = _workspace_name(db, identity.tenant_id)
    return {
        "workspaceId": identity.tenant_id,
        "workspaceName": workspace_name,
        "keys": [
            {
                "id": row.id,
                "prefix": row.prefix,
                "userId": row.user_id,
                "userEmail": users[row.user_id].email if row.user_id in users else row.user_id,
                "applicationId": row.application_id,
                "role": row.role,
                "status": row.status,
                "workspaceId": identity.tenant_id,
                "workspaceName": workspace_name,
                "createdAt": row.created_at,
                "expiresAt": row.expires_at,
                "lastUsedAt": row.last_used_at,
            }
            for row in rows
        ],
        "page": meta(total, size, start),
    }


@router.post("/api/keys")
def create_key(
    body: KeyBody,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user = db.get(User, body.userId)
    if user is None or user.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=400, detail={"message": "Choose a user in this tenant."})
    if user.status != "active":
        raise HTTPException(status_code=400, detail={"message": "That user is disabled."})
    plaintext = generate_key()
    expires_at = _allowed_until(body.expiresOn)
    db.add(
        ApiKey(
            id=f"key_{uuid.uuid4().hex[:12]}",
            prefix=plaintext[:16],
            key_hash=hash_key(plaintext),
            tenant_id=user.tenant_id,
            application_id=user.application_id,
            user_id=user.id,
            role=user.role,
            status="active",
            created_at=datetime.now(UTC).isoformat(),
            expires_at=expires_at,
        )
    )
    db.commit()
    return {
        "plaintext": plaintext,
        "prefix": plaintext[:16],
        "userId": user.id,
        "userEmail": user.email,
        "applicationId": user.application_id,
        "role": user.role,
        "expiresAt": expires_at,
    }


@router.post("/api/keys/{key_id}/revoke")
def revoke_key(
    key_id: str,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    row = db.get(ApiKey, key_id)
    if row is None or row.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail={"message": "Key was not found."})
    if row.id == identity.key_id:
        raise HTTPException(status_code=400, detail={"message": "You cannot revoke the key you are using."})
    row.status = "revoked"
    db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/api/keys/{key_id}/rotate")
def rotate_key(
    key_id: str,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    row = db.get(ApiKey, key_id)
    if row is None or row.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail={"message": "Key was not found."})
    if row.status != "active":
        raise HTTPException(status_code=400, detail={"message": "Only an active key can be rotated."})
    plaintext = generate_key()
    row.prefix = plaintext[:16]
    row.key_hash = hash_key(plaintext)
    db.commit()
    return {"plaintext": plaintext, "prefix": row.prefix, "id": row.id}


@router.get("/api/providers")
def list_providers(
    _: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = []
    if q.strip():
        filters.append(or_(contains(Provider.id, q), contains(Provider.type, q)))
    statement = select(Provider).order_by(Provider.id)
    if filters:
        statement = statement.where(*filters)
    rows, total = fetch(db, statement, size, start)
    return {
        "providers": [
            {
                "id": row.id,
                "type": row.type,
                "baseUrl": row.base_url,
                "region": row.region,
                "apiVersion": row.api_version,
                "enabled": row.enabled == 1,
                "hasSecret": bool(row.secret_ciphertext),
            }
            for row in rows
        ],
        "page": meta(total, size, start),
    }


@router.post("/api/providers")
def create_provider(
    body: ProviderBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _check_id(body.id)
    if body.type not in PROVIDER_TYPES:
        raise HTTPException(status_code=400, detail={"message": "Unknown provider type."})
    if db.get(Provider, body.id) is not None:
        raise HTTPException(status_code=409, detail={"message": "That provider id already exists."})
    db.add(
        Provider(
            id=body.id,
            type=body.type,
            base_url=body.baseUrl or None,
            region=body.region or None,
            api_version=body.apiVersion or None,
            secret_ciphertext=encrypt_secret(body.secret) if body.secret else None,
            enabled=1,
        )
    )
    db.commit()
    invalidate()
    return {"id": body.id}


@router.patch("/api/providers/{provider_id}")
def patch_provider(
    provider_id: str,
    body: ProviderPatch,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    row = db.get(Provider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"message": "Provider not found."})
    if body.enabled is not None:
        row.enabled = 1 if body.enabled else 0
    if body.baseUrl is not None:
        row.base_url = body.baseUrl or None
    if body.region is not None:
        row.region = body.region or None
    if body.apiVersion is not None:
        row.api_version = body.apiVersion or None
    if body.secret:
        row.secret_ciphertext = encrypt_secret(body.secret)
    db.commit()
    return {"id": row.id}


@router.delete("/api/providers/{provider_id}")
def delete_provider(
    provider_id: str,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    row = db.get(Provider, provider_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"message": "Provider not found."})
    db.delete(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={"message": "Remove models and routes that use this provider first."},
        ) from None
    return {"id": provider_id}


@router.get("/api/models")
def list_models(
    _: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    kind: str = "",
    provider: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = []
    if kind in {"chat", "embedding"}:
        filters.append(CatalogModel.kind == kind)
    if provider.strip():
        filters.append(CatalogModel.provider_id == provider.strip())
    if q.strip():
        filters.append(or_(contains(CatalogModel.id, q), contains(CatalogModel.display_name, q), contains(CatalogModel.provider_id, q)))
    statement = select(CatalogModel).order_by(CatalogModel.id)
    if filters:
        statement = statement.where(*filters)
    rows, total = fetch(db, statement, size, start)
    return {"models": [_model(row) for row in rows], "page": meta(total, size, start)}


@router.post("/api/models")
def create_model(
    body: ModelBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _check_id(body.id)
    if body.kind not in {"chat", "embedding"}:
        raise HTTPException(status_code=400, detail={"message": "Kind must be chat or embedding."})
    if db.get(Provider, body.providerId) is None:
        raise HTTPException(status_code=400, detail={"message": "Choose a provider that exists."})
    if db.get(CatalogModel, body.id) is not None:
        raise HTTPException(status_code=409, detail={"message": "That model id already exists."})
    db.add(
        CatalogModel(
            id=body.id,
            provider_id=body.providerId,
            provider_model=body.providerModel,
            display_name=body.displayName,
            input_price_per_million=body.inputPricePerMillion,
            output_price_per_million=body.outputPricePerMillion,
            kind=body.kind,
        )
    )
    db.commit()
    invalidate()
    return {"id": body.id}


@router.get("/api/routes")
def list_routes(
    _: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = []
    if q.strip():
        filters.append(or_(contains(VirtualModel.id, q), contains(VirtualModel.display_name, q), contains(VirtualModel.provider_id, q)))
    statement = select(VirtualModel).order_by(VirtualModel.id)
    if filters:
        statement = statement.where(*filters)
    rows, total = fetch(db, statement, size, start)
    model_ids = {row.model_id for row in rows}
    kinds = {}
    if model_ids:
        kinds = {
            row.id: row.kind or "chat"
            for row in db.scalars(select(CatalogModel).where(CatalogModel.id.in_(model_ids))).all()
        }
    return {
        "routes": [
            {
                "id": row.id,
                "displayName": row.display_name,
                "providerId": row.provider_id,
                "modelId": row.model_id,
                "modelKind": kinds.get(row.model_id, "chat"),
                "fallbackModelId": row.fallback_model_id,
                "strategy": row.strategy or "direct",
                "targets": json.loads(row.targets_json or "[]"),
            }
            for row in rows
        ],
        "page": meta(total, size, start),
    }


@router.post("/api/routes")
def create_route(
    body: RouteBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    _check_id(body.id)
    strategy = body.strategy or "direct"
    if strategy not in {"direct", "weighted", "least_cost", "conditional", "round_robin", "least_latency", "canary", "region"}:
        raise HTTPException(status_code=400, detail={"message": "Strategy must be direct, weighted, least_cost, conditional, round_robin, least_latency, canary, or region."})
    targets = body.targets
    model_id = body.modelId
    provider_id = body.providerId
    if strategy == "direct":
        model = db.get(CatalogModel, model_id)
        if model is None or model.provider_id != provider_id:
            raise HTTPException(status_code=400, detail={"message": "The route model must belong to the selected provider."})
    else:
        if not targets:
            raise HTTPException(status_code=400, detail={"message": "This strategy needs at least one target model."})
        first = db.get(CatalogModel, str(targets[0].get("modelId") or ""))
        if first is None:
            raise HTTPException(status_code=400, detail={"message": "A target model was not found."})
        for target in targets:
            if db.get(CatalogModel, str(target.get("modelId") or "")) is None:
                raise HTTPException(status_code=400, detail={"message": "A target model was not found."})
        model_id = first.id
        provider_id = first.provider_id
    if db.get(VirtualModel, body.id) is not None:
        raise HTTPException(status_code=409, detail={"message": "That route id already exists."})
    if body.fallbackModelId:
        fallback = db.get(CatalogModel, body.fallbackModelId)
        if fallback is None:
            raise HTTPException(status_code=400, detail={"message": "The fallback model was not found."})
    db.add(
        VirtualModel(
            id=body.id,
            display_name=body.displayName,
            provider_id=provider_id,
            model_id=model_id,
            fallback_model_id=body.fallbackModelId,
            strategy=strategy,
            targets_json=json.dumps(targets),
        )
    )
    db.commit()
    invalidate()
    return {"id": body.id}


@router.get("/api/guardrails")
def list_guardrails(
    _: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = []
    if q.strip():
        filters.append(or_(contains(GuardrailRule.name, q), contains(GuardrailRule.kind, q)))
    statement = select(GuardrailRule).order_by(GuardrailRule.id)
    if filters:
        statement = statement.where(*filters)
    rows, total = fetch(db, statement, size, start)
    return {
        "rules": [
            {
                "id": row.id,
                "name": row.name,
                "kind": row.kind,
                "phase": row.phase,
                "enabled": row.enabled == 1,
                "routeId": row.route_id,
                "config": json.loads(row.config_json or "{}"),
            }
            for row in rows
        ],
        "page": meta(total, size, start),
    }


@router.patch("/api/guardrails/{rule_id}")
def patch_guardrail(
    rule_id: str,
    body: GuardrailPatch,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    row = db.get(GuardrailRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"message": "Guardrail not found."})
    if body.enabled is not None:
        row.enabled = 1 if body.enabled else 0
    if body.config is not None:
        row.config_json = json.dumps(body.config)
    db.commit()
    return {"id": row.id}


@router.get("/api/guardrail-hits")
def guardrail_hits(
    _: Identity = Depends(require("audit:read")),
    db: Session = Depends(get_db),
    q: str = "",
    action: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = []
    if action in {"deny", "redact"}:
        filters.append(GuardrailHit.action == action)
    if q.strip():
        filters.append(or_(contains(GuardrailHit.rule, q), contains(GuardrailHit.reason, q), contains(GuardrailHit.phase, q)))
    statement = select(GuardrailHit).order_by(GuardrailHit.id.desc())
    if filters:
        statement = statement.where(*filters)
    rows, total = fetch(db, statement, size, start)
    return {
        "hits": [
            {
                "id": row.id,
                "traceId": row.trace_id,
                "requestId": row.request_id,
                "phase": row.phase,
                "rule": row.rule,
                "action": row.action,
                "reason": row.reason,
                "createdAt": row.created_at,
            }
            for row in rows
        ],
        "page": meta(total, size, start),
    }


@router.get("/api/audit")
def audit(
    identity: Identity = Depends(require("audit:read")),
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = [AuditEvent.tenant_id == identity.tenant_id]
    if q.strip():
        matched_users = select(User.id).where(
            User.tenant_id == identity.tenant_id,
            or_(contains(User.email, q), contains(User.display_name, q)),
        )
        filters.append(
            or_(
                contains(AuditEvent.event, q),
                contains(AuditEvent.requested_model, q),
                contains(AuditEvent.provider, q),
                contains(AuditEvent.prompt_text, q),
                AuditEvent.user_id.in_(matched_users),
            )
        )
    rows, total = fetch(db, select(AuditEvent).where(*filters).order_by(AuditEvent.id.desc()), size, start)
    ids = {row.user_id for row in rows}
    users = {}
    if ids:
        users = {row.id: row for row in db.scalars(select(User).where(User.id.in_(ids))).all()}
    return {
        "verified": verify_audit(db),
        "events": [
            {
                "id": row.id,
                "event": row.event,
                "requestId": row.request_id,
                "traceId": row.trace_id,
                "userId": row.user_id,
                "userEmail": users[row.user_id].email if row.user_id in users else row.user_id,
                "applicationId": row.application_id,
                "requestedModel": row.requested_model,
                "provider": row.provider,
                "providerModel": row.provider_model,
                "inputTokens": row.input_tokens,
                "outputTokens": row.output_tokens,
                "estimatedCost": row.estimated_cost,
                "timestamp": row.timestamp,
                "prevHash": row.prev_hash,
                "hash": row.hash,
                "prompt": row.prompt_text or "",
                "sent": row.sent_text or "",
                "response": row.response_text or "",
            }
            for row in rows
        ],
        "page": meta(total, size, start),
    }


@router.get("/api/traces")
def traces(
    identity: Identity = Depends(require("audit:read")),
    db: Session = Depends(get_db),
    q: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = [Trace.tenant_id == identity.tenant_id]
    if status.strip():
        filters.append(Trace.status == status.strip())
    if q.strip():
        filters.append(or_(contains(Trace.model, q), contains(Trace.provider, q), contains(Trace.user_id, q), contains(Trace.status, q)))
    rows, total = fetch(db, select(Trace).where(*filters).order_by(Trace.started_at.desc()), size, start)
    return {"traces": [_trace(row) for row in rows], "page": meta(total, size, start)}


@router.get("/api/traces/{trace_id}")
def trace_detail(
    trace_id: str,
    identity: Identity = Depends(require("audit:read")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    row = db.get(Trace, trace_id)
    if row is None or row.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail={"message": "Trace not found."})
    spans = db.scalars(select(Span).where(Span.trace_id == trace_id).order_by(Span.id)).all()
    hits = db.scalars(select(GuardrailHit).where(GuardrailHit.trace_id == trace_id).order_by(GuardrailHit.id)).all()
    audit = db.scalar(select(AuditEvent).where(AuditEvent.trace_id == trace_id).order_by(AuditEvent.id.desc()))
    return {
        "trace": _trace(row),
        "prompt": "" if audit is None else audit.prompt_text or "",
        "sent": "" if audit is None else audit.sent_text or "",
        "response": "" if audit is None else audit.response_text or "",
        "spans": [
            {
                "name": span.name,
                "offsetMs": span.offset_ms,
                "durationMs": span.duration_ms,
                "status": span.status,
                "detail": span.detail,
            }
            for span in spans
        ],
        "guardrails": [
            {"phase": hit.phase, "rule": hit.rule, "action": hit.action, "reason": hit.reason}
            for hit in hits
        ],
    }


@router.get("/api/budget")
def get_budget(
    identity: Identity = Depends(require("admin:read")),
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    rpm = db.get(Setting, "requests_per_minute")
    size, start = clamp(limit, offset)
    tenant = identity.tenant_id
    filters = [User.tenant_id == tenant]
    if q.strip():
        filters.append(or_(contains(User.email, q), contains(User.display_name, q)))
    users, total = fetch(db, select(User).where(*filters).order_by(User.email), size, start)
    ids = [user.id for user in users]
    budgets = {}
    if ids:
        budgets = {row.user_id: row for row in db.scalars(select(UserBudget).where(UserBudget.user_id.in_(ids))).all()}
    start_at = month_start()
    usage_filter = [UsageEvent.tenant_id == tenant, UsageEvent.created_at >= start_at]
    requests, input_tokens, output_tokens, spent = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.coalesce(func.sum(UsageEvent.estimated_cost), 0),
        ).where(*usage_filter)
    ).one()
    by_kind = [
        {"kind": kind, "requests": int(count), "spentUsd": float(cost or 0)}
        for kind, count, cost in db.execute(
            select(UsageEvent.kind, func.count(), func.coalesce(func.sum(UsageEvent.estimated_cost), 0))
            .where(*usage_filter)
            .group_by(UsageEvent.kind)
        ).all()
    ]
    by_model = [
        {
            "model": model,
            "kind": kind or "chat",
            "requests": int(count),
            "inputTokens": int(inputs or 0),
            "outputTokens": int(outputs or 0),
            "spentUsd": float(cost or 0),
        }
        for model, kind, count, inputs, outputs, cost in db.execute(
            select(
                UsageEvent.model,
                UsageEvent.kind,
                func.count(),
                func.coalesce(func.sum(UsageEvent.input_tokens), 0),
                func.coalesce(func.sum(UsageEvent.output_tokens), 0),
                func.coalesce(func.sum(UsageEvent.estimated_cost), 0),
            )
            .where(*usage_filter)
            .group_by(UsageEvent.model, UsageEvent.kind)
            .order_by(func.sum(UsageEvent.estimated_cost).desc())
            .limit(20)
        ).all()
    ]
    leaders = [
        {
            "userId": user_id,
            "email": email,
            "displayName": name,
            "requests": int(count),
            "spentUsd": float(cost or 0),
            "monthlyUsd": float(cap or 0),
        }
        for user_id, email, name, count, cost, cap in db.execute(
            select(
                User.id,
                User.email,
                User.display_name,
                func.count(UsageEvent.id),
                func.coalesce(func.sum(UsageEvent.estimated_cost), 0),
                func.max(UserBudget.monthly_usd),
            )
            .join(UsageEvent, UsageEvent.user_id == User.id)
            .outerjoin(UserBudget, UserBudget.user_id == User.id)
            .where(User.tenant_id == tenant, UsageEvent.created_at >= start_at)
            .group_by(User.id, User.email, User.display_name)
            .order_by(func.sum(UsageEvent.estimated_cost).desc())
            .limit(12)
        ).all()
    ]
    workspace_name = _workspace_name(db, tenant)
    return {
        "defaultMonthlyUsd": default_monthly(db, tenant),
        "overallUsd": workspace_ceiling(db, tenant),
        "requestsPerMinute": int(rpm.value) if rpm is not None else 60,
        "cacheTtlSeconds": int(db.get(Setting, "cache_ttl_seconds").value) if db.get(Setting, "cache_ttl_seconds") is not None else 0,
        "period": period_label(),
        "consumedUsd": float(spent or 0),
        "usage": {
            "requests": int(requests or 0),
            "inputTokens": int(input_tokens or 0),
            "outputTokens": int(output_tokens or 0),
            "spentUsd": float(spent or 0),
            "byKind": by_kind,
            "byModel": by_model,
        },
        "workspaceId": tenant,
        "workspaceName": workspace_name,
        "leaders": leaders,
        "users": [
            {
                "userId": user.id,
                "email": user.email,
                "displayName": user.display_name,
                "status": user.status,
                "workspaceId": tenant,
                "workspaceName": workspace_name,
                "monthlyUsd": budgets[user.id].monthly_usd if user.id in budgets else default_monthly(db, tenant),
                "spentUsd": budgets[user.id].spent_usd if user.id in budgets else 0,
            }
            for user in users
        ],
        "page": meta(total, size, start),
    }


@router.get("/api/usage")
def usage(
    identity: Identity = Depends(require("self:read")),
    db: Session = Depends(get_db),
    q: str = "",
    kind: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    size, start = clamp(limit, offset)
    filters = [UsageEvent.tenant_id == identity.tenant_id, UsageEvent.created_at >= month_start()]
    if identity.role == "inference":
        filters.append(UsageEvent.user_id == identity.user_id)
    if kind in {"chat", "embedding"}:
        filters.append(UsageEvent.kind == kind)
    if q.strip():
        filters.append(or_(contains(UsageEvent.model, q), contains(UsageEvent.provider, q)))
    rows, total = fetch(db, select(UsageEvent).where(*filters).order_by(UsageEvent.id.desc()), size, start)
    requests, input_tokens, output_tokens, spent = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.coalesce(func.sum(UsageEvent.estimated_cost), 0),
        ).where(*filters)
    ).one()
    return {
        "period": period_label(),
        "requests": int(requests or 0),
        "inputTokens": int(input_tokens or 0),
        "outputTokens": int(output_tokens or 0),
        "spentUsd": float(spent or 0),
        "events": [
            {
                "id": row.id,
                "model": row.model,
                "provider": row.provider,
                "kind": row.kind or "chat",
                "inputTokens": row.input_tokens,
                "outputTokens": row.output_tokens,
                "estimatedCost": row.estimated_cost,
                "createdAt": row.created_at,
            }
            for row in rows
        ],
        "page": meta(total, size, start),
    }


@router.put("/api/budget")
def put_budget(
    body: WorkspaceBudgetBody,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, float]:
    row = db.get(Budget, identity.tenant_id)
    if row is None:
        row = Budget(tenant_id=identity.tenant_id, monthly_usd=body.monthlyUsd, overall_usd=body.overallUsd, spent_usd=0)
        db.add(row)
    else:
        row.monthly_usd = body.monthlyUsd
        row.overall_usd = body.overallUsd
    db.commit()
    return {"defaultMonthlyUsd": row.monthly_usd, "overallUsd": row.overall_usd}


@router.put("/api/users/{user_id}/budget")
def put_user_budget(
    user_id: str,
    body: BudgetBody,
    identity: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail={"message": "User was not found."})
    row = ensure_user_budget(db, user.id, user.tenant_id)
    row.monthly_usd = body.monthlyUsd
    db.commit()
    return {"userId": user.id, "monthlyUsd": row.monthly_usd, "spentUsd": row.spent_usd}


@router.put("/api/settings/rate-limit")
def put_rate(
    body: RateBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    row = db.get(Setting, "requests_per_minute")
    if row is None:
        db.add(Setting(key="requests_per_minute", value=str(body.requestsPerMinute)))
    else:
        row.value = str(body.requestsPerMinute)
    db.commit()
    return {"requestsPerMinute": body.requestsPerMinute}


@router.put("/api/settings/cache")
def put_cache(
    body: CacheBody,
    _: Identity = Depends(require("admin:write")),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    row = db.get(Setting, "cache_ttl_seconds")
    if row is None:
        db.add(Setting(key="cache_ttl_seconds", value=str(body.ttlSeconds)))
    else:
        row.value = str(body.ttlSeconds)
    db.commit()
    return {"ttlSeconds": body.ttlSeconds}


@router.get("/v1/models")
def inference_models(
    identity: Identity = Depends(require("inference:invoke")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    del identity
    routes = db.scalars(select(VirtualModel).order_by(VirtualModel.id)).all()
    models = db.scalars(select(CatalogModel).order_by(CatalogModel.id)).all()
    route_kinds = {}
    model_ids = {row.model_id for row in routes}
    if model_ids:
        route_kinds = {row.id: row.kind or "chat" for row in db.scalars(select(CatalogModel).where(CatalogModel.id.in_(model_ids))).all()}
    data = [{"id": row.id, "object": "model", "owned_by": row.provider_id, "kind": route_kinds.get(row.model_id, "chat")} for row in routes]
    data.extend({"id": row.id, "object": "model", "owned_by": row.provider_id, "kind": row.kind or "chat"} for row in models)
    return {"object": "list", "data": data}


@router.post("/v1/chat/completions")
async def chat(
    body: ChatBody,
    request: Request,
    identity: Identity = Depends(require("inference:invoke")),
    db: Session = Depends(get_db),
):
    request_id, trace_id = new_ids()
    incoming_request = request.headers.get("x-request-id")
    incoming_trace = request.headers.get("x-trace-id")
    if incoming_request:
        request_id = incoming_request[:64]
    if incoming_trace:
        trace_id = incoming_trace[:64]
    header_map = {key: value for key, value in request.headers.items()}
    messages = [message.model_dump(exclude_none=True) for message in body.messages]
    if body.stream and not body.tools:
        _virtual, _catalog, provider = resolve_target(db, body.model, header_map)
        if provider is not None and provider.type in {"openai", "openai-compatible", "azure-openai"}:
            generator = iter_live_sse(
                db,
                identity,
                model=body.model,
                messages=messages,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                request_id=request_id,
                trace_id=trace_id,
                headers=header_map,
            )
            try:
                first = await generator.__anext__()
            except GatewayFailure as error:
                raise HTTPException(
                    status_code=error.status,
                    detail={"code": error.code, "message": error.message, "request_id": error.request_id},
                    headers={"x-request-id": request_id, "x-trace-id": trace_id},
                ) from error

            async def live():
                yield first
                async for line in generator:
                    yield line

            return StreamingResponse(
                live(),
                media_type="text/event-stream",
                headers={"x-request-id": request_id, "x-trace-id": trace_id},
            )
    try:
        payload = await execute_chat(
            db,
            identity,
            model=body.model,
            messages=messages,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            request_id=request_id,
            trace_id=trace_id,
            tools=body.tools,
            tool_choice=body.tool_choice,
            headers=header_map,
            prompt_id=body.promptId,
        )
    except GatewayFailure as error:
        raise HTTPException(
            status_code=error.status,
            detail={"code": error.code, "message": error.message, "request_id": error.request_id},
            headers={"x-request-id": request_id, "x-trace-id": trace_id},
        ) from error
    if not body.stream:
        return _json_response(payload, request_id, trace_id)
    text = ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            text = message["content"]

    def chunks():
        first = {
            "id": payload["id"],
            "object": "chat.completion.chunk",
            "model": body.model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
        }
        done = {
            "id": payload["id"],
            "object": "chat.completion.chunk",
            "model": body.model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        yield f"data: {json.dumps(first)}\n\n"
        yield f"data: {json.dumps(done)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        chunks(),
        media_type="text/event-stream",
        headers={"x-request-id": request_id, "x-trace-id": trace_id},
    )


def _json_response(payload: dict[str, object], request_id: str, trace_id: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(payload, headers={"x-request-id": request_id, "x-trace-id": trace_id})


def _model(row: CatalogModel) -> dict[str, object]:
    return {
        "id": row.id,
        "providerId": row.provider_id,
        "providerModel": row.provider_model,
        "displayName": row.display_name,
        "inputPricePerMillion": row.input_price_per_million,
        "outputPricePerMillion": row.output_price_per_million,
        "kind": row.kind or "chat",
    }


def _trace(row: Trace) -> dict[str, object]:
    return {
        "traceId": row.trace_id,
        "requestId": row.request_id,
        "userId": row.user_id,
        "applicationId": row.application_id,
        "model": row.model,
        "provider": row.provider,
        "status": row.status,
        "errorCode": row.error_code,
        "startedAt": row.started_at,
        "durationMs": row.duration_ms,
    }
