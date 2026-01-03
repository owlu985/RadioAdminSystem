from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import List, Optional, Set

import requests
from flask import current_app

from app.logger import init_logger
from app.services.listener_analytics import append_listener_sample, load_listener_history

logger = init_logger()


def _ignored_ip_set() -> Set[str]:
    ignored = current_app.config.get("ICECAST_IGNORED_IPS", [])
    if not ignored:
        return set()
    return {ip.strip() for ip in ignored if ip and isinstance(ip, str)}


def _parse_listeners_from_listclients(text: str, ignored_ips: Set[str]) -> Optional[int]:
    try:
        root = ET.fromstring(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Icecast listclients parse failed: %s", exc)
        return None

    ips: List[str] = []
    for node in root.iter():
        tag = (node.tag or "").lower()
        if tag in {"ip", "client_ip", "clientip", "listenerip"}:
            val = (node.text or "").strip()
            if val:
                ips.append(val)
    if not ips:
        # fallback to <listener><IP> nesting
        for listener in root.findall(".//listener"):
            ip = listener.findtext("IP") or listener.findtext("ip")
            ip = ip.strip() if ip else None
            if ip:
                ips.append(ip)
    if not ips:
        return None
    if ignored_ips:
        ips = [ip for ip in ips if ip not in ignored_ips]
    return len(ips)


def fetch_icecast_listeners() -> Optional[int]:
    auth = None
    if current_app.config.get("ICECAST_USERNAME") and current_app.config.get("ICECAST_PASSWORD"):
        auth = (current_app.config.get("ICECAST_USERNAME"), current_app.config.get("ICECAST_PASSWORD"))
    mount = current_app.config.get("ICECAST_MOUNT")
    ignored_ips = _ignored_ip_set()

    listclients_url = current_app.config.get("ICECAST_LISTCLIENTS_URL")
    if listclients_url:
        try:
            resp = requests.get(listclients_url, auth=auth, timeout=5)
            resp.raise_for_status()
            from_listclients = _parse_listeners_from_listclients(resp.text, ignored_ips)
            if from_listclients is not None:
                return from_listclients
        except Exception as exc:  # noqa: BLE001
            logger.warning("Icecast listclients request failed: %s", exc)

    url = current_app.config.get("ICECAST_STATUS_URL")
    if not url:
        return None
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
        detailed = source.findall("listener")
        if detailed:
            filtered = []
            for listener in detailed:
                ip = listener.findtext("IP") or listener.findtext("ip")
                ip = ip.strip() if ip else None
                if ip and ignored_ips and ip in ignored_ips:
                    continue
                filtered.append(listener)
            listeners = len(filtered)
            break
        val = source.findtext("listeners") or source.findtext("listener_total") or source.findtext("listeners_peak")
        if val and val.isdigit():
            listeners = int(val)
            break
    return listeners


def record_icecast_stat() -> Optional[dict]:
    listeners = fetch_icecast_listeners()
    if listeners is None:
        return None
    return append_listener_sample(listeners)


def recent_icecast_stats(hours: int = 24) -> List[dict]:
    return load_listener_history(hours=hours)
