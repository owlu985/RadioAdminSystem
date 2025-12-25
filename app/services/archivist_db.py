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
    return {
        "title": lower.get("title") or lower.get("song") or lower.get("track") or None,
        "artist": lower.get("artist") or lower.get("performer") or None,
        "album": lower.get("album") or lower.get("release") or None,
        "catalog_number": lower.get("catalog") or lower.get("catalog_number") or lower.get("catno") or None,
        "notes": lower.get("notes") or lower.get("comment") or None,
        "extra": json.dumps(row, ensure_ascii=False),
    }


def import_archivist_csv(file_storage, storage_path: str | None = None) -> int:
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
