import time

_windows: dict[str, list[float]] = {}
_redis = None
_redis_url = ""


def client():
    return _client()


def _client():
    global _redis, _redis_url
    import os

    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return None
    if _redis is not None and _redis_url == url:
        return _redis
    from redis import Redis

    _redis = Redis.from_url(url, socket_timeout=0.4, socket_connect_timeout=0.4)
    _redis_url = url
    return _redis


def allow(bucket: str, limit: int) -> bool:
    if limit <= 0:
        return False
    client = _client()
    if client is not None:
        try:
            window = int(time.time() // 60)
            key = f"aigw:rl:{bucket}:{window}"
            count = int(client.incr(key))
            if count == 1:
                client.expire(key, 70)
            return count <= limit
        except Exception:
            pass
    now = time.time()
    window_rows = _windows.setdefault(bucket, [])
    cutoff = now - 60
    _windows[bucket] = [stamp for stamp in window_rows if stamp >= cutoff]
    if len(_windows[bucket]) >= limit:
        return False
    _windows[bucket].append(now)
    return True


def reset_memory() -> None:
    _windows.clear()
