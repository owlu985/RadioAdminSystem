from datetime import datetime
from flask import Blueprint, jsonify, current_app, request
from app.models import ShowRun, StreamProbe, LogEntry
from app.utils import get_current_show, format_show_window
from app.services.show_run_service import get_or_create_active_run
from app.services.radiodj_client import import_news_or_calendar, RadioDJClient
from app.services.detection import probe_stream
from sqlalchemy import func
from app.logger import init_logger

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
