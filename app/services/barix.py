from __future__ import annotations

from typing import Optional

import requests
from flask import current_app

from app.logger import init_logger

logger = init_logger()


def restart_instreamer(reason: Optional[str] = None) -> bool:
    """Attempt to restart a Barix InStreamer via its HTTP API.

    Supports either a direct restart URL or IP/credentials based URL building.
    Returns True when a restart command is accepted.
    """

    cfg = current_app.config
    if not cfg.get("BARIX_AUTO_RESTART_ENABLED", False):
        return False

    restart_url = cfg.get("BARIX_RESTART_URL")
    if not restart_url:
        ip = cfg.get("BARIX_IP")
        if ip:
            restart_url = f"http://{ip}/rc.cgi?cmd=reboot"

    if not restart_url:
        logger.warning("Barix auto-restart enabled but no restart endpoint is configured.")
        return False

    auth = None
    username = cfg.get("BARIX_USERNAME")
    password = cfg.get("BARIX_PASSWORD")
    if username:
        auth = (username, password or "")

    try:
        resp = requests.get(restart_url, timeout=5, auth=auth)
        if resp.status_code < 400:
            logger.warning("Triggered Barix restart (%s): %s", reason or "no_reason", restart_url)
            return True
        logger.error("Barix restart failed with HTTP %s", resp.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.error("Barix restart request failed: %s", exc)
    return False
