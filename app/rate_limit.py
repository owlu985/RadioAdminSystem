"""Simple in-memory rate limiting for incoming requests."""

from __future__ import annotations

import time
from typing import Iterable

from cachelib import SimpleCache
from flask import jsonify, request

_cache = SimpleCache(default_timeout=0)


def _client_ip(trusted_proxies: Iterable[str] = ()) -> str:
    """Resolve forwarding headers only when the direct peer is trusted."""
    remote = request.remote_addr or "unknown"
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded and _is_trusted(remote, trusted_proxies):
        return forwarded.split(",")[0].strip()
    return remote


def _is_trusted(ip: str, trusted: Iterable[str]) -> bool:
    ip_norm = ip.strip().lower()
    for candidate in trusted:
        cand = (candidate or "").strip().lower()
        if not cand:
            continue
        if ip_norm == cand:
            return True
    return False


def rate_limit_check(app):
    """Apply a fixed-window rate limit per client IP.

    Returns a Flask response when a request should be rejected with HTTP 429,
    otherwise returns None to allow the request to proceed.
    """

    cfg = app.config
    if not cfg.get("RATE_LIMIT_ENABLED", False):
        return None

    # Skip limiting for static assets to keep UX snappy
    if request.endpoint == "static":
        return None

    limit = int(cfg.get("RATE_LIMIT_REQUESTS", 120) or 120)
    window = int(cfg.get("RATE_LIMIT_WINDOW_SECONDS", 60) or 60)
    trusted = cfg.get("RATE_LIMIT_TRUSTED_IPS") or []

    client_ip = _client_ip(cfg.get("RATE_LIMIT_TRUSTED_PROXIES") or [])
    if _is_trusted(client_ip, trusted):
        return None

    now = time.time()
    key = f"rl:{client_ip}"
    bucket = _cache.get(key)
    if not bucket or now - bucket["started_at"] >= window:
        bucket = {"started_at": now, "count": 0}
    if bucket["count"] >= limit:
        reset = int(window - (now - bucket["started_at"]))
        resp = jsonify({"error": "rate_limited", "message": "Too many requests. Please slow down."})
        resp.status_code = 429
        resp.headers["Retry-After"] = str(max(reset, 1))
        return resp

    bucket["count"] += 1
    _cache.set(key, bucket, timeout=window)
    return None
