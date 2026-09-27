import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from gateway.models import ApiKey, User


@dataclass(frozen=True)
class Identity:
    key_id: str
    prefix: str
    tenant_id: str
    application_id: str
    user_id: str
    role: str


def hash_key(plaintext: str) -> str:
    pepper = os.environ.get("GATEWAY_KEY_PEPPER", "")
    return hashlib.sha256(f"{pepper}\0{plaintext}".encode()).hexdigest()


def generate_key() -> str:
    return f"aigw_live_{secrets.token_urlsafe(24)}"


def authenticate(db: Session, authorization: str | None) -> Identity | None:
    if authorization is None:
        return None
    token = authorization.removeprefix("Bearer ").removeprefix("bearer ").strip()
    if token.count(".") == 2 and not token.startswith("aigw_live_"):
        return _jwt_identity(db, token)
    if not token.startswith("aigw_live_"):
        return None
    row = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(token), ApiKey.status == "active"))
    if row is None:
        return None
    if row.expires_at and row.expires_at < datetime.now(UTC).isoformat():
        return None
    user = db.get(User, row.user_id)
    if user is not None and user.status != "active":
        return None
    row.last_used_at = datetime.now(UTC).isoformat()
    db.commit()
    return Identity(
        key_id=row.id,
        prefix=row.prefix,
        tenant_id=row.tenant_id,
        application_id=row.application_id,
        user_id=row.user_id,
        role=row.role,
    )


def _setting(db: Session, key: str) -> str:
    from gateway.models import Setting

    row = db.get(Setting, key)
    return row.value.strip() if row is not None else ""


def _claims(token: str, issuer: str, audience: str, jwks_url: str) -> dict[str, object]:
    import jwt
    from jwt import PyJWKClient

    client = PyJWKClient(jwks_url, timeout=3)
    signing = client.get_signing_key_from_jwt(token)
    decoded = jwt.decode(
        token,
        signing.key,
        algorithms=["RS256", "ES256", "RS384", "RS512"],
        audience=audience,
        issuer=issuer,
    )
    return decoded if isinstance(decoded, dict) else {}


def _jwt_identity(db: Session, token: str) -> Identity | None:
    issuer = _setting(db, "jwt_issuer")
    audience = _setting(db, "jwt_audience")
    jwks_url = _setting(db, "jwt_jwks_url")
    if not issuer or not audience or not jwks_url:
        return None
    try:
        claims = _claims(token, issuer, audience, jwks_url)
    except Exception:
        return None
    email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    subject = str(claims.get("sub") or "").strip()
    user = None
    if email:
        user = db.scalar(select(User).where(User.email == email, User.status == "active"))
    if user is None and subject:
        user = db.scalar(select(User).where(User.external_subject == subject, User.status == "active"))
    if user is None:
        return None
    return Identity(
        key_id="jwt",
        prefix="jwt",
        tenant_id=user.tenant_id,
        application_id=user.application_id,
        user_id=user.id,
        role=user.role,
    )


def session_identity(db: Session, session_id: str | None) -> Identity | None:
    if not session_id:
        return None
    from gateway.models import LoginSession

    row = db.get(LoginSession, session_id)
    if row is None or row.expires_at < datetime.now(UTC).isoformat():
        return None
    user = db.get(User, row.user_id)
    if user is None or user.status != "active":
        return None
    return Identity(
        key_id="session",
        prefix="session",
        tenant_id=user.tenant_id,
        application_id=user.application_id,
        user_id=user.id,
        role=user.role,
    )


def allows(identity: Identity, permission: str) -> bool:
    if identity.role == "admin":
        return True
    if identity.role == "auditor":
        return permission in {"audit:read", "admin:read", "self:read"}
    return permission in {"inference:invoke", "self:read"}
