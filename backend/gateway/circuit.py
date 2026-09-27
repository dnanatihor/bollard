import time

from gateway import limits

_open_until: dict[str, float] = {}
_failures: dict[str, int] = {}


def before_call(provider_id: str) -> None:
    client = limits.client()
    if client is not None:
        try:
            until = client.get(f"aigw:cb:open:{provider_id}")
            if isinstance(until, bytes):
                until = until.decode()
            if until and float(until) > time.time():
                raise RuntimeError(provider_id)
            return
        except RuntimeError:
            raise
        except Exception:
            pass
    until_memory = _open_until.get(provider_id, 0)
    if until_memory > time.time():
        raise RuntimeError(provider_id)


def record_success(provider_id: str) -> None:
    client = limits.client()
    if client is not None:
        try:
            client.delete(f"aigw:cb:open:{provider_id}", f"aigw:cb:fail:{provider_id}")
        except Exception:
            pass
    _failures.pop(provider_id, None)
    _open_until.pop(provider_id, None)


def record_failure(provider_id: str) -> None:
    client = limits.client()
    if client is not None:
        try:
            count = int(client.incr(f"aigw:cb:fail:{provider_id}"))
            if count == 1:
                client.expire(f"aigw:cb:fail:{provider_id}", 120)
            if count >= 5:
                client.set(f"aigw:cb:open:{provider_id}", str(time.time() + 30), ex=30)
                client.delete(f"aigw:cb:fail:{provider_id}")
            return
        except Exception:
            pass
    count = _failures.get(provider_id, 0) + 1
    _failures[provider_id] = count
    if count >= 5:
        _open_until[provider_id] = time.time() + 30
        _failures[provider_id] = 0


def reset() -> None:
    _open_until.clear()
    _failures.clear()
