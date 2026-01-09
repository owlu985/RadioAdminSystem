import time
from typing import Any, Dict, Optional

_cache: Dict[str, Dict[str, Any]] = {}


def get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if not entry:
        return None
    if entry.get("expires_at", 0) < time.time():
        _cache.pop(key, None)
        return None
    return entry.get("value")


def set(key: str, value: Any, ttl: int = 60) -> None:
    _cache[key] = {"value": value, "expires_at": time.time() + ttl}


def invalidate(key: Optional[str] = None) -> None:
    if key:
        _cache.pop(key, None)
    else:
        _cache.clear()
