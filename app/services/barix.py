from __future__ import annotations

from typing import Optional
from datetime import datetime, timedelta

import requests
from flask import current_app

from app.logger import init_logger

logger = init_logger()

_state = {
    "restart_attempts": [],
    "locked_out_until": None,
}


def _utcnow() -> datetime:
    return datetime.utcnow()


def is_locked_out() -> bool:
    until = _state.get("locked_out_until")
    return bool(until and until > _utcnow())


def restart_instreamer(reason: Optional[str] = None) -> bool:
    """Attempt to restart a Barix InStreamer via its HTTP API.

    Supports either a direct restart URL or IP/credentials based URL building.
    Returns True when a restart command is accepted.
    """

    cfg = current_app.config
    if not cfg.get("BARIX_AUTO_RESTART_ENABLED", False):
        return False

    window_minutes = int(cfg.get("BARIX_RESTART_WINDOW_MINUTES", 20))
    max_restarts = int(cfg.get("BARIX_MAX_RESTARTS_PER_WINDOW", 3))
    if is_locked_out():
        logger.error("Barix auto-heal lockout active until %s", _state.get("locked_out_until"))
        return False

    cutoff = _utcnow() - timedelta(minutes=window_minutes)
    _state["restart_attempts"] = [t for t in _state["restart_attempts"] if t >= cutoff]
    if len(_state["restart_attempts"]) >= max_restarts:
        _state["locked_out_until"] = _utcnow() + timedelta(minutes=window_minutes)
        logger.error("Barix Down, auto-heal locked out; manual intervention required.")
        return False

    restart_url = cfg.get("BARIX_RESTART_URL")
    if not restart_url:
        ip = cfg.get("BARIX_IP")
        if ip:
            restart_url = f"http://{ip}/setup.cgi?c=99"

    if not restart_url:
        logger.warning("Barix auto-restart enabled but no restart endpoint is configured.")
        return False

    try:
        _state["restart_attempts"].append(_utcnow())
        resp = requests.get(restart_url, timeout=8)
        if resp.status_code < 400:
            logger.warning("Triggered Barix restart (%s): %s", reason or "no_reason", restart_url)
            return True
        logger.error("Barix restart failed with HTTP %s", resp.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.error("Barix restart request failed: %s", exc)
    return False
