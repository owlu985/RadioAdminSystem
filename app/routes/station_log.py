"""Authenticated master-log viewer, review tools and operator CLI."""
from datetime import date, datetime, timedelta, timezone
from functools import wraps
import hmac
import json
import os
from pathlib import Path
import secrets
from zoneinfo import ZoneInfo

import click
from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, Response, session, url_for)
from app.auth_utils import permission_required
from app.models import db, StationLogAiring, StationLogArchive, StationLogImport
from app.services import station_log as logs
from app.services import radiodj_history as history
from app.services.runtime_state import locked_state

station_log_bp = Blueprint("station_log", __name__, url_prefix="/station-log")


def _day():
    try:
        return date.fromisoformat(request.args.get("day") or datetime.now(logs.station_zone()).date().isoformat())
    except ValueError:
        abort(400, description="Use a date in YYYY-MM-DD format")


def _csrf(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        expected = session.get("station_log_csrf", "")
        supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(expected, supplied):
            abort(400, description="Refresh the master log page before submitting")
        return f(*args, **kwargs)
    return wrapped


def _actor():
    return session.get("user_email") or session.get("display_name") or session.get("role") or "operator"


def _read_rows(day):
    logs.drain_events()
    rows = logs.daily_log(day, raw=request.args.get("raw") == "1")
    query = request.args.get("q", "").strip().casefold()
    kind = request.args.get("kind", "")
    if query:
        rows = [row for row in rows if query in json.dumps(row, ensure_ascii=False).casefold()]
    if kind == "engineering":
        rows = [row for row in rows if row["source"] not in {"dj_submission", "airing_review"} and row["event_type"] != "playback"]
    elif kind == "programming":
        rows = [row for row in rows if row["event_type"] == "playback" or row["source"] in {"dj_submission", "airing_review"}]
    return rows


@station_log_bp.get("")
@permission_required({"logs:view"})
def index():
    day = _day()
    session.setdefault("station_log_csrf", secrets.token_urlsafe(32))
    rows = _read_rows(day)
    page = max(1, request.args.get("page", 1, type=int))
    start, end = logs.day_bounds(day)
    airings = StationLogAiring.query.filter(StationLogAiring.starts_at < end,
        StationLogAiring.ends_at > start).order_by(StationLogAiring.starts_at).all()
    for airing in airings:
        airing.local_start = airing.starts_at.replace(tzinfo=timezone.utc).astimezone(logs.station_zone()).isoformat(timespec="minutes")
        airing.local_end = airing.ends_at.replace(tzinfo=timezone.utc).astimezone(logs.station_zone()).isoformat(timespec="minutes")
    for row in rows:
        value = datetime.fromisoformat(row["occurred_at"]).astimezone(logs.station_zone())
        row["display_time"] = value.strftime("%I:%M:%S %p %Z").lstrip("0")
    archives = StationLogArchive.query.filter_by(day=day).order_by(StationLogArchive.revision.desc()).all()
    sql = history.history_status()
    for key in ("last_success", "coverage_start"):
        if sql[key]:
            sql[key] = datetime.fromisoformat(sql[key])
    return render_template("station_log.html", day=day, rows=rows[(page-1)*100:page*100],
        total=len(rows), page=page, pages=max(1, (len(rows)+99)//100),
        airings=airings, archives=archives, sql=sql,
        csrf_token=session["station_log_csrf"])


@station_log_bp.get("/events")
@permission_required({"logs:view"})
def events():
    rows = _read_rows(_day())
    if request.args.get("order") == "desc":
        rows.reverse()
    offset = max(0, request.args.get("offset", 0, type=int))
    limit = max(1, min(500, request.args.get("limit", 100, type=int)))
    return jsonify(events=rows[offset:offset+limit], total=len(rows), sql=history.history_status())


@station_log_bp.get("/csv")
@permission_required({"logs:view"})
def download():
    day = _day()
    return Response(logs.csv_bytes(_read_rows(day)), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="station-log-{day}.csv"'})


@station_log_bp.get("/archives/<int:archive_id>/<variant>")
@permission_required({"logs:view"})
def archive_download(archive_id, variant):
    from flask import send_file
    archive = db.get_or_404(StationLogArchive, archive_id)
    if variant not in {"compiled", "sources"}:
        abort(404)
    path = Path(archive.csv_path if variant == "compiled" else archive.sources_path)
    mount = current_app.config.get("STATION_LOG_ARCHIVE_MOUNT")
    if (mount and not os.path.ismount(mount)) or not path.is_file():
        abort(503, description="NAS archive is unavailable; the current daily log is still readable")
    return send_file(path, as_attachment=True, download_name=path.name)


@station_log_bp.post("/airings")
@permission_required({"logs:edit"})
@_csrf
def review_airing():
    try:
        airing_id = request.form.get("airing_id", type=int)
        airing = db.get_or_404(StationLogAiring, airing_id) if airing_id else None
        logs.review_airing(airing=airing, show_name=request.form.get("show_name", ""),
            dj=request.form.get("dj", ""), starts_at=logs.local_to_utc(datetime.fromisoformat(request.form["starts_at"])),
            ends_at=logs.local_to_utc(datetime.fromisoformat(request.form["ends_at"])),
            status=request.form.get("status"), actor=_actor(), reason=request.form.get("reason", ""))
        flash("Airing saved. The daily log now reflects this review.", "success")
    except (ValueError, KeyError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("station_log.index", day=request.form.get("day")))


@station_log_bp.post("/note")
@permission_required({"logs:edit"})
@_csrf
def note():
    title = request.form.get("title", "").strip()
    if not title:
        abort(400, description="Enter a note or reported problem")
    severity = request.form.get("severity", "info")
    if severity not in {"info", "warning", "error", "critical"}:
        abort(400)
    occurred = None
    if request.form.get("occurred_at"):
        try:
            occurred = logs.local_to_utc(datetime.fromisoformat(request.form["occurred_at"]))
        except ValueError as exc:
            abort(400, description=str(exc))
    logs.emit_event("reported_problem" if severity != "info" else "operator_note", title,
        severity=severity, code=request.form.get("code", "")[:32], occurred_at=occurred,
        details={"actor": _actor()})
    return redirect(url_for("station_log.index", day=request.form.get("day")))


@station_log_bp.post("/import")
@permission_required({"logs:edit"})
@_csrf
def import_now():
    try:
        result = history.import_history(day=date.fromisoformat(request.form["day"]))
        flash(f"SQL import: {result['status']}; {result['imported']} records added.", "success")
    except (history.HistoryImportError, ValueError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("station_log.index", day=request.form.get("day")))


@station_log_bp.route("/connection", methods=["GET", "POST"])
@permission_required({"settings:edit"})
def connection():
    session.setdefault("station_log_csrf", secrets.token_urlsafe(32))
    if request.method == "POST":
        return _save_connection()
    return render_template("station_log_connection.html", csrf_token=session["station_log_csrf"],
                           sql=history.history_status())


@_csrf
def _save_connection():
    cfg = current_app.config
    try:
        port = int(request.form.get("port", "3306"))
        if not 1 <= port <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        zone = request.form.get("timezone", "America/New_York").strip()
        ZoneInfo(zone)
        source = request.form.get("source", "radiodj-main").strip()
        host, database = request.form.get("host", "").strip(), request.form.get("database", "radiodj").strip()
        if not source or len(source) > 128:
            raise ValueError("Choose a source name of 1–128 characters")
        if (host != cfg.get("RADIODJ_SQL_HOST", "") or database != cfg.get("RADIODJ_SQL_DATABASE", "radiodj")) and source == history.source_name():
            if db.session.get(StationLogImport, source):
                raise ValueError("Changing databases requires a new source name to preserve record identities")
        updates = dict(RADIODJ_SQL_ENABLED=request.form.get("enabled") == "1", RADIODJ_SQL_HOST=host,
            RADIODJ_SQL_PORT=port, RADIODJ_SQL_DATABASE=database,
            RADIODJ_SQL_USER=request.form.get("username", "").strip(),
            RADIODJ_SQL_TIMEZONE=zone, RADIODJ_SQL_SOURCE=source,
            STATION_LOG_ARCHIVE_DIR=request.form.get("archive_dir", "").strip() or None,
            STATION_LOG_ARCHIVE_MOUNT=request.form.get("archive_mount", "").strip() or None)
        if request.form.get("password"):
            updates["RADIODJ_SQL_PASSWORD"] = request.form["password"]
        path = Path(current_app.instance_path) / "user_config.json"
        with locked_state("station-log-config"):
            saved = json.loads(path.read_text()) if path.exists() else {}
            saved.update(updates)
            logs._atomic_bytes(path, logs._json_bytes(saved))
            os.chmod(path, 0o600)
        cfg.update(updates)
        logs.emit_event("station_log_settings_changed", "Station log connection/archive settings changed",
                        details={"actor": _actor(), "source": source})
        flash("Settings saved. Restart the RAMS service so all workers use the new settings.", "success")
    except (ValueError, OSError, KeyError) as exc:
        flash(str(exc), "danger")
    return redirect(url_for("station_log.connection"))


def register_station_log_cli(app):
    @app.cli.group("station-log")
    def group():
        """Import, reconcile and archive the permanent station log."""

    @group.command("init-db")
    def init_db():
        """Create only the new station-log tables (safe for WSGI safe-mode installs)."""
        from app.models import StationLogEvent, StationLogSubmission
        for model in (StationLogEvent, StationLogImport, StationLogAiring, StationLogSubmission, StationLogArchive):
            model.__table__.create(db.engine, checkfirst=True)
        click.echo("Station log tables ready.")

    @group.command("import-history")
    @click.option("--day", help="Station-local YYYY-MM-DD; omit for incremental capture")
    def import_history(day):
        try:
            result = history.import_history(day=date.fromisoformat(day) if day else None)
            logs.drain_events()
            click.echo(json.dumps(result))
        except (history.HistoryImportError, ValueError) as exc:
            raise click.ClickException(str(exc)) from None

    @group.command("archive")
    @click.option("--day", help="Export a particular day, or all days older than the review window")
    def archive(day):
        logs.drain_events()
        if day:
            result = logs.archive_day(date.fromisoformat(day))
            click.echo(result.csv_path if result else "Set STATION_LOG_ARCHIVE_DIR first")
        else:
            click.echo(f"Checked {logs.archive_due_days()} days")

    @group.command("reset-source")
    @click.option("--confirm", is_flag=True, required=True, help="Confirm a database restore/ID reset; preserves existing events")
    def reset(confirm):
        history.reset_generation()
        click.echo("New source generation created; old evidence retained.")

    @group.command("import-dj-csv")
    @click.argument("csv_path", type=click.Path(exists=True))
    @click.option("--start", required=True, help="Actual/proposed start as ISO timestamp; include offset for DST ambiguity")
    @click.option("--end", required=True)
    def import_dj_csv(csv_path, start, end):
        from app.services.log_export import read_log_csv
        entries = read_log_csv(csv_path)
        if not entries:
            raise click.ClickException("CSV has no playlist entries")
        submission = logs.save_submission(entries=entries, show_name=entries[0].show_name,
            dj=entries[0].dj, starts_at=logs.local_to_utc(datetime.fromisoformat(start)),
            ends_at=logs.local_to_utc(datetime.fromisoformat(end)), actor="operator CSV import",
            recording_file=str(Path(csv_path).with_suffix(".mp3")))
        click.echo(f"Imported revision {submission.revision}; review airing {submission.airing_id} before substitution.")
