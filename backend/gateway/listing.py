"""Bounded list queries. Callers page in the database and return one window."""

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def clamp(limit: int = DEFAULT_LIMIT, offset: int = 0) -> tuple[int, int]:
    size = min(max(int(limit), 1), MAX_LIMIT)
    start = max(int(offset), 0)
    return size, start


def meta(total: int, limit: int, offset: int) -> dict[str, int]:
    return {"total": int(total), "limit": limit, "offset": offset}


def needle(value: str) -> str:
    return value.strip()[:200]


def contains(column, value: str):
    text = needle(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{text}%", escape="\\")


def fetch(db: Session, statement: Select, limit: int, offset: int):
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    rows = db.scalars(statement.limit(limit).offset(offset)).all()
    return rows, int(total)
