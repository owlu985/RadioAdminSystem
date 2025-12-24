from datetime import datetime
import time
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, current_app, request
from app.models import ShowRun, StreamProbe, LogEntry, DJ, Show
from app.utils import get_current_show, format_show_window
from app.services.show_run_service import get_or_create_active_run
from app.services.radiodj_client import import_news_or_calendar, RadioDJClient
from app.services.detection import probe_stream
from app.services.stream_monitor import fetch_icecast_listeners
from app.services.music_search import (
    search_music,
    get_track,
    bulk_update_metadata,
    queues_snapshot,
    load_cue,
    save_cue,
    scan_library,
    find_duplicates_and_quality,
)
from sqlalchemy import func
from app.logger import init_logger
from app.services.audit import start_audit_job, get_audit_status
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


@api_bp.route("/now")
def now_playing():
    show = get_current_show()
    if not show:
        return jsonify({
            "status": "off_air",
            "message": current_app.config.get("DEFAULT_OFF_AIR_MESSAGE"),
        })

    run = get_or_create_active_run(
        show_name=show.show_name or f"{show.host_first_name} {show.host_last_name}",
        dj_first_name=show.host_first_name,
        dj_last_name=show.host_last_name,
    )

    return jsonify({
        "status": "on_air",
        "show": {
            "name": show.show_name or f"{show.host_first_name} {show.host_last_name}",
            "host": f"{show.host_first_name} {show.host_last_name}",
            "genre": show.genre,
            "description": show.description,
            "regular_host": show.is_regular_host,
            "window": format_show_window(show),
        },
        "run": _serialize_show_run(run),
    })


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
    if not q:
        return jsonify([])
    results = search_music(q)[:200]
    return jsonify(results)


@api_bp.route("/music/detail")
def music_detail():
    path = request.args.get("path")
    if not path:
        return jsonify({"status": "error", "message": "path required"}), 400
    track = get_track(path)
    if not track:
        return jsonify({"status": "error", "message": "not found"}), 404
    return jsonify(track)


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
        return jsonify({
            "path": path,
            "cue": {
                "cue_in": cue.cue_in if cue else None,
                "intro": cue.intro if cue else None,
                "outro": cue.outro if cue else None,
                "fade_in": cue.fade_in if cue else None,
                "fade_out": cue.fade_out if cue else None,
            },
        })
    payload = request.get_json(force=True, silent=True) or {}
    cue = save_cue(path, payload)
    return jsonify({"status": "ok", "cue": {
        "cue_in": cue.cue_in,
        "intro": cue.intro,
        "outro": cue.outro,
        "fade_in": cue.fade_in,
        "fade_out": cue.fade_out,
    }})


@api_bp.route("/schedule")
def schedule_api():
    tz = current_app.config.get("SCHEDULE_TIMEZONE", "America/New_York")
    shows = Show.query.order_by(Show.days_of_week, Show.start_time).all()
    events = []
    for show in shows:
        days = [d.strip() for d in (show.days_of_week or "").split(',') if d.strip()]
        for day in days:
            events.append({
                "title": show.show_name or f"{show.host_first_name} {show.host_last_name}",
                "day": day,
                "start_time": show.start_time.strftime("%H:%M"),
                "end_time": show.end_time.strftime("%H:%M"),
                "start_date": show.start_date.isoformat(),
                "end_date": show.end_date.isoformat(),
                "timezone": tz,
                "description": show.description,
                "genre": show.genre,
            })
    return jsonify({"events": events, "timezone": tz})


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


@api_bp.route("/audit/start", methods=["POST"])
def start_audit():
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in {"recordings", "explicit"}:
        return jsonify({"status": "error", "message": "invalid action"}), 400
    params = {
        "folder": data.get("folder"),
        "rate": float(data.get("rate") or current_app.config["AUDIT_ITUNES_RATE_LIMIT_SECONDS"]),
    }
    job_id = start_audit_job(action, params)
    return jsonify({"status": "queued", "job_id": job_id})


@api_bp.route("/audit/status/<job_id>")
def audit_status(job_id):
    return jsonify(get_audit_status(job_id))


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
            "photo_url": dj.photo_url,
            "shows": [
                {
                    "id": s.id,
                    "name": s.show_name or f"{s.host_first_name} {s.host_last_name}",
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
