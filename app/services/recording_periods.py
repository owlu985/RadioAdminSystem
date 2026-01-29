from __future__ import annotations

import json
import os
from typing import Iterable

from flask import current_app

DEFAULT_PERIOD_NAME = "Current Period"
UNASSIGNED_PERIOD_LABEL = "Unassigned"


def _periods_path() -> str:
    path = current_app.config.get("RECORDING_PERIODS_PATH")
    if path:
        return path
    data_root = current_app.config.get("DATA_ROOT") or current_app.instance_path
    return os.path.join(data_root, "recording_periods.json")


def _normalize_periods(payload: dict | None) -> dict:
    periods: list[str] = []
    current = None
    if isinstance(payload, dict):
        current = payload.get("current")
        raw_periods = payload.get("periods", [])
        if isinstance(raw_periods, list):
            periods = [str(item).strip() for item in raw_periods if str(item).strip()]

    if not periods:
        periods = [DEFAULT_PERIOD_NAME]

    if current is None or str(current).strip() == "":
        current = periods[0]
    current = str(current).strip()

    if current not in periods:
        periods.insert(0, current)

    return {"current": current, "periods": periods}


def load_recording_periods() -> dict:
    path = _periods_path()
    if not os.path.isfile(path):
        return _normalize_periods({})
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return _normalize_periods({})
    return _normalize_periods(payload)


def save_recording_periods(*, periods: Iterable[str], current: str | None = None) -> dict:
    payload = {"periods": list(periods), "current": current}
    normalized = _normalize_periods(payload)
    path = _periods_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=2)
    return normalized


def current_recording_period() -> str:
    return load_recording_periods()["current"]


def period_folder_name(period: str) -> str:
    return period.replace("/", "_").replace("\\", "_").strip()


def recordings_base_root() -> str:
    root = current_app.config.get("OUTPUT_FOLDER") or os.path.join(current_app.instance_path, "recordings")
    os.makedirs(root, exist_ok=True)
    return root


def recordings_period_root(period: str | None = None) -> str:
    if period is None:
        period = current_recording_period()
    base = recordings_base_root()
    folder = period_folder_name(period)
    full = os.path.join(base, folder)
    os.makedirs(full, exist_ok=True)
    return full
