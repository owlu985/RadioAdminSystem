import time
from typing import Any, Dict, Optional

_cache: Dict[str, Dict[str, Any]] = {}
_MAX_ENTRIES = 256


def _prune_expired(now: Optional[float] = None) -> None:
    now = now or time.time()
    expired_keys = [key for key, entry in _cache.items() if entry.get("expires_at", 0) < now]
    for key in expired_keys:
        _cache.pop(key, None)


def _enforce_limit() -> None:
    if len(_cache) <= _MAX_ENTRIES:
        return
    sorted_entries = sorted(_cache.items(), key=lambda item: item[1].get("expires_at", 0))
    for key, _ in sorted_entries[: max(0, len(_cache) - _MAX_ENTRIES)]:
        _cache.pop(key, None)


def get(key: str) -> Optional[Any]:
    _prune_expired()
    entry = _cache.get(key)
    if not entry:
        return None
    if entry.get("expires_at", 0) < time.time():
        _cache.pop(key, None)
        return None
    return entry.get("value")


def set(key: str, value: Any, ttl: int = 60) -> None:
    _prune_expired()
    _cache[key] = {"value": value, "expires_at": time.time() + ttl}
    _enforce_limit()


def invalidate(key: Optional[str] = None) -> None:
    if key:
        _cache.pop(key, None)
    else:
        _cache.clear()


def stats() -> Dict[str, int]:
    _prune_expired()
    return {"entries": len(_cache)}
