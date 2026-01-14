from datetime import datetime, date, time
import json
import csv
import io
import zipfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, current_app
from app.models import db, LogEntry, LogSheet, DJ, Show, ShowRun
from app.utils import get_current_show, show_display_title, show_primary_host
from app.services.show_run_service import get_or_create_active_run, start_show_run
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
        show_name = request.form.get("show_name", "").strip()
        dj_first = request.form.get("dj_first_name", "").strip()
        dj_last = request.form.get("dj_last_name", "").strip()
        show_date_raw = request.form.get("show_date") or date.today().isoformat()

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

        try:
            show_date = datetime.strptime(show_date_raw, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid show date.", "danger")
            return redirect(url_for("logs.submit_log"))

        sheet = LogSheet(
            dj_first_name=dj_first,
            dj_last_name=dj_last,
            show_name=show_name,
            show_date=show_date,
        )
        db.session.add(sheet)

        current_show = get_current_show()
        show_run = None
        if current_show:
            show_run = get_or_create_active_run(
                show_name=current_show.show_name or f"{current_show.host_first_name} {current_show.host_last_name}",
                dj_first_name=current_show.host_first_name,
                dj_last_name=current_show.host_last_name,
            )
        else:
            # Fallback run to satisfy NOT NULL constraint on legacy DBs
            show_run = start_show_run(
                dj_first_name=dj_first,
                dj_last_name=dj_last,
                show_name=show_name or "Unscheduled Show"
            )

        rows = request.form.getlist("row_index")
        for idx in rows:
            entry_time = _parse_time(request.form.get(f"time_{idx}", "").strip())
            entry_type = request.form.get(f"type_{idx}")
            title = request.form.get(f"title_{idx}", "").strip() or None
            artist = request.form.get(f"artist_{idx}", "").strip() or None
            message = f"{entry_type} - {title or ''}".strip(" -")

            ts = datetime.combine(show_date, entry_time or datetime.now().time())

            entry = LogEntry(
                log_sheet=sheet,
                show_run_id=show_run.id if show_run else None,
                timestamp=ts,
                entry_time=entry_time,
                entry_type=entry_type,
                title=title,
                artist=artist if entry_type == "music" else None,
                message=message,
            )
            db.session.add(entry)

        db.session.commit()
        flash("Log submitted successfully!", "success")
        logger.info("Log sheet %s saved with %s entries", sheet.id, len(rows))
        return redirect(url_for("logs.submit_log"))

    djs = DJ.query.order_by(DJ.last_name, DJ.first_name).all()
    shows = Show.query.order_by(Show.show_name).all()
    dj_payload = [
        {"id": d.id, "first_name": d.first_name, "last_name": d.last_name}
        for d in djs
    ]
    show_payload = [
        {
            "id": s.id,
            "name": s.show_name or f"{s.host_first_name} {s.host_last_name}",
            "schedule": f"{s.days_of_week} {s.start_time}-{s.end_time}",
            "hosts": [d.id for d in s.djs],
        }
        for s in shows
    ]
    return render_template(
        "logs_submit.html",
        now=datetime.utcnow(),
        djs=dj_payload,
        shows=show_payload,
    )


@logs_bp.route("/manage")
@admin_required
def manage_logs():
    sheets = LogSheet.query.order_by(LogSheet.created_at.desc()).limit(200).all()
    return render_template("logs_manage.html", sheets=sheets)


def _entries_query(sheet_id=None):
    q = LogEntry.query.join(LogSheet, LogEntry.log_sheet_id == LogSheet.id)
    if sheet_id:
        q = q.filter(LogEntry.log_sheet_id == sheet_id)
    return q.order_by(LogSheet.show_date.desc(), LogEntry.timestamp.asc())


@logs_bp.route("/view")
@admin_required
def view_logs():
    sheet_id = request.args.get("sheet_id", type=int)
    entries = _entries_query(sheet_id).all()
    return render_template("logs_view.html", entries=entries, sheet_id=sheet_id)


@logs_bp.route("/download/csv")
@admin_required
def download_csv():
    sheet_id = request.args.get("sheet_id", type=int)
    entries = _entries_query(sheet_id).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Show Date", "Show Name", "DJ", "Time", "Type", "Title", "Artist"])
    for e in entries:
        writer.writerow([
            e.log_sheet.show_date.isoformat() if e.log_sheet else "",
            e.log_sheet.show_name if e.log_sheet else "",
            f"{e.log_sheet.dj_first_name} {e.log_sheet.dj_last_name}" if e.log_sheet else "",
            e.entry_time.strftime("%H:%M") if e.entry_time else "",
            e.entry_type or "",
            e.title or "",
            e.artist or "",
        ])
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = "attachment; filename=logs.csv"
    resp.headers["Content-Type"] = "text/csv"
    return resp


@logs_bp.route("/download/show-run/csv")
def download_show_run_csv():
    show_run_id = request.args.get("show_run_id", type=int)
    if not show_run_id:
        return make_response("show_run_id required", 400)
    show_run = ShowRun.query.get(show_run_id)
    entries = LogEntry.query.filter_by(show_run_id=show_run_id).order_by(LogEntry.timestamp.asc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Show Run ID",
        "Show Name",
        "DJ",
        "Timestamp",
        "Type",
        "Title",
        "Artist",
        "Message",
        "Duration",
        "Event",
        "Reason",
    ])
    show_name = show_run.show_name if show_run else ""
    dj_name = f"{show_run.dj_first_name} {show_run.dj_last_name}".strip() if show_run else ""
    for entry in entries:
        duration = ""
        event = ""
        reason = ""
        if entry.description:
            try:
                payload = json.loads(entry.description)
                duration = payload.get("duration", "")
                event = payload.get("event", "")
                reason = payload.get("reason", "")
            except json.JSONDecodeError:
                duration = ""
        writer.writerow([
            show_run_id,
            show_name,
            dj_name,
            entry.timestamp.isoformat(),
            entry.entry_type or "",
            entry.title or "",
            entry.artist or "",
            entry.message or "",
            duration,
            event,
            reason,
        ])
    resp = make_response(output.getvalue())
    resp.headers["Content-Disposition"] = f"attachment; filename=show-run-{show_run_id}.csv"
    resp.headers["Content-Type"] = "text/csv"
    return resp


def _build_docx(entries):
    """
    Build a minimal DOCX file (WordprocessingML) without external deps.
    """
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
 xmlns:v="urn:schemas-microsoft-com:vml"
 xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
 xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
 xmlns:w10="urn:schemas-microsoft-com:office:word"
 xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
 xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
 xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk"
 xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml"
 xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" mc:Ignorable="w14 wp14">
  <w:body>
    {rows}
    <w:sectPr/>
  </w:body>
</w:document>
"""
    row_template = """
    <w:p><w:r><w:t>{show_date} - {show_name} ({dj})</w:t></w:r></w:p>
    <w:p><w:r><w:t>{time} | {type} | {title} | {artist}</w:t></w:r></w:p>
    """
    rows_xml = ""
    for e in entries:
        rows_xml += row_template.format(
            show_date=e.log_sheet.show_date.isoformat() if e.log_sheet else "",
            show_name=e.log_sheet.show_name if e.log_sheet else "",
            dj=f"{e.log_sheet.dj_first_name} {e.log_sheet.dj_last_name}" if e.log_sheet else "",
            time=e.entry_time.strftime("%H:%M") if e.entry_time else "",
            type=e.entry_type or "",
            title=e.title or "",
            artist=e.artist or "",
        )

    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", document_xml.format(rows=rows_xml))
    mem.seek(0)
    return mem.getvalue()


@logs_bp.route("/download/docx")
@admin_required
def download_docx():
    sheet_id = request.args.get("sheet_id", type=int)
    entries = _entries_query(sheet_id).all()
    payload = _build_docx(entries)
    resp = make_response(payload)
    resp.headers["Content-Disposition"] = "attachment; filename=logs.docx"
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return resp
