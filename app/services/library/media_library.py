from datetime import date, datetime
import base64
import json
import os
import time
from typing import Dict, List, Optional, Tuple

from flask import current_app, url_for

from app.models import ImagingAsset, PsaAsset, db
from app.services.library.music_search import load_cue  # type: ignore[attr-defined]


AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".wav", ".ogg")
_MEDIA_INDEX_CACHE: Dict[str, Optional[object]] = {"data": None, "loaded_at": None, "root": None}
ASSET_METADATA_KINDS = {"psa", "imaging"}
_FILESYSTEM_TOKEN_MARKER = b"\x00"
_SURROGATE_TOKEN_MARKER = b"\x01"


def encode_media_token(path: str) -> str:
    """Encode a filesystem path without losing undecodable filename bytes.

    On POSIX, Python represents filename bytes that are not valid UTF-8 with
    surrogate escapes.  ``os.fsencode`` reverses that representation, unlike a
    strict UTF-8 encode (which raises) or ``errors=ignore/replace`` (which makes
    the token point at a different file).
    """

    try:
        payload = _FILESYSTEM_TOKEN_MARKER + os.fsencode(path)
    except UnicodeEncodeError:
        # A manually edited/stale index can contain surrogates that did not
        # originate from the OS. Keep the library endpoint usable and make the
        # token reversible even though such a path cannot normally name a file.
        payload = _SURROGATE_TOKEN_MARKER + path.encode("utf-8", "surrogatepass")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def decode_media_token(token: str) -> str:
    """Decode a token produced by :func:`encode_media_token` losslessly."""

    payload = base64.urlsafe_b64decode(token.encode("ascii"))
    if payload.startswith(_FILESYSTEM_TOKEN_MARKER):
        return os.fsdecode(payload[1:])
    if payload.startswith(_SURROGATE_TOKEN_MARKER):
        return payload[1:].decode("utf-8", "surrogatepass")
    # Continue accepting tokens generated before markers were introduced.
    return payload.decode("utf-8")


def _json_safe_text(value):
    """Replace invalid surrogate code points while preserving visible spacing."""

    if not isinstance(value, str):
        return value
    return value.encode("utf-8", "replace").decode("utf-8")


def _media_index_path() -> str:
    return os.path.join(current_app.instance_path, "media_index.json")


def _media_roots() -> List[Tuple[str, str, str]]:
    roots: List[Tuple[str, str, str]] = []
    psa_root = current_app.config.get("PSA_LIBRARY_PATH") or os.path.join(current_app.instance_path, "psa")
    if psa_root:
        roots.append(("PSA", psa_root, "psa"))
    imaging_root = current_app.config.get("IMAGING_LIBRARY_PATH") or os.path.join(current_app.instance_path, "imaging")
    if imaging_root:
        roots.append(("Imaging", imaging_root, "imaging"))
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


def load_media_meta(path: str) -> Dict:
    meta_path = os.path.splitext(path)[0] + ".json"
    if not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def save_media_meta(path: str, updates: Dict) -> Dict:
    meta_path = os.path.splitext(path)[0] + ".json"
    meta = load_media_meta(path)
    meta.update(updates)
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh)
    return meta


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


def _asset_model(kind: str):
    if kind == "psa":
        return PsaAsset
    if kind == "imaging":
        return ImagingAsset
    return None


def _parse_metadata_date(value) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(value, fmt).date()
                except ValueError:
                    continue
    return None


def _normalize_text(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned if cleaned else None
    return str(value)


def _serialize_asset(asset) -> Dict[str, Optional[str]]:
    return {
        "title": asset.title,
        "category": asset.category,
        "expires_on": asset.expires_on.isoformat() if asset.expires_on else None,
        "usage_rules": asset.usage_rules,
    }


def get_asset_metadata(path: str, kind: str) -> Dict[str, Optional[str]]:
    model = _asset_model(kind)
    if not model:
        return {}
    asset = model.query.filter_by(path=path).first()
    if not asset:
        return {}
    return _serialize_asset(asset)


def save_asset_metadata(path: str, kind: str, payload: Dict) -> Dict[str, Optional[str]]:
    model = _asset_model(kind)
    if not model:
        raise ValueError("Unsupported asset kind.")
    asset = model.query.filter_by(path=path).first()
    if not asset:
        asset = model(path=path)
    asset.title = _normalize_text(payload.get("title"))
    asset.category = _normalize_text(payload.get("category"))
    asset.expires_on = _parse_metadata_date(payload.get("expires_on") or payload.get("expiry"))
    asset.usage_rules = _normalize_text(payload.get("usage_rules") or payload.get("usage"))
    db.session.add(asset)
    db.session.commit()
    return _serialize_asset(asset)


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
    items: List[Dict] = []
    for entry in index.get("files", {}).values():
        path = entry["path"]
        duration = None
        meta = load_media_meta(path)
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
        token = encode_media_token(path)
        asset_meta: Dict[str, Optional[str]] = {}
        if entry.get("kind") in ASSET_METADATA_KINDS:
            asset_meta = get_asset_metadata(path, entry["kind"])
            if not asset_meta:
                asset_meta = {
                    "title": meta.get("title"),
                    "category": meta.get("category"),
                    "expires_on": meta.get("expires_on") or meta.get("expiry"),
                    "usage_rules": meta.get("usage_rules") or meta.get("usage"),
                }
        effective_category = _json_safe_text(asset_meta.get("category") or entry.get("category"))
        title = _json_safe_text(asset_meta.get("title"))
        items.append({
            "name": _json_safe_text(entry["name"]),
            "title": title,
            "url": url_for("main.media_file", token=token),
            "token": token,
            "duration": duration,
            "category": effective_category,
            "library_category": _json_safe_text(entry.get("category")),
            "kind": entry["kind"],
            "loop": bool(meta.get("loop")),
            "cues": cues,
            "expires_on": _json_safe_text(asset_meta.get("expires_on")),
            "usage_rules": _json_safe_text(asset_meta.get("usage_rules")),
        })

    if kind:
        items = [f for f in items if f.get("kind") == kind]
    all_categories = sorted({f.get("category") for f in items if f.get("category")})
    if category:
        items = [f for f in items if f.get("category") == category]
    if query:
        q = query.lower()
        items = [
            f for f in items
            if q in (f.get("title") or "").lower() or q in f.get("name", "").lower()
        ]

    total = len(items)
    page = max(1, page)
    per_page = max(1, min(per_page, 100))
    start = (page - 1) * per_page
    end = start + per_page
    page_files = items[start:end]

    return {
        "items": page_files,
        "total": total,
        "page": page,
        "per_page": per_page,
        "categories": all_categories,
    }
