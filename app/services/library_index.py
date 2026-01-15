from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Dict

from flask import current_app

from app.services import music_search

_library_index_state: Dict[str, object] = {
    "status": "idle",
    "progress": 0,
    "total": 0,
    "updated_at": None,
    "error": None,
}
_state_lock = threading.Lock()


def _set_state(**updates: object) -> None:
    with _state_lock:
        _library_index_state.update(updates)
        _library_index_state["updated_at"] = datetime.utcnow().isoformat()


def _calculate_progress(completed: int, total: int) -> int:
    if total <= 0:
        return 0
    return min(100, int((completed / total) * 100))


def _build_index(app) -> None:
    with app.app_context():
        root = app.config.get("NAS_MUSIC_ROOT")
        existing = music_search._load_music_index_file()
        if not root or not os.path.exists(root):
            payload = {"files": {}, "generated_at": time.time(), "root": root}
            music_search._write_music_index_file(payload)
            music_search._MUSIC_INDEX_CACHE["data"] = payload
            music_search._MUSIC_INDEX_CACHE["loaded_at"] = time.time()
            music_search._MUSIC_INDEX_CACHE["root"] = root
            _set_state(status="idle", progress=0, total=0, error=None)
            return

        files = list(music_search._walk_music())
        total = len(files)
        _set_state(status="running", progress=0, total=total, error=None)

        existing_files = (existing or {}).get("files", {})
        new_files: Dict[str, Dict] = {}
        for idx, path in enumerate(files):
            full = os.path.normpath(path)
            try:
                stat = os.stat(full)
            except OSError:
                progress = _calculate_progress(idx + 1, total)
                _set_state(progress=progress)
                continue
            prev = existing_files.get(full)
            if prev and prev.get("mtime") == stat.st_mtime and prev.get("size") == stat.st_size:
                new_files[full] = prev
                progress = _calculate_progress(idx + 1, total)
                _set_state(progress=progress)
                continue

            tags = music_search._read_tags(full)
            rel_dir = os.path.relpath(os.path.dirname(full), root)
            folder = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            search_blob = " ".join(filter(None, [
                tags.get("title"),
                tags.get("artist"),
                tags.get("album_artist"),
                tags.get("album"),
                tags.get("composer"),
                tags.get("genre"),
                tags.get("year"),
            ])).lower()
            entry = {
                "path": full,
                "title": tags.get("title"),
                "artist": tags.get("artist"),
                "album_artist": tags.get("album_artist"),
                "album": tags.get("album"),
                "composer": tags.get("composer"),
                "genre": tags.get("genre"),
                "year": tags.get("year"),
                "folder": folder,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "search": search_blob,
                "track_num": music_search._parse_track_number(tags.get("track")),
                "disc_num": music_search._parse_track_number(tags.get("disc")),
            }
            new_files[full] = entry
            progress = _calculate_progress(idx + 1, total)
            _set_state(progress=progress)

        payload = {"files": new_files, "generated_at": time.time(), "root": root}
        music_search._write_music_index_file(payload)
        music_search._MUSIC_INDEX_CACHE["data"] = payload
        music_search._MUSIC_INDEX_CACHE["loaded_at"] = time.time()
        music_search._MUSIC_INDEX_CACHE["root"] = root
        _set_state(status="idle", progress=100 if total else 0, total=total, error=None)


def start_library_index_job() -> bool:
    with _state_lock:
        if _library_index_state.get("status") in {"running", "queued"}:
            return False
        _library_index_state["status"] = "queued"
        _library_index_state["progress"] = 0
        _library_index_state["error"] = None
        _library_index_state["updated_at"] = datetime.utcnow().isoformat()

    app = current_app._get_current_object()
    thread = threading.Thread(target=_run_job, args=(app,), daemon=True)
    thread.start()
    return True


def _run_job(app) -> None:
    try:
        _build_index(app)
    except Exception as exc:  # noqa: BLE001
        _set_state(status="error", error=str(exc))


def get_library_index_status() -> Dict[str, object]:
    with _state_lock:
        payload = dict(_library_index_state)
    return payload
