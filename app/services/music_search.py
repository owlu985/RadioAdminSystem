import os
import json
import hashlib
import time
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from flask import current_app
import re
import requests

try:
    import mutagen  # type: ignore
    from mutagen.id3 import APIC, ID3  # type: ignore
except Exception:  # noqa: BLE001
    mutagen = None
    APIC = None
    ID3 = None

try:
    from pydub import AudioSegment  # type: ignore
except Exception:  # noqa: BLE001
    AudioSegment = None

from app.models import db, MusicAnalysis, MusicCue


AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".wav", ".ogg")
_MUSIC_INDEX_CACHE: Dict[str, Optional[object]] = {"data": None, "loaded_at": None, "root": None}


def _parse_year(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
    if isinstance(value, (int, float)):
        year = int(value)
        return str(year) if year > 0 else None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"(19|20)\d{2}", text)
    if match:
        return match.group(0)
    return None


def _walk_music():
    root = current_app.config.get("NAS_MUSIC_ROOT")
    if not root or not os.path.exists(root):
        return []
    for base, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(AUDIO_EXTS):
                yield os.path.join(base, f)


def _music_index_path() -> str:
    return os.path.join(current_app.instance_path, "music_index.json")


def _load_music_index_file() -> Dict:
    path = _music_index_path()
    if not os.path.exists(path):
        return {"files": {}, "generated_at": None, "root": None}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh) or {"files": {}, "generated_at": None, "root": None}
    except Exception:
        return {"files": {}, "generated_at": None, "root": None}


def _write_music_index_file(payload: Dict) -> None:
    path = _music_index_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def build_music_index(existing: Optional[Dict] = None) -> Dict:
    root = current_app.config.get("NAS_MUSIC_ROOT")
    if not root or not os.path.exists(root):
        return {"files": {}, "generated_at": time.time(), "root": root}

    existing_files = (existing or {}).get("files", {})
    new_files: Dict[str, Dict] = {}
    for path in _walk_music():
        full = os.path.normpath(path)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        prev = existing_files.get(full)
        if prev and prev.get("mtime") == stat.st_mtime and prev.get("size") == stat.st_size:
            new_files[full] = prev
            continue
        tags = _read_tags(full)
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
            "track_num": _parse_track_number(tags.get("track")),
            "disc_num": _parse_track_number(tags.get("disc")),
        }
        new_files[full] = entry
    payload = {"files": new_files, "generated_at": time.time(), "root": root}
    _write_music_index_file(payload)
    return payload


def get_music_index(refresh: bool = False) -> Dict:
    ttl = current_app.config.get("MUSIC_INDEX_TTL", 60)
    root = current_app.config.get("NAS_MUSIC_ROOT")
    cached = _MUSIC_INDEX_CACHE.get("data")
    loaded_at = _MUSIC_INDEX_CACHE.get("loaded_at") or 0
    if cached and not refresh and time.time() - loaded_at < ttl and _MUSIC_INDEX_CACHE.get("root") == root:
        return cached  # type: ignore[return-value]

    disk = _load_music_index_file()
    if disk.get("files") and not refresh and disk.get("root") == root:
        _MUSIC_INDEX_CACHE["data"] = disk
        _MUSIC_INDEX_CACHE["loaded_at"] = time.time()
        _MUSIC_INDEX_CACHE["root"] = root
        return disk

    payload = build_music_index(disk)
    _MUSIC_INDEX_CACHE["data"] = payload
    _MUSIC_INDEX_CACHE["loaded_at"] = time.time()
    _MUSIC_INDEX_CACHE["root"] = root
    return payload


def _library_media_roots() -> List[Tuple[str, str]]:
    roots: List[Tuple[str, str]] = []
    psa_root = current_app.config.get("PSA_LIBRARY_PATH") or os.path.join(current_app.instance_path, "psa")
    if psa_root:
        roots.append(("PSA", psa_root))
    imaging_root = current_app.config.get("MEDIA_ASSETS_ROOT")
    if imaging_root:
        roots.append(("Imaging", imaging_root))
    voice_root = current_app.config.get("VOICE_TRACKS_ROOT") or os.path.join(current_app.instance_path, "voice_tracks")
    if voice_root:
        roots.append(("Voice Tracks", voice_root))
    return roots


def build_library_editor_index() -> Dict:
    index = get_music_index()
    entries = list(index.get("files", {}).values())
    artists_map: Dict[str, Dict[str, Dict]] = {}
    for entry in entries:
        path = entry.get("path") or ""
        title = entry.get("title") or os.path.splitext(os.path.basename(path))[0]
        artist = entry.get("artist") or "Unknown Artist"
        album = entry.get("album") or "Unknown Album"
        year = entry.get("year")
        genre = entry.get("genre")
        artist_bucket = artists_map.setdefault(artist, {})
        album_bucket = artist_bucket.setdefault(album, {"year": year, "genre": genre, "tracks": []})
        if not album_bucket.get("year") and year:
            album_bucket["year"] = year
        if not album_bucket.get("genre") and genre:
            album_bucket["genre"] = genre
        album_bucket["tracks"].append({
            "title": title,
            "path": path,
            "artist": artist,
            "album": album,
            "year": year,
            "genre": genre,
            "track_num": entry.get("track_num"),
            "disc_num": entry.get("disc_num"),
        })

    music_artists = []
    for artist_name in sorted(artists_map.keys(), key=lambda name: name.lower()):
        albums_map = artists_map[artist_name]
        albums_payload = []
        for album_name in sorted(albums_map.keys(), key=lambda name: name.lower()):
            album_payload = albums_map[album_name]
            tracks = album_payload.get("tracks", [])
            tracks.sort(
                key=lambda track: (
                    track.get("disc_num") or 0,
                    track.get("track_num") or 0,
                    (track.get("title") or "").lower(),
                )
            )
            albums_payload.append({
                "name": album_name,
                "year": album_payload.get("year"),
                "genre": album_payload.get("genre"),
                "tracks": tracks,
            })
        music_artists.append({"name": artist_name, "albums": albums_payload})

    psa_imaging = []
    for label, root in _library_media_roots():
        if not root:
            continue
        os.makedirs(root, exist_ok=True)
        items = []
        for base, _, files in os.walk(root):
            for fname in files:
                if not fname.lower().endswith(AUDIO_EXTS):
                    continue
                full = os.path.normpath(os.path.join(base, fname))
                tags = _read_tags(full)
                title = tags.get("title") or os.path.splitext(fname)[0]
                items.append({
                    "title": title,
                    "path": full,
                    "artist": tags.get("artist"),
                    "album": tags.get("album"),
                    "year": tags.get("year"),
                    "genre": tags.get("genre"),
                    "folder": os.path.relpath(base, root).replace(os.sep, "/"),
                })
        items.sort(key=lambda item: (item.get("title") or "").lower())
        psa_imaging.append({"category": label, "items": items})

    return {
        "music": music_artists,
        "psa_imaging": psa_imaging,
        "generated_at": time.time(),
    }


def _read_tags(path: str) -> Dict:
    base_title = os.path.splitext(os.path.basename(path))[0]
    data = {
        "path": path,
        "title": None,
        "artist": None,
        "album_artist": None,
        "album": None,
        "composer": None,
        "isrc": None,
        "genre": None,
        "mood": None,
        "explicit": None,
        "year": None,
        "genre": None,
        "track": None,
        "disc": None,
        "copyright": None,
        "bitrate": None,
        "explicit": None,
        "cover_embedded": False,
    }
    if not mutagen:
        data["title"] = base_title
        return data

    def _coerce(val):
        if isinstance(val, list):
            if len(val) == 1:
                return _coerce(val[0])
            return [_coerce(v) for v in val]

        if hasattr(val, "decode"):
            try:
                val = val.decode("utf-8", errors="ignore")
            except Exception:
                pass

        try:
            from mutagen.mp4 import MP4FreeForm  # type: ignore
        except Exception:  # noqa: BLE001
            MP4FreeForm = None  # type: ignore

        if MP4FreeForm is not None and isinstance(val, MP4FreeForm):
            try:
                return val.decode("utf-8", errors="ignore").strip("\x00")
            except Exception:
                try:
                    return bytes(val).decode("utf-8", errors="ignore").strip("\x00")
                except Exception:
                    return str(val)

        if isinstance(val, bytes):
            try:
                return val.decode("utf-8", errors="ignore").strip("\x00")
            except Exception:
                return val

        if isinstance(val, tuple) and len(val) == 2 and all(isinstance(x, (int, float)) for x in val):
            return val

        try:
            return str(val)
        except Exception:
            return val

    def _first_text(val):
        coerced = _coerce(val)
        if isinstance(coerced, list):
            for item in coerced:
                if isinstance(item, str) and item.strip():
                    return item
            return coerced[0] if coerced else None
        return coerced

    def _parse_explicit(val):
        if val is None:
            return None
        coerced = _coerce(val)
        if isinstance(coerced, list) and coerced:
            coerced = coerced[0]
        if isinstance(coerced, (int, float)):
            if int(coerced) == 2:
                return True
            if int(coerced) == 1:
                return False
        if isinstance(coerced, tuple) and len(coerced) == 2 and all(isinstance(x, (int, float)) for x in coerced):
            if int(coerced[0]) == 2:
                return True
            if int(coerced[0]) == 1:
                return False
        if isinstance(coerced, bytes):
            try:
                coerced = coerced.decode("utf-8", errors="ignore")
            except Exception:
                return None
        if isinstance(coerced, str):
            lowered = coerced.strip().lower()
            if lowered.isdigit():
                val_num = int(lowered)
                if val_num == 2:
                    return True
                if val_num == 1:
                    return False
            if "explicit" in lowered:
                return True
            if "clean" in lowered or "not explicit" in lowered:
                return False
        return None

    try:
        audio_easy = mutagen.File(path, easy=True)
        if audio_easy:
            if hasattr(audio_easy, "info") and getattr(audio_easy.info, "bitrate", None):
                data["bitrate"] = getattr(audio_easy.info, "bitrate")
            if getattr(audio_easy, "tags", None) and "covr" in audio_easy.tags:
                data["cover_embedded"] = True
            for key, target in [
                ("title", "title"),
                ("artist", "artist"),
                ("albumartist", "album_artist"),
                ("album", "album"),
                ("composer", "composer"),
                ("isrc", "isrc"),
                ("date", "year"),
                ("year", "year"),
                ("genre", "genre"),
                ("tracknumber", "track"),
                ("discnumber", "disc"),
                ("copyright", "copyright"),
            ]:
                val = audio_easy.tags.get(key) if audio_easy.tags else None
                if val:
                    data[target] = _first_text(val)
            if audio_easy.tags and data.get("explicit") is None:
                for key in ["itunesadvisory", "explicit", "contentadvisory"]:
                    if key in audio_easy.tags:
                        data["explicit"] = _parse_explicit(audio_easy.tags.get(key))
                        if data["explicit"] is not None:
                            break

        if path.lower().endswith((".m4a", ".mp4")):
            try:
                from mutagen.mp4 import EasyMP4  # type: ignore

                easy_mp4 = EasyMP4(path)
                if hasattr(easy_mp4, "info") and getattr(easy_mp4.info, "bitrate", None) and not data.get("bitrate"):
                    data["bitrate"] = getattr(easy_mp4.info, "bitrate")
                for key, target in [
                    ("title", "title"),
                    ("artist", "artist"),
                    ("albumartist", "album_artist"),
                    ("album", "album"),
                    ("composer", "composer"),
                    ("isrc", "isrc"),
                    ("date", "year"),
                    ("year", "year"),
                    ("genre", "genre"),
                    ("tracknumber", "track"),
                    ("discnumber", "disc"),
                    ("copyright", "copyright"),
                ]:
                    if data.get(target):
                        continue
                    if not getattr(easy_mp4, "tags", None):
                        continue
                    val = easy_mp4.tags.get(key)
                    if val and not data.get(target):
                        coerced = _first_text(val)
                        if coerced:
                            data[target] = coerced
            except Exception:
                pass

            try:
                from mutagen.mp4 import MP4  # type: ignore

                mp4 = MP4(path)
                mp4_tags = mp4.tags or {}
                if "covr" in mp4_tags:
                    data["cover_embedded"] = True
                if data.get("explicit") is None:
                    for atom in ["rtng", "----:com.apple.iTunes:ITUNESADVISORY", "----:com.apple.iTunes:Explicit"]:
                        val = mp4_tags.get(atom)
                        if val is not None:
                            data["explicit"] = _parse_explicit(val)
                            if data["explicit"] is not None:
                                break

                atom_map = {
                    "title": ["©nam", "----:com.apple.iTunes:TITLE"],
                    "artist": ["©ART", "----:com.apple.iTunes:ARTIST"],
                    "album_artist": ["aART", "----:com.apple.iTunes:ALBUMARTIST"],
                    "album": ["©alb", "----:com.apple.iTunes:ALBUM"],
                    "composer": ["©wrt", "----:com.apple.iTunes:COMPOSER"],
                    "isrc": ["----:com.apple.iTunes:ISRC", "----:com.apple.iTunes:isrc"],
                    "year": ["©day", "----:com.apple.iTunes:YEAR", "----:com.apple.iTunes:DATE"],
                    "genre": ["©gen", "gnre", "----:com.apple.iTunes:GENRE"],
                    "track": ["trkn"],
                    "disc": ["disk"],
                    "copyright": ["cprt", "----:com.apple.iTunes:COPYRIGHT"],
                }

                for target, atoms in atom_map.items():
                    if data.get(target):
                        continue
                    for atom in atoms:
                        val = mp4_tags.get(atom)
                        if val:
                            coerced = _first_text(val)
                            if coerced:
                                data[target] = coerced
                                break

                if not data.get("artist") or not data.get("title"):
                    for key, val in mp4_tags.items():
                        if not isinstance(key, str):
                            continue
                        lowered = key.lower()
                        if "artist" in lowered and not data.get("artist"):
                            coerced = _first_text(val)
                            if coerced:
                                data["artist"] = coerced
                        if "title" in lowered and not data.get("title"):
                            coerced = _first_text(val)
                            if coerced:
                                data["title"] = coerced

                if not data.get("artist") or not data.get("title") or not data.get("album"):
                    for key, val in mp4_tags.items():
                        coerced = _first_text(val)
                        if not coerced:
                            continue
                        if isinstance(coerced, str):
                            lower_val = coerced.lower()
                            if not data.get("artist") and "artist" in lower_val:
                                data["artist"] = coerced
                            if not data.get("title") and ("title" in lower_val or "name" in lower_val):
                                data["title"] = coerced
                            if not data.get("album") and "album" in lower_val:
                                data["album"] = coerced

                if data.get("explicit") is None:
                    for key, val in mp4_tags.items():
                        if not isinstance(key, str):
                            continue
                        if "advisory" in key.lower() or "explicit" in key.lower():
                            data["explicit"] = _parse_explicit(val)
                            if data["explicit"] is not None:
                                break

                if hasattr(mp4, "info") and getattr(mp4.info, "bitrate", None) and not data.get("bitrate"):
                    data["bitrate"] = getattr(mp4.info, "bitrate")
            except Exception:
                pass

        if not all([data.get("title"), data.get("artist"), data.get("album")]):
            try:
                raw_audio = mutagen.File(path, easy=False)
                if raw_audio and getattr(raw_audio, "tags", None):
                    if getattr(raw_audio.tags, "get", None):
                        if raw_audio.tags.get("APIC:") or raw_audio.tags.get("APIC"):
                            data["cover_embedded"] = True
                    for key, val in raw_audio.tags.items():
                        text_val = _first_text(val)
                        if not text_val:
                            continue
                        lkey = str(key).lower()
                        if not data.get("title") and any(hint in lkey for hint in ["©nam", "title", "name"]):
                            data["title"] = text_val
                        if not data.get("artist") and any(hint in lkey for hint in ["©art", "artist", "aart"]):
                            data["artist"] = text_val
                        if not data.get("album_artist") and any(hint in lkey for hint in ["aart", "albumartist"]):
                            data["album_artist"] = text_val
                        if not data.get("album") and any(hint in lkey for hint in ["©alb", "album"]):
                            data["album"] = text_val
                        if not data.get("isrc") and "isrc" in lkey:
                            data["isrc"] = text_val
                        if not data.get("composer") and any(hint in lkey for hint in ["wrt", "composer"]):
                            data["composer"] = text_val
                        if not data.get("copyright") and "cprt" in lkey:
                            data["copyright"] = text_val
                        if not data.get("year") and any(hint in lkey for hint in ["day", "year", "date"]):
                            data["year"] = text_val
                        if not data.get("genre") and "genre" in lkey:
                            data["genre"] = text_val
            except Exception:
                pass

        if data.get("year"):
            data["year"] = _parse_year(data.get("year"))
        if data.get("explicit") is not None:
            data["explicit"] = _parse_explicit(data.get("explicit"))
        if not data.get("title") or str(data.get("title")).strip().lower() == "none":
            data["title"] = base_title
        data["year"] = _parse_year(data.get("year"))
        return data
    except Exception:
        if data.get("year"):
            data["year"] = _parse_year(data.get("year"))
        if data.get("explicit") is not None:
            data["explicit"] = _parse_explicit(data.get("explicit"))
        if not data.get("title") or str(data.get("title")).strip().lower() == "none":
            data["title"] = base_title
        data["year"] = _parse_year(data.get("year"))
        return data


def _audio_stats(path: str) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[Dict]]:
    """Return duration/peak/rms plus mono+stereo-friendly peaks.

    The waveform preview now returns a dict that can contain mono-only data
    (``{"mono": [...]}``) or stereo channels (``{"left": [...], "right": [...], "mono": [...]}``).
    Existing callers that stored a flat list will continue to work because we
    keep the mono key and front-end code gracefully handles legacy list data.
    """
    if not AudioSegment:
        return None, None, None, None
    try:
        audio = AudioSegment.from_file(path)
        duration_seconds = len(audio) / 1000.0
        rms_db = audio.rms if hasattr(audio, "rms") else None
        peak_db = audio.max_dBFS if hasattr(audio, "max_dBFS") else None
        samples = audio.get_array_of_samples()
        channels = audio.channels or 1
        if not samples:
            return duration_seconds, peak_db, rms_db, None

        def _peaks_for_channel(channel_samples: List[int]) -> List[float]:
            step = max(1, int(len(channel_samples) / 180))
            peaks_local: List[float] = []
            max_val = max(abs(int(s)) for s in channel_samples) or 1
            for i in range(0, len(channel_samples), step):
                window = channel_samples[i : i + step]
                if not window:
                    continue
                local_peak = max(abs(int(s)) for s in window) / max_val
                peaks_local.append(round(local_peak, 4))
            return peaks_local

        peaks_payload: Dict[str, List[float]] = {}
        if channels >= 2:
            left = samples[0::channels]
            right = samples[1::channels]
            peaks_payload["left"] = _peaks_for_channel(left)
            peaks_payload["right"] = _peaks_for_channel(right)
            # mono mix for compatibility/needle math
            mono_mix = [int((l + r) / 2) for l, r in zip(left, right)]
            peaks_payload["mono"] = _peaks_for_channel(mono_mix)
        else:
            peaks_payload["mono"] = _peaks_for_channel(list(samples))
        return duration_seconds, peak_db, rms_db, peaks_payload
    except Exception:
        return None, None, None, None


def detect_audio_cues(
    path: str,
    start_threshold: float = -25.0,
    mix_threshold: float = -15.0,
    end_threshold: float = -28.0,
    chunk_ms: int = 50,
) -> Dict[str, float]:
    """Best-effort cue detection when tags are missing.

    Returns a dict that may include cue_in (start), start_next (mix point), and
    cue_out (end) using RadioDJ-style defaults. Only returned keys should be
    merged into existing cues when they are absent.
    """

    if not AudioSegment:
        return {}

    try:
        audio = AudioSegment.from_file(path)
    except Exception:
        return {}

    duration_sec = len(audio) / 1000.0 if audio else 0.0
    start_at: Optional[float] = None
    mix_at: Optional[float] = None
    end_at: Optional[float] = None

    # Walk the waveform in fixed windows to approximate cues.
    for idx, pos in enumerate(range(0, len(audio), chunk_ms)):
        window = audio[pos : pos + chunk_ms]
        if not window:
            continue
        try:
            level = window.dBFS if window.rms else -120.0
        except Exception:
            level = -120.0

        ts = idx * (chunk_ms / 1000.0)

        if start_at is None and level >= start_threshold:
            start_at = ts

        if level >= mix_threshold:
            mix_at = ts

        if level >= end_threshold:
            end_at = ts + (chunk_ms / 1000.0)

    cues: Dict[str, float] = {}
    if start_at is not None:
        cues["cue_in"] = max(0.0, start_at)
    if end_at is not None:
        cues["cue_out"] = min(duration_sec, end_at)
    if mix_at is not None:
        cues["start_next"] = min(duration_sec, mix_at)
    return cues


def auto_fill_missing_cues(path: str, cues: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """Fill missing start/end/mix cues using audio analysis thresholds."""

    detected = detect_audio_cues(path)
    merged = cues.copy()
    for key in ("cue_in", "cue_out", "start_next"):
        if merged.get(key) is None and key in detected:
            merged[key] = detected[key]
    return merged


def _hash_file(path: str) -> Optional[str]:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _ensure_analysis(path: str, tags: Dict) -> MusicAnalysis:
    existing = MusicAnalysis.query.filter_by(path=path).first()
    if existing:
        existing.updated_at = datetime.utcnow()
        base_title = os.path.splitext(os.path.basename(path))[0]
        existing.missing_tags = not (tags.get("artist") and tags.get("title") and tags.get("title") != base_title)
        if tags.get("bitrate"):
            existing.bitrate = tags["bitrate"]
        db.session.commit()
        return existing
    duration_seconds, peak_db, rms_db, peaks = _audio_stats(path)
    bitrate = tags.get("bitrate")
    file_hash = _hash_file(path)
    base_title = os.path.splitext(os.path.basename(path))[0]
    missing_tags = not (tags.get("artist") and tags.get("title") and tags.get("title") != base_title)
    analysis = MusicAnalysis(
        path=path,
        duration_seconds=duration_seconds,
        peak_db=peak_db,
        rms_db=rms_db,
        peaks=json.dumps(peaks) if peaks else None,
        bitrate=bitrate,
        hash=file_hash,
        missing_tags=missing_tags,
    )
    db.session.add(analysis)
    db.session.commit()
    return analysis


def _augment_with_analysis(tags: Dict) -> Dict:
    analysis = MusicAnalysis.query.filter_by(path=tags["path"]).first()
    if not analysis:
        analysis = _ensure_analysis(tags["path"], tags)
    payload = tags.copy()
    cover_path = os.path.splitext(tags["path"])[0] + ".jpg"
    payload.update({
        "duration_seconds": analysis.duration_seconds,
        "peak_db": analysis.peak_db,
        "rms_db": analysis.rms_db,
        "peaks": json.loads(analysis.peaks) if analysis.peaks else None,
        "bitrate": payload.get("bitrate") or analysis.bitrate,
        "hash": analysis.hash,
        "missing_tags": analysis.missing_tags,
        "cover_path": cover_path if os.path.exists(cover_path) else None,
        "cover_embedded": bool(tags.get("cover_embedded")),
    })
    return payload


def lookup_musicbrainz(
    title: Optional[str],
    artist: Optional[str],
    limit: int = 5,
    include_releases: bool = False,
) -> List[Dict]:
    """Query MusicBrainz for recordings (and optionally release variants) to suggest metadata including ISRC."""
    if not title and not artist:
        return []

    limit = max(1, min(limit or 5, 15))
    query_parts = []
    if artist:
        query_parts.append(f'artist:"{artist}"')
    if title:
        query_parts.append(f'recording:"{title}"')
    query = " AND ".join(query_parts) if query_parts else title or artist

    params = {
        "query": query,
        "fmt": "json",
        "limit": limit,
        "inc": "isrcs+releases" if not include_releases else "isrcs+releases+recordings+media",
    }

    ua = current_app.config.get("MUSICBRAINZ_USER_AGENT") or f"RAMS/1.0 ({current_app.config.get('STATION_NAME', 'RAMS')})"
    headers = {"User-Agent": ua}

    try:
        resp = requests.get("https://musicbrainz.org/ws/2/recording", params=params, headers=headers, timeout=8)
        resp.raise_for_status()
        payload = resp.json() or {}
    except Exception:
        return []

    results: List[Dict] = []
    for rec in payload.get("recordings", [])[:limit]:
        artist_credit = rec.get("artist-credit") or rec.get("artist_credit") or []
        artist_name = None
        if isinstance(artist_credit, list) and artist_credit:
            first = artist_credit[0]
            if isinstance(first, dict):
                artist_name = first.get("name") or first.get("artist", {}).get("name")

        release_title = None
        release_date = None
        release_format = None
        releases = rec.get("releases") or []
        if releases:
            release_title = releases[0].get("title")
            release_date = releases[0].get("date") or releases[0].get("first-release-date")
            media = releases[0].get("media") or releases[0].get("mediums") or []
            if media:
                release_format = media[0].get("format")

        year = None
        if release_date:
            year = str(release_date).split("-")[0]

        isrcs = rec.get("isrcs") or []
        isrc = isrcs[0] if isrcs else None

        entry = {
            "title": rec.get("title"),
            "artist": artist_name,
            "album": release_title,
            "year": year,
            "isrc": isrc,
            "musicbrainz_id": rec.get("id"),
            "format": release_format,
        }

        if include_releases:
            release_variants = []
            for rel in releases:
                medium_list = rel.get("media") or rel.get("mediums") or []
                fmt = medium_list[0].get("format") if medium_list else None
                tracks = []
                for medium in medium_list:
                    for track in medium.get("tracks", []) or []:
                        tracks.append(
                            {
                                "title": track.get("title"),
                                "length_ms": track.get("length"),
                                "position": track.get("position"),
                                "format": medium.get("format"),
                            }
                        )
                release_variants.append(
                    {
                        "id": rel.get("id"),
                        "title": rel.get("title"),
                        "date": rel.get("date") or rel.get("first-release-date"),
                        "country": rel.get("country"),
                        "format": fmt,
                        "track_count": rel.get("track-count") or rel.get("track_count"),
                        "tracks": tracks,
                    }
                )
            entry["releases"] = release_variants

        results.append(entry)

    return results


def search_music(
    query: Optional[str],
    page: int = 1,
    per_page: int = 50,
    folder: Optional[str] = None,
    genre: Optional[str] = None,
    year: Optional[str] = None,
    mood: Optional[str] = None,
    explicit: Optional[bool] = None,
) -> Dict:
    index = get_music_index()
    entries = list(index.get("files", {}).values())
    query_lower = (query or "").lower().strip()

    if folder:
        folder = folder.strip().replace("\\", "/").strip("/")
        entries = [e for e in entries if (e.get("folder") or "").startswith(folder)]

    if query_lower and query_lower not in {"%", "*"}:
        entries = [e for e in entries if query_lower in (e.get("search") or "")]

    def _norm(val: Optional[str]) -> str:
        return (val or "").strip().lower()

    base_entries = entries[:]
    genre_map = {(_norm(e.get("genre"))): e.get("genre") for e in base_entries if e.get("genre")}
    mood_map = {(_norm(e.get("mood"))): e.get("mood") for e in base_entries if e.get("mood")}
    genres = sorted(genre_map.values(), key=lambda v: _norm(v))
    moods = sorted(mood_map.values(), key=lambda v: _norm(v))
    years = sorted({str(e.get("year")) for e in base_entries if e.get("year")})

    if genre:
        genre_norm = _norm(genre)
        entries = [e for e in entries if _norm(e.get("genre")) == genre_norm]
    if mood:
        mood_norm = _norm(mood)
        entries = [e for e in entries if _norm(e.get("mood")) == mood_norm]
    if year:
        year_str = str(year).strip()
        entries = [e for e in entries if str(e.get("year") or "").strip() == year_str]
    if explicit is not None:
        entries = [e for e in entries if bool(e.get("explicit")) is explicit]

    entries.sort(
        key=lambda e: (
            (e.get("artist") or "").lower(),
            1 if _is_compilation(e.get("album_artist")) else 0,
            (e.get("album") or "").lower(),
            e.get("disc_num") or 0,
            e.get("track_num") or 0,
            (e.get("title") or "").lower(),
        )
    )

    total = len(entries)
    page = max(1, page)
    per_page = max(1, min(per_page, 100))
    start = (page - 1) * per_page
    end = start + per_page
    page_entries = entries[start:end]

    items: List[Dict] = []
    for entry in page_entries:
        path = entry["path"]
        tags = {
            "path": path,
            "title": entry.get("title"),
            "artist": entry.get("artist"),
            "album_artist": entry.get("album_artist"),
            "album": entry.get("album"),
            "composer": entry.get("composer"),
        }
        analysis = MusicAnalysis.query.filter_by(path=path).first()
        payload = tags.copy()
        payload.update({
            "duration_seconds": analysis.duration_seconds if analysis else None,
            "folder": entry.get("folder"),
            "genre": entry.get("genre"),
            "mood": entry.get("mood"),
            "year": entry.get("year"),
            "explicit": entry.get("explicit"),
            "track_num": entry.get("track_num"),
            "disc_num": entry.get("disc_num"),
        })
        items.append(payload)

    folders = sorted({e.get("folder") or "" for e in entries})
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "folders": folders,
        "genres": genres,
        "moods": moods,
        "years": years,
    }


def get_track(path: str) -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    tags = _read_tags(path)
    return _augment_with_analysis(tags)


def scan_library() -> List[Dict]:
    tracks = []
    for path in _walk_music():
        tags = _read_tags(path)
        tracks.append(_augment_with_analysis(tags))
    return tracks


def find_duplicates_and_quality(tracks: List[Dict]):
    by_hash = {}
    duplicates = []
    low_bitrate = []
    needs_metadata = []
    missing_art = []
    recently_added = []
    now = datetime.utcnow().timestamp()
    for t in tracks:
        h = t.get("hash")
        if h:
            if h in by_hash:
                duplicates.append([by_hash[h], t])
            else:
                by_hash[h] = t
        if t.get("bitrate") and t.get("bitrate") < 128000:
            low_bitrate.append(t)
        if t.get("missing_tags"):
            needs_metadata.append(t)
        cover_path = os.path.splitext(t["path"])[0] + ".jpg"
        if not os.path.exists(cover_path):
            missing_art.append(t)
        try:
            mtime = os.path.getmtime(t["path"])
            if now - mtime < 7 * 86400:
                recently_added.append(t)
        except Exception:
            pass
    return {
        "duplicates": duplicates,
        "low_bitrate": low_bitrate,
        "needs_metadata": needs_metadata,
        "missing_art": missing_art,
        "recently_added": recently_added,
    }


def _parse_track_tuple(val: Optional[str]) -> Optional[Tuple[int, int]]:
    if not val:
        return None
    try:
        if isinstance(val, (list, tuple)):
            if len(val) == 2 and all(isinstance(v, (int, float)) for v in val):
                return int(val[0]), int(val[1])
            if len(val) == 1:
                return int(val[0]), 0
        if isinstance(val, str) and "/" in val:
            parts = val.split("/")
            return int(parts[0]), int(parts[1] or 0)
        return int(val), 0
    except Exception:
        return None


def _parse_track_number(val: Optional[str]) -> Optional[int]:
    parsed = _parse_track_tuple(val)
    if parsed:
        return parsed[0]
    return None


def _is_compilation(album_artist: Optional[str]) -> bool:
    if not album_artist:
        return False
    lowered = album_artist.strip().lower()
    return lowered in {"various artists", "various", "va"}


def update_metadata(path: str, updates: Dict, cover_art_bytes: Optional[bytes] = None) -> Dict:
    """Update common tags with MP4-safe handling to avoid invalid-key errors."""
    if not mutagen:
        return {"status": "error", "message": "mutagen_required"}

    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in {".m4a", ".mp4", ".m4b"}:
            from mutagen.mp4 import MP4, MP4FreeForm  # type: ignore

            mp4 = MP4(path)
            atom_map = {
                "title": "©nam",
                "artist": "©ART",
                "album": "©alb",
                "composer": "©wrt",
                "isrc": "----:com.apple.iTunes:ISRC",
                "year": "©day",
                "genre": "©gen",
                "copyright": "cprt",
            }

            for field, atom in atom_map.items():
                val = updates.get(field)
                if val is None:
                    continue
                if not val:
                    mp4.pop(atom, None)
                    continue
                if atom.startswith("----"):
                    mp4[atom] = [MP4FreeForm(str(val).encode("utf-8"))]
                else:
                    mp4[atom] = [str(val)]

            if updates.get("track") is not None:
                parsed = _parse_track_tuple(updates.get("track"))
                if parsed:
                    mp4["trkn"] = [parsed]
                else:
                    mp4.pop("trkn", None)
            if updates.get("disc") is not None:
                parsed = _parse_track_tuple(updates.get("disc"))
                if parsed:
                    mp4["disk"] = [parsed]
                else:
                    mp4.pop("disk", None)
            if cover_art_bytes:
                try:
                    from mutagen.mp4 import MP4Cover  # type: ignore

                    mp4["covr"] = [MP4Cover(cover_art_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
                except Exception:
                    mp4["covr"] = [cover_art_bytes]
            mp4.save()
        else:
            audio = mutagen.File(path, easy=True)
            if not audio:
                return {"status": "error", "message": "unsupported"}
            key_map = {
                "year": "date",
                "track": "tracknumber",
                "disc": "discnumber",
            }
            for field in ["title", "artist", "album", "composer", "isrc", "year", "genre", "track", "disc", "copyright"]:
                tag_key = key_map.get(field, field)
                if updates.get(field) is None:
                    continue
                val = updates.get(field)
                if val:
                    audio[tag_key] = [val]
                elif tag_key in audio:
                    del audio[tag_key]
            audio.save()
            if cover_art_bytes and APIC and path.lower().endswith(".mp3"):
                id3 = ID3(path)
                id3.add(APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,
                    desc=u"Cover",
                    data=cover_art_bytes,
                ))
                id3.save()

        tags = _read_tags(path)
        _ensure_analysis(path, tags)
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)}


def bulk_update_metadata(paths: List[str], updates: Dict, cover_art_bytes: Optional[bytes] = None) -> Dict:
    results = []
    if not mutagen:
        return {"status": "error", "message": "mutagen_required"}
    for path in paths:
        outcome = {"path": path}
        result = update_metadata(path, updates, cover_art_bytes)
        outcome.update(result)
        results.append(outcome)
    db.session.commit()
    return {"status": "ok", "results": results}


def harvest_cover_art(path: str, tags: Optional[Dict] = None) -> Dict:
    tags = tags or _read_tags(path)
    title = tags.get("title") or os.path.splitext(os.path.basename(path))[0]
    artist = tags.get("artist") or ""
    query = f"{title} {artist}".strip()
    if not query:
        return {"status": "error", "message": "no_query"}

    try:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "media": "music", "limit": 1},
            timeout=8,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        results = payload.get("results") or []
        if not results:
            return {"status": "error", "message": "no_match"}
        art_url = results[0].get("artworkUrl100") or results[0].get("artworkUrl60")
        if not art_url:
            return {"status": "error", "message": "no_art"}
        # prefer higher res
        art_url = art_url.replace("100x100", "600x600")
        img = requests.get(art_url, timeout=8)
        img.raise_for_status()
        art_bytes = img.content
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)}

    dest = os.path.splitext(path)[0] + ".jpg"
    try:
        with open(dest, "wb") as f:
            f.write(art_bytes)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)}

    if mutagen and APIC and path.lower().endswith(".mp3"):
        try:
            id3 = ID3(path)
            id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc=u"Cover", data=art_bytes))
            id3.save()
        except Exception:
            pass

    return {"status": "ok", "art_path": dest}


def cover_art_candidates(path: str, tags: Optional[Dict] = None, limit: int = 6) -> Dict:
    """Return multiple cover-art options (high-res first) from public sources."""
    tags = tags or _read_tags(path)
    title = tags.get("title") or os.path.splitext(os.path.basename(path))[0]
    artist = tags.get("artist") or ""
    query = f"{title} {artist}".strip()
    if not query:
        return {"status": "error", "message": "no_query"}

    options: List[Dict] = []
    # iTunes search results
    try:
        resp = requests.get(
            "https://itunes.apple.com/search",
            params={"term": query, "media": "music", "limit": limit},
            timeout=8,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        for item in payload.get("results", [])[:limit]:
            art_url = item.get("artworkUrl100") or item.get("artworkUrl60")
            if not art_url:
                continue
            # upgrade to 1200 where available
            art_url = art_url.replace("100x100", "1200x1200")
            options.append(
                {
                    "source": "itunes",
                    "label": item.get("collectionName") or item.get("trackName") or "iTunes",
                    "url": art_url,
                    "resolution": "1200x1200",
                }
            )
    except Exception:
        pass

    # CoverArtArchive if we have a MusicBrainz release id
    mbid = tags.get("musicbrainz_albumid") or tags.get("musicbrainz_releasegroupid")
    if mbid:
        try:
            resp = requests.get(f"https://coverartarchive.org/release/{mbid}", timeout=8)
            if resp.status_code == 404:
                resp = requests.get(f"https://coverartarchive.org/release-group/{mbid}", timeout=8)
            resp.raise_for_status()
            payload = resp.json() or {}
            images = payload.get("images") or []
            for img in images:
                if not img.get("image"):
                    continue
                options.append(
                    {
                        "source": "coverartarchive",
                        "label": img.get("comment") or img.get("types", ["Front"])[0],
                        "url": img.get("image"),
                        "resolution": img.get("thumbnails", {}).get("large") and "large" or "full",
                    }
                )
        except Exception:
            pass

    if not options:
        return {"status": "error", "message": "no_candidates"}
    return {"status": "ok", "options": options}


def enrich_metadata_external(tags: Dict) -> Dict:
    """Fetch genre/mood/BPM/key suggestions from open services (AudioDB/AcoustID optional)."""
    artist = tags.get("artist") or ""
    title = tags.get("title") or ""
    suggestions: Dict[str, Optional[str]] = {}

    # AudioDB track lookup (open, no key required for basic fields)
    if artist and title:
        try:
            resp = requests.get(
                "https://theaudiodb.com/api/v1/json/2/searchtrack.php",
                params={"s": artist, "t": title},
                timeout=8,
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            tracks = payload.get("track") or []
            if tracks:
                t = tracks[0]
                suggestions["genre"] = t.get("strGenre")
                suggestions["mood"] = t.get("strMood")
                suggestions["bpm"] = t.get("intTempo")
                suggestions["key"] = t.get("strMusicVidDirector") or t.get("strStyle")
        except Exception:
            pass

    # Placeholder for AcoustID / other services; only used if key configured
    acoustid_key = current_app.config.get("ACOUSTID_API_KEY")
    if acoustid_key and tags.get("path"):
        # For simplicity, just flag that enrichment is available; full fingerprinting is heavier.
        suggestions.setdefault("note", "AcoustID enrichment available when fingerprinting is enabled")

    if not suggestions:
        return {"status": "error", "message": "no_suggestions"}
    return {"status": "ok", "suggestions": suggestions}


def queues_snapshot():
    tracks = scan_library()
    metrics = find_duplicates_and_quality(tracks)
    return {"tracks": tracks, "metrics": metrics}


def load_cue(path: str) -> Optional[MusicCue]:
    return MusicCue.query.filter_by(path=path).first()


def save_cue(path: str, payload: Dict) -> MusicCue:
    cue = load_cue(path) or MusicCue(path=path)
    cue.cue_in = payload.get("cue_in")
    cue.intro = payload.get("intro")
    cue.outro = payload.get("outro")
    cue.cue_out = payload.get("cue_out")
    cue.loop_in = payload.get("loop_in")
    cue.loop_out = payload.get("loop_out")
    cue.hook_in = payload.get("hook_in")
    cue.hook_out = payload.get("hook_out")
    cue.start_next = payload.get("start_next")
    cue.fade_in = payload.get("fade_in")
    cue.fade_out = payload.get("fade_out")
    db.session.add(cue)
    db.session.commit()
    _write_radiodj_cue_tag(path, payload)
    return cue


def _write_radiodj_cue_tag(path: str, payload: Dict) -> None:
    """Persist cue points into the "MusicID PUID" tag RadioDJ expects.

    RadioDJ stores cue data as an ampersand-delimited string inside the
    "MusicID PUID" tag (for MP3 ID3 this is a TXXX frame; for MP4/M4A this is
    a freeform atom; for other containers we try a generic tag). Example:

    &sta=0.19&int=16.37&pin=0.42&pou=8.76&hin=39.78&hou=54.66&out=218.18&xta=220.07&end=221.30&fin=0&fou=1.22
    """

    if not mutagen:
        return

    mapping = [
        ("sta", "cue_in"),
        ("int", "intro"),
        ("pin", "loop_in"),
        ("pou", "loop_out"),
        ("hin", "hook_in"),
        ("hou", "hook_out"),
        ("out", "outro"),
        ("xta", "start_next"),
        ("end", "cue_out"),
        ("fin", "fade_in"),
        ("fou", "fade_out"),
    ]

    parts = []
    for key, field in mapping:
        val = payload.get(field)
        if val is None:
            continue
        try:
            val_num = float(val)
        except Exception:
            continue
        parts.append(f"{key}={val_num}")

    tag_value = "&" + "&".join(parts) if parts else ""

    try:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".mp3":
            try:
                from mutagen.id3 import ID3, TXXX  # type: ignore
            except Exception:
                return
            id3 = ID3(path)
            # remove existing MusicID PUID frames
            for frame_id in list(id3.keys()):
                if frame_id.startswith("TXXX"):
                    frame = id3[frame_id]
                    if getattr(frame, "desc", "") == "MusicID PUID":
                        del id3[frame_id]
            if tag_value:
                id3.add(TXXX(encoding=3, desc="MusicID PUID", text=[tag_value]))
            id3.save()
            return

        if ext in {".m4a", ".mp4", ".m4b"}:
            try:
                from mutagen.mp4 import MP4, MP4FreeForm  # type: ignore
            except Exception:
                return
            mp4 = MP4(path)
            key = "----:com.apple.iTunes:MusicID PUID"
            if tag_value:
                mp4[key] = [MP4FreeForm(tag_value.encode("utf-8"))]
            elif key in mp4:
                mp4.pop(key, None)
            mp4.save()
            return

        # Fallback for formats that support simple key/value tagging
        audio = mutagen.File(path, easy=False)
        if not audio:
            return
        if tag_value:
            audio["MusicID PUID"] = tag_value
        elif "MusicID PUID" in audio:
            del audio["MusicID PUID"]
        audio.save()
    except Exception:
        # Silently ignore tagging errors so cue saving still succeeds in RAMS
        return
