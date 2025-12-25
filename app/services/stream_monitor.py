from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Optional

import requests
from flask import current_app

from app.logger import init_logger
from app.models import IcecastStat, db

logger = init_logger()


def fetch_icecast_listeners() -> Optional[int]:
    url = current_app.config.get("ICECAST_STATUS_URL")
    if not url:
        return None
    auth = None
    if current_app.config.get("ICECAST_USERNAME") and current_app.config.get("ICECAST_PASSWORD"):
        auth = (current_app.config.get("ICECAST_USERNAME"), current_app.config.get("ICECAST_PASSWORD"))
    mount = current_app.config.get("ICECAST_MOUNT")
    try:
        resp = requests.get(url, auth=auth, timeout=5)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Icecast status request failed: %s", exc)
        return None

    try:
        root = ET.fromstring(resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Icecast status parse failed: %s", exc)
        return None

    listeners = None
    for source in root.findall(".//source"):
        mount_name = source.findtext("mount") or source.findtext("mountname") or source.attrib.get("mount")
        if mount and mount_name and mount_name != mount:
            continue
        val = source.findtext("listeners") or source.findtext("listener_total") or source.findtext("listeners_peak")
        if val and val.isdigit():
            listeners = int(val)
            break
    return listeners


def record_icecast_stat() -> Optional[IcecastStat]:
    listeners = fetch_icecast_listeners()
    if listeners is None:
        return None
    stat = IcecastStat(listeners=listeners, created_at=datetime.utcnow())
    db.session.add(stat)
    db.session.commit()
    return stat


def recent_icecast_stats(hours: int = 24) -> List[IcecastStat]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    return (
        IcecastStat.query.filter(IcecastStat.created_at >= cutoff)
        .order_by(IcecastStat.created_at.asc())
        .all()
    )
