from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gateway.models import Budget, UsageEvent, UserBudget
from gateway.security import Identity


def period_label() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def month_start() -> str:
    now = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.isoformat()


def default_monthly(db: Session, tenant_id: str) -> float:
    row = db.get(Budget, tenant_id)
    return 25 if row is None else float(row.monthly_usd)


def workspace_ceiling(db: Session, tenant_id: str) -> float:
    row = db.get(Budget, tenant_id)
    return 0 if row is None else float(row.overall_usd or 0)


def month_spent(db: Session, tenant_id: str) -> float:
    spent = db.scalar(
        select(func.coalesce(func.sum(UsageEvent.estimated_cost), 0)).where(
            UsageEvent.tenant_id == tenant_id,
            UsageEvent.created_at >= month_start(),
        )
    )
    return float(spent or 0)


def workspace_blocks(db: Session, tenant_id: str, estimated: float) -> bool:
    ceiling = workspace_ceiling(db, tenant_id)
    if ceiling <= 0:
        return False
    return month_spent(db, tenant_id) + estimated > ceiling


def ensure_user_budget(db: Session, user_id: str, tenant_id: str) -> UserBudget:
    row = db.get(UserBudget, user_id)
    if row is None:
        db.flush()
        row = UserBudget(user_id=user_id, monthly_usd=default_monthly(db, tenant_id), spent_usd=0, period=period_label())
        db.add(row)
        return row
    current = period_label()
    stored = row.period or ""
    if stored != current:
        if stored == "" and row.spent_usd:
            row.period = current
        else:
            row.period = current
            row.spent_usd = 0
    return row


def record_usage(
    db: Session,
    identity: Identity,
    *,
    request_id: str,
    trace_id: str,
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    cost: float,
    kind: str,
) -> float:
    budget = ensure_user_budget(db, identity.user_id, identity.tenant_id)
    amount = round(float(cost), 8)
    budget.spent_usd += amount
    db.add(
        UsageEvent(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=identity.tenant_id,
            application_id=identity.application_id,
            user_id=identity.user_id,
            key_id=identity.key_id,
            model=model,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=amount,
            kind=kind,
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    return amount
