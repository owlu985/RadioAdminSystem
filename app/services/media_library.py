import base64
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from flask import current_app, url_for

from app.models import ImagingAsset, PsaAsset, db
from app.services.music_search import load_cue  # type: ignore[attr-defined]


AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".wav", ".ogg")
_MEDIA_INDEX_CACHE: Dict[str, Optional[object]] = {"data": None, "loaded_at": None, "root": None}


def _media_index_path() -> str:
    return os.path.join(current_app.instance_path, "media_index.json")


def _media_roots() -> List[Tuple[str, str, str]]:
    roots: List[Tuple[str, str, str]] = []
    psa_root = current_app.config.get("PSA_LIBRARY_PATH") or os.path.join(current_app.instance_path, "psa")
    if psa_root:
        roots.append(("PSA", psa_root, "psa"))
    music_root = current_app.config.get("NAS_MUSIC_ROOT")
    if music_root:
        roots.append(("Music", music_root, "music"))
    assets_root = current_app.config.get("MEDIA_ASSETS_ROOT")
    if assets_root:
        roots.append(("Assets", assets_root, "asset"))
    voice_root = current_app.config.get("VOICE_TRACKS_ROOT") or os.path.join(current_app.instance_path, "voice_tracks")
    roots.append(("Voice Tracks", voice_root, "voicetrack"))
    return roots


def _normalize_category(label: str, root: str, base: str) -> str:
    rel = os.path.relpath(base, root)
    if rel == ".":
        return label
    rel = rel.replace(os.sep, "/")
    return f"{label}/{rel}"


def _read_index_file() -> Dict:
    path = _media_index_path()
    if not os.path.exists(path):
        return {"files": {}, "generated_at": None}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh) or {"files": {}, "generated_at": None}
    except Exception:
        return {"files": {}, "generated_at": None}


def _write_index_file(payload: Dict) -> None:
    path = _media_index_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def build_media_index(existing: Optional[Dict] = None) -> Dict:
    existing_files = (existing or {}).get("files", {})
    new_files: Dict[str, Dict] = {}
    for label, root, kind in _media_roots():
        if not root:
            continue
        os.makedirs(root, exist_ok=True)
        for base, _, files in os.walk(root):
            category = _normalize_category(label, root, base)
            for fname in files:
                if not fname.lower().endswith(AUDIO_EXTS):
                    continue
                full = os.path.normpath(os.path.join(base, fname))
                try:
                    stat = os.stat(full)
                except OSError:
                    continue
                prev = existing_files.get(full)
                if prev and prev.get("mtime") == stat.st_mtime and prev.get("size") == stat.st_size:
                    new_files[full] = prev
                    continue
                entry = {
                    "path": full,
                    "name": fname,
                    "category": category,
                    "kind": kind,
                    "mtime": stat.st_mtime,
                    "size": stat.st_size,
                }
                new_files[full] = entry
    payload = {"generated_at": time.time(), "files": new_files}
    _write_index_file(payload)
    return payload


def get_media_index(refresh: bool = False) -> Dict:
    ttl = current_app.config.get("MEDIA_INDEX_TTL", 60)
    cached = _MEDIA_INDEX_CACHE.get("data")
    loaded_at = _MEDIA_INDEX_CACHE.get("loaded_at") or 0
    if cached and not refresh and time.time() - loaded_at < ttl:
        return cached  # type: ignore[return-value]

    disk = _read_index_file()
    if disk.get("files") and not refresh:
        _MEDIA_INDEX_CACHE["data"] = disk
        _MEDIA_INDEX_CACHE["loaded_at"] = time.time()
        return disk

    payload = build_media_index(disk)
    _MEDIA_INDEX_CACHE["data"] = payload
    _MEDIA_INDEX_CACHE["loaded_at"] = time.time()
    return payload


def list_media(
    query: Optional[str] = None,
    category: Optional[str] = None,
    kind: Optional[str] = None,
    page: int = 1,
    per_page: int = 50,
) -> Dict:
    index = get_media_index()
    files = list(index.get("files", {}).values())
    all_categories = sorted({f.get("category") for f in files if f.get("category")})
    if kind:
        files = [f for f in files if f.get("kind") == kind]
    if category:
        files = [f for f in files if f.get("category") == category]
    if query:
        q = query.lower()
        files = [f for f in files if q in f.get("name", "").lower()]

    total = len(files)
    page = max(1, page)
    per_page = max(1, min(per_page, 100))
    start = (page - 1) * per_page
    end = start + per_page
    page_files = files[start:end]

    items: List[Dict] = []
    for entry in page_files:
        path = entry["path"]
        duration = None
        meta: Dict = {}
        meta_path = os.path.splitext(path)[0] + ".json"
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except Exception:
                meta = {}
        try:
            import mutagen  # type: ignore

            audio = mutagen.File(path)
            if audio and getattr(audio, "info", None) and getattr(audio.info, "length", None):
                duration = round(audio.info.length, 2)
        except Exception:
            duration = None

        cues: Dict[str, Optional[float]] = {}
        cue_obj = load_cue(path)
        if cue_obj:
            cues.update({
                "cue_in": cue_obj.cue_in,
                "intro": cue_obj.intro,
                "outro": cue_obj.outro,
                "cue_out": cue_obj.cue_out,
                "loop_in": cue_obj.loop_in,
                "loop_out": cue_obj.loop_out,
                "hook_in": cue_obj.hook_in,
                "hook_out": cue_obj.hook_out,
                "start_next": cue_obj.start_next,
            })
        cues.update({
            k: meta.get(k)
            for k in ["cue_in", "cue_out", "intro", "outro", "loop_in", "loop_out", "hook_in", "hook_out", "start_next"]
            if meta.get(k) is not None
        })
        cues = {k: v for k, v in cues.items() if v is not None}
        token = base64.urlsafe_b64encode(path.encode("utf-8")).decode("utf-8")
        items.append({
            "name": entry["name"],
            "url": url_for("main.media_file", token=token),
            "duration": duration,
            "category": entry["category"],
            "kind": entry["kind"],
            "loop": bool(meta.get("loop")),
            "cues": cues,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "categories": all_categories,
    }
