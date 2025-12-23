import os
import time
import json
import requests
from typing import List, Dict
from flask import current_app
from app.services.detection import analyze_audio
from app.services.music_search import _walk_music, _read_tags


def audit_recordings(folder: str | None = None) -> List[Dict]:
    """
    Analyze recordings using the existing silence/automation classifier.
    """
    folder = folder or current_app.config["OUTPUT_FOLDER"]
    results = []
    for base, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith((".mp3", ".wav", ".flac", ".aac", ".m4a")):
                path = os.path.join(base, f)
                analysis = analyze_audio(path, current_app.config)
                results.append({
                    "path": path,
                    "classification": analysis.classification,
                    "reason": analysis.reason,
                    "avg_db": analysis.avg_db,
                    "silence_ratio": analysis.silence_ratio,
                    "automation_ratio": analysis.automation_ratio,
                })
    return results


def _itunes_search(title: str, artist: str, rate_limit_s: float = 0.5):
    params = {
        "term": f"{title} {artist}".strip(),
        "limit": 5,
        "entity": "song"
    }
    resp = requests.get("https://itunes.apple.com/search", params=params, timeout=10)
    time.sleep(rate_limit_s)
    resp.raise_for_status()
    return resp.json().get("results", [])


def audit_explicit_music(rate_limit_s: float = 0.5, max_files: int = 500) -> List[Dict]:
    """
    Check songs for explicit flag and presence of a clean version via iTunes API.
    """
    results = []
    for idx, path in enumerate(_walk_music()):
        if idx >= max_files:
            break
        tags = _read_tags(path)
        title = tags.get("title") or ""
        artist = tags.get("artist") or ""
        if not title or not artist:
            continue
        try:
            api_results = _itunes_search(title, artist, rate_limit_s=rate_limit_s)
        except Exception as exc:  # noqa: BLE001
            results.append({
                "path": path,
                "title": title,
                "artist": artist,
                "error": str(exc),
            })
            continue

        explicit_found = None
        clean_available = False
        for item in api_results:
            explicitness = item.get("trackExplicitness")
            if explicitness == "explicit":
                explicit_found = True
            if explicitness == "notExplicit":
                clean_available = True
        results.append({
            "path": path,
            "title": title,
            "artist": artist,
            "explicit": bool(explicit_found),
            "clean_available": clean_available,
        })
    return results
