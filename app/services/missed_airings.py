"""Small, atomic per-airing overrides shared by all archive copies."""
from datetime import date, datetime
import hashlib
import os

from app.services.log_export import read_recording_metadata, write_recording_metadata


def _status_path(root, identity):
    show, day = identity
    digest = hashlib.sha256(f"{show}\0{day.isoformat()}".encode("utf-8", "surrogatepass")).hexdigest()
    return os.path.join(root, ".rams-airing-status", digest + ".json")


def read_missed_status(root, identity):
    """Read one override without scanning either the archive or the status folder."""
    if not identity:
        return None
    payload = read_recording_metadata(_status_path(root, identity))
    value = payload.get("flagged_missed")
    return value if isinstance(value, bool) else None


def write_missed_status(root, identity, flagged):
    write_recording_metadata(_status_path(root, identity), {
        "show_key": identity[0], "show_date": identity[1].isoformat(),
        "flagged_missed": flagged, "missed_updated_at": datetime.utcnow().isoformat(),
    })


def clear_probe_status(root, identity):
    """Dismiss only automatic flags; retain an explicit manual Missed decision."""
    path = _status_path(root, identity)
    payload = read_recording_metadata(path)
    payload.update({"show_key": identity[0], "show_date": identity[1].isoformat(),
                    "probe_missed_cleared": True, "probe_cleared_at": datetime.utcnow().isoformat()})
    write_recording_metadata(path, payload)


def load_probe_clearances(root):
    cleared = set()
    directory = os.path.join(root, ".rams-airing-status")
    if not os.path.isdir(directory):
        return cleared
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.name.endswith(".json"):
                continue
            payload = read_recording_metadata(entry.path)
            try:
                if payload.get("probe_missed_cleared") is True and isinstance(payload["show_key"], str):
                    cleared.add((payload["show_key"], date.fromisoformat(payload["show_date"])))
            except (KeyError, TypeError, ValueError):
                continue
    return cleared


def probe_status_cleared(root, identity):
    return bool(identity and read_recording_metadata(_status_path(root, identity)).get("probe_missed_cleared") is True)


def load_missed_statuses(root):
    """One snapshot per archive listing/audit, rather than one lookup per file."""
    statuses = {}
    directory = os.path.join(root, ".rams-airing-status")
    if not os.path.isdir(directory):
        return statuses
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.name.endswith(".json"):
                continue
            payload = read_recording_metadata(entry.path)
            try:
                identity = payload["show_key"], date.fromisoformat(payload["show_date"])
                value = payload["flagged_missed"]
                if isinstance(identity[0], str) and isinstance(value, bool):
                    statuses[identity] = value
            except (KeyError, TypeError, ValueError):
                continue
    return statuses
