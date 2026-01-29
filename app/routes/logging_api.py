from datetime import datetime, date, time
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, current_app
from app.models import DJ, Show, ShowRun
from app.utils import get_current_show, show_display_title, show_primary_host
from app.services.show_run_service import get_or_create_active_run, start_show_run
from app.services.log_export import (
    LogCsvEntry,
    build_recording_base_path,
    recording_csv_path,
    write_log_csv,
)
from app.services.recording_periods import recordings_period_root
from app.logger import init_logger
from app.auth_utils import admin_required

logs_bp = Blueprint("logs", __name__, url_prefix="/logs")
logger = init_logger()


def _parse_time(value: str) -> time | None:
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except Exception:
        return None


@logs_bp.route("/submit", methods=["GET", "POST"])
def submit_log():
    """
    Public log submission form (no auth required).
    """
    if request.method == "POST":
        dj_id = request.form.get("dj_id", type=int)
        show_id = request.form.get("show_id", type=int)
        show_run_id = request.form.get("show_run_id", type=int)
        show_name = request.form.get("show_name", "").strip()
        dj_first = request.form.get("dj_first_name", "").strip()
        dj_last = request.form.get("dj_last_name", "").strip()
        show_date_raw = request.form.get("show_date") or date.today().isoformat()
        selected_run = ShowRun.query.get(show_run_id) if show_run_id else None
        if show_run_id and not selected_run:
            flash("Selected airing could not be found. Please try again.", "danger")
            return redirect(url_for("logs.submit_log"))

        if selected_run:
            show_name = selected_run.show_name
            dj_first = selected_run.dj_first_name
            dj_last = selected_run.dj_last_name

        dj_obj = DJ.query.get(dj_id) if dj_id else None
        if dj_obj:
            dj_first = dj_obj.first_name
            dj_last = dj_obj.last_name

        if show_id:
            show_obj = Show.query.get(show_id)
            if show_obj:
                show_name = show_display_title(show_obj)
                if not dj_obj and show_obj.djs:
                    dj_first, dj_last = show_primary_host(show_obj)

        if not all([dj_first, dj_last, show_name]):
            flash("DJ and show selection are required.", "danger")
            return redirect(url_for("logs.submit_log"))

        if selected_run:
            show_date = selected_run.start_time.date()
        else:
            try:
                show_date = datetime.strptime(show_date_raw, "%Y-%m-%d").date()
            except ValueError:
                flash("Invalid show date.", "danger")
                return redirect(url_for("logs.submit_log"))

        if not selected_run:
            current_show = get_current_show()
            if current_show:
                get_or_create_active_run(
                    show_name=current_show.show_name or f"{current_show.host_first_name} {current_show.host_last_name}",
                    dj_first_name=current_show.host_first_name,
                    dj_last_name=current_show.host_last_name,
                )
            else:
                # Keep historical run linkage behavior for legacy reports.
                start_show_run(
                    dj_first_name=dj_first,
                    dj_last_name=dj_last,
                    show_name=show_name or "Unscheduled Show"
                )

        rows = request.form.getlist("row_index")
        csv_entries: list[LogCsvEntry] = []
        for idx in rows:
            entry_time = _parse_time(request.form.get(f"time_{idx}", "").strip())
            entry_type = request.form.get(f"type_{idx}")
            title = request.form.get(f"title_{idx}", "").strip() or ""
            artist = request.form.get(f"artist_{idx}", "").strip() or ""
            message = f"{entry_type} - {title}".strip(" -")
            csv_entries.append(
                LogCsvEntry(
                    show_date=show_date.isoformat(),
                    show_name=show_name,
                    dj=f"{dj_first} {dj_last}".strip(),
                    time=entry_time.strftime("%H:%M") if entry_time else "",
                    entry_type=entry_type or "",
                    title=title,
                    artist=artist if entry_type == "music" else "",
                    message=message,
                )
            )

        output_root = recordings_period_root()
        recording_base = build_recording_base_path(
            output_root=output_root,
            show_name=show_name,
            show_date=show_date,
            auto_create_show_folders=current_app.config.get("AUTO_CREATE_SHOW_FOLDERS", False),
        )
        recording_path = f"{recording_base}.mp3"
        csv_path = recording_csv_path(recording_path)
        write_log_csv(csv_path=csv_path, entries=csv_entries)
        logger.info("Log CSV written to %s with %s entries.", csv_path, len(csv_entries))
        flash("Log submitted successfully!", "success")
        logger.info("Log CSV saved with %s entries", len(rows))
        return redirect(url_for("logs.submit_log"))

    djs = DJ.query.order_by(DJ.last_name, DJ.first_name).all()
    shows = Show.query.order_by(Show.show_name).all()
    dj_payload = [
        {"id": d.id, "first_name": d.first_name, "last_name": d.last_name}
        for d in djs
    ]
    dj_ids_by_name = {(d.first_name, d.last_name): d.id for d in djs}

    def _show_host_ids(show: Show) -> list[int]:
        host_ids = {d.id for d in show.djs}
        primary_id = dj_ids_by_name.get((show.host_first_name, show.host_last_name))
        if primary_id:
            host_ids.add(primary_id)
        return sorted(host_ids)

    show_payload = [
        {
            "id": s.id,
            "name": s.show_name or f"{s.host_first_name} {s.host_last_name}",
            "schedule": f"{s.days_of_week} {s.start_time}-{s.end_time}",
            "hosts": _show_host_ids(s),
        }
        for s in shows
    ]
    show_runs = ShowRun.query.order_by(ShowRun.start_time.desc()).limit(60).all()
    show_run_payload = [
        {
            "id": run.id,
            "show_name": run.show_name,
            "dj_first_name": run.dj_first_name,
            "dj_last_name": run.dj_last_name,
            "start_time": run.start_time.isoformat(),
            "start_label": run.start_time.strftime("%Y-%m-%d %H:%M"),
        }
        for run in show_runs
    ]
    return render_template(
        "logs_submit.html",
        now=datetime.utcnow(),
        djs=dj_payload,
        shows=show_payload,
        show_runs=show_run_payload,
    )


@logs_bp.route("/manage")
@admin_required
def manage_logs():
    return redirect(url_for("main.recordings_manage"))


@logs_bp.route("/view")
@admin_required
def view_logs():
    return redirect(url_for("main.recordings_manage"))


@logs_bp.route("/download/csv")
@admin_required
def download_csv():
    return redirect(url_for("main.recordings_manage"))


@logs_bp.route("/download/show-run/csv")
def download_show_run_csv():
    return make_response("Run exports are not available in CSV-only logging mode.", 400)


@logs_bp.route("/download/docx")
@admin_required
def download_docx():
    return redirect(url_for("main.recordings_manage"))
