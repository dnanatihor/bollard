import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from gateway.budgets import ensure_user_budget
from gateway.models import ApiKey, Application, Tenant, User, UserBudget

DEFAULT_TENANT = "tenant-local"
ADMIN_USER = "local-admin"


def now() -> str:
    return datetime.now(UTC).isoformat()


def ensure_directory(db: Session) -> None:
    if db.get(Tenant, DEFAULT_TENANT) is None:
        db.add(Tenant(id=DEFAULT_TENANT, name="Local", created_at=now()))
    orphans = db.scalars(select(ApiKey).where(~ApiKey.user_id.in_(select(User.id)))).all()
    for key in orphans:
        ensure_application(db, key.tenant_id, key.application_id, key.application_id)
        db.add(
            User(
                id=key.user_id,
                tenant_id=key.tenant_id,
                application_id=key.application_id,
                email=key.user_id if "@" in key.user_id else f"{key.user_id}@localhost",
                display_name=key.user_id,
                role=key.role,
                status="active",
                created_at=key.created_at,
            )
        )
        ensure_user_budget(db, key.user_id, key.tenant_id)
    missing = db.scalars(select(User).where(~User.id.in_(select(UserBudget.user_id)))).all()
    for user in missing:
        ensure_user_budget(db, user.id, user.tenant_id)


def ensure_application(db: Session, tenant_id: str, application_id: str, name: str) -> Application:
    row = db.get(Application, application_id)
    if row is None:
        row = Application(id=application_id, tenant_id=tenant_id, name=name or application_id, created_at=now())
        db.add(row)
        db.flush()
    return row


def create_user(
    db: Session,
    *,
    tenant_id: str,
    email: str,
    display_name: str,
    role: str,
    application_id: str,
    external_subject: str | None = None,
) -> User:
    normalized = email.strip().lower()
    existing = db.scalar(select(User).where(User.tenant_id == tenant_id, User.email == normalized))
    if existing is not None:
        raise ValueError("A user with that email already exists.")
    ensure_application(db, tenant_id, application_id, application_id)
    user = User(
        id=f"user_{uuid.uuid4().hex[:12]}",
        tenant_id=tenant_id,
        application_id=application_id,
        email=normalized,
        display_name=display_name.strip() or normalized,
        role=role,
        status="active",
        external_subject=(external_subject or "").strip() or None,
        created_at=now(),
    )
    db.add(user)
    return user
