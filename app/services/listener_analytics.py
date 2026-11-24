from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.models import StreamProbe
from app.services.listener_store import samples, save_sample
from app.utils import get_current_show, recordings_paused, show_display_title


def _latest_probe() -> Optional[StreamProbe]:
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    probe = StreamProbe.query.order_by(StreamProbe.created_at.desc()).first()
    if probe and probe.created_at >= cutoff:
        return probe
    return None


def _listener_state() -> Dict[str, Optional[str]]:
    if recordings_paused():
        return {"state": "automation", "show_name": None, "classification": None}
    show = get_current_show()
    show_name = show_display_title(show) if show else None
    probe = _latest_probe()
    classification = probe.classification if probe else None

    state = "live_show" if show else "automation"
    if classification == "dead_air":
        state = "dead_air"

    return {
        "state": state,
        "show_name": show_name,
        "classification": classification,
    }


def append_listener_sample(listeners: int, clients=None) -> Dict[str, Any]:
    ctx = _listener_state()
    now = datetime.now(timezone.utc)
    save_sample(listeners, ctx, clients, now)
    return {"ts": now.replace(tzinfo=None).isoformat(), "listeners": listeners,
            "state": ctx["state"], "show": ctx["show_name"], "classification": ctx["classification"]}


def load_listener_history(hours: Optional[int] = None) -> List[Dict[str, Any]]:
    start = datetime.now(timezone.utc) - timedelta(hours=hours) if hours is not None else None
    return list(samples(int(start.timestamp()) if start else None))


def peak_listeners_for_show(show_name: str, start: datetime, end: datetime) -> Optional[int]:
    """Return the highest recorded listener sample in a show window."""
    lower = int(start.replace(tzinfo=timezone.utc).timestamp())
    upper = int(end.replace(tzinfo=timezone.utc).timestamp()) + 1
    return max((sample["listeners"] for sample in samples(lower, upper)
                if not sample["show"] or sample["show"] == show_name), default=None)
