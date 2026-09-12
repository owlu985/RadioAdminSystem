"""Discovery and metadata helpers for recordings made before RAMS."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import re
import tempfile

import mutagen  # type: ignore

from app.services.log_export import read_recording_metadata, write_recording_metadata


AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac")
LEGACY_PATTERNS = (
    re.compile(r"^(?P<dj>.+?)[_-](?P<month>\d{1,2})[-_](?P<day>\d{1,2})[-_](?P<year>\d{2,4})(?:[_ -].*)?$", re.I),
    re.compile(r"^(?P<dj>.+?)\s+(?P<month>\d{1,2})-(?P<day>\d{1,2})-(?P<year>\d{2,4})(?:\s+.*)?$", re.I),
)


@dataclass(frozen=True)
class LegacySuggestion:
    path: str
    relative_path: str
    filename: str
    show_name: str
    dj_names: str
    recorded_date: str
    recognized: bool
    duration_seconds: int | None
    modified_at: datetime

    @property
    def duration_label(self) -> str:
        if self.duration_seconds is None:
            return "Unknown"
        hours, remainder = divmod(self.duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def _display_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ").replace("-", " ")).strip()


def split_dj_names(value: str) -> list[str]:
    """An ampersand always separates hosts in legacy metadata."""
    return [name.strip() for name in value.split("&") if name.strip()]


def legacy_ignore_path(path: str) -> str:
    return f"{path}.rams-ignore"


def ignore_legacy_recording(path: str) -> str:
    """Atomically mark a non-show audio file so future scans skip it."""
    marker = legacy_ignore_path(path)
    directory = os.path.dirname(marker) or "."
    fd, temporary = tempfile.mkstemp(prefix=".rams-ignore-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({
                "ignored_by": "legacy_recording_import",
                "source_filename": os.path.basename(path),
                "ignored_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            }, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, marker)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return marker


def _audio_duration(path: str) -> int | None:
    try:
        audio = mutagen.File(path)
        length = getattr(getattr(audio, "info", None), "length", None)
        return max(0, round(float(length))) if length is not None else None
    except Exception:
        return None


def _modified_at(path: str) -> datetime:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        return datetime.fromtimestamp(0)


def parse_legacy_filename(path: str, period_root: str) -> LegacySuggestion:
    filename = os.path.basename(path)
    stem = os.path.splitext(filename)[0]
    match = next((pattern.match(stem) for pattern in LEGACY_PATTERNS if pattern.match(stem)), None)
    dj_names = ""
    recorded_date = ""
    if match:
        dj_names = _display_name(match.group("dj"))
        year = int(match.group("year"))
        if year < 100:
            year += 2000 if year <= 68 else 1900
        try:
            recorded_date = datetime(year, int(match.group("month")), int(match.group("day"))).date().isoformat()
        except ValueError:
            recorded_date = ""

    # Older archives commonly use a DJ-named directory. Only use it when the
    # filename itself did not provide a recognized host.
    parent = os.path.basename(os.path.dirname(path))
    if not dj_names and os.path.abspath(os.path.dirname(path)) != os.path.abspath(period_root):
        dj_names = _display_name(parent)
    return LegacySuggestion(
        path=path,
        relative_path=os.path.relpath(path, period_root),
        filename=filename,
        show_name="",
        dj_names=dj_names,
        recorded_date=recorded_date,
        recognized=bool(match),
        duration_seconds=_audio_duration(path),
        modified_at=_modified_at(path),
    )


def discover_legacy_recordings(period_root: str) -> list[LegacySuggestion]:
    if not os.path.isdir(period_root):
        return []
    found = []
    for root, dirs, files in os.walk(period_root):
        dirs.sort(key=str.casefold)
        for filename in sorted(files, key=str.casefold):
            path = os.path.join(root, filename)
            if (filename.lower().endswith(AUDIO_EXTENSIONS)
                    and not os.path.isfile(legacy_ignore_path(path))
                    and not read_recording_metadata(path)):
                found.append(parse_legacy_filename(path, period_root))
    return found


def write_legacy_sidecar(path: str, *, period: str, show_name: str, dj_names: list[str], recorded_date: str | None) -> str:
    timestamp = None
    if recorded_date:
        timestamp = f"{recorded_date}T00:00:00"
    return write_recording_metadata(path, {
        "schema_version": 1,
        "recording_type": "legacy",
        "legacy_import": True,
        "show_name": show_name.strip(),
        "dj_names": dj_names,
        "hosts": dj_names,
        "period": period,
        "recorded_date": recorded_date or None,
        "show_start": timestamp,
        "show_end": None,
        "log_file": None,
        "listener_metrics_applicable": False,
        "compliance_applicable": False,
        "source_filename": os.path.basename(path),
        "imported_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })
