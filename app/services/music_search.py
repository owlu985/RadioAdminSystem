import os
from typing import List, Dict, Optional
from flask import current_app

try:
    import mutagen  # type: ignore
except Exception:  # noqa: BLE001
    mutagen = None


def _walk_music():
    root = current_app.config.get("NAS_MUSIC_ROOT")
    if not root or not os.path.exists(root):
        return []
    for base, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith((".mp3", ".flac", ".m4a", ".wav", ".ogg")):
                yield os.path.join(base, f)


def _read_tags(path: str) -> Dict:
    data = {
        "path": path,
        "title": os.path.splitext(os.path.basename(path))[0],
        "artist": None,
        "album": None,
        "composer": None,
        "isrc": None,
        "year": None,
        "track": None,
        "disc": None,
        "copyright": None,
    }
    if not mutagen:
        return data
    try:
        audio = mutagen.File(path, easy=True)
        if not audio:
            return data
        for key, target in [
            ("title", "title"),
            ("artist", "artist"),
            ("album", "album"),
            ("composer", "composer"),
            ("isrc", "isrc"),
            ("date", "year"),
            ("year", "year"),
            ("tracknumber", "track"),
            ("discnumber", "disc"),
            ("copyright", "copyright"),
        ]:
            val = audio.tags.get(key) if audio.tags else None
            if val:
                data[target] = val[0] if isinstance(val, list) else val
        return data
    except Exception:
        return data


def search_music(query: str) -> List[Dict]:
    query_lower = query.lower()
    results = []
    for path in _walk_music():
        tags = _read_tags(path)
        haystack = " ".join(filter(None, [
            tags.get("title"),
            tags.get("artist"),
            tags.get("album"),
            tags.get("composer"),
        ])).lower()
        if query_lower in haystack:
            results.append(tags)
    return results


def get_track(path: str) -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    return _read_tags(path)
