import json
import random
import time

from sqlalchemy.orm import Session

from gateway import limits
from gateway.models import CatalogModel, Provider, Setting, VirtualModel

_rr: dict[str, int] = {}
_latency: dict[str, float] = {}
_memo: dict[str, tuple[float, tuple[str | None, str | None, str | None]]] = {}


def reset() -> None:
    _rr.clear()
    _latency.clear()
    _memo.clear()


def invalidate() -> None:
    _memo.clear()


def record_latency(model_id: str, elapsed_ms: float) -> None:
    previous = _latency.get(model_id, elapsed_ms)
    _latency[model_id] = previous * 0.7 + elapsed_ms * 0.3
    client = limits.client()
    if client is not None:
        try:
            client.set(f"aigw:lat:{model_id}", str(_latency[model_id]), ex=3600)
        except Exception:
            pass


def resolve_target(
    db: Session,
    model: str,
    headers: dict[str, str] | None = None,
) -> tuple[VirtualModel | None, CatalogModel | None, Provider | None]:
    cached = _cached(db, model, headers or {})
    if cached is not None:
        return cached
    virtual = db.get(VirtualModel, model)
    if virtual is None:
        catalog = db.get(CatalogModel, model)
        provider = db.get(Provider, catalog.provider_id) if catalog is not None else None
        _store(model, None if virtual is None else virtual.id, None if catalog is None else catalog.id, None if provider is None else provider.id, db)
        return None, catalog, provider
    catalog = _choose(db, virtual, headers or {})
    provider = db.get(Provider, catalog.provider_id) if catalog is not None else None
    if (virtual.strategy or "direct") in {"direct", "least_cost"}:
        _store(model, virtual.id, None if catalog is None else catalog.id, None if provider is None else provider.id, db)
    return virtual, catalog, provider


def region_alternates(db: Session, virtual: VirtualModel | None, current_id: str) -> list[CatalogModel]:
    if virtual is None or (virtual.strategy or "") != "region":
        return []
    found: list[CatalogModel] = []
    for target in _targets(virtual):
        catalog = db.get(CatalogModel, str(target.get("modelId") or ""))
        if catalog is not None and catalog.id != current_id:
            found.append(catalog)
    return found


def _choose(db: Session, virtual: VirtualModel, headers: dict[str, str]) -> CatalogModel | None:
    strategy = virtual.strategy or "direct"
    targets = _targets(virtual)
    if strategy == "direct" or not targets:
        return db.get(CatalogModel, virtual.model_id)
    catalogs = []
    for target in targets:
        catalog = db.get(CatalogModel, str(target.get("modelId") or ""))
        if catalog is not None:
            catalogs.append((target, catalog))
    if not catalogs:
        return db.get(CatalogModel, virtual.model_id)
    if strategy == "least_cost":
        return min(catalogs, key=lambda item: (item[1].input_price_per_million, item[1].output_price_per_million))[1]
    if strategy == "conditional":
        return _conditional(catalogs, headers) or catalogs[0][1]
    if strategy == "round_robin":
        return _round_robin(virtual.id, catalogs)
    if strategy == "least_latency":
        return _least_latency(catalogs)
    if strategy == "canary":
        return _canary(catalogs)
    if strategy == "region":
        return _region(db, catalogs, headers)
    return _weighted(catalogs)


def _targets(virtual: VirtualModel) -> list[dict[str, object]]:
    try:
        parsed = json.loads(virtual.targets_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _weighted(catalogs: list[tuple[dict[str, object], CatalogModel]]) -> CatalogModel:
    weights = [max(float(target.get("weight") or 0), 0) for target, _catalog in catalogs]
    if sum(weights) <= 0:
        weights = [1.0 for _item in catalogs]
    return random.choices([catalog for _target, catalog in catalogs], weights=weights, k=1)[0]


def _conditional(
    catalogs: list[tuple[dict[str, object], CatalogModel]],
    headers: dict[str, str],
) -> CatalogModel | None:
    lowered = {key.lower(): value for key, value in headers.items()}
    default: CatalogModel | None = None
    for target, catalog in catalogs:
        header = str(target.get("header") or "")
        if not header:
            default = catalog
            continue
        if lowered.get(header.lower()) == str(target.get("equals") or ""):
            return catalog
    return default


def _round_robin(route_id: str, catalogs: list[tuple[dict[str, object], CatalogModel]]) -> CatalogModel:
    index = _rr.get(route_id, 0)
    client = limits.client()
    if client is not None:
        try:
            index = int(client.incr(f"aigw:rr:{route_id}")) - 1
        except Exception:
            pass
    _rr[route_id] = index + 1
    return catalogs[index % len(catalogs)][1]


def _latency_of(model_id: str) -> float:
    client = limits.client()
    if client is not None:
        try:
            raw = client.get(f"aigw:lat:{model_id}")
            if isinstance(raw, bytes):
                raw = raw.decode()
            if raw:
                return float(raw)
        except Exception:
            pass
    return _latency.get(model_id, 1_000_000.0)


def _least_latency(catalogs: list[tuple[dict[str, object], CatalogModel]]) -> CatalogModel:
    return min(catalogs, key=lambda item: _latency_of(item[1].id))[1]


def _canary(catalogs: list[tuple[dict[str, object], CatalogModel]]) -> CatalogModel:
    roll = random.random() * 100
    primary = catalogs[-1][1]
    for target, catalog in catalogs:
        if target.get("percent") is None:
            primary = catalog
            continue
        if roll < float(target.get("percent") or 0):
            return catalog
    return primary


def _region(
    db: Session,
    catalogs: list[tuple[dict[str, object], CatalogModel]],
    headers: dict[str, str],
) -> CatalogModel:
    wanted = ""
    for key, value in headers.items():
        if key.lower() == "x-region":
            wanted = value
            break
    if not wanted:
        row = db.get(Setting, "gateway_region")
        wanted = row.value.strip() if row is not None else ""
    for target, catalog in catalogs:
        if str(target.get("region") or "") == wanted:
            return catalog
    return catalogs[0][1]


def _cache_ttl(db: Session) -> int:
    row = db.get(Setting, "config_cache_seconds")
    if row is None:
        return 0
    try:
        return max(int(row.value), 0)
    except ValueError:
        return 0


def _store(model: str, virtual_id: str | None, catalog_id: str | None, provider_id: str | None, db: Session) -> None:
    ttl = _cache_ttl(db)
    if ttl <= 0 or not catalog_id:
        return
    _memo[model] = (time.time() + ttl, (virtual_id, catalog_id, provider_id))


def _cached(
    db: Session,
    model: str,
    headers: dict[str, str],
) -> tuple[VirtualModel | None, CatalogModel | None, Provider | None] | None:
    del headers
    item = _memo.get(model)
    if item is None or item[0] < time.time():
        _memo.pop(model, None)
        return None
    virtual_id, catalog_id, provider_id = item[1]
    virtual = db.get(VirtualModel, virtual_id) if virtual_id else None
    catalog = db.get(CatalogModel, catalog_id) if catalog_id else None
    provider = db.get(Provider, provider_id) if provider_id else None
    if catalog is None or provider is None:
        return None
    return virtual, catalog, provider
