from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from flask import current_app

from app.logger import init_logger
from app.models import StreamProbe
from app.utils import get_current_show, show_display_title

logger = init_logger()


def _history_path() -> str:
    path = current_app.config.get("ICECAST_ANALYTICS_FILE")
    if not path:
        path = os.path.join(current_app.instance_path, "analytics", "icecast_listener_history.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _latest_probe() -> Optional[StreamProbe]:
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    probe = StreamProbe.query.order_by(StreamProbe.created_at.desc()).first()
    if probe and probe.created_at >= cutoff:
        return probe
    return None


def _listener_state() -> Dict[str, Optional[str]]:
    show = get_current_show()
    show_name = show_display_title(show) if show else None
    probe = _latest_probe()
    classification = probe.classification if probe else None

    state = "automation"
    if classification == "dead_air":
        state = "dead_air"
    elif classification == "live":
        state = "live_show"
    elif show:
        state = "live_show" if classification != "automation" else "automation"
    elif classification == "automation":
        state = "automation"

    return {
        "state": state,
        "show_name": show_name,
        "classification": classification,
    }


def _parse_line(line: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(line)
    except Exception:
        return None


def _last_entry(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    last: Optional[Dict[str, Any]] = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parsed = _parse_line(line)
            if parsed:
                last = parsed
    return last


def append_listener_sample(listeners: int) -> Dict[str, Any]:
    """Record a listener snapshot to the JSONL history file (15-minute buckets)."""

    now = datetime.utcnow()
    bucket = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    ctx = _listener_state()
    entry = {
        "ts": bucket.isoformat(),
        "listeners": listeners,
        "state": ctx.get("state"),
        "show": ctx.get("show_name"),
        "classification": ctx.get("classification"),
    }

    path = _history_path()
    last = _last_entry(path)
    if last and last.get("ts") == entry["ts"]:
        return entry

    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to append listener analytics: %s", exc)
    return entry


def load_listener_history(hours: Optional[int] = None) -> List[Dict[str, Any]]:
    path = _history_path()
    if not os.path.exists(path):
        return []

    cutoff = None
    if hours is not None:
        cutoff = datetime.utcnow() - timedelta(hours=hours)

    results: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parsed = _parse_line(line)
            if not parsed:
                continue
            ts = parsed.get("ts")
            try:
                dt = datetime.fromisoformat(ts) if ts else None
            except Exception:
                dt = None
            if cutoff and dt and dt < cutoff:
                continue
            results.append(parsed)
    return results
