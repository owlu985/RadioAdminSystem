import os
import json
import hashlib
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from flask import current_app
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
                    data[target] = _first_text(val)

        if path.lower().endswith((".m4a", ".mp4")):
            try:
                from mutagen.mp4 import EasyMP4  # type: ignore

                easy_mp4 = EasyMP4(path)
                if hasattr(easy_mp4, "info") and getattr(easy_mp4.info, "bitrate", None) and not data.get("bitrate"):
                    data["bitrate"] = getattr(easy_mp4.info, "bitrate")
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

                atom_map = {
                    "title": ["©nam", "----:com.apple.iTunes:TITLE"],
                    "artist": ["©ART", "----:com.apple.iTunes:ARTIST"],
                    "album": ["©alb", "----:com.apple.iTunes:ALBUM"],
                    "composer": ["©wrt", "----:com.apple.iTunes:COMPOSER"],
                    "isrc": ["----:com.apple.iTunes:ISRC", "----:com.apple.iTunes:isrc"],
                    "year": ["©day", "----:com.apple.iTunes:YEAR", "----:com.apple.iTunes:DATE"],
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

                if hasattr(mp4, "info") and getattr(mp4.info, "bitrate", None) and not data.get("bitrate"):
                    data["bitrate"] = getattr(mp4.info, "bitrate")
            except Exception:
                pass

        if not all([data.get("title"), data.get("artist"), data.get("album")]):
            try:
                raw_audio = mutagen.File(path, easy=False)
                if raw_audio and getattr(raw_audio, "tags", None):
                    for key, val in raw_audio.tags.items():
                        text_val = _first_text(val)
                        if not text_val:
                            continue
                        lkey = str(key).lower()
                        if not data.get("title") and any(hint in lkey for hint in ["©nam", "title", "name"]):
                            data["title"] = text_val
                        if not data.get("artist") and any(hint in lkey for hint in ["©art", "artist", "aart"]):
                            data["artist"] = text_val
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
            except Exception:
                pass

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
    })
    return payload


def lookup_musicbrainz(title: Optional[str], artist: Optional[str], limit: int = 5) -> List[Dict]:
    """Query the MusicBrainz recordings API to suggest metadata including ISRC."""
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
        "inc": "isrcs+releases",
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
        releases = rec.get("releases") or []
        if releases:
            release_title = releases[0].get("title")
            release_date = releases[0].get("date") or releases[0].get("first-release-date")

        year = None
        if release_date:
            year = str(release_date).split("-")[0]

        isrcs = rec.get("isrcs") or []
        isrc = isrcs[0] if isrcs else None

        results.append(
            {
                "title": rec.get("title"),
                "artist": artist_name,
                "album": release_title,
                "year": year,
                "isrc": isrc,
                "musicbrainz_id": rec.get("id"),
            }
        )

    return results


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
