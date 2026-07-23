from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import requests
from flask import current_app

from app.logger import init_logger

logger = init_logger()

_state = {
    "restart_attempts": [],
    "locked_out_until": None,
}


@dataclass(frozen=True)
class BarixRestartResult:
    """Detailed outcome from a Barix restart decision."""

    attempted: bool
    accepted: bool
    status: str
    message: str
    locked_out_until: Optional[datetime] = None
    http_status: Optional[int] = None

    @property
    def should_count_restart(self) -> bool:
        return self.accepted


def _utcnow() -> datetime:
    return datetime.utcnow()


def is_locked_out() -> bool:
    until = _state.get("locked_out_until")
    return bool(until and until > _utcnow())


def _redact_url(url: str) -> str:
    """Return a log-safe URL without credentials, query strings, or fragments."""
    try:
        parsed = urlsplit(url)
    except Exception:  # noqa: BLE001
        return "<invalid restart url>"
    host = parsed.hostname or parsed.netloc
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def restart_instreamer(reason: Optional[str] = None) -> BarixRestartResult:
    """Attempt to restart a Barix InStreamer via its HTTP API.

    Supports either a direct restart URL or IP/credentials based URL building.
    Returns a detailed result so callers can distinguish lockout, skipped,
    failed, and accepted restart decisions.
    """

    cfg = current_app.config
    if not cfg.get("BARIX_AUTO_RESTART_ENABLED", False):
        return BarixRestartResult(
            attempted=False,
            accepted=False,
            status="disabled",
            message="Barix auto-restart is disabled.",
        )

    window_minutes = int(cfg.get("BARIX_RESTART_WINDOW_MINUTES", 20))
    max_restarts = int(cfg.get("BARIX_MAX_RESTARTS_PER_WINDOW", 3))
    if is_locked_out():
        locked_out_until = _state.get("locked_out_until")
        logger.error("Barix auto-heal lockout active until %s", locked_out_until)
        return BarixRestartResult(
            attempted=False,
            accepted=False,
            status="locked_out",
            message=f"Barix auto-heal lockout active until {locked_out_until}.",
            locked_out_until=locked_out_until,
        )

    cutoff = _utcnow() - timedelta(minutes=window_minutes)
    _state["restart_attempts"] = [t for t in _state["restart_attempts"] if t >= cutoff]
    if len(_state["restart_attempts"]) >= max_restarts:
        locked_out_until = _utcnow() + timedelta(minutes=window_minutes)
        _state["locked_out_until"] = locked_out_until
        logger.error("Barix Down, auto-heal locked out; manual intervention required.")
        return BarixRestartResult(
            attempted=False,
            accepted=False,
            status="lockout_started",
            message=(
                "Barix auto-heal restart limit reached; "
                f"manual intervention required until {locked_out_until}."
            ),
            locked_out_until=locked_out_until,
        )

    restart_url = cfg.get("BARIX_RESTART_URL")
    if not restart_url:
        ip = cfg.get("BARIX_IP")
        if ip:
            restart_url = f"http://{ip}/setup.cgi?c=99"

    if not restart_url:
        logger.warning("Barix auto-restart enabled but no restart endpoint is configured.")
        return BarixRestartResult(
            attempted=False,
            accepted=False,
            status="missing_endpoint",
            message="Barix auto-restart enabled but no restart endpoint is configured.",
        )

    safe_url = _redact_url(restart_url)
    try:
        _state["restart_attempts"].append(_utcnow())
        resp = requests.get(restart_url, timeout=8)
        if resp.status_code < 400:
            logger.warning("Triggered Barix restart (%s): %s", reason or "no_reason", safe_url)
            return BarixRestartResult(
                attempted=True,
                accepted=True,
                status="accepted",
                message=f"Triggered Barix restart ({reason or 'no_reason'}).",
                http_status=resp.status_code,
            )
        logger.error("Barix restart failed with HTTP %s", resp.status_code)
        return BarixRestartResult(
            attempted=True,
            accepted=False,
            status="http_error",
            message=f"Barix restart request returned HTTP {resp.status_code}.",
            http_status=resp.status_code,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Barix restart request failed: %s", exc)
        return BarixRestartResult(
            attempted=True,
            accepted=False,
            status="request_failed",
            message=f"Barix restart request failed: {exc}",
        )
