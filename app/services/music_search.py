import os
import json
import hashlib
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from flask import current_app

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


def _walk_music():
    root = current_app.config.get("NAS_MUSIC_ROOT")
    if not root or not os.path.exists(root):
        return []
    for base, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(AUDIO_EXTS):
                yield os.path.join(base, f)


def _read_tags(path: str) -> Dict:
    base_title = os.path.splitext(os.path.basename(path))[0]
    data = {
        "path": path,
        "title": None,
        "artist": None,
        "album": None,
        "composer": None,
        "isrc": None,
        "year": None,
        "track": None,
        "disc": None,
        "copyright": None,
        "bitrate": None,
    }
    if not mutagen:
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

        # Handle MP4FreeForm, tuples (track/disc), and bytes gracefully
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

    try:
        audio_easy = mutagen.File(path, easy=True)
        if audio_easy:
            if hasattr(audio_easy, "info") and getattr(audio_easy.info, "bitrate", None):
                data["bitrate"] = getattr(audio_easy.info, "bitrate")
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
                val = audio_easy.tags.get(key) if audio_easy.tags else None
                if val:
                    data[target] = _coerce(val)

        # Always attempt MP4 atom parsing for M4A/MP4 to capture richer tags
        if path.lower().endswith((".m4a", ".mp4")):
            # First try the friendly EasyMP4 mapper; fall back to raw atoms if needed
            try:
                from mutagen.mp4 import EasyMP4  # type: ignore

                easy_mp4 = EasyMP4(path)
                if easy_mp4 and easy_mp4.tags:
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
                        val = easy_mp4.tags.get(key)
                        if val and not data.get(target):
                            coerced = _coerce(val)
                            if coerced:
                                data[target] = coerced
            except Exception:
                pass

            try:
                from mutagen.mp4 import MP4  # type: ignore

                mp4 = MP4(path)
                mp4_tags = mp4.tags or {}

                # Support multiple atom spellings (including custom iTunes freeform atoms)
                atom_map = {
                    "title": ["\xa9nam", "----:com.apple.iTunes:TITLE"],
                    "artist": ["\xa9ART", "----:com.apple.iTunes:ARTIST"],
                    "album": ["\xa9alb", "----:com.apple.iTunes:ALBUM"],
                    "composer": ["\xa9wrt", "----:com.apple.iTunes:COMPOSER"],
                    "isrc": ["----:com.apple.iTunes:ISRC", "----:com.apple.iTunes:isrc"],
                    "year": ["\xa9day", "----:com.apple.iTunes:YEAR", "----:com.apple.iTunes:DATE"],
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
                            coerced = _coerce(val)
                            if coerced:
                                data[target] = coerced
                                break

                # If artist/title still missing, try any MP4FreeForm text-like atoms as a last resort
                if not data.get("artist") or not data.get("title"):
                    for key, val in mp4_tags.items():
                        if not isinstance(key, str):
                            continue
                        lowered = key.lower()
                        if "artist" in lowered and not data.get("artist"):
                            coerced = _coerce(val)
                            if coerced:
                                data["artist"] = coerced
                        if "title" in lowered and not data.get("title"):
                            coerced = _coerce(val)
                            if coerced:
                                data["title"] = coerced

                # As an ultimate fallback, scan all remaining tag values for string-like content
                if not data.get("artist") or not data.get("title") or not data.get("album"):
                    for key, val in mp4_tags.items():
                        coerced = _coerce(val)
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

                if hasattr(mp4, "info") and getattr(mp4.info, "bitrate", None) and not data.get("bitrate"):
                    data["bitrate"] = getattr(mp4.info, "bitrate")
            except Exception:
                pass
        # Final fallback: if the title is missing or literally the string "None",
        # use the filename (prevents blank titles on stubborn files).
        if not data.get("title") or str(data.get("title")).strip().lower() == "none":
            data["title"] = base_title
        return data
    except Exception:
        if not data.get("title") or str(data.get("title")).strip().lower() == "none":
            data["title"] = base_title
        return data


def _audio_stats(path: str) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[List[float]]]:
    if not AudioSegment:
        return None, None, None, None
    try:
        audio = AudioSegment.from_file(path)
        duration_seconds = len(audio) / 1000.0
        rms_db = audio.rms if hasattr(audio, "rms") else None
        peak_db = audio.max_dBFS if hasattr(audio, "max_dBFS") else None
        samples = audio.get_array_of_samples()
        if not samples:
            return duration_seconds, peak_db, rms_db, None
        # downsample to ~120 points for waveform preview
        step = max(1, int(len(samples) / 120))
        peaks = []
        max_val = max(abs(int(s)) for s in samples) or 1
        for i in range(0, len(samples), step):
            window = samples[i:i + step]
            if not window:
                continue
            local_peak = max(abs(int(s)) for s in window) / max_val
            peaks.append(round(local_peak, 4))
        return duration_seconds, peak_db, rms_db, peaks
    except Exception:
        return None, None, None, None


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
    payload.update({
        "duration_seconds": analysis.duration_seconds,
        "peak_db": analysis.peak_db,
        "rms_db": analysis.rms_db,
        "peaks": json.loads(analysis.peaks) if analysis.peaks else None,
        "bitrate": payload.get("bitrate") or analysis.bitrate,
        "hash": analysis.hash,
        "missing_tags": analysis.missing_tags,
    })
    return payload


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
            results.append(_augment_with_analysis(tags))
    return results


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


def bulk_update_metadata(paths: List[str], updates: Dict, cover_art_bytes: Optional[bytes] = None) -> Dict:
    results = []
    if not mutagen:
        return {"status": "error", "message": "mutagen_required"}
    for path in paths:
        outcome = {"path": path, "status": "updated"}
        try:
            audio = mutagen.File(path, easy=True)
            if not audio:
                outcome["status"] = "unsupported"
                results.append(outcome)
                continue
            for field in ["title", "artist", "album", "composer", "isrc", "year", "track", "disc", "copyright"]:
                val = updates.get(field)
                if val is None:
                    continue
                if val == "":
                    if field in audio:
                        del audio[field]
                else:
                    audio[field] = [val]
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
        except Exception as exc:  # noqa: BLE001
            outcome["status"] = f"error: {exc}"
        results.append(outcome)
    db.session.commit()
    return {"status": "ok", "results": results}


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
    cue.hook_in = payload.get("hook_in")
    cue.hook_out = payload.get("hook_out")
    cue.start_next = payload.get("start_next")
    cue.fade_in = payload.get("fade_in")
    cue.fade_out = payload.get("fade_out")
    db.session.add(cue)
    db.session.commit()
    return cue
