"""Small, dependency-free system resource helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict


class MemoryStatus(TypedDict):
    available: bool
    used_bytes: int
    total_bytes: int
    percent: float
    level: str
    label: str


def _read_linux_memory(path: Path = Path("/proc/meminfo")) -> tuple[int, int] | None:
    try:
        values = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        if total > 0 and 0 <= available <= total:
            return total, available
    except (OSError, ValueError, IndexError):
        return None
    return None


def get_memory_status() -> MemoryStatus:
    """Return host-memory utilization and a UI-friendly severity."""
    sample = _read_linux_memory()
    if sample is None:
        return {
            "available": False,
            "used_bytes": 0,
            "total_bytes": 0,
            "percent": 0.0,
            "level": "secondary",
            "label": "Unavailable",
        }

    total, available = sample
    used = total - available
    percent = round((used / total) * 100, 1)
    if percent >= 85:
        level, label = "danger", "High"
    elif percent >= 70:
        level, label = "warning", "Elevated"
    else:
        level, label = "success", "Normal"
    return {
        "available": True,
        "used_bytes": used,
        "total_bytes": total,
        "percent": percent,
        "level": level,
        "label": label,
    }
