from __future__ import annotations

import csv
import io
import json
import os
import time
from typing import List, Dict, Optional

from werkzeug.utils import secure_filename

try:
    from pydub import AudioSegment
    from pydub.silence import detect_silence
except Exception:  # noqa: BLE001
    AudioSegment = None
    detect_silence = None

from sqlalchemy import or_

from app.models import ArchivistEntry, db


def _album_tmp_dir() -> str:
    base = os.path.join(os.getcwd(), "instance", "album_rip_tmp")
    os.makedirs(base, exist_ok=True)
    return base


def save_album_rip_upload(file_storage) -> str:
    """Persist a single album-rip upload to a temp folder and return its path."""

    tmp_dir = _album_tmp_dir()
    # Remove stale files older than 20 minutes
    cleanup_album_tmp(max_age_seconds=20 * 60)

    filename = secure_filename(file_storage.filename or "rip.wav") or "rip.wav"
    ts = int(time.time())
    path = os.path.join(tmp_dir, f"{ts}_{filename}")

    file_storage.save(path)
    return path


def delete_album_rip_upload(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        # Best-effort cleanup
        pass


def cleanup_album_tmp(max_age_seconds: int = 15 * 60):
    """Remove album-rip temp files older than the provided age (default 15 minutes)."""

    tmp_dir = _album_tmp_dir()
    now = time.time()
    for name in os.listdir(tmp_dir):
        path = os.path.join(tmp_dir, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if now - stat.st_mtime > max_age_seconds:
            try:
                os.remove(path)
            except OSError:
                continue


def _normalize_row(row: Dict[str, str]):
    lower = {k.lower(): (v or "").strip() for k, v in row.items()}

    # Preferred headers for the paid archivist database
    artist = lower.get("artist") or lower.get("akaoracronym") or lower.get("aka")
    title = lower.get("title") or lower.get("song") or lower.get("track")
    catalog_number = lower.get("catno") or lower.get("catalog") or lower.get("catalog_number")
    label = lower.get("label")
    fmt = lower.get("format")
    price = lower.get("pricerange")
    year = lower.get("year")
    notes = lower.get("notes") or lower.get("comment")

    note_parts = []
    for prefix, value in (
        ("Format", fmt),
        ("Price", price),
        ("Year", year),
        ("Notes", notes),
    ):
        if value:
            note_parts.append(f"{prefix}: {value}")

    combined_notes = " | ".join(note_parts) if note_parts else None

    return {
        "title": title or None,
        "artist": artist or None,
        # Store label in album so it is searchable in the existing UI
        "album": label or lower.get("album") or None,
        "catalog_number": catalog_number or None,
        "notes": combined_notes,
        "extra": json.dumps(row, ensure_ascii=False),
    }


def import_archivist_csv(file_storage, storage_path: str | None = None, upload_dir: str | None = None) -> int:
    """Import a CSV/TSV file into the ArchivistEntry table, replacing existing rows."""

    raw = file_storage.read()
    file_storage.stream.seek(0)
    text = raw.decode("utf-8", errors="ignore")
    delimiter = ","
    first_line = text.splitlines()[0] if text.splitlines() else ""
    if "\t" in first_line:
        delimiter = "\t"
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    entries: List[ArchivistEntry] = []
    for row in reader:
        normalized = _normalize_row(row)
        entries.append(ArchivistEntry(**normalized))

    db.session.query(ArchivistEntry).delete()
    if entries:
        db.session.bulk_save_objects(entries)
    db.session.commit()

    if storage_path:
        os.makedirs(os.path.dirname(storage_path), exist_ok=True)
        with open(storage_path, "w", encoding="utf-8") as f:
            json.dump([json.loads(e.extra) for e in entries], f, ensure_ascii=False, indent=2)

    if upload_dir:
        os.makedirs(upload_dir, exist_ok=True)
        dest_path = os.path.join(upload_dir, "archivist_upload.csv")
        with open(dest_path, "wb") as fh:
            fh.write(raw)

    return len(entries)


def search_archivist(query: str, limit: int = 200):
    q = ArchivistEntry.query
    if query and query != "%":
        like = f"%{query}%"
        q = q.filter(
            or_(
                ArchivistEntry.title.ilike(like),
                ArchivistEntry.artist.ilike(like),
                ArchivistEntry.album.ilike(like),
                ArchivistEntry.catalog_number.ilike(like),
            )
        )
    return q.order_by(ArchivistEntry.artist.asc().nulls_last(), ArchivistEntry.title.asc().nulls_last()).limit(limit).all()


def lookup_album(query: str, limit: int = 5):
    """Return archivist rows matching an album/catalog/title clue for album rips."""
    like = f"%{query}%"
    q = ArchivistEntry.query.filter(
        or_(
            ArchivistEntry.album.ilike(like),
            ArchivistEntry.title.ilike(like),
            ArchivistEntry.catalog_number.ilike(like),
        )
    )
    return q.order_by(ArchivistEntry.artist.asc().nulls_last()).limit(limit).all()


def analyze_album_rip(
    path: str,
    silence_thresh_db: int = -38,
    min_gap_ms: int = 1200,
    min_track_ms: int = 60_000,
) -> Optional[dict]:
    """
    Analyze a full-album rip to suggest track breakpoints while ignoring pops/crackles.
    Returns dict with duration_ms and segments (start_ms/end_ms list) when pydub is available.
    """
    if not AudioSegment or not detect_silence:
        return None
    if not os.path.exists(path):
        return None
    try:
        audio = AudioSegment.from_file(path)
        duration_ms = len(audio)
        # light smoothing: a tiny lowpass to dampen pops
        smooth = audio.low_pass_filter(6000) if hasattr(audio, "low_pass_filter") else audio
        silences = detect_silence(smooth, min_silence_len=min_gap_ms, silence_thresh=silence_thresh_db)
        segments = []
        last_start = 0
        for start, end in silences:
            if start - last_start >= min_track_ms:
                segments.append({"start_ms": last_start, "end_ms": start})
                last_start = end
        if duration_ms - last_start >= max(min_track_ms // 2, 30_000):
            segments.append({"start_ms": last_start, "end_ms": duration_ms})
        return {"duration_ms": duration_ms, "segments": segments}
    except Exception:
        return None
