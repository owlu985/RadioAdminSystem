from __future__ import annotations

import csv
import io
import json
import os
from typing import List, Dict

from sqlalchemy import or_

from app.models import ArchivistEntry, db


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
