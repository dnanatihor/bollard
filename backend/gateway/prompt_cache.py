import hashlib
import json
import math
import time
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from gateway import limits
from gateway.models import CatalogModel, Provider, SemanticEntry, Setting

_memory: dict[str, tuple[float, str]] = {}


def ttl_seconds(db: Session) -> int:
    row = db.get(Setting, "cache_ttl_seconds")
    if row is None:
        return 0
    try:
        return max(int(row.value), 0)
    except ValueError:
        return 0


def make_key(tenant_id: str, model: str, messages: list[dict[str, object]], temperature: float | None) -> str:
    blob = json.dumps(
        {"tenant": tenant_id, "model": model, "messages": messages, "temperature": temperature},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def read(key: str) -> dict[str, object] | None:
    raw = _read_raw(key)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def write(key: str, payload: dict[str, object], ttl: int) -> None:
    if ttl <= 0:
        return
    raw = json.dumps(payload)
    client = limits.client()
    if client is not None:
        try:
            client.set(f"aigw:cache:{key}", raw, ex=ttl)
            return
        except Exception:
            pass
    _memory[key] = (time.time() + ttl, raw)


def reset() -> None:
    _memory.clear()


def semantic_threshold(db: Session) -> float:
    row = db.get(Setting, "semantic_threshold")
    if row is None:
        return 0
    try:
        return max(float(row.value), 0)
    except ValueError:
        return 0


async def read_semantic(db: Session, tenant_id: str, model: str, text: str) -> dict[str, object] | None:
    threshold = semantic_threshold(db)
    if threshold <= 0 or not text.strip():
        return None
    vector = await _embed_text(db, text)
    if not vector:
        return None
    rows = db.scalars(
        select(SemanticEntry)
        .where(SemanticEntry.tenant_id == tenant_id, SemanticEntry.model == model)
        .order_by(SemanticEntry.id.desc())
        .limit(200)
    ).all()
    best: SemanticEntry | None = None
    best_score = threshold
    for row in rows:
        try:
            stored = json.loads(row.vector_json or "[]")
        except json.JSONDecodeError:
            continue
        if not isinstance(stored, list):
            continue
        score = _cosine(vector, [float(item) for item in stored])
        if score >= best_score:
            best_score = score
            best = row
    if best is None:
        return None
    return {
        "text": best.response_text,
        "input_tokens": best.input_tokens,
        "output_tokens": best.output_tokens,
    }


async def write_semantic(
    db: Session,
    tenant_id: str,
    model: str,
    text: str,
    response_text: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    if semantic_threshold(db) <= 0 or not text.strip():
        return
    vector = await _embed_text(db, text)
    if not vector:
        return
    db.add(
        SemanticEntry(
            tenant_id=tenant_id,
            model=model,
            vector_json=json.dumps(vector),
            response_text=response_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    count = db.scalar(
        select(func.count()).select_from(SemanticEntry).where(SemanticEntry.tenant_id == tenant_id, SemanticEntry.model == model)
    )
    if count and int(count) > 200:
        oldest = db.scalars(
            select(SemanticEntry.id)
            .where(SemanticEntry.tenant_id == tenant_id, SemanticEntry.model == model)
            .order_by(SemanticEntry.id)
            .limit(int(count) - 200)
        ).all()
        if oldest:
            db.execute(delete(SemanticEntry).where(SemanticEntry.id.in_(oldest)))


async def _embed_text(db: Session, text: str) -> list[float] | None:
    row = db.get(Setting, "semantic_model")
    if row is None or not row.value.strip():
        return None
    catalog = db.get(CatalogModel, row.value.strip())
    if catalog is None:
        return None
    provider = db.get(Provider, catalog.provider_id)
    if provider is None or provider.enabled != 1:
        return None
    from gateway.crypto import decrypt_secret
    from gateway.providers import ProviderError, embed

    secret = decrypt_secret(provider.secret_ciphertext)
    if not secret:
        return None
    try:
        vectors, _tokens = await embed(provider, secret, catalog.provider_model, [text])
    except ProviderError:
        return None
    return vectors[0] if vectors else None


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0
    return dot / (left_norm * right_norm)


def _read_raw(key: str) -> str | None:
    client = limits.client()
    if client is not None:
        try:
            raw = client.get(f"aigw:cache:{key}")
            if isinstance(raw, bytes):
                return raw.decode()
            if isinstance(raw, str):
                return raw
        except Exception:
            pass
    item = _memory.get(key)
    if item is None or item[0] < time.time():
        _memory.pop(key, None)
        return None
    return item[1]
