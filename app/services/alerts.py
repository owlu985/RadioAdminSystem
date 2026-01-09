from __future__ import annotations

from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Optional, TYPE_CHECKING

import requests
import smtplib
from flask import current_app

from app.logger import init_logger

if TYPE_CHECKING:  # pragma: no cover
    from app.services.detection import DetectionResult

logger = init_logger()

_state = {
    "dead_air_since": None,
    "down_since": None,
    "last_alert": {},
    "autodj_reenabled_at": None,
}


def _now() -> datetime:
    return datetime.utcnow()


def _min_interval(cfg) -> timedelta:
    return timedelta(minutes=float(cfg.get("ALERT_REPEAT_MINUTES", 15)))


def _send_alert(subject: str, body: str) -> None:
    cfg = current_app.config
    dry_run = cfg.get("ALERTS_DRY_RUN", True) or not cfg.get("ALERTS_ENABLED", False)

    discord_hook = cfg.get("ALERTS_DISCORD_WEBHOOK")
    email_enabled = cfg.get("ALERTS_EMAIL_ENABLED", False)
    email_to = cfg.get("ALERTS_EMAIL_TO")
    email_from = cfg.get("ALERTS_EMAIL_FROM")
    smtp_server = cfg.get("ALERTS_SMTP_SERVER")
    smtp_port = int(cfg.get("ALERTS_SMTP_PORT", 587))
    smtp_username = cfg.get("ALERTS_SMTP_USERNAME")
    smtp_password = cfg.get("ALERTS_SMTP_PASSWORD")

    if dry_run:
        logger.warning("[Alert simulate] %s -- %s", subject, body)
        return

    # Discord webhook
    if discord_hook:
        try:
            requests.post(discord_hook, json={"content": f"**{subject}**\n{body}"}, timeout=5)
        except Exception as exc:  # noqa: BLE001
            logger.error("Discord alert failed: %s", exc)

    # Email alert
    if email_enabled and email_to and email_from and smtp_server:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = email_from
        msg["To"] = email_to
        msg.set_content(body)

        try:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as smtp:
                smtp.starttls()
                if smtp_username and smtp_password:
                    smtp.login(smtp_username, smtp_password)
                smtp.send_message(msg)
        except Exception as exc:  # noqa: BLE001
            logger.error("Email alert failed: %s", exc)


def _maybe_send(kind: str, message: str) -> None:
    now = _now()
    cfg = current_app.config
    last = _state["last_alert"].get(kind)
    if last and now - last < _min_interval(cfg):
        return
    _state["last_alert"][kind] = now
    _send_alert(subject=f"RAMS Alert: {kind.replace('_', ' ').title()}", body=message)


def check_stream_up(url: str) -> bool:
    try:
        resp = requests.get(url, stream=True, timeout=3)
        return resp.status_code < 500
    except Exception:
        return False


def process_probe_alerts(stream_up: bool, result: Optional["DetectionResult"]) -> None:
    cfg = current_app.config
    now = _now()

    # Stream down handling
    if not stream_up:
        if _state["down_since"] is None:
            _state["down_since"] = now
        down_threshold = timedelta(minutes=float(cfg.get("ALERT_STREAM_DOWN_THRESHOLD_MINUTES", 1)))
        if now - _state["down_since"] >= down_threshold:
            _maybe_send(
                "stream_down",
                f"Stream appears DOWN since {_state['down_since']} UTC (last check {now}).",
            )
    else:
        _state["down_since"] = None

    classification = result.classification if result else None
    if classification == "dead_air":
        if _state["dead_air_since"] is None:
            _state["dead_air_since"] = now
        dead_threshold = timedelta(minutes=float(cfg.get("ALERT_DEAD_AIR_THRESHOLD_MINUTES", 5)))
        if now - _state["dead_air_since"] >= dead_threshold:
            _maybe_send(
                "dead_air",
                f"Dead air detected since {_state['dead_air_since']} UTC (last probe {now}).",
            )
        autodj_threshold = timedelta(minutes=float(cfg.get("AUTODJ_DEAD_AIR_MINUTES", 4)))
        if (
            cfg.get("AUTODJ_AUTO_RECOVER", True)
            and now - _state["dead_air_since"] >= autodj_threshold
            and _state.get("autodj_reenabled_at") is None
        ):
            try:
                from app.services.radiodj_client import RadioDJClient

                client = RadioDJClient()
                if client.enabled:
                    client.set_autodj(True)
                    _state["autodj_reenabled_at"] = now
                    logger.info("AutoDJ re-enabled after sustained dead air.")
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to auto-enable AutoDJ: %s", exc)
    else:
        _state["dead_air_since"] = None
        _state["autodj_reenabled_at"] = None
