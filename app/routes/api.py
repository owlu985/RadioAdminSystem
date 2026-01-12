import csv
from datetime import datetime, timedelta
import io
import time
from flask import Blueprint, Response, jsonify, current_app, request, session, url_for, render_template
import os
import shutil
import json
import base64
from typing import Optional
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


@api_bp.route("/now")
def now_playing():
    now = datetime.utcnow()
    show = get_current_show()
    absence = active_absence_for_show(show, now=now) if show else None

    if not show:
        rdj = RadioDJClient()
        rdj_payload = rdj.now_playing()
        base = {
            "status": "off_air",
            "message": current_app.config.get("DEFAULT_OFF_AIR_MESSAGE"),
        }
        if rdj_payload:
            base.update({"status": "automation", "source": "radiodj", "track": rdj_payload})
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


@api_bp.route("/now/widget")
def now_widget():
    """
    Widget-friendly now-playing: if a scheduled show is active, show it; otherwise
    return RadioDJ automation metadata when available.
    """
    base = now_playing().get_json()  # type: ignore
    if base and base.get("status") != "off_air":
        return jsonify(base)
    rdj = RadioDJClient()
    rdj_payload = rdj.now_playing()
    if rdj_payload:
        base = base or {}
        base.update({"status": "automation", "source": "radiodj", "track": rdj_payload})
        return jsonify(base)
    return jsonify(base or {"status": "off_air"})


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
