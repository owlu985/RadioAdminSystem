from __future__ import annotations

from datetime import datetime
import time
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, current_app, request, session, url_for, render_template, abort, send_file
import os
import shutil
import json
import base64
import io
from typing import Optional, TypedDict
from urllib.parse import urlparse, quote
from app.models import (
    ShowRun,
    StreamProbe,
    LogEntry,
    DJ,
    Show,
    SavedSearch,
    Plugin,
    WebsiteContent,
    WebsiteArticle,
    PressFeature,
    WebsiteBanner,
    PodcastEpisode,
    DJAbsence,
    HostedAudio,
    MarathonEvent,
    ArchivistRipResult,
    NowPlayingState,
    db,
)
from app.utils import (
    get_current_show,
    format_show_window,
    show_display_title,
    show_primary_host,
    show_host_names,
    active_absence_for_show,
    next_show_occurrence,
)
from app.services.show_run_service import get_or_create_active_run
from app.services.radiodj_client import import_news_or_calendar, RadioDJClient
from app.services.detection import probe_stream
from app.services import api_cache
from app.services.stream_monitor import fetch_icecast_listeners, recent_icecast_stats
from app.services.music_search import (
    auto_fill_missing_cues,
    search_music,
    get_music_index,
    get_track,
    bulk_update_metadata,
    queues_snapshot,
    load_cue,
    save_cue,
    scan_library,
    find_duplicates_and_quality,
    lookup_musicbrainz,
    harvest_cover_art,
    cover_art_candidates,
    enrich_metadata_external,
)
from app.services.media_library import list_media, load_media_meta, save_media_meta, _media_roots
from app.services.archivist_db import (
    lookup_album,
    analyze_album_rip,
    save_album_rip_upload,
    delete_album_rip_upload,
    cleanup_album_tmp,
)
from sqlalchemy import func
from app.logger import init_logger
from app.services.audit import start_audit_job, get_audit_status, list_audit_runs, get_audit_run
import requests
api_bp = Blueprint("api", __name__, url_prefix="/api")
logger = init_logger()
QUEUE_ITEM_TYPES = {"music", "psa", "imaging", "voicetrack"}
_RADIODJ_NOWPLAYING_CACHE: dict[str, float | dict | None] = {
    "fetched_at": None,
    "payload": None,
}


def _deserialize_metadata(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def _serialize_queue_item(item: PlaybackQueueItem) -> dict:
    return {
        "id": item.id,
        "session_id": item.session_id,
        "position": item.position,
        "type": item.item_type,
        "title": item.title,
        "artist": item.artist,
        "duration": item.duration,
        "metadata": _deserialize_metadata(item.metadata),
        "created_at": item.created_at.isoformat(),
    }


def _serialize_now_playing(state: NowPlayingState | None) -> dict | None:
    if not state:
        return None
    return {
        "session_id": state.session_id,
        "queue_item_id": state.queue_item_id,
        "type": state.item_type,
        "title": state.title,
        "artist": state.artist,
        "duration": state.duration,
        "metadata": _deserialize_metadata(state.metadata),
        "status": state.status,
        "started_at": state.started_at.isoformat() if state.started_at else None,
        "cue_in": state.cue_in,
        "cue_out": state.cue_out,
        "fade_out": state.fade_out,
        "updated_at": state.updated_at.isoformat(),
    }


def _get_playback_session() -> PlaybackSession:
    requested = request.headers.get("X-Playback-Session") or request.args.get("session_id")
    stored = session.get("playback_session_id")
    session_id = requested or stored
    playback = None
    if session_id:
        try:
            playback = db.session.get(PlaybackSession, int(session_id))
        except (TypeError, ValueError):
            playback = None
    if requested and not playback:
        abort(404, description="playback_session_not_found")
    if not playback:
        playback = PlaybackSession()
        db.session.add(playback)
        db.session.commit()
        session["playback_session_id"] = playback.id
    return playback


def _touch_playback_session(playback: PlaybackSession) -> None:
    playback.updated_at = datetime.utcnow()
    db.session.add(playback)


def _queue_items(session_id: int) -> list[PlaybackQueueItem]:
    return PlaybackQueueItem.query.filter_by(session_id=session_id).order_by(PlaybackQueueItem.position).all()


def _resequence_queue(items: list[PlaybackQueueItem]) -> None:
    for idx, item in enumerate(items):
        item.position = idx
        db.session.add(item)


def _now_playing_for(session_id: int) -> NowPlayingState:
    state = NowPlayingState.query.filter_by(session_id=session_id).first()
    if not state:
        state = NowPlayingState(session_id=session_id, status="idle", updated_at=datetime.utcnow())
        db.session.add(state)
        db.session.flush()
    return state


def _set_now_playing_from_item(state: NowPlayingState, item: PlaybackQueueItem | None, status: str) -> None:
    state.queue_item_id = item.id if item else None
    state.item_type = item.item_type if item else None
    state.title = item.title if item else None
    state.artist = item.artist if item else None
    state.duration = item.duration if item else None
    state.metadata = item.metadata if item else None
    state.status = status
    state.started_at = datetime.utcnow() if item else None
    state.updated_at = datetime.utcnow()
    db.session.add(state)


class PlaybackQueueItem(TypedDict, total=False):
    name: str
    artist: str
    album: str
    duration: float
    source: str


class PlaybackQueueItem(TypedDict, total=False):
    name: str
    artist: str
    album: str
    duration: float
    source: str


def _serialize_show_run(run: ShowRun) -> dict:
    return {
        "id": run.id,
        "show_name": run.show_name,
        "dj": f"{run.dj_first_name} {run.dj_last_name}",
        "start_time": run.start_time.isoformat(),
        "end_time": run.end_time.isoformat() if run.end_time else None,
        "classification": run.classification,
        "classification_reason": run.classification_reason,
        "avg_db": run.avg_db,
        "silence_ratio": run.silence_ratio,
        "automation_ratio": run.automation_ratio,
        "flagged_missed": run.flagged_missed,
    }


def _psa_root() -> str:
    root = current_app.config.get("PSA_LIBRARY_PATH") or os.path.join(current_app.instance_path, "psa")
    os.makedirs(root, exist_ok=True)
    return root


def _media_roots() -> list[str]:
    roots = []
    psa_root = current_app.config.get("PSA_LIBRARY_PATH") or os.path.join(current_app.instance_path, "psa")
    if psa_root:
        roots.append(psa_root)
    music_root = current_app.config.get("NAS_MUSIC_ROOT")
    if music_root:
        roots.append(music_root)
    assets_root = current_app.config.get("MEDIA_ASSETS_ROOT")
    if assets_root:
        roots.append(assets_root)
    voice_root = current_app.config.get("VOICE_TRACKS_ROOT") or os.path.join(current_app.instance_path, "voice_tracks")
    roots.append(voice_root)
    return roots


def _decode_media_token(token: str) -> Optional[str]:
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return None
    full = os.path.normcase(os.path.abspath(os.path.normpath(decoded)))
    allowed = False
    for root in _media_roots():
        root_abs = os.path.normcase(os.path.abspath(os.path.normpath(root)))
        if full.startswith(root_abs):
            allowed = True
            break
    if not allowed or not os.path.isfile(full):
        return None
    return full


def _safe_music_path(path: str) -> Optional[str]:
    if not path:
        return None
    full = os.path.normcase(os.path.abspath(os.path.normpath(path)))
    music_root = current_app.config.get("NAS_MUSIC_ROOT")
    if not music_root:
        return None
    root_abs = os.path.normcase(os.path.abspath(os.path.normpath(music_root)))
    if not full.startswith(root_abs):
        return None
    return full if os.path.exists(full) else None


def _icecast_update_url() -> Optional[str]:
    status_url = current_app.config.get("ICECAST_STATUS_URL")
    list_url = current_app.config.get("ICECAST_LISTCLIENTS_URL")
    candidate = status_url or list_url
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if not parsed.scheme or not parsed.netloc:
        return None
    return parsed._replace(path="/admin/metadata", query="", fragment="").geturl()


def _push_icecast_metadata(track: dict) -> None:
    update_url = _icecast_update_url()
    mount = current_app.config.get("ICECAST_MOUNT")
    if not update_url or not mount:
        return
    artist = _strip_p_tag(track.get("artist") or track.get("Artist"))
    title = _strip_p_tag(track.get("title") or track.get("Title"))
    if not title and not artist:
        return
    song = " - ".join([part for part in [artist, title] if part])
    params = {"mount": mount, "mode": "updinfo", "song": song}
    username = current_app.config.get("ICECAST_USERNAME") or "admin"
    password = current_app.config.get("ICECAST_PASSWORD")
    try:
        resp = requests.get(update_url, params=params, auth=(username, password) if password else None, timeout=5)
        if resp.ok:
            logger.info("Icecast metadata update ok: %s", song)
        else:
            logger.warning("Icecast metadata update failed: %s (status %s)", song, resp.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Icecast metadata update failed: %s", exc)


def _strip_p_tag(value: Optional[str]) -> Optional[str]:
    if not value:
        return value
    text = str(value).strip()
    if text.endswith(" (P)"):
        return text[:-4].rstrip()
    return text


def _get_cached_radiodj_nowplaying() -> Optional[dict]:
    now_ts = time.time()
    last_fetch = _RADIODJ_NOWPLAYING_CACHE.get("fetched_at")
    if isinstance(last_fetch, (int, float)) and now_ts - last_fetch < 8:
        return _RADIODJ_NOWPLAYING_CACHE.get("payload")  # type: ignore[return-value]
    payload: Optional[dict] = None
    rdj = RadioDJClient()
    if rdj.enabled:
        payload = rdj.now_playing()
    _RADIODJ_NOWPLAYING_CACHE["fetched_at"] = now_ts
    if payload:
        track = _extract_radiodj_track(payload)
        if track:
            _RADIODJ_NOWPLAYING_CACHE["payload"] = track
            _push_icecast_metadata(track)
    return _RADIODJ_NOWPLAYING_CACHE.get("payload")  # type: ignore[return-value]


def _extract_radiodj_track(payload: dict) -> Optional[dict]:
    if not payload:
        return None
    track_payload = payload.get("CurrentTrack") if isinstance(payload.get("CurrentTrack"), dict) else payload
    raw_track_type = track_payload.get("TrackType") or track_payload.get("tracktype") or ""
    track_type_label = None
    is_music = True
    if isinstance(raw_track_type, (int, float)):
        track_type_label = "music" if int(raw_track_type) == 0 else str(raw_track_type)
        is_music = int(raw_track_type) == 0
    else:
        track_type = str(raw_track_type).strip().lower()
        track_type_label = track_type or None
        if track_type.isdigit():
            is_music = int(track_type) == 0
        elif track_type:
            is_music = track_type == "music"
    artist = _strip_p_tag(track_payload.get("Artist") or track_payload.get("artist"))
    title = _strip_p_tag(track_payload.get("Title") or track_payload.get("title"))
    album = _strip_p_tag(track_payload.get("Album") or track_payload.get("album"))
    started_at = track_payload.get("DatePlayed") or track_payload.get("dateplayed")
    return {
        "artist": artist,
        "album": album,
        "title": title,
        "started_at": started_at,
        "track_type": track_type_label,
        "is_music": is_music,
    }


def _get_now_playing_state() -> NowPlayingState:
    state = NowPlayingState.query.first()
    if not state:
        state = NowPlayingState()
        db.session.add(state)
        db.session.commit()
    return state


def _override_enabled() -> bool:
    if current_app.config.get("NOW_PLAYING_OVERRIDE_ENABLED"):
        return True
    state = NowPlayingState.query.first()
    return bool(state.override_enabled) if state else False


def _serialize_show(show: Show, absence: DJAbsence | None = None, window: dict | None = None) -> dict:
    host_label = show_host_names(show)
    if absence and absence.replacement_name:
        host_label = f"{absence.replacement_name} (cover)"
    return {
        "name": show_display_title(show),
        "host": host_label,
        "genre": show.genre,
        "description": show.description,
        "regular_host": show.is_regular_host,
        "window": window or format_show_window(show),
    }


def _find_next_show(now: datetime) -> tuple[Show | None, tuple[datetime, datetime] | None, DJAbsence | None]:
    soonest: tuple[datetime, datetime, Show] | None = None
    for show in Show.query.all():
        occ = next_show_occurrence(show, now=now)
        if not occ:
            continue
        start_dt, end_dt = occ
        if start_dt <= now:
            continue
        if not soonest or start_dt < soonest[0]:
            soonest = (start_dt, end_dt, show)

    if not soonest:
        return None, None, None

    start_dt, end_dt, show_obj = soonest
    absence = DJAbsence.query.filter(
        DJAbsence.show_id == show_obj.id,
        DJAbsence.start_time <= start_dt,
        DJAbsence.end_time >= start_dt,
        DJAbsence.status.in_(["approved", "pending"]),
    ).first()
    return show_obj, (start_dt, end_dt), absence


@api_bp.route("/now", strict_slashes=False)
def now_playing():
    now = datetime.utcnow()
    show = get_current_show()
    absence = active_absence_for_show(show, now=now) if show else None
    override_enabled = _override_enabled()

    if not show:
        base = {
            "status": "off_air",
            "message": current_app.config.get("DEFAULT_OFF_AIR_MESSAGE"),
            "override_enabled": override_enabled,
        }
        if override_enabled:
            track = _get_cached_radiodj_nowplaying()
            if track:
                base.update({"status": "automation", "source": "radiodj_cached", "track": track})
        next_show, window, next_absence = _find_next_show(now)
        if next_show and window:
            start_dt, end_dt = window
            base["next_show"] = {
                "name": show_display_title(next_show),
                "host": show_host_names(next_show) if not next_absence or not next_absence.replacement_name else f"{next_absence.replacement_name} (cover)",
                "window": {
                    "start_time": start_dt.strftime("%H:%M"),
                    "end_time": end_dt.strftime("%H:%M"),
                },
                "absence": {
                    "status": next_absence.status,
                    "replacement": next_absence.replacement_name,
                } if next_absence else None,
            }
        return jsonify(base)

    dj_first, dj_last = show_primary_host(show)
    if absence and absence.replacement_name:
        parts = absence.replacement_name.split(" ", 1)
        if parts:
            dj_first, dj_last = parts[0], parts[1] if len(parts) > 1 else ""

    run = get_or_create_active_run(
        show_name=show_display_title(show),
        dj_first_name=dj_first,
        dj_last_name=dj_last,
    )

    next_show, window, next_absence = _find_next_show(now)

    payload = {
        "status": "on_air",
        "show": _serialize_show(show, absence=absence),
        "run": _serialize_show_run(run),
        "override_enabled": override_enabled,
        "absence": {
            "status": absence.status,
            "replacement": absence.replacement_name,
            "dj": absence.dj_name,
        } if absence else None,
    }
    if next_show and window:
        start_dt, end_dt = window
        payload["next_show"] = {
            "name": show_display_title(next_show),
            "host": show_host_names(next_show) if not next_absence or not next_absence.replacement_name else f"{next_absence.replacement_name} (cover)",
            "window": {
                "start_time": start_dt.strftime("%H:%M"),
                "end_time": end_dt.strftime("%H:%M"),
            },
            "absence": {
                "status": next_absence.status,
                "replacement": next_absence.replacement_name,
            } if next_absence else None,
        }

    return jsonify(payload)


@api_bp.route("/now/widget", strict_slashes=False)
def now_widget():
    """
    Widget-friendly now-playing: if a scheduled show is active, show it; otherwise
    return RadioDJ automation metadata when available.
    """
    base = now_playing().get_json()  # type: ignore
    if base and base.get("status") != "off_air":
        return jsonify(base)
    nowplaying_payload = _get_cached_radiodj_nowplaying()
    if nowplaying_payload:
        base = base or {}
        base.update({"status": "automation", "source": "radiodj_cached", "track": nowplaying_payload})
        return jsonify(base)
    return jsonify(base or {"status": "off_air"})


@api_bp.route("/now/recent-tracks")
def recent_tracks():
    entries = (
        LogEntry.query.filter_by(entry_type="music")
        .order_by(LogEntry.timestamp.desc())
        .limit(10)
        .all()
    )
    index = get_music_index()
    index_entries = list(index.get("files", {}).values())
    lookup: dict[tuple[str, str], dict] = {}
    for entry in index_entries:
        title = (entry.get("title") or "").strip().lower()
        artist = (entry.get("artist") or "").strip().lower()
        if title and artist and (title, artist) not in lookup:
            lookup[(title, artist)] = entry
    payload = []
    for entry in entries:
        title = entry.title or ""
        artist = entry.artist or ""
        key = (title.strip().lower(), artist.strip().lower())
        match = lookup.get(key)
        album = match.get("album") if match else None
        path = match.get("path") if match else None
        cover_url = None
        if path:
            cover_url = f"/api/music/cover-image?path={quote(path)}"
        payload.append({
            "title": title or None,
            "artist": artist or None,
            "album": album,
            "cover_url": cover_url,
            "played_at": entry.timestamp.isoformat(),
        })
    return jsonify({"status": "ok", "tracks": payload})


@api_bp.route("/now/override", methods=["GET", "POST"])
def now_override():
    state = _get_now_playing_state()
    if request.method == "POST":
        payload = request.get_json(force=True, silent=True) or {}
        enabled = payload.get("enabled")
        if enabled is None:
            return jsonify({"status": "error", "message": "enabled required"}), 400
        enabled_flag = str(enabled).lower() in {"1", "true", "yes", "on"}
        state.override_enabled = enabled_flag
        state.updated_at = datetime.utcnow()
        db.session.commit()
    return jsonify({"status": "ok", "override_enabled": state.override_enabled})


@api_bp.route("/probe", methods=["POST"])
def probe_now():
    """Trigger an on-demand probe of the stream and return the result."""
    result = probe_stream(current_app.config["STREAM_URL"])
    if result is None:
        return jsonify({"status": "error", "message": "probe_failed"}), 500
    return jsonify({
        "avg_db": result.avg_db,
        "silence_ratio": result.silence_ratio,
        "automation_ratio": result.automation_ratio,
        "classification": result.classification,
        "reason": result.reason,
    })


@api_bp.route("/runs/<int:run_id>")
def get_run(run_id: int):
    run = ShowRun.query.get_or_404(run_id)
    return jsonify(_serialize_show_run(run))


@api_bp.route("/runs/<int:run_id>/logs")
def run_logs(run_id: int):
    logs = LogEntry.query.filter_by(show_run_id=run_id).order_by(LogEntry.timestamp.asc()).all()
    return jsonify([
        {
            "id": log.id,
            "timestamp": log.timestamp.isoformat(),
            "message": log.message,
            "entry_type": log.entry_type,
            "title": log.title,
            "artist": log.artist,
            "recording_file": log.recording_file,
            "description": log.description,
        } for log in logs
    ])


@api_bp.route("/psa/compliance/<int:run_id>")
def psa_compliance(run_id: int):
    psa_entries = LogEntry.query.filter_by(show_run_id=run_id, entry_type="psa").count()
    live_reads = LogEntry.query.filter_by(show_run_id=run_id, entry_type="live_read").count()
    total = psa_entries + live_reads
    return jsonify({
        "show_run_id": run_id,
        "psa_entries": psa_entries,
        "live_reads": live_reads,
        "meets_requirement": total >= 2,
    })


@api_bp.route("/probes/latest")
def latest_probe():
    probe = StreamProbe.query.order_by(StreamProbe.created_at.desc()).first()
    if not probe:
        return jsonify({"status": "no_data"})

    return jsonify({
        "status": "ok",
        "classification": probe.classification,
        "reason": probe.reason,
        "avg_db": probe.avg_db,
        "silence_ratio": probe.silence_ratio,
        "automation_ratio": probe.automation_ratio,
        "timestamp": probe.created_at.isoformat(),
        "show_run_id": probe.show_run_id,
    })


@api_bp.route("/reports/artist-frequency")
def artist_frequency():
    """
    Returns artist play counts grouped by artist (optionally filtered by show_run_id or date range).
    """
    show_run_id = request.args.get("show_run_id", type=int)
    start = request.args.get("start")
    end = request.args.get("end")

    q = LogEntry.query.filter(LogEntry.entry_type == "music")
    if show_run_id:
        q = q.filter(LogEntry.show_run_id == show_run_id)
    if start:
        q = q.filter(LogEntry.timestamp >= datetime.fromisoformat(start))
    if end:
        q = q.filter(LogEntry.timestamp <= datetime.fromisoformat(end))

    counts = (
        q.with_entities(LogEntry.artist, func.count(LogEntry.id))
        .group_by(LogEntry.artist)
        .order_by(func.count(LogEntry.id).desc())
        .all()
    )
    return jsonify([{"artist": artist or "Unknown", "plays": plays} for artist, plays in counts])


@api_bp.route("/music/search")
def music_search():
    q = request.args.get("q", "").strip()
    page = request.args.get("page", type=int, default=1)
    per_page = request.args.get("per_page", type=int, default=50)
    folder = request.args.get("folder")
    genre = request.args.get("genre")
    mood = request.args.get("mood")
    year = request.args.get("year")
    explicit_raw = request.args.get("explicit")
    explicit = None
    if explicit_raw is not None and explicit_raw != "":
        explicit = str(explicit_raw).lower() in {"1", "true", "yes", "y"}
    refresh = request.args.get("refresh", type=int, default=0)
    if refresh:
        get_music_index(refresh=True)
    if not q:
        return jsonify({
            "items": [],
            "total": 0,
            "page": page,
            "per_page": per_page,
            "folders": [],
            "genres": [],
            "moods": [],
            "years": [],
        })
    payload = search_music(
        q,
        page=page,
        per_page=per_page,
        folder=folder,
        genre=genre,
        year=year,
        mood=mood,
        explicit=explicit,
    )
    return jsonify(payload)


@api_bp.route("/music/saved-searches", methods=["GET", "POST", "DELETE"])
def music_saved_searches():
    user_email = session.get("user_email") or "anonymous"
    if request.method == "GET":
        searches = (
            db.session.query(SavedSearch)
            .filter(
                (SavedSearch.created_by == user_email) | (SavedSearch.created_by.is_(None))
            )
            .order_by(SavedSearch.created_at.desc())
            .limit(25)
            .all()
        )
        return jsonify([
            {
                "id": s.id,
                "name": s.name,
                "query": s.query,
                "filters": s.filters,
                "created_at": s.created_at.isoformat(),
            }
            for s in searches
        ])

    if request.method == "DELETE":
        sid = request.args.get("id", type=int)
        if not sid:
            return jsonify({"status": "error", "message": "id required"}), 400
        s = db.session.get(SavedSearch, sid)
        if not s:
            return jsonify({"status": "error", "message": "not found"}), 404
        if s.created_by and s.created_by != user_email:
            return jsonify({"status": "error", "message": "forbidden"}), 403
        db.session.delete(s)
        db.session.commit()
    return jsonify({"status": "ok"})


@api_bp.route("/archivist/album-info")
def archivist_album_info():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify([])
    rows = lookup_album(query)
    return jsonify([
        {
            "artist": r.artist,
            "title": r.title,
            "album": r.album,
            "catalog_number": r.catalog_number,
            "price_range": r.price_range,
            "notes": r.notes,
        }
        for r in rows
    ])


@api_bp.route("/archivist/album-rip/upload", methods=["POST"])
def archivist_album_rip_upload():
    file = request.files.get("rip_file")
    if not file or file.filename == "":
        return jsonify({"status": "error", "message": "file required"}), 400
    try:
        path = save_album_rip_upload(file)
        session["album_rip_path"] = path
        session["album_rip_uploaded_at"] = time.time()
        return jsonify({"status": "ok", "path": path})
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Album rip upload failed: {exc}")
        return jsonify({"status": "error", "message": "upload_failed"}), 500


@api_bp.route("/archivist/album-rip/cleanup", methods=["POST"])
def archivist_album_rip_cleanup():
    path = session.pop("album_rip_path", None)
    if path:
        delete_album_rip_upload(path)
    cleanup_album_tmp()
    session.pop("album_rip_uploaded_at", None)
    return jsonify({"status": "ok"})


@api_bp.route("/archivist/album-rip", methods=["POST"])
def archivist_album_rip():
    payload = request.get_json(force=True) or {}
    threshold = int(payload.get("silence_thresh_db", -38))
    min_gap_ms = int(payload.get("min_gap_ms", 1200))
    min_track_ms = int(payload.get("min_track_ms", 60_000))
    path = session.get("album_rip_path")
    if not path:
        return jsonify({"status": "error", "message": "no_upload"}), 400
    result = analyze_album_rip(path, silence_thresh_db=threshold, min_gap_ms=min_gap_ms, min_track_ms=min_track_ms)
    if not result:
        return jsonify({"status": "error", "message": "analysis_unavailable"}), 400
    try:
        db.session.add(
            ArchivistRipResult(
                filename=os.path.basename(path),
                duration_ms=result.get("duration_ms"),
                segments_json=json.dumps(result.get("segments", [])),
                settings_json=json.dumps(
                    {"silence_thresh_db": threshold, "min_gap_ms": min_gap_ms, "min_track_ms": min_track_ms}
                ),
            )
        )
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to store rip result: %s", exc)
    return jsonify({"status": "ok", **result, "source_path": path})


@api_bp.route("/music/detail")
def music_detail():
    path = request.args.get("path")
    if not path:
        return jsonify({"status": "error", "message": "path required"}), 400
    track = get_track(path)
    if not track:
        return jsonify({"status": "error", "message": "not found"}), 404
    return jsonify(track)


@api_bp.route("/music/cover-image")
def music_cover_image():
    path = request.args.get("path")
    if not path:
        abort(404)
    safe_path = _safe_music_path(path)
    if not safe_path:
        abort(404)
    cover_path = os.path.splitext(safe_path)[0] + ".jpg"
    if os.path.exists(cover_path):
        return send_file(cover_path, conditional=True)
    try:
        import mutagen  # type: ignore
        from mutagen.id3 import ID3  # type: ignore
        from mutagen.mp4 import MP4  # type: ignore
    except Exception:
        abort(404)
    audio = mutagen.File(safe_path, easy=False)
    if not audio:
        abort(404)
    image_data = None
    mime = "image/jpeg"
    if isinstance(audio, MP4):
        covr = audio.tags.get("covr") if audio.tags else None
        if covr:
            image_data = bytes(covr[0])
    else:
        try:
            id3 = ID3(safe_path)
            apic = id3.get("APIC:") or id3.get("APIC")
            if apic:
                image_data = apic.data
                mime = apic.mime or mime
        except Exception:
            image_data = None
    if not image_data:
        abort(404)
    return send_file(
        io.BytesIO(image_data),
        mimetype=mime,
        conditional=True,
        download_name=os.path.basename(cover_path),
    )


@api_bp.route("/music/cover-art", methods=["POST"])
def music_cover_art():
    payload = request.get_json(force=True, silent=True) or {}
    path = payload.get("path")
    if not path:
        return jsonify({"status": "error", "message": "path required"}), 400
    if payload.get("art_url"):
        # Download explicit selection
        try:
            resp = requests.get(payload["art_url"], timeout=10)
            resp.raise_for_status()
            dest = os.path.splitext(path)[0] + ".jpg"
            with open(dest, "wb") as fh:
                fh.write(resp.content)
            result = {"status": "ok", "art_path": dest}
        except Exception as exc:  # noqa: BLE001
            result = {"status": "error", "message": str(exc)}
    else:
        result = harvest_cover_art(path, get_track(path))
    code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), code


@api_bp.route("/music/cover-art/options")
def music_cover_art_options():
    path = request.args.get("path")
    if not path:
        return jsonify({"status": "error", "message": "path required"}), 400
    result = cover_art_candidates(path, get_track(path))
    code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), code


@api_bp.route("/music/musicbrainz")
def music_musicbrainz():
    title = (request.args.get("title") or "").strip()
    artist = (request.args.get("artist") or "").strip()
    limit = request.args.get("limit", type=int, default=5)
    if not title and not artist:
        return jsonify({"status": "error", "message": "title_or_artist_required"}), 400
    results = lookup_musicbrainz(title=title or None, artist=artist or None, limit=limit)
    return jsonify({"status": "ok", "results": results})


@api_bp.route("/music/enrich")
def music_enrich():
    path = request.args.get("path")
    tags = get_track(path) if path else {}
    if not tags:
        return jsonify({"status": "error", "message": "path required"}), 400
    result = enrich_metadata_external(tags)
    code = 200 if result.get("status") == "ok" else 400
    return jsonify(result), code


@api_bp.route("/archivist/musicbrainz-releases")
def archivist_musicbrainz_releases():
    title = request.args.get("title") or request.args.get("q")
    artist = request.args.get("artist")
    limit = request.args.get("limit", type=int, default=5)
    data = lookup_musicbrainz(title=title or None, artist=artist or None, limit=limit, include_releases=True)
    return jsonify({"status": "ok", "results": data})


@api_bp.route("/music/bulk-update", methods=["POST"])
def music_bulk_update():
    payload = request.get_json(force=True, silent=True) or {}
    paths = payload.get("paths") or []
    updates = payload.get("updates") or {}
    cover_art_b64 = payload.get("cover_art")
    cover_bytes = None
    if cover_art_b64:
        try:
            import base64

            cover_bytes = base64.b64decode(cover_art_b64)
        except Exception:
            cover_bytes = None
    if not paths:
        return jsonify({"status": "error", "message": "paths required"}), 400
    result = bulk_update_metadata(paths, updates, cover_bytes)
    return jsonify(result)


@api_bp.route("/psa/library")
def psa_library():
    page = request.args.get("page", type=int, default=1)
    per_page = request.args.get("per_page", type=int, default=50)
    category = request.args.get("category")
    kind = request.args.get("kind")
    query = request.args.get("q")
    payload = list_media(query=query, category=category, kind=kind, page=page, per_page=per_page)
    return jsonify(payload)


@api_bp.route("/psa/cue", methods=["GET", "POST"])
def psa_cue():
    token = request.values.get("token")
    payload = request.get_json(force=True, silent=True) or {}
    token = token or payload.get("token")
    if not token:
        return jsonify({"status": "error", "message": "token required"}), 400
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return jsonify({"status": "error", "message": "invalid token"}), 400
    full = os.path.normcase(os.path.abspath(os.path.normpath(decoded)))
    allowed = False
    for _, root, _ in _media_roots():
        root_abs = os.path.normcase(os.path.abspath(os.path.normpath(root)))
        if full.startswith(root_abs):
            allowed = True
            break
    if not allowed or not os.path.isfile(full):
        return jsonify({"status": "error", "message": "file not found"}), 404

    if request.method == "GET":
        meta = load_media_meta(full)
        cue = {k: meta.get(k) for k in ["cue_in", "intro", "loop_in", "loop_out", "start_next", "outro", "cue_out"]}
        cue = {k: v for k, v in cue.items() if v is not None}
        return jsonify({"status": "ok", "cue": cue})

    cue_payload = payload.get("cue") or {}
    cue_updates = {
        k: cue_payload.get(k)
        for k in ["cue_in", "intro", "loop_in", "loop_out", "start_next", "outro", "cue_out"]
        if cue_payload.get(k) is not None
    }
    meta = save_media_meta(full, cue_updates)
    cue = {k: meta.get(k) for k in ["cue_in", "intro", "loop_in", "loop_out", "start_next", "outro", "cue_out"]}
    cue = {k: v for k, v in cue.items() if v is not None}
    return jsonify({"status": "ok", "cue": cue})


@api_bp.route("/music/scan/library")
def music_scan_library():
    snapshot = queues_snapshot()
    return jsonify(snapshot)


@api_bp.route("/music/cue", methods=["GET", "POST"])
def music_cue():
    path = request.values.get("path")
    if not path:
        return jsonify({"status": "error", "message": "path required"}), 400
    if request.method == "GET":
        cue = load_cue(path)
        cue_payload = {
            "cue_in": cue.cue_in if cue else None,
            "intro": cue.intro if cue else None,
            "outro": cue.outro if cue else None,
            "cue_out": cue.cue_out if cue else None,
            "loop_in": cue.loop_in if cue else None,
            "loop_out": cue.loop_out if cue else None,
            "hook_in": cue.hook_in if cue else None,
            "hook_out": cue.hook_out if cue else None,
            "start_next": cue.start_next if cue else None,
            "fade_in": cue.fade_in if cue else None,
            "fade_out": cue.fade_out if cue else None,
        }
        cue_payload = auto_fill_missing_cues(path, cue_payload)
        return jsonify({"path": path, "cue": cue_payload})
    payload = request.get_json(force=True, silent=True) or {}
    cue = save_cue(path, payload)
    return jsonify({"status": "ok", "cue": {
        "cue_in": cue.cue_in,
        "intro": cue.intro,
        "loop_in": cue.loop_in,
        "loop_out": cue.loop_out,
        "outro": cue.outro,
        "cue_out": cue.cue_out,
        "hook_in": cue.hook_in,
        "hook_out": cue.hook_out,
        "start_next": cue.start_next,
        "fade_in": cue.fade_in,
        "fade_out": cue.fade_out,
    }})


@api_bp.route("/schedule")
def schedule_api():
    tz = current_app.config.get("SCHEDULE_TIMEZONE", "America/New_York")
    cached = api_cache.get("schedule")
    if cached:
        return jsonify(cached)

    shows = Show.query.order_by(Show.days_of_week, Show.start_time).all()
    now = datetime.utcnow()
    marathons = MarathonEvent.query.filter(
        MarathonEvent.end_time >= now, MarathonEvent.canceled_at.is_(None)
    ).all()
    absences = DJAbsence.query.filter(
        DJAbsence.end_time >= now - timedelta(days=1),
        DJAbsence.status.in_(["approved", "pending"]),
    ).all()
    events = []
    for show in shows:
        days = [d.strip() for d in (show.days_of_week or "").split(',') if d.strip()]
        for day in days:
            absence = None
            for a in absences:
                if a.show_id == show.id and a.start_time.strftime('%a').lower().startswith(day.lower()[:3]):
                    absence = a
                    break
            events.append({
                "title": show_display_title(show),
                "day": day,
                "start_time": show.start_time.strftime("%H:%M"),
                "end_time": show.end_time.strftime("%H:%M"),
                "start_date": show.start_date.isoformat() if show.start_date else None,
                "end_date": show.end_date.isoformat() if show.end_date else None,
                "timezone": tz,
                "description": show.description,
                "genre": show.genre,
                "absence": {
                    "status": absence.status,
                    "replacement": absence.replacement_name,
                    "dj": absence.dj_name,
                } if absence else None,
                "type": "show",
            })
    for marathon in marathons:
        day_label = marathon.start_time.strftime('%a')
        events.append({
            "title": marathon.name,
            "day": day_label,
            "start_time": marathon.start_time.strftime("%H:%M"),
            "end_time": marathon.end_time.strftime("%H:%M"),
            "start_date": marathon.start_time.isoformat(),
            "end_date": marathon.end_time.isoformat(),
            "timezone": tz,
            "description": "Marathon recording window",
            "genre": "Marathon",
            "absence": None,
            "type": "marathon",
        })
    payload = {"events": events, "timezone": tz}
    api_cache.set("schedule", payload, ttl=300)
    return jsonify(payload)


@api_bp.route("/plugins/website/content")
def website_plugin_content():
    plugin = Plugin.query.filter_by(name="website_content").first()
    if plugin and not plugin.enabled:
        return jsonify({"status": "disabled", "message": "website_content plugin disabled"}), 503

    content = WebsiteContent.query.first()
    articles = WebsiteArticle.query.order_by(WebsiteArticle.position, WebsiteArticle.id).all()
    podcasts = PodcastEpisode.query.order_by(PodcastEpisode.created_at.desc()).all()
    press = PressFeature.query.order_by(PressFeature.position, PressFeature.id).all()

    hero = None
    if articles:
        first = articles[0]
        hero = {
            "headline": first.title,
            "body": first.body,
            "image_url": first.image_url,
            "updated_at": first.created_at.isoformat(),
        }
    elif content:
        hero = {
            "headline": content.headline,
            "body": content.body,
            "image_url": content.image_url,
            "updated_at": content.updated_at.isoformat() if content.updated_at else None,
        }

    return jsonify({
        "status": "ok",
        "content": hero,
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "body": a.body,
                "image_url": a.image_url,
                "position": a.position,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in articles
        ],
        "press": [
            {
                "id": f.id,
                "name": f.name,
                "url": f.url,
                "logo": f.logo,
                "position": f.position,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in press
        ],
        "podcasts": [
            {
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "embed_code": p.embed_code,
                "created_at": p.created_at.isoformat(),
            }
            for p in podcasts
        ],
    })


@api_bp.route("/plugins/website/banner")
def website_banner():
    plugin = Plugin.query.filter_by(name="website_content").first()
    if plugin and not plugin.enabled:
        return "", 204

    banner = WebsiteBanner.query.first()
    if not banner or not banner.message:
        return "", 204
    return jsonify({
        "message": banner.message,
        "link": banner.link,
        "tone": banner.tone,
    })


@api_bp.route("/plugins/audio/embed/<int:item_id>")
def audio_embed(item_id: int):
    item = HostedAudio.query.get_or_404(item_id)
    backdrop_url = item.backdrop_url
    upload_dir = current_app.config.get("AUDIO_HOST_UPLOAD_DIR")
    default_backdrop = current_app.config.get("AUDIO_HOST_BACKDROP_DEFAULT")
    if not backdrop_url:
        if default_backdrop and os.path.isfile(default_backdrop):
            if upload_dir:
                os.makedirs(upload_dir, exist_ok=True)
                dest = os.path.join(upload_dir, os.path.basename(default_backdrop))
                if not os.path.exists(dest):
                    try:
                        shutil.copyfile(default_backdrop, dest)
                    except Exception:
                        pass
                backdrop_url = url_for('audio_host_plugin.serve_file', filename=os.path.basename(default_backdrop), _external=True)
        elif default_backdrop and default_backdrop.startswith("http"):
            backdrop_url = default_backdrop
    if not backdrop_url:
        backdrop_url = url_for('static', filename='logo.png', _external=True)
    return render_template("embed_audio.html", item=item, backdrop_url=backdrop_url)


@api_bp.route("/weather/tempest")
def weather_tempest():
    token = current_app.config.get("TEMPEST_API_KEY")
    station_id = current_app.config.get("TEMPEST_STATION_ID", 118392)
    units_temp = current_app.config.get("TEMPEST_UNITS_TEMP", "f")
    units_wind = current_app.config.get("TEMPEST_UNITS_WIND", "mph")

    if not token:
        return jsonify({"status": "error", "message": "tempest_not_configured"}), 400

    params = {
        "station_id": station_id,
        "units_temp": units_temp,
        "units_wind": units_wind,
        "token": token,
    }

    try:
        resp = requests.get("https://swd.weatherflow.com/swd/rest/better_forecast", params=params, timeout=8)
        resp.raise_for_status()
        payload = resp.json() or {}
    except Exception as e:
        logger.error(f"Tempest fetch failed: {e}")
        return jsonify({"status": "error", "message": "tempest_fetch_failed"}), 502

    forecast_root = payload.get("forecast") or payload
    current = forecast_root.get("current_conditions") or payload.get("current_conditions") or {}

    def _fmt_time(value):
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value).strftime("%I:%M %p").lstrip("0")
            except Exception:
                return str(value)
        if isinstance(value, str):
            return value
        return ""

    def _fmt_day(value):
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value).strftime("%a")
            except Exception:
                return str(value)
        if isinstance(value, str):
            return value
        return ""

    hourly = (
        forecast_root.get("hourly")
        or forecast_root.get("hourly_forecast")
        or payload.get("hourly")
        or payload.get("hourly_forecast")
        or []
    )

    def _parse_ts(value):
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            if value.isdigit():
                try:
                    return float(value)
                except Exception:
                    return None
        return None

    next_hours = []
    targets = [1, 2, 4, 8]
    now_ts = time.time()
    for target in targets:
        target_ts = now_ts + target * 3600
        best_item = None
        best_diff = None
        fallback_item = None
        for item in hourly:
            ts_raw = item.get("time") or item.get("timestamp") or item.get("start_time")
            ts = _parse_ts(ts_raw)
            if ts is None:
                if fallback_item is None:
                    fallback_item = item
                continue
            if ts < now_ts and fallback_item is None:
                fallback_item = item
            diff = ts - target_ts
            if diff >= 0 and (best_diff is None or diff < best_diff):
                best_item = item
                best_diff = diff
        chosen = best_item or fallback_item
        if not chosen:
            continue
        next_hours.append(
            {
                "time": _fmt_time(
                    chosen.get("time")
                    or chosen.get("timestamp")
                    or chosen.get("start_time")
                ),
                "temp": chosen.get("air_temperature") or chosen.get("temperature"),
                "condition": chosen.get("conditions") or chosen.get("icon") or chosen.get("weather"),
                "icon": chosen.get("icon") or chosen.get("icon_code"),
            }
        )

    daily = (
        forecast_root.get("daily")
        or forecast_root.get("daily_forecast")
        or payload.get("daily_forecast")
        or []
    )
    next_days = []
    for item in daily[:3]:
        next_days.append(
            {
                "day": _fmt_day(item.get("day_start_local") or item.get("day") or item.get("start_time")),
                "high": item.get("air_temp_high") or item.get("high_temperature"),
                "low": item.get("air_temp_low") or item.get("low_temperature"),
                "condition": item.get("conditions") or item.get("icon") or item.get("weather"),
                "icon": item.get("icon") or item.get("icon_code"),
            }
        )

    current_block = {
        "temp": current.get("air_temperature") or current.get("temperature"),
        "condition": current.get("conditions") or current.get("icon") or current.get("weather"),
        "icon": current.get("icon") or current.get("icon_code"),
    }

    return jsonify({
        "status": "ok",
        "current": current_block,
        "next_hours": next_hours,
        "next_days": next_days,
    })


@api_bp.route("/stream/status")
def stream_status():
    url = current_app.config["STREAM_URL"]
    status = {"stream_up": False, "probe": None}
    try:
        resp = requests.get(url, stream=True, timeout=3)
        status["stream_up"] = resp.status_code < 500
    except Exception:
        status["stream_up"] = False

    probe = StreamProbe.query.order_by(StreamProbe.created_at.desc()).first()
    if probe is None or (datetime.utcnow() - probe.created_at) > timedelta(minutes=10):
        probe_result = probe_stream(url)
        if probe_result:
            status["probe"] = {
                "classification": probe_result.classification,
                "reason": probe_result.reason,
                "avg_db": probe_result.avg_db,
                "silence_ratio": probe_result.silence_ratio,
                "automation_ratio": probe_result.automation_ratio,
            }
    else:
        status["probe"] = {
            "classification": probe.classification,
            "reason": probe.reason,
            "avg_db": probe.avg_db,
            "silence_ratio": probe.silence_ratio,
            "automation_ratio": probe.automation_ratio,
            "timestamp": probe.created_at.isoformat(),
        }

    listeners = fetch_icecast_listeners()
    if listeners is not None:
        status["listeners"] = listeners
    return jsonify(status)


@api_bp.route("/icecast/analytics")
def icecast_analytics():
    hours = request.args.get("hours", default=24, type=int)
    stats = recent_icecast_stats(hours=hours)
    return jsonify(stats)


@api_bp.route("/audit/start", methods=["POST"])
def start_audit():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in {"recordings", "explicit"}:
        return jsonify({"status": "error", "message": "invalid action"}), 400
    max_files = data.get("max_files")
    params = {
        "folder": data.get("folder"),
        "rate": float(data.get("rate") or current_app.config["AUDIT_ITUNES_RATE_LIMIT_SECONDS"]),
        "max_files": int(max_files) if max_files not in (None, "") else None,
        "lyrics_check": bool(data.get("lyrics_check")),
    }
    job_id = start_audit_job(action, params)
    return jsonify({"status": "queued", "job_id": job_id})


@api_bp.route("/audit/status/<job_id>")
def audit_status(job_id):
    return jsonify(get_audit_status(job_id))


@api_bp.route("/audit/runs")
def audit_runs():
    limit = request.args.get("limit", default=20, type=int)
    return jsonify(list_audit_runs(limit=limit))


@api_bp.route("/audit/runs/<int:run_id>")
def audit_run_detail(run_id):
    run = get_audit_run(run_id)
    if not run:
        return jsonify({"status": "error", "message": "not found"}), 404
    return jsonify(run)


@api_bp.route("/djs")
def list_djs_api():
    items = DJ.query.order_by(DJ.last_name, DJ.first_name).all()
    payload = []
    for dj in items:
        payload.append({
            "id": dj.id,
            "first_name": dj.first_name,
            "last_name": dj.last_name,
            "bio": dj.bio,
            "description": dj.description,
            "photo_url": dj.photo_url,
            "shows": [
                {
                    "id": s.id,
                    "name": show_display_title(s),
                    "start_time": s.start_time.strftime("%H:%M"),
                    "end_time": s.end_time.strftime("%H:%M"),
                    "days_of_week": s.days_of_week,
                    "genre": s.genre,
                    "description": s.description,
                }
                for s in dj.shows
            ]
        })
    return jsonify(payload)


@api_bp.route("/radiodj/psas")
def list_psas():
    client = RadioDJClient()
    try:
        items = client.list_psas()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ok", "psas": items})


@api_bp.route("/radiodj/now-playing")
def radiodj_now_playing():
    client = RadioDJClient()
    if not client.enabled:
        return jsonify({"status": "disabled", "track": None}), 503
    payload = client.now_playing()
    if not payload:
        return jsonify({"status": "empty", "track": None})
    return jsonify({"status": "ok", "track": payload})


@api_bp.route("/radiodj/queue", methods=["POST"])
def radiodj_queue():
    client = RadioDJClient()
    if not client.enabled:
        return jsonify({"status": "error", "message": "radiodj_disabled"}), 503
    payload = request.get_json(force=True, silent=True) or {}
    items = payload.get("items") or []
    if not items:
        return jsonify({"status": "error", "message": "items_required"}), 400
    results = []
    for item in items:
        token = (item or {}).get("token") if isinstance(item, dict) else None
        name = (item or {}).get("name") if isinstance(item, dict) else None
        path = _decode_media_token(token or "")
        if not path:
            results.append({"name": name or "Unknown", "status": "error", "message": "invalid_token"})
            continue
        try:
            target = client.import_file(path)
            results.append({"name": name or os.path.basename(path), "status": "ok", "target": str(target)})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": name or os.path.basename(path), "status": "error", "message": str(exc)})
    return jsonify({"status": "ok", "results": results})


@api_bp.route("/radiodj/import/<kind>", methods=["POST"])
def import_radiodj(kind: str):
    if kind not in {"news", "community_calendar"}:
        return jsonify({"status": "error", "message": "invalid_kind"}), 400
    try:
        target = import_news_or_calendar(kind)
    except Exception as exc:  # noqa: BLE001
        logger.error("Import error: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500

    return jsonify({"status": "ok", "imported_path": str(target)})


@api_bp.route("/radiodj/psas/<psa_id>/metadata", methods=["PATCH"])
def update_psa(psa_id: str):
    client = RadioDJClient()
    if not client.enabled:
        return jsonify({"status": "error", "message": "RadioDJ API disabled"}), 400
    metadata = request.get_json(silent=True) or {}
    try:
        updated = client.update_psa_metadata(psa_id, metadata)
    except Exception as exc:  # noqa: BLE001
        logger.error("Metadata update failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ok", "psa": updated})


@api_bp.route("/radiodj/psas/<psa_id>/enable", methods=["POST"])
def enable_psa(psa_id: str):
    client = RadioDJClient()
    try:
        res = client.enable_psa(psa_id)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ok", "psa": res})


@api_bp.route("/radiodj/psas/<psa_id>/disable", methods=["POST"])
def disable_psa(psa_id: str):
    client = RadioDJClient()
    try:
        res = client.disable_psa(psa_id)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ok", "psa": res})


@api_bp.route("/radiodj/psas/<psa_id>", methods=["DELETE"])
def delete_psa(psa_id: str):
    client = RadioDJClient()
    try:
        res = client.delete_psa(psa_id)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ok", "psa": res})


@api_bp.route("/radiodj/autodj", methods=["POST"])
def radiodj_autodj():
    payload = request.get_json(force=True, silent=True) or {}
    enabled = payload.get("enabled")
    if enabled is None:
        enabled = request.args.get("enabled")
    if enabled is None:
        return jsonify({"status": "error", "message": "enabled required"}), 400
    enabled_flag = str(enabled).lower() in {"1", "true", "yes", "on"}
    client = RadioDJClient()
    if not client.enabled:
        return jsonify({"status": "error", "message": "radiodj_disabled"}), 503
    try:
        result = client.set_autodj(enabled_flag)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify(result)


@api_bp.route("/playback/session", methods=["GET", "POST"])
def playback_session():
    playback = _get_playback_session()
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        playback.show_name = payload.get("show_name", playback.show_name)
        playback.dj_name = payload.get("dj_name", playback.dj_name)
        playback.notes = payload.get("notes", playback.notes)
        _touch_playback_session(playback)
        db.session.commit()
    return jsonify({
        "id": playback.id,
        "show_name": playback.show_name,
        "dj_name": playback.dj_name,
        "notes": playback.notes,
        "created_at": playback.created_at.isoformat(),
        "updated_at": playback.updated_at.isoformat(),
    })


@api_bp.route("/playback/session/attach", methods=["POST"])
def playback_session_attach():
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id")
    if not session_id:
        return jsonify({"status": "error", "message": "session_id required"}), 400
    playback = db.session.get(PlaybackSession, session_id)
    if not playback:
        return jsonify({"status": "error", "message": "playback_session_not_found"}), 404
    session["playback_session_id"] = playback.id
    return jsonify({
        "status": "ok",
        "session": {
            "id": playback.id,
            "show_name": playback.show_name,
            "dj_name": playback.dj_name,
            "notes": playback.notes,
        },
    })


@api_bp.route("/playback/queue", methods=["GET"])
def playback_queue_list():
    playback = _get_playback_session()
    items = _queue_items(playback.id)
    now_playing = _serialize_now_playing(_now_playing_for(playback.id))
    return jsonify({
        "session_id": playback.id,
        "queue": [_serialize_queue_item(item) for item in items],
        "now_playing": now_playing,
    })


@api_bp.route("/playback/queue/enqueue", methods=["POST"])
def playback_queue_enqueue():
    playback = _get_playback_session()
    payload = request.get_json(silent=True) or {}
    item_type = (payload.get("type") or "").lower()
    if item_type not in QUEUE_ITEM_TYPES:
        return jsonify({"status": "error", "message": "invalid_type"}), 400
    items = _queue_items(playback.id)
    position = payload.get("position")
    if position is None:
        position = len(items)
    else:
        try:
            position = int(position)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "invalid_position"}), 400
        position = max(0, min(position, len(items)))
    metadata = payload.get("metadata")
    item = PlaybackQueueItem(
        session_id=playback.id,
        position=position,
        item_type=item_type,
        title=payload.get("title"),
        artist=payload.get("artist"),
        duration=payload.get("duration"),
        metadata=json.dumps(metadata) if metadata is not None else None,
    )
    items.insert(position, item)
    db.session.add(item)
    _resequence_queue(items)
    _touch_playback_session(playback)
    db.session.commit()
    return jsonify({"status": "ok", "item": _serialize_queue_item(item)})


@api_bp.route("/playback/queue/dequeue", methods=["POST"])
def playback_queue_dequeue():
    playback = _get_playback_session()
    payload = request.get_json(silent=True) or {}
    item_id = payload.get("item_id")
    if item_id is not None:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "invalid_item_id"}), 400
    items = _queue_items(playback.id)
    target = None
    if item_id is not None:
        target = next((item for item in items if item.id == item_id), None)
    elif items:
        target = items[0]
    if not target:
        return jsonify({"status": "error", "message": "item_not_found"}), 404
    db.session.delete(target)
    items = [item for item in items if item.id != target.id]
    _resequence_queue(items)
    _touch_playback_session(playback)
    db.session.commit()
    return jsonify({"status": "ok", "item": _serialize_queue_item(target)})


@api_bp.route("/playback/queue/move", methods=["POST"])
def playback_queue_move():
    playback = _get_playback_session()
    payload = request.get_json(silent=True) or {}
    item_id = payload.get("item_id")
    new_position = payload.get("position")
    if item_id is None or new_position is None:
        return jsonify({"status": "error", "message": "item_id and position required"}), 400
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "invalid_item_id"}), 400
    items = _queue_items(playback.id)
    target = next((item for item in items if item.id == item_id), None)
    if not target:
        return jsonify({"status": "error", "message": "item_not_found"}), 404
    try:
        new_position = int(new_position)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "invalid_position"}), 400
    items = [item for item in items if item.id != target.id]
    new_position = max(0, min(new_position, len(items)))
    items.insert(new_position, target)
    _resequence_queue(items)
    _touch_playback_session(playback)
    db.session.commit()
    return jsonify({"status": "ok", "queue": [_serialize_queue_item(item) for item in items]})


@api_bp.route("/playback/queue/skip", methods=["POST"])
def playback_queue_skip():
    playback = _get_playback_session()
    items = _queue_items(playback.id)
    now_playing = _now_playing_for(playback.id)
    if now_playing.queue_item_id:
        items = [item for item in items if item.id != now_playing.queue_item_id]
        item = db.session.get(PlaybackQueueItem, now_playing.queue_item_id)
        if item:
            db.session.delete(item)
    next_item = items[0] if items else None
    if next_item:
        items = items[1:]
        db.session.delete(next_item)
    _set_now_playing_from_item(now_playing, next_item, status="playing" if next_item else "idle")
    _resequence_queue(items)
    _touch_playback_session(playback)
    db.session.commit()
    return jsonify({
        "status": "ok",
        "now_playing": _serialize_now_playing(now_playing),
        "queue": [_serialize_queue_item(item) for item in items],
    })


@api_bp.route("/playback/queue/cue", methods=["POST"])
def playback_queue_cue():
    playback = _get_playback_session()
    payload = request.get_json(silent=True) or {}
    now_playing = _now_playing_for(playback.id)
    if not now_playing.queue_item_id and not now_playing.title:
        return jsonify({"status": "error", "message": "nothing_playing"}), 400
    now_playing.cue_in = payload.get("cue_in", now_playing.cue_in)
    now_playing.cue_out = payload.get("cue_out", now_playing.cue_out)
    now_playing.updated_at = datetime.utcnow()
    _touch_playback_session(playback)
    db.session.add(now_playing)
    db.session.commit()
    return jsonify({"status": "ok", "now_playing": _serialize_now_playing(now_playing)})


@api_bp.route("/playback/queue/fade", methods=["POST"])
def playback_queue_fade():
    playback = _get_playback_session()
    payload = request.get_json(silent=True) or {}
    now_playing = _now_playing_for(playback.id)
    if not now_playing.queue_item_id and not now_playing.title:
        return jsonify({"status": "error", "message": "nothing_playing"}), 400
    now_playing.fade_out = payload.get("fade_out", now_playing.fade_out)
    now_playing.updated_at = datetime.utcnow()
    _touch_playback_session(playback)
    db.session.add(now_playing)
    db.session.commit()
    return jsonify({"status": "ok", "now_playing": _serialize_now_playing(now_playing)})


@api_bp.route("/playback/now-playing", methods=["POST"])
def playback_set_now_playing():
    playback = _get_playback_session()
    payload = request.get_json(silent=True) or {}
    now_playing = _now_playing_for(playback.id)
    item_id = payload.get("item_id")
    status = payload.get("status", "playing")
    if item_id is not None:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "invalid_item_id"}), 400
        item = PlaybackQueueItem.query.filter_by(session_id=playback.id, id=item_id).first()
        if not item:
            return jsonify({"status": "error", "message": "item_not_found"}), 404
        db.session.delete(item)
        _set_now_playing_from_item(now_playing, item, status=status)
    else:
        now_playing.queue_item_id = None
        now_playing.item_type = payload.get("type", now_playing.item_type)
        now_playing.title = payload.get("title", now_playing.title)
        now_playing.artist = payload.get("artist", now_playing.artist)
        now_playing.duration = payload.get("duration", now_playing.duration)
        metadata = payload.get("metadata")
        if metadata is not None:
            now_playing.metadata = json.dumps(metadata)
        now_playing.status = status
        now_playing.started_at = datetime.utcnow()
        now_playing.updated_at = datetime.utcnow()
        db.session.add(now_playing)
    _touch_playback_session(playback)
    db.session.commit()
    return jsonify({"status": "ok", "now_playing": _serialize_now_playing(now_playing)})
