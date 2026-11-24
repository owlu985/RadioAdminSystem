from __future__ import annotations

import ipaddress
import time
from threading import Lock
import xml.etree.ElementTree as ET

import requests
from flask import current_app

from app.logger import init_logger
from app.services.listener_analytics import append_listener_sample, load_listener_history

logger = init_logger()
_live_lock = Lock()
_INTERNAL = tuple(ipaddress.ip_network(net) for net in ("172.16.0.0/16", "192.168.0.0/16"))
_CLIENTS = (
    ("tunein", "TuneIn"), ("radiogarden", "Radio Garden"),
    ("radio garden", "Radio Garden"), ("claudebot", "ClaudeBot (crawler)"),
    ("spider", "Other crawler"), ("crawler", "Other crawler"), ("bot", "Other crawler"),
    ("lavf", "FFmpeg / Lavf"), ("ffmpeg", "FFmpeg / Lavf"), ("vlc", "VLC"),
    ("edg/", "Microsoft Edge"), ("edga/", "Microsoft Edge"), ("edgios/", "Microsoft Edge"), ("opr/", "Opera"), ("firefox", "Firefox"),
    ("fxios", "Firefox"), ("chrome", "Chrome"), ("crios", "Chrome"), ("safari", "Safari"),
)


def _network(value):
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return "Unknown"
    addr = getattr(addr, "ipv4_mapped", None) or addr
    return "Internal" if any(addr in net for net in _INTERNAL) else "External"


def _number(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def parse_snapshot(text, mount, ignored_ips, *, listclients=False):
    root = ET.fromstring(text)
    for source in root.iter("source"):
        name = source.get("mount") or source.findtext("mount") or source.findtext("mountname")
        if mount and name != mount:
            continue
        declared = _number(source.findtext("Listeners") or source.findtext("listeners"))
        nodes = source.findall("listener")
        if nodes or (listclients and declared == 0):
            clients = []
            for node in nodes:
                fields = {child.tag.lower(): (child.text or "").strip() for child in node}
                ip = fields.get("ip") or fields.get("client_ip", "")
                if ip in ignored_ips:
                    continue
                agent = fields.get("useragent", "").lower()
                label = next((label for token, label in _CLIENTS if token in agent), "Other / unknown")
                client_id = fields.get("id")
                clients.append({"network": _network(ip), "client": label,
                                "id": f"{name}/{client_id}" if client_id else None,
                                "seconds": _number(fields.get("connected"))})
            return len(clients), clients
        if declared is not None:
            return declared, None
    return None


def _snapshot():
    cfg = current_app.config
    auth = (cfg["ICECAST_USERNAME"], cfg["ICECAST_PASSWORD"]) if cfg.get("ICECAST_USERNAME") and cfg.get("ICECAST_PASSWORD") else None
    ignored = {item.strip() for item in cfg.get("ICECAST_IGNORED_IPS", []) if item}
    for key in ("ICECAST_LISTCLIENTS_URL", "ICECAST_STATUS_URL"):
        if not cfg.get(key):
            continue
        try:
            response = requests.get(cfg[key], auth=auth, timeout=5)
            response.raise_for_status()
            result = parse_snapshot(response.text, cfg.get("ICECAST_MOUNT"), ignored,
                                    listclients=key == "ICECAST_LISTCLIENTS_URL")
            if result is not None:
                return result
        except (requests.RequestException, ET.ParseError) as exc:
            logger.warning("Icecast %s sample failed (%s)", key, type(exc).__name__)
    return None


def fetch_icecast_listeners():
    # Dashboard clients poll every five seconds. Share a short-lived count per worker.
    with _live_lock:
        checked, count = current_app.extensions.get("icecast_live_count", (float("-inf"), None))
        if time.monotonic() - checked >= 30:
            result = _snapshot()
            count = result[0] if result is not None else None
            current_app.extensions["icecast_live_count"] = (time.monotonic(), count)
        return count


def record_icecast_stat():
    result = _snapshot()
    return append_listener_sample(*result) if result is not None else None


def recent_icecast_stats(hours=24):
    return load_listener_history(hours=hours)
