from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_file, abort, send_from_directory, make_response, jsonify, Response, stream_with_context
from io import BytesIO, StringIO
from dataclasses import dataclass
import mutagen  # type: ignore
from mutagen.id3 import ID3  # type: ignore
from mutagen.mp4 import MP4  # type: ignore
import ffmpeg
import json
import csv
import os
import subprocess
import secrets
import base64
import hashlib
import zipfile
import re
from tempfile import NamedTemporaryFile
from .scheduler import refresh_schedule, pause_shows_until, schedule_marathon_event, cancel_marathon_event
from .utils import (
    update_user_config,
    get_current_show,
    format_show_window,
    normalize_days_list,
    show_host_names,
    show_display_title,
    show_primary_host,
    next_show_occurrence,
    active_absence_for_show,
)
from datetime import datetime, time, timedelta, date
from .models import (
    db,
    Show,
    User,
    DJAbsence,
    SavedSearch,
    DJDisciplinary,
    DJ,
    LiveReadCard,
    ArchivistEntry,
    ArchivistRipResult,
    LogSheet,
    DJHandoffNote,
    Plugin,
    WebsiteContent,
    PodcastEpisode,
    MarathonEvent,
)
from app.plugins import ensure_plugin_record, plugin_display_name
from sqlalchemy import case, func, tuple_
from sqlalchemy.orm import load_only, selectinload
from .logger import init_logger
from app.auth_utils import (
    admin_required,
    login_required,
    permission_required,
    ROLE_PERMISSIONS,
    ALLOWED_ADMIN_ROLES,
    PERMISSION_GROUPS,
    PERMISSION_LOOKUP,
    effective_permissions,
)
from app.services.library.music_search import (
    search_music,
    get_track,
    load_cue,
    save_cue,
    update_metadata,
    build_library_editor_index,
)
from app.services.library.dj_library import (
    build_dj_library_index,
    search_dj_library,
    match_text_playlist,
    match_youtube_playlist,
)
from app.services.audit import audit_recordings, audit_explicit_music
from app.services.log_export import build_docx, read_log_csv, recording_csv_path
from app.services.recording_periods import (
    UNASSIGNED_PERIOD_LABEL,
    current_recording_period,
    load_recording_periods,
    save_recording_periods,
    period_folder_name,
    recordings_base_root,
)
from app.services.health import get_health_snapshot
from app.services.settings_backup import backup_settings, backup_data_snapshot
from app.services.live_reads import upsert_cards, card_query, chunk_cards
from app.services.archivist_db import import_archivist_csv, search_archivist
from app.oauth import init_oauth
from werkzeug.utils import secure_filename

main_bp = Blueprint('main', __name__)
logger = init_logger()
logger.info("Routes logger initialized.")

BASE_ROLE_CHOICES = [
    ("admin", "Admin"),
    ("manager", "Manager"),
    ("ops", "Ops"),
    ("viewer", "Viewer"),
]

def _role_choices():
    custom = current_app.config.get("CUSTOM_ROLES") or []
    choices = list(BASE_ROLE_CHOICES)
    for role in custom:
        choices.append((role, role.title()))
    return choices


@main_bp.app_context_processor
def inject_branding():
    def _resolve_station_background():
        background = current_app.config.get("STATION_BACKGROUND")
        if not background:
            return url_for("static", filename="first-bkg-variant.jpg")
        if isinstance(background, str) and background.startswith(("http://", "https://", "//")):
            return background
        return url_for("static", filename=background.lstrip("/"))

    return {
        "rams_name": "RAMS",
        "station_name": current_app.config.get("STATION_NAME", "WLMC"),
        "station_slogan": current_app.config.get("STATION_SLOGAN", ""),
        "station_background": _resolve_station_background(),
        "current_year": datetime.utcnow().year,
        "theme_default": current_app.config.get("THEME_DEFAULT", "system"),
        "inline_help_enabled": current_app.config.get("INLINE_HELP_ENABLED", True),
        "high_contrast_default": current_app.config.get("HIGH_CONTRAST_DEFAULT", False),
        "font_scale_percent": current_app.config.get("FONT_SCALE_PERCENT", 100),
        "session_permissions": effective_permissions(),
        "can": lambda perm: (perm == "*" or ("*" in effective_permissions()) or (perm in effective_permissions())),
    }

# noinspection PyTypeChecker
@main_bp.route('/shows')
@permission_required({"schedule:view", "schedule:edit"})
def shows():
    """Render the shows database page sorted and paginated."""

    day_order = case(
        (Show.days_of_week.like('mon%'), 1),
        (Show.days_of_week.like('tue%'), 2),
        (Show.days_of_week.like('wed%'), 3),
        (Show.days_of_week.like('thu%'), 4),
        (Show.days_of_week.like('fri%'), 5),
        (Show.days_of_week.like('sat%'), 6),
        (Show.days_of_week.like('sun%'), 7),
        else_=8,
    )

    page = request.args.get('page', 1, type=int)
    shows_column = Show.query.options(
        selectinload(Show.djs).load_only(DJ.id, DJ.first_name, DJ.last_name)
    ).order_by(
        day_order,
        Show.start_time,
        Show.start_date
    ).paginate(page=page, per_page=15)

    all_djs = (
        DJ.query.options(load_only(DJ.id, DJ.first_name, DJ.last_name))
        .order_by(DJ.first_name, DJ.last_name)
        .all()
    )
    logger.info("Rendering shows database page.")
    return render_template('shows_database.html', shows=shows_column, djs=all_djs)


def _psa_library_root():
    root = current_app.config.get("PSA_LIBRARY_PATH") or os.path.join(current_app.instance_path, "psa")
    os.makedirs(root, exist_ok=True)
    return root


def _imaging_library_root():
    root = current_app.config.get("IMAGING_LIBRARY_PATH") or os.path.join(current_app.instance_path, "imaging")
    os.makedirs(root, exist_ok=True)
    return root


def _media_roots() -> list[tuple[str, str]]:
    roots: list[tuple[str, str]] = [("PSA", _psa_library_root()), ("Imaging", _imaging_library_root())]
    music_root = current_app.config.get("NAS_MUSIC_ROOT")
    if music_root:
        os.makedirs(music_root, exist_ok=True)
        roots.append(("Music", music_root))
    assets_root = current_app.config.get("MEDIA_ASSETS_ROOT")
    if assets_root:
        os.makedirs(assets_root, exist_ok=True)
        roots.append(("Assets", assets_root))
    voice_root = current_app.config.get("VOICE_TRACKS_ROOT") or os.path.join(current_app.instance_path, "voice_tracks")
    os.makedirs(voice_root, exist_ok=True)
    roots.append(("Voice Tracks", voice_root))
    return roots


ALL_PERIODS_VALUE = "__all__"
DEFAULT_GROUP_BY = "both"
GROUP_BY_VALUES = {"show", "dj", "both"}


def _recordings_root() -> str:
    return recordings_base_root(create=False)


@dataclass
class RecordingEntry:
    show_name: str
    dj_name: str
    show_key: str
    dj_key: str
    filename: str
    full_path: str
    log_csv_path: str
    has_log: bool
    token: str
    size_bytes: int
    modified_at: datetime
    period_label: str


def _parse_recording_label(filename: str) -> str | None:
    match = re.match(r"(.+)[_-]\d{2}[-_]\d{2}[-_]\d{2,4}_RAWDATA", filename)
    if match:
        return match.group(1)
    return None


def _normalize_key(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().lower()


def _normalize_group_by(value: str | None) -> str:
    if value in GROUP_BY_VALUES:
        return value
    return DEFAULT_GROUP_BY


def _safe_folder_value(value: str | None) -> str:
    if not value:
        return "Unknown"
    return value.replace(" ", "_")


def _read_recording_tags(path: str) -> dict[str, str]:
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return {}
    if not audio or not getattr(audio, "tags", None):
        return {}

    def _first_value(key: str) -> str | None:
        if key not in audio.tags:
            return None
        value = audio.tags.get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        return str(value).strip() if value else None

    title = _first_value("title")
    artist = _first_value("artist")
    album = _first_value("album")
    payload = {}
    if title:
        payload["title"] = title
    if artist:
        payload["artist"] = artist
    if album:
        payload["album"] = album
    return payload


def _parse_id3_show_name(tags: dict[str, str]) -> str | None:
    title = tags.get("title")
    if title:
        match = re.match(r"(.+)\s+\(\d{2}-\d{2}-\d{4}\)$", title)
        if match:
            return match.group(1).strip()
        return title.strip()
    album = tags.get("album")
    if album:
        return album.strip()
    return None


def _path_parts(full: str, base_root: str) -> list[str]:
    rel = os.path.relpath(full, base_root)
    if rel in (".", ""):
        return []
    return rel.split(os.sep)


def _extract_recording_names(
    *,
    full: str,
    base_root: str,
    period_folders: set[str],
) -> tuple[str, str]:
    tags = _read_recording_tags(full)
    show_name = _parse_id3_show_name(tags)
    dj_name = (tags.get("artist") or "").strip() or None

    parts = _path_parts(full, base_root)
    period_folder = None
    remaining = parts
    if parts and parts[0] in period_folders:
        period_folder = parts[0]
        remaining = parts[1:]

    dj_folder = None
    show_folder = None
    if remaining:
        if len(remaining) > 1:
            dj_folder = remaining[0]
            if len(remaining) > 2 and remaining[1].lower() == "radio shows":
                show_folder = remaining[2] if len(remaining) > 3 else None
            elif len(remaining) > 2:
                show_folder = remaining[1]
        elif period_folder is None:
            dj_folder = remaining[0]

    if not dj_name and dj_folder:
        dj_name = dj_folder.replace("_", " ").strip()

    if not show_name and show_folder:
        show_name = show_folder.replace("_", " ").strip()

    if not show_name:
        filename = os.path.basename(full)
        safe_label = _parse_recording_label(filename) or os.path.splitext(filename)[0]
        show_name = safe_label.replace("_", " ").strip()
        if not dj_name and safe_label:
            dj_name = safe_label.replace("_", " ").strip()

    return show_name or "Unknown", dj_name or "Unknown"


def _period_folder_map(periods: list[str]) -> dict[str, str]:
    return {period_folder_name(period): period for period in periods}


def _period_label_for_path(path: str, base_root: str, period_map: dict[str, str]) -> str:
    rel = os.path.relpath(os.path.dirname(path), base_root)
    if rel == ".":
        return UNASSIGNED_PERIOD_LABEL
    folder = rel.split(os.sep, 1)[0]
    return period_map.get(folder, folder)


def _build_recording_entry(
    *,
    full: str,
    base_root: str,
    period_map: dict[str, str],
    period_folders: set[str],
    root: str,
) -> RecordingEntry:
    filename = os.path.basename(full)
    show_name, dj_name = _extract_recording_names(
        full=full,
        base_root=base_root,
        period_folders=period_folders,
    )
    stat = os.stat(full)
    log_path = recording_csv_path(full)
    has_log = os.path.isfile(log_path)
    return RecordingEntry(
        show_name=show_name,
        dj_name=dj_name,
        show_key=_normalize_key(show_name),
        dj_key=_normalize_key(dj_name),
        filename=filename,
        full_path=full,
        log_csv_path=log_path,
        has_log=has_log,
        token=base64.urlsafe_b64encode(full.encode("utf-8")).decode("utf-8"),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime),
        period_label=_period_label_for_path(full, base_root, period_map),
    )


def _collect_recordings(
    show: str | None = None,
    dj: str | None = None,
    period: str | None = None,
) -> list[RecordingEntry]:
    base_root = _recordings_root()
    periods = load_recording_periods().get("periods", [])
    period_map = _period_folder_map(periods)
    period_folders = set(period_map.keys())
    root = base_root
    if period and period != ALL_PERIODS_VALUE:
        root = os.path.join(base_root, period_folder_name(period))
        if not os.path.isdir(root):
            return []
    show_filter = _normalize_key(show)
    dj_filter = _normalize_key(dj)

    entries: list[RecordingEntry] = []
    for current_root, _, files in os.walk(root):
        for filename in files:
            if not filename.lower().endswith((".mp3", ".wav", ".m4a", ".aac")):
                continue
            full = os.path.join(current_root, filename)
            if (
                period
                and period != ALL_PERIODS_VALUE
                and not os.path.normcase(os.path.abspath(full)).startswith(os.path.normcase(os.path.abspath(root)))
            ):
                continue
            entry = _build_recording_entry(
                full=full,
                base_root=base_root,
                period_map=period_map,
                period_folders=period_folders,
                root=root,
            )
            if show_filter and entry.show_key != show_filter:
                continue
            if dj_filter and entry.dj_key != dj_filter:
                continue
            entries.append(entry)
    entries.sort(key=lambda item: item.modified_at, reverse=True)
    return entries


def _resolve_recording_path(token: str) -> str | None:
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return None
    full = os.path.normcase(os.path.abspath(os.path.normpath(decoded)))
    root = os.path.normcase(os.path.abspath(os.path.normpath(_recordings_root())))
    if not full.startswith(root) or not os.path.isfile(full):
        return None
    return full


@main_bp.route("/psa/player")
def psa_player():
    return redirect(url_for("main.show_automator"))


@main_bp.route("/dj/autodj")
def autodj_menu():
    resp = make_response(render_template("autodj_menu.html"))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@main_bp.route("/dj/tools")
def dj_tools():
    notes = DJHandoffNote.query.order_by(DJHandoffNote.created_at.desc()).limit(10).all()
    shows = [
        {"id": show.id, "display": show_display_title(show)}
        for show in Show.query.order_by(Show.show_name, Show.host_last_name).all()
    ]
    resp = make_response(render_template("dj_tools.html", notes=notes, shows=shows))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@main_bp.route("/dj/library")
def dj_library():
    resp = make_response(render_template("dj_library.html"))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@main_bp.route("/dj/library/data")
def dj_library_data():
    payload = build_dj_library_index()
    return jsonify(payload)


@main_bp.route("/dj/library/search")
def dj_library_search():
    query = (request.args.get("q") or "").strip()
    results = search_dj_library(query)
    return jsonify({"items": results})


def _dj_playlist_export_dir() -> str:
    export_dir = os.path.join(current_app.instance_path, "dj_playlists")
    os.makedirs(export_dir, exist_ok=True)
    return export_dir


@main_bp.route("/dj/library/import", methods=["POST"])
def dj_library_import_text():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    name = (payload.get("name") or "Imported Playlist").strip() or "Imported Playlist"
    matched = match_text_playlist(name, text)
    status = 200 if not matched.get("error") else 400
    return jsonify(matched), status


@main_bp.route("/dj/library/youtube", methods=["POST"])
def dj_library_youtube():
    payload = request.get_json(silent=True) or {}
    playlist_url = (payload.get("playlist_url") or "").strip()
    if not playlist_url:
        return jsonify({"error": "Please provide a YouTube playlist URL."}), 400
    matched = match_youtube_playlist(playlist_url)
    status = 200 if not matched.get("error") else 400
    return jsonify(matched), status


@main_bp.route("/dj/library/playlist/save", methods=["POST"])
def dj_library_playlist_save():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "rams_playlist").strip() or "rams_playlist"
    content = (payload.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Playlist content is required."}), 400
    safe_name = secure_filename(name) or "rams_playlist"
    filename = f"{safe_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
    export_dir = _dj_playlist_export_dir()
    target = os.path.join(export_dir, filename)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(content + "\n")
    return jsonify({"filename": filename, "download_url": url_for("main.dj_library_playlist_download", filename=filename)})


@main_bp.route("/dj/library/playlist/download/<path:filename>")
def dj_library_playlist_download(filename: str):
    export_dir = _dj_playlist_export_dir()
    return send_from_directory(export_dir, filename, mimetype="text/plain", as_attachment=True)


@main_bp.route("/dj/show-automator")
def show_automator():
    psa_root = _psa_library_root()
    imaging_root = _imaging_library_root()
    resp = make_response(render_template("show_automating_player.html", psa_root=psa_root, imaging_root=imaging_root))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@main_bp.route("/help/tutorial")
def tutorial_page():
    return render_template("tutorial.html")


@main_bp.route("/dj/handoff", methods=["GET", "POST"])
@login_required
def dj_handoff_notes():
    shows = [
        {"id": show.id, "display": show_display_title(show)}
        for show in Show.query.order_by(Show.show_name, Show.host_last_name).all()
    ]
    if request.method == "POST":
        notes = (request.form.get("notes") or "").strip()
        show_id = request.form.get("show_id", type=int)
        if not notes:
            flash("Please add a handoff note before saving.", "warning")
            redirect_to = request.form.get("redirect_to")
            if redirect_to and redirect_to.startswith("/") and "//" not in redirect_to:
                return redirect(redirect_to)
            return redirect(url_for("main.dj_handoff_notes"))
        show_name = None
        if show_id:
            show = Show.query.get(show_id)
            show_name = show_display_title(show) if show else None
        note = DJHandoffNote(
            author_name=session.get("display_name") or session.get("user_email") or "Staff",
            author_email=session.get("user_email"),
            show_name=show_name,
            notes=notes,
        )
        db.session.add(note)
        db.session.commit()
        flash("Handoff note saved.", "success")
        redirect_to = request.form.get("redirect_to")
        if redirect_to and redirect_to.startswith("/") and "//" not in redirect_to:
            return redirect(redirect_to)
        return redirect(url_for("main.dj_handoff_notes"))

    notes = DJHandoffNote.query.order_by(DJHandoffNote.created_at.desc()).limit(50).all()
    return render_template("dj_handoff.html", notes=notes, shows=shows)


@main_bp.route("/recordings")
@permission_required({"logs:view"})
def recordings_manage():
    show_filter = (request.args.get("show") or "").strip() or None
    dj_filter = (request.args.get("dj") or "").strip() or None
    period_param = request.args.get("period") or current_recording_period()
    group_by = _normalize_group_by(request.args.get("group_by"))
    periods_payload = load_recording_periods()
    periods = periods_payload.get("periods", [])
    if period_param not in periods and period_param != ALL_PERIODS_VALUE:
        period_param = current_recording_period()
    entries = _collect_recordings(show=show_filter, dj=dj_filter, period=period_param)
    available_entries = _collect_recordings(period=period_param)
    shows = sorted({entry.show_name for entry in available_entries})
    djs = sorted({entry.dj_name for entry in available_entries})
    return render_template(
        "recordings_manage.html",
        recordings=entries,
        shows=shows,
        djs=djs,
        selected_show=show_filter or "",
        selected_dj=dj_filter or "",
        selected_period=period_param,
        selected_group_by=group_by,
        periods=periods,
        all_periods_value=ALL_PERIODS_VALUE,
    )


@main_bp.route("/recordings/file/<path:token>")
@permission_required({"logs:view"})
def recordings_file(token: str):
    full = _resolve_recording_path(token)
    if not full:
        abort(404)
    resp = send_file(full, conditional=True)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@main_bp.route("/recordings/view/<path:token>")
@permission_required({"logs:view"})
def recordings_view(token: str):
    full = _resolve_recording_path(token)
    if not full:
        abort(404)
    csv_path = recording_csv_path(full)
    entries = read_log_csv(csv_path) if os.path.isfile(csv_path) else []
    recording_name = os.path.basename(full)
    return render_template(
        "recordings_view.html",
        entries=entries,
        recording_name=recording_name,
        token=token,
    )


@main_bp.route("/recordings/logs/view/<path:token>")
@permission_required({"logs:view"})
def recordings_log_view(token: str):
    return redirect(url_for("main.recordings_view", token=token))


@main_bp.route("/recordings/logs/download/csv/<path:token>")
@permission_required({"logs:view"})
def recordings_log_download_csv(token: str):
    full = _resolve_recording_path(token)
    if not full:
        abort(404)
    csv_path = recording_csv_path(full)
    if not os.path.isfile(csv_path):
        abort(404)
    return send_file(
        csv_path,
        as_attachment=True,
        download_name=f"{os.path.splitext(os.path.basename(full))[0]}.csv",
        mimetype="text/csv",
    )


@main_bp.route("/recordings/logs/download/docx/<path:token>")
@permission_required({"logs:view"})
def recordings_log_download_docx(token: str):
    full = _resolve_recording_path(token)
    if not full:
        abort(404)
    csv_path = recording_csv_path(full)
    if not os.path.isfile(csv_path):
        abort(404)
    entries = read_log_csv(csv_path)
    payload = build_docx(entries)
    resp = make_response(payload)
    resp.headers["Content-Disposition"] = f"attachment; filename={os.path.splitext(os.path.basename(full))[0]}.docx"
    resp.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return resp


@main_bp.route("/recordings/download", methods=["GET", "POST"])
@permission_required({"logs:view"})
def recordings_download():
    show_filter = (request.values.get("show") or "").strip() or None
    dj_filter = (request.values.get("dj") or "").strip() or None
    period_param = request.values.get("period") or current_recording_period()
    group_by = _normalize_group_by(request.values.get("group_by"))
    periods = load_recording_periods().get("periods", [])
    period_map = _period_folder_map(periods)
    period_folders = set(period_map.keys())
    if period_param not in periods and period_param != ALL_PERIODS_VALUE:
        period_param = current_recording_period()
    entries: list[RecordingEntry] = []
    if request.method == "POST":
        tokens = request.form.getlist("tokens")
        if not tokens:
            flash("Select at least one recording to download.", "warning")
            return redirect(url_for(
                "main.recordings_manage",
                show=show_filter,
                dj=dj_filter,
                period=period_param,
                group_by=group_by,
            ))
        base_root = _recordings_root()
        for token in tokens:
            full = _resolve_recording_path(token)
            if not full:
                continue
            entries.append(
                _build_recording_entry(
                    full=full,
                    base_root=base_root,
                    period_map=period_map,
                    period_folders=period_folders,
                    root=base_root,
                )
            )
    else:
        entries = _collect_recordings(show=show_filter, dj=dj_filter, period=period_param)
    if not entries:
        flash("No recordings found for that filter.", "warning")
        return redirect(url_for(
            "main.recordings_manage",
            show=show_filter,
            dj=dj_filter,
            period=period_param,
            group_by=group_by,
        ))
    label = "recordings"
    if show_filter:
        label = show_filter
    elif dj_filter:
        label = dj_filter

    tmp = NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = tmp.name
    tmp.close()

    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            period_folder = entry.period_label.replace(" ", "_")
            show_folder = _safe_folder_value(entry.show_name)
            dj_folder = _safe_folder_value(entry.dj_name)
            if group_by == "dj":
                arcname = os.path.join(period_folder, dj_folder, entry.filename)
            elif group_by == "show":
                arcname = os.path.join(period_folder, show_folder, entry.filename)
            else:
                arcname = os.path.join(period_folder, dj_folder, show_folder, entry.filename)
            archive.write(entry.full_path, arcname=arcname)
            if entry.has_log:
                if group_by == "dj":
                    log_arcname = os.path.join(period_folder, dj_folder, os.path.basename(entry.log_csv_path))
                elif group_by == "show":
                    log_arcname = os.path.join(period_folder, show_folder, os.path.basename(entry.log_csv_path))
                else:
                    log_arcname = os.path.join(
                        period_folder,
                        dj_folder,
                        show_folder,
                        os.path.basename(entry.log_csv_path),
                    )
                archive.write(entry.log_csv_path, arcname=log_arcname)

    response = send_file(
        tmp_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{label.replace(' ', '_')}_recordings.zip",
    )
    response.call_on_close(lambda: os.unlink(tmp_path))
    return response


@main_bp.route("/media/file/<path:token>")
def media_file(token: str):
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
    except Exception:
        abort(404)
    full = os.path.normcase(os.path.abspath(os.path.normpath(decoded)))
    allowed = False
    for _, root in _media_roots():
        root_abs = os.path.normcase(os.path.abspath(os.path.normpath(root)))
        if full.startswith(root_abs):
            allowed = True
            break
    if not allowed or not os.path.isfile(full):
        abort(404)
    resp = _send_audio(full)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@main_bp.route("/djs")
@admin_required
def list_djs():
        djs = (
            DJ.query.options(
                selectinload(DJ.shows).load_only(
                    Show.id,
                    Show.show_name,
                    Show.host_first_name,
                    Show.host_last_name,
                )
            )
            .order_by(DJ.last_name, DJ.first_name)
            .all()
        )
        name_pairs = {(dj.first_name, dj.last_name) for dj in djs}
        primary_shows = []
        if name_pairs:
            primary_shows = (
                Show.query.options(
                    load_only(
                        Show.id,
                        Show.show_name,
                        Show.host_first_name,
                        Show.host_last_name,
                    )
                )
                .filter(tuple_(Show.host_first_name, Show.host_last_name).in_(name_pairs))
                .all()
            )
        primary_by_name = {}
        for show in primary_shows:
            key = (show.host_first_name, show.host_last_name)
            primary_by_name.setdefault(key, []).append(show)

        for dj in djs:
            combined = []
            seen_ids = set()
            for show in list(dj.shows or []) + primary_by_name.get((dj.first_name, dj.last_name), []):
                if show.id in seen_ids:
                    continue
                combined.append(show)
                seen_ids.add(show.id)
            dj.display_shows = combined

        return render_template("djs_list.html", djs=djs)


@main_bp.route("/djs/<int:dj_id>")
@login_required
def dj_profile(dj_id: int):
        dj = DJ.query.get_or_404(dj_id)
        full_name = f"{dj.first_name} {dj.last_name}".strip()
        perms = set(session.get("permissions") or []) | ROLE_PERMISSIONS.get(session.get("role"), set())
        role = session.get("role")
        can_view_discipline = role in ALLOWED_ADMIN_ROLES or "dj:discipline" in perms or "*" in perms

        disciplinary = dj.disciplinary_records if can_view_discipline else []
        absences = DJAbsence.query.filter_by(dj_name=full_name).order_by(DJAbsence.start_time.desc()).all()
        log_sheets = (
            LogSheet.query.filter(
                LogSheet.dj_first_name == dj.first_name,
                LogSheet.dj_last_name == dj.last_name,
            )
            .order_by(LogSheet.show_date.desc())
            .all()
        )

        return render_template(
            "dj_profile.html",
            dj=dj,
            can_view_discipline=can_view_discipline,
            disciplinary=disciplinary,
            absences=absences,
            log_sheets=log_sheets,
            allowed_roles=sorted(ALLOWED_ADMIN_ROLES),
        )


@main_bp.route("/djs/discipline", methods=["GET", "POST"])
@permission_required({"dj:discipline"})
def manage_dj_discipline():
        djs = DJ.query.order_by(DJ.last_name, DJ.first_name).all()
        if request.method == "POST":
                action = request.form.get("action", "create")
                if action == "delete":
                        rec_id = request.form.get("record_id", type=int)
                        if rec_id:
                                rec = DJDisciplinary.query.get(rec_id)
                                if rec:
                                        db.session.delete(rec)
                                        db.session.commit()
                                        flash("Disciplinary record removed.", "success")
                        return redirect(url_for("main.manage_dj_discipline"))

                rec_id = request.form.get("record_id", type=int)
                dj_id = request.form.get("dj_id", type=int)
                severity = request.form.get("severity") or None
                notes = request.form.get("notes") or None
                action_taken = request.form.get("action_taken") or None
                resolved = bool(request.form.get("resolved"))

                if action == "update" and rec_id:
                        rec = DJDisciplinary.query.get(rec_id)
                        if rec:
                                rec.dj_id = dj_id or rec.dj_id
                                rec.severity = severity
                                rec.notes = notes
                                rec.action_taken = action_taken
                                rec.resolved = resolved
                                db.session.commit()
                                flash("Disciplinary record updated.", "success")
                        return redirect(url_for("main.manage_dj_discipline"))

                if dj_id:
                        rec = DJDisciplinary(
                                dj_id=dj_id,
                                severity=severity,
                                notes=notes,
                                action_taken=action_taken,
                                resolved=resolved,
                                created_by=session.get("display_name") or session.get("user_email"),
                        )
                        db.session.add(rec)
                        db.session.commit()
                        flash("Disciplinary record saved.", "success")
                return redirect(url_for("main.manage_dj_discipline"))

        records = DJDisciplinary.query.order_by(DJDisciplinary.issued_at.desc()).all()
        return render_template("dj_discipline.html", djs=djs, records=records)


@main_bp.route('/users', methods=['GET', 'POST'])
@permission_required({"users:manage"})
def manage_users():
        if request.method == 'POST':
                action = request.form.get('action', 'update')
                user_id = request.form.get('user_id', type=int)
                if not user_id:
                        flash("Invalid user selection.", "danger")
                        return redirect(url_for('main.manage_users'))

                user = User.query.get(user_id)
                if not user:
                        flash("User not found.", "danger")
                        return redirect(url_for('main.manage_users'))

                if action == 'delete':
                        db.session.delete(user)
                        db.session.commit()
                        flash("User removed.", "success")
                        return redirect(url_for('main.manage_users'))

                user.display_name = request.form.get('display_name') or user.display_name
                user.role = request.form.get('role') or None
                user.custom_role = request.form.get('custom_role') or None
                selected_perms = {p for p in request.form.getlist('permissions') if p}
                user.permissions = ",".join(sorted(selected_perms)) if selected_perms else None
                user.notification_email = request.form.get('notification_email') or None

                status = request.form.get('approval_status') or 'pending'
                if status == 'approved':
                        user.approved = True
                        user.rejected = False
                        user.approval_status = 'approved'
                        user.approved_at = datetime.utcnow()
                elif status == 'rejected':
                        user.approved = False
                        user.rejected = True
                        user.approval_status = 'rejected'
                        user.approved_at = None
                else:
                        user.approved = False
                        user.rejected = False
                        user.approval_status = 'pending'
                        user.approved_at = None

                db.session.commit()
                flash("User updated.", "success")
                return redirect(url_for('main.manage_users'))

        users = User.query.order_by(User.requested_at.desc()).all()
        return render_template(
                "users_manage.html",
                users=users,
                role_choices=_role_choices(),
                permission_groups=PERMISSION_GROUPS,
        )


@main_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
        user_id = session.get("user_id")
        user = User.query.get(user_id) if user_id else None
        if not user:
                abort(403, description="Requires login")

        if request.method == 'POST':
                user.notification_email = request.form.get('notification_email') or None
                db.session.commit()
                flash("Profile updated.", "success")
                return redirect(url_for('main.profile'))

        role = user.custom_role or user.role or "viewer"
        perms = set()
        if user.permissions:
                perms.update(p.strip() for p in (user.permissions or "").split(',') if p.strip())
        perms.update(ROLE_PERMISSIONS.get(role, set()))
        perms_display = sorted(perms) if perms else []

        return render_template(
                "profile.html",
                user=user,
                role=role,
                perms=perms_display,
        )


@main_bp.route("/djs/add", methods=["GET", "POST"])
@admin_required
def add_dj():
    from app.models import Show
    if request.method == "POST":
        upload_dir = current_app.config.get("DJ_PHOTO_UPLOAD_DIR") or os.path.join(current_app.instance_path, "dj_photos")
        os.makedirs(upload_dir, exist_ok=True)
        photo_url = request.form.get("photo_url") or None
        file = request.files.get("photo_file")
        if file and file.filename:
            fname = secure_filename(file.filename)
            stored_name = f"{secrets.token_hex(8)}_{fname}"
            file.save(os.path.join(upload_dir, stored_name))
            photo_url = url_for("main.dj_photo", filename=stored_name, _external=True)
        dj = DJ(
            first_name=request.form.get("first_name").strip(),
            last_name=request.form.get("last_name").strip(),
            bio=request.form.get("bio"),
            description=request.form.get("description"),
            photo_url=photo_url,
            is_public=bool(request.form.get("is_public")),
        )
        selected = request.form.getlist("show_ids")
        if selected:
            dj.shows = Show.query.filter(Show.id.in_(selected)).all()
        db.session.add(dj)
        db.session.commit()
        flash("DJ added.", "success")
        return redirect(url_for("main.list_djs"))

    shows = Show.query.order_by(Show.start_time).all()
    return render_template("dj_form.html", dj=None, shows=shows)


@main_bp.route("/djs/bulk-add", methods=["POST"])
@admin_required
def bulk_add_djs():
    raw_names = (request.form.get("bulk_names") or "").strip()
    if not raw_names:
        flash("Enter at least one DJ name to add.", "warning")
        return redirect(url_for("main.list_djs"))

    entries = [entry.strip() for entry in raw_names.split(",") if entry.strip()]
    created = []
    skipped = []
    for entry in entries:
        parts = entry.split(maxsplit=1)
        if len(parts) < 2:
            skipped.append(entry)
            continue
        first_name, last_name = parts[0].strip(), parts[1].strip()
        if not first_name or not last_name:
            skipped.append(entry)
            continue
        dj = DJ(first_name=first_name, last_name=last_name)
        db.session.add(dj)
        created.append(f"{first_name} {last_name}")

    if created:
        db.session.commit()
        flash(f"Added {len(created)} DJs.", "success")
    else:
        flash("No DJs were added. Provide first and last names separated by a space.", "warning")

    if skipped:
        flash(
            f"Skipped {len(skipped)} entries without a first and last name: {', '.join(skipped)}",
            "warning",
        )

    return redirect(url_for("main.list_djs"))


@main_bp.route("/shows/bulk-add", methods=["POST"])
@permission_required({"schedule:edit"})
def bulk_add_shows():
    raw_lines = (request.form.get("bulk_shows") or "").strip()
    csv_file = request.files.get("bulk_shows_csv")

    if not raw_lines and (not csv_file or not csv_file.filename):
        flash("Enter at least one show line or upload a CSV file.", "warning")
        return redirect(url_for("main.shows"))

    start_date = current_app.config["DEFAULT_START_DATE"]
    end_date = current_app.config["DEFAULT_END_DATE"]
    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()

    pattern = re.compile(
        r"^\s*(?P<name>[^/]+?)\s*/\s*(?P<start>\d{1,2}:\d{2})\s*[-–—]\s*(?P<end>\d{1,2}:\d{2})\s*/\s*(?P<day>M|T|W|TH|F|SA|SU|MON|MONDAY|TUE|TUES|TUESDAY|WED|WEDNESDAY|THU|THUR|THURSDAY|FRI|FRIDAY|SAT|SATURDAY|SUN|SUNDAY)\s*$",
        re.IGNORECASE,
    )
    day_map = {
        "M": "mon",
        "MON": "mon",
        "MONDAY": "mon",
        "T": "tue",
        "TUE": "tue",
        "TUES": "tue",
        "TUESDAY": "tue",
        "W": "wed",
        "WED": "wed",
        "WEDNESDAY": "wed",
        "TH": "thu",
        "THU": "thu",
        "THUR": "thu",
        "THURSDAY": "thu",
        "F": "fri",
        "FRI": "fri",
        "FRIDAY": "fri",
        "SA": "sat",
        "SAT": "sat",
        "SATURDAY": "sat",
        "SU": "sun",
        "SUN": "sun",
        "SUNDAY": "sun",
    }

    created = []
    skipped = []

    def _create_show(show_name: str, start_time_obj, end_time_obj, day_key: str) -> None:
        show = Show(
            host_first_name="",
            host_last_name="",
            show_name=show_name,
            genre=None,
            description=None,
            is_regular_host=True,
            start_date=start_date_obj,
            end_date=end_date_obj,
            start_time=start_time_obj,
            end_time=end_time_obj,
            days_of_week=normalize_days_list([day_key]),
        )
        db.session.add(show)
        created.append(show_name)

    for raw_line in raw_lines.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = pattern.match(line)
        if not match:
            skipped.append(raw_line)
            continue
        show_name = match.group("name").strip()
        day_key = day_map.get(match.group("day").upper())
        if not show_name or not day_key:
            skipped.append(raw_line)
            continue
        try:
            start_time_obj = datetime.strptime(match.group("start"), "%H:%M").time()
            end_time_obj = datetime.strptime(match.group("end"), "%H:%M").time()
        except ValueError:
            skipped.append(raw_line)
            continue
        _create_show(show_name, start_time_obj, end_time_obj, day_key)

    if csv_file and csv_file.filename:
        content = csv_file.read().decode("utf-8-sig")
        reader = csv.DictReader(StringIO(content))
        for row in reader:
            show_name = (row.get("ShowName") or "").strip()
            start_val = (row.get("StartTime") or "").strip()
            end_val = (row.get("EndTime") or "").strip()
            day_val = (row.get("DayOfWeek") or "").strip()
            if not show_name or not start_val or not end_val or not day_val:
                skipped.append(", ".join([show_name, start_val, end_val, day_val]).strip() or "CSV row")
                continue
            day_key = day_map.get(day_val.upper())
            if not day_key:
                skipped.append(", ".join([show_name, start_val, end_val, day_val]))
                continue
            try:
                start_time_obj = datetime.strptime(start_val, "%H:%M").time()
                end_time_obj = datetime.strptime(end_val, "%H:%M").time()
            except ValueError:
                skipped.append(", ".join([show_name, start_val, end_val, day_val]))
                continue
            _create_show(show_name, start_time_obj, end_time_obj, day_key)

    if created:
        db.session.commit()
        refresh_schedule()
        flash(f"Added {len(created)} shows.", "success")
    else:
        flash("No shows were added. Check the formatting and try again.", "warning")

    if skipped:
        flash(
            f"Skipped {len(skipped)} lines that did not match the required format.",
            "warning",
        )

    return redirect(url_for("main.shows"))


@main_bp.route("/djs/<int:dj_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_dj(dj_id):
    from app.models import Show
    dj = DJ.query.get_or_404(dj_id)
    if request.method == "POST":
        upload_dir = current_app.config.get("DJ_PHOTO_UPLOAD_DIR") or os.path.join(current_app.instance_path, "dj_photos")
        os.makedirs(upload_dir, exist_ok=True)
        photo_url = request.form.get("photo_url") or dj.photo_url
        file = request.files.get("photo_file")
        if file and file.filename:
            fname = secure_filename(file.filename)
            stored_name = f"{secrets.token_hex(8)}_{fname}"
            file.save(os.path.join(upload_dir, stored_name))
            photo_url = url_for("main.dj_photo", filename=stored_name, _external=True)
        dj.first_name = request.form.get("first_name").strip()
        dj.last_name = request.form.get("last_name").strip()
        dj.bio = request.form.get("bio")
        dj.description = request.form.get("description")
        dj.photo_url = photo_url
        dj.is_public = bool(request.form.get("is_public"))
        selected = request.form.getlist("show_ids")
        dj.shows = Show.query.filter(Show.id.in_(selected)).all() if selected else []
        db.session.commit()
        flash("DJ updated.", "success")
        return redirect(url_for("main.list_djs"))

    shows = Show.query.order_by(Show.start_time).all()
    return render_template("dj_form.html", dj=dj, shows=shows)


@main_bp.route("/dj/absence", methods=["GET", "POST"])
def dj_absence_submit():
        shows = Show.query.order_by(Show.show_name).all()
        djs = DJ.query.order_by(DJ.last_name, DJ.first_name).all()
        if request.method == "POST":
                dj_id = request.form.get("dj_id", type=int)
                show_id = request.form.get("show_id") or None
                show_name = request.form.get("show_name")
                dj_name = request.form.get("dj_name")
                replacement_id = request.form.get("replacement_id", type=int)
                replacement_name = None
                notes = request.form.get("notes") or None

                dj_obj = DJ.query.get(dj_id) if dj_id else None
                if dj_obj:
                        dj_name = f"{dj_obj.first_name} {dj_obj.last_name}"

                show_obj = Show.query.get(show_id) if show_id else None
                if show_obj:
                        show_name = show_obj.show_name or f"{show_obj.host_first_name} {show_obj.host_last_name}"

                if replacement_id:
                        rep_obj = DJ.query.get(replacement_id)
                        if rep_obj:
                                replacement_name = f"{rep_obj.first_name} {rep_obj.last_name}"

                if not all([dj_name, show_name, show_obj]):
                        flash("All fields except replacement/notes are required.", "danger")
                        return redirect(url_for("main.dj_absence_submit"))

                occurrence = next_show_occurrence(show_obj)
                if not occurrence:
                        flash("Could not determine the next scheduled slot for this show.", "danger")
                        return redirect(url_for("main.dj_absence_submit"))
                start_dt, end_dt = occurrence

                absence = DJAbsence(
                        dj_name=dj_name,
                        show_name=show_name,
                        show_id=int(show_id) if show_id else None,
                        start_time=start_dt,
                        end_time=end_dt,
                        replacement_name=replacement_name,
                        notes=notes,
                        status="pending",
                )
                db.session.add(absence)
                db.session.commit()
                flash("Absence submitted for approval.", "success")
                return redirect(url_for("main.dj_absence_submit"))

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
        return render_template("absence_submit.html", shows=show_payload, djs=dj_payload)


@main_bp.route("/absences", methods=["GET", "POST"])
@permission_required({"dj:absence"})
def manage_absences():
        djs = DJ.query.order_by(DJ.first_name.asc().nulls_last(), DJ.last_name.asc().nulls_last()).all()

        if request.method == "POST":
                abs_id = request.form.get("absence_id", type=int)
                status_choice = request.form.get("status") or "approved"
                replacement_id = request.form.get("replacement_id", type=int)
                absence = DJAbsence.query.get_or_404(abs_id)

                if status_choice in {"pending", "approved", "rejected", "resolved"}:
                        absence.status = status_choice

                if replacement_id:
                        sub = DJ.query.get(replacement_id)
                        if sub:
                                absence.replacement_name = f"{sub.first_name or ''} {sub.last_name or ''}".strip()
                elif request.form.get("replacement_clear"):
                        absence.replacement_name = None

                db.session.commit()
                flash("Absence updated.", "success")
                return redirect(url_for("main.manage_absences"))

        status_filter = request.args.get("status", "pending")
        base_query = DJAbsence.query
        if status_filter and status_filter != "all":
                base_query = base_query.filter(DJAbsence.status == status_filter)

        order_clause = [case((DJAbsence.status == "pending", 0), else_=1), DJAbsence.start_time]
        absences = base_query.order_by(*order_clause).all()

        counts = {
                "pending": DJAbsence.query.filter_by(status="pending").count(),
                "approved": DJAbsence.query.filter_by(status="approved").count(),
                "rejected": DJAbsence.query.filter_by(status="rejected").count(),
                "resolved": DJAbsence.query.filter_by(status="resolved").count(),
        }
        return render_template(
                "absence_manage.html", absences=absences, status_filter=status_filter, counts=counts, djs=djs
        )


@main_bp.route("/music/search")
@permission_required({"music:view"})
def music_search_page():
    email = session.get("user_email") or None
    saved = []
    if email:
        saved = (
            db.session.query(SavedSearch)
            .filter(
                (SavedSearch.created_by == email) | (SavedSearch.created_by.is_(None))
            )
            .order_by(SavedSearch.created_at.desc())
            .limit(25)
            .all()
        )
    return render_template("music_search.html", saved_searches=saved)


@main_bp.route("/music/library/editor")
@permission_required({"music:view"})
def library_editor_page():
    library_index = build_library_editor_index()
    return render_template("library_editor.html", library_index=library_index)


def _safe_music_path(path: str) -> str:
    root = current_app.config.get("NAS_MUSIC_ROOT") or ""
    if not root:
        abort(404)
    normalized = os.path.normcase(os.path.realpath(os.path.normpath(path)))
    root_norm = os.path.normcase(os.path.realpath(os.path.normpath(root)))
    if not normalized.startswith(root_norm):
        abort(403)
    if not os.path.exists(normalized):
        abort(404)
    return normalized


def _is_alac(path: str) -> bool:
    try:
        probe = ffmpeg.probe(path)
    except Exception:
        return False
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "audio" and stream.get("codec_name") == "alac":
            return True
    return False


def _transcode_cache_dir() -> str:
    cache_dir = os.path.join(current_app.instance_path, "transcodes")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _transcode_cache_path(path: str) -> str:
    stat = os.stat(path)
    key = f"{path}:{stat.st_mtime}:{stat.st_size}".encode("utf-8")
    digest = hashlib.sha1(key).hexdigest()
    return os.path.join(_transcode_cache_dir(), f"{digest}.mp3")


def _ensure_transcoded_mp3(path: str) -> str | None:
    if not current_app.config.get("TRANSCODE_ALAC_TO_MP3", True):
        return None
    if os.path.splitext(path)[1].lower() != ".m4a":
        return None
    if not _is_alac(path):
        return None
    target = _transcode_cache_path(path)
    if os.path.exists(target):
        return target
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                path,
                "-vn",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                target,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
        return None
    return target


def _send_audio(path: str):
    transcoded = _ensure_transcoded_mp3(path)
    if transcoded:
        return send_file(transcoded, mimetype="audio/mpeg", conditional=True)
    return send_file(path, conditional=True)


@main_bp.route("/music/stream")
@permission_required({"music:view"})
def music_stream():
    path = request.args.get("path")
    if not path:
        abort(404)
    safe_path = _safe_music_path(path)
    return _send_audio(safe_path)


@main_bp.route("/music/pretranscode", methods=["POST"])
@permission_required({"music:view"})
def music_pretranscode():
    payload = request.get_json(force=True, silent=True) or {}
    path = payload.get("path")
    if not path:
        return jsonify({"status": "error", "message": "path required"}), 400
    safe_path = _safe_music_path(path)
    transcoded = _ensure_transcoded_mp3(safe_path)
    return jsonify({"status": "ok", "transcoded": bool(transcoded)})


@main_bp.route("/music/cover")
@permission_required({"music:view"})
def music_cover():
    path = request.args.get("path")
    if not path:
        abort(404)
    safe_path = _safe_music_path(path)
    audio = mutagen.File(safe_path, easy=False)
    if not audio:
        abort(404)

    image_data = None
    mime = "image/jpeg"

    if isinstance(audio, MP4):
        covr = audio.tags.get("covr") if audio.tags else None
        if covr:
            image_data = bytes(covr[0])
            mime = "image/jpeg"
    else:
        try:
            id3 = ID3(safe_path)
            apic = id3.get("APIC:") or id3.get("APIC")
            if apic:
                image_data = apic.data
                mime = apic.mime or mime
        except Exception:
            if hasattr(audio, "pictures") and audio.pictures:
                pic = audio.pictures[0]
                image_data = pic.data
                mime = pic.mime or mime

    if not image_data:
        abort(404)
    return send_file(BytesIO(image_data), mimetype=mime, conditional=True)


@main_bp.route("/music/detail")
@permission_required({"music:view"})
def music_detail_page():
    path = request.args.get("path")
    track = get_track(path) if path else None
    if track:
        peaks = track.get("peaks")
        preview_peaks: list = []
        if isinstance(peaks, dict):
            preview_source = (
                peaks.get("mono")
                or peaks.get("left")
                or peaks.get("right")
                or []
            )
            if isinstance(preview_source, list):
                preview_peaks = preview_source
        elif isinstance(peaks, list):
            preview_peaks = peaks

        numeric_peaks: list = []
        for val in preview_peaks:
            try:
                numeric_peaks.append(float(val))
            except (TypeError, ValueError):
                continue
        track["peaks_preview"] = numeric_peaks
    return render_template("music_detail.html", track=track)


@main_bp.route("/music/edit", methods=["GET", "POST"])
@permission_required({"music:edit"})
def music_edit_page():
    path = request.values.get("path")
    if not path:
        return render_template("music_edit.html", track=None, error="Missing path")
    track = get_track(path)
    if request.method == "POST":
        if not track:
            return render_template("music_edit.html", track=None, error="Track not found")
        result = update_metadata(
            path,
            {field: request.form.get(field) for field in ["title", "artist", "album", "composer", "isrc", "year", "track", "disc", "copyright"]},
        )
        if result.get("status") != "ok":
            return render_template("music_edit.html", track=track, error=result.get("message") or "Unable to save metadata")
        track = get_track(path)
        flash("Metadata updated.", "success")
        return redirect(url_for("main.music_detail_page", path=path))
    return render_template("music_edit.html", track=track, error=None)


@main_bp.route("/music/cue", methods=["GET", "POST"])
@permission_required({"music:edit"})
def music_cue_page():
    path = request.values.get("path")
    if not path:
        return render_template("music_edit.html", track=None, error="Missing path for cue editor")
    track = get_track(path)
    try:
        safe_path = _safe_music_path(path)
    except Exception:
        safe_path = None
    if safe_path:
        _ensure_transcoded_mp3(safe_path)
    cue_obj = load_cue(path)
    cue = {
        "cue_in": cue_obj.cue_in if cue_obj else None,
        "intro": cue_obj.intro if cue_obj else None,
        "loop_in": cue_obj.loop_in if cue_obj else None,
        "loop_out": cue_obj.loop_out if cue_obj else None,
        "hook_in": cue_obj.hook_in if cue_obj else None,
        "hook_out": cue_obj.hook_out if cue_obj else None,
        "start_next": cue_obj.start_next if cue_obj else None,
        "outro": cue_obj.outro if cue_obj else None,
        "cue_out": cue_obj.cue_out if cue_obj else None,
    }
    if request.method == "POST":
        payload = {}
        for field in [
            "cue_in",
            "intro",
            "loop_in",
            "loop_out",
            "hook_in",
            "hook_out",
            "start_next",
            "outro",
            "cue_out",
        ]:
            raw = request.form.get(field)
            if raw is None or raw == "" or raw.lower() == "none":
                payload[field] = None
                continue
            try:
                payload[field] = float(raw)
            except ValueError:
                payload[field] = None
        cue_obj = save_cue(path, payload)
        cue.update(payload)
        flash("CUE points saved.", "success")
        return redirect(url_for("main.music_cue_page", path=path))
    return render_template("music_cue.html", track=track, cue=cue)


@main_bp.route("/audit", methods=["GET", "POST"])
@permission_required({"audit:run"})
def audit_page():
    recordings_results = None
    explicit_results = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "recordings":
            folder = request.form.get("recordings_folder") or None
            recordings_results = audit_recordings(folder)
        if action == "explicit":
            rate = float(request.form.get("rate_limit") or current_app.config["AUDIT_ITUNES_RATE_LIMIT_SECONDS"])
            limit = int(request.form.get("max_files") or current_app.config["AUDIT_MUSIC_MAX_FILES"])
            lyrics_check = request.form.get("lyrics_check") == "1"
            explicit_results = audit_explicit_music(rate_limit_s=rate, max_files=limit, lyrics_check=lyrics_check)
    return render_template(
        "audit.html",
        recordings_results=recordings_results,
        explicit_results=explicit_results,
        default_rate=current_app.config["AUDIT_ITUNES_RATE_LIMIT_SECONDS"],
        default_limit=current_app.config["AUDIT_MUSIC_MAX_FILES"],
        default_lyrics=current_app.config.get("AUDIT_LYRICS_CHECK_ENABLED", False),
    )


@main_bp.route("/production/live-reads", methods=["GET", "POST"])
@permission_required({"news:edit"})
def live_read_cards():
    """Create and manage live read cards for printing."""

    if request.method == "POST":
        titles = request.form.getlist("card_title[]")
        expiries = request.form.getlist("card_expiry[]")
        copies = request.form.getlist("card_copy[]")
        created = upsert_cards(titles, expiries, copies)
        if created:
            flash(f"Saved {created} card(s).", "success")
        else:
            flash("No cards were created. Please add a title or copy.", "warning")
        return redirect(url_for("main.live_read_cards"))

    include_expired = request.args.get("include_expired") == "1"
    cards = card_query(include_expired=include_expired).all()
    return render_template("live_reads.html", cards=cards, include_expired=include_expired, today=date.today())


@main_bp.route("/production/live-reads/print")
@permission_required({"news:edit"})
def live_read_cards_print():
    include_expired = request.args.get("include_expired") == "1"
    cards = card_query(include_expired=include_expired).all()
    return render_template(
        "live_reads_print.html",
        cards=cards,
        include_expired=include_expired,
        today=date.today(),
        chunks=list(chunk_cards(cards)),
    )


@main_bp.route("/production/live-reads/<int:card_id>/delete", methods=["POST"])
@admin_required
def delete_live_read(card_id: int):
    card = LiveReadCard.query.get_or_404(card_id)
    db.session.delete(card)
    db.session.commit()
    flash("Card deleted.", "info")
    return redirect(url_for("main.live_read_cards"))


@main_bp.route("/archivist")
@permission_required({"music:view"})
def archivist_page():
    query = (request.args.get("q") or "").strip()
    show_all = request.args.get("show_all") == "1" or query == "%"
    results = []
    if query or show_all:
        limit = 500 if show_all else 200
        results = search_archivist(query or "%", limit=limit)

    total = ArchivistEntry.query.count()
    raw_results = ArchivistRipResult.query.order_by(ArchivistRipResult.created_at.desc()).limit(10).all()
    rip_results = []
    for item in raw_results:
        segments = []
        if item.segments_json:
            try:
                segments = json.loads(item.segments_json)
            except json.JSONDecodeError:
                segments = []
        rip_results.append(
            {
                "id": item.id,
                "filename": item.filename,
                "duration_ms": item.duration_ms or 0,
                "settings_json": item.settings_json or "",
                "segments": segments,
                "track_count": len(segments),
            }
        )
    return render_template(
        "archivist.html",
        results=results,
        query=query,
        total=total,
        show_all=show_all,
        rip_results=rip_results,
    )


@main_bp.route("/marathon", methods=["GET", "POST"])
@permission_required({"schedule:marathon"})
def marathon_page():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        start_raw = request.form.get("start")
        end_raw = request.form.get("end")
        chunk_hours = int(request.form.get("chunk_hours") or 2)
        if not name or not start_raw or not end_raw:
            flash("Please provide name, start, and end.", "warning")
        else:
            try:
                start_dt = datetime.fromisoformat(start_raw)
                end_dt = datetime.fromisoformat(end_raw)
                schedule_marathon_event(name, start_dt, end_dt, chunk_hours=chunk_hours)
                flash("Marathon recording scheduled.", "success")
                return redirect(url_for("main.marathon_page"))
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to schedule marathon: %s", exc)
                flash("Could not schedule marathon. Check dates and try again.", "danger")

    events = MarathonEvent.query.order_by(MarathonEvent.start_time.desc()).all()
    now = datetime.utcnow()
    for ev in events:
        if ev.status == "pending" and ev.start_time <= now < ev.end_time and not ev.canceled_at:
            ev.status = "running"
    db.session.commit()

    return render_template("marathon.html", events=events, now=now)


@main_bp.route("/marathon/<int:event_id>/cancel", methods=["POST"])
@admin_required
def marathon_cancel(event_id: int):
    if cancel_marathon_event(event_id):
        flash("Marathon cancelled. Current chunk will finish, future chunks removed.", "info")
    else:
        flash("Marathon not found.", "warning")
    return redirect(url_for("main.marathon_page"))


@main_bp.route("/djs/photos/<path:filename>")
def dj_photo(filename: str):
    upload_dir = current_app.config.get("DJ_PHOTO_UPLOAD_DIR") or os.path.join(current_app.instance_path, "dj_photos")
    return send_from_directory(upload_dir, filename, as_attachment=False)


@main_bp.route('/show/add', methods=['GET', 'POST'])
@permission_required({"schedule:edit"})
def add_show():
    """Route to add a new show."""

    try:
        if request.method == 'POST':
            start_date = request.form['start_date'] or current_app.config['DEFAULT_START_DATE']
            end_date = request.form['end_date'] or current_app.config['DEFAULT_END_DATE']
            start_time = request.form['start_time']
            end_time = request.form['end_time']

            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            start_time_obj = datetime.strptime(start_time, '%H:%M').time()
            end_time_obj = datetime.strptime(end_time, '%H:%M').time()

            today = datetime.today().date()
            if end_date_obj < today:
                flash("End date cannot be in the past!", "danger")
                return redirect(url_for('main.add_show'))

            selected_days = request.form.getlist('days_of_week')
            normalized_days = normalize_days_list(selected_days)
            primary_dj_id = request.form.get('primary_dj_id', type=int)
            primary_dj = (
                DJ.query.options(load_only(DJ.id, DJ.first_name, DJ.last_name))
                .filter_by(id=primary_dj_id)
                .first()
            )
            if not primary_dj:
                flash("Select a primary DJ for this show.", "danger")
                return redirect(url_for('main.add_show'))
            selected_djs = request.form.getlist('dj_ids')
            cohost_ids = [dj_id for dj_id in selected_djs if str(dj_id) != str(primary_dj_id)]
            dj_objs = DJ.query.filter(DJ.id.in_(cohost_ids)).all() if cohost_ids else []

            show = Show(
                host_first_name=primary_dj.first_name,
                host_last_name=primary_dj.last_name,
                show_name=request.form.get('show_name'),
                genre=request.form.get('genre'),
                description=request.form.get('description'),
                is_regular_host='is_regular_host' in request.form,
                is_temporary='is_temporary' in request.form,
                start_date=start_date_obj,
                end_date=end_date_obj,
                start_time=start_time_obj,
                end_time=end_time_obj,
                days_of_week=normalized_days
            )
            show.djs = dj_objs
            db.session.add(show)
            db.session.commit()
            refresh_schedule()
            logger.info("Show added successfully.")
            flash("Show added successfully!", "success")
            return redirect(url_for('main.shows'))

        logger.info("Rendering add show page.")
        all_djs = (
            DJ.query.options(load_only(DJ.id, DJ.first_name, DJ.last_name))
            .order_by(DJ.first_name, DJ.last_name)
            .all()
        )
        return render_template('add_show.html', config=current_app.config, djs=all_djs)
    except Exception as e:
        logger.error(f"Error adding show: {e}")
        flash(f"Error adding show: {e}", "danger")
        return redirect(url_for('main.shows'))

@main_bp.route('/show/edit/<int:id>', methods=['GET', 'POST'])
@permission_required({"schedule:edit"})
def edit_show(id):
    """Route to edit an existing show."""

    show = Show.query.get_or_404(id)
    try:
        if request.method == 'POST':
            selected_days = request.form.getlist('days_of_week')
            normalized_days = normalize_days_list(selected_days)
            primary_dj_id = request.form.get('primary_dj_id', type=int)
            primary_dj = (
                DJ.query.options(load_only(DJ.id, DJ.first_name, DJ.last_name))
                .filter_by(id=primary_dj_id)
                .first()
            )
            if not primary_dj:
                flash("Select a primary DJ for this show.", "danger")
                return redirect(url_for('main.edit_show', id=id))
            selected_djs = request.form.getlist('dj_ids')
            cohost_ids = [dj_id for dj_id in selected_djs if str(dj_id) != str(primary_dj_id)]
            dj_objs = DJ.query.filter(DJ.id.in_(cohost_ids)).all() if cohost_ids else []
            show.show_name = request.form.get('show_name')
            show.genre = request.form.get('genre')
            show.description = request.form.get('description')
            show.is_regular_host = 'is_regular_host' in request.form
            show.is_temporary = 'is_temporary' in request.form
            show.host_first_name = primary_dj.first_name
            show.host_last_name = primary_dj.last_name
            show.start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d').date()
            show.end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d').date()
            show.start_time = datetime.strptime(request.form['start_time'].strip(), '%H:%M').time()
            show.end_time = datetime.strptime(request.form['end_time'].strip(), '%H:%M').time()
            show.days_of_week = normalized_days
            show.djs = dj_objs

            db.session.commit()
            refresh_schedule()
            logger.info("Show updated successfully.")
            flash("Show updated successfully!", "success")

            return redirect(url_for('main.shows'))

        logger.info(f'Rendering edit show page for show {id}.')
        all_djs = (
            DJ.query.options(load_only(DJ.id, DJ.first_name, DJ.last_name))
            .order_by(DJ.first_name, DJ.last_name)
            .all()
        )
        primary_dj = (
            DJ.query.options(load_only(DJ.id, DJ.first_name, DJ.last_name))
            .filter_by(first_name=show.host_first_name, last_name=show.host_last_name)
            .first()
        )
        selected_primary_id = primary_dj.id if primary_dj else None
        selected_ids = {dj.id for dj in show.djs}
        if selected_primary_id in selected_ids:
            selected_ids.discard(selected_primary_id)
        return render_template(
            'edit_show.html',
            show=show,
            djs=all_djs,
            selected_ids=selected_ids,
            selected_primary_id=selected_primary_id,
        )
    except Exception as e:
        logger.error(f"Error editing show: {e}")
        flash(f"Error editing show: {e}", "danger")
        return redirect(url_for('main.shows'))


@main_bp.route('/plugins')
@permission_required({"plugins:manage"})
def plugins_home():
    ensure_plugin_record("website_content")
    plugins = Plugin.query.order_by(Plugin.name.asc()).all()
    plugin_meta = current_app.config.get("PLUGIN_REGISTRY", {})
    return render_template(
        'plugins.html',
        plugins=plugins,
        plugin_labels=current_app.config.get("PLUGIN_DISPLAY_NAMES", {}),
        plugin_meta=plugin_meta,
    )


@main_bp.route('/plugins/<string:name>/toggle', methods=['POST'])
@admin_required
def toggle_plugin(name):
    plugin = ensure_plugin_record(name)
    plugin.enabled = not plugin.enabled
    db.session.commit()
    flash(f"Plugin '{name}' is now {'enabled' if plugin.enabled else 'disabled'}.", "success")
    return redirect(url_for('main.plugins_home'))

ALLOWED_SETTINGS_KEYS = [
    'ADMIN_USERNAME', 'ADMIN_PASSWORD', 'BIND_HOST', 'BIND_PORT', 'STREAM_URL', 'OUTPUT_FOLDER', 'DEFAULT_START_DATE', 'DEFAULT_END_DATE',
    'AUTO_CREATE_SHOW_FOLDERS', 'STATION_NAME', 'STATION_SLOGAN', 'STATION_BACKGROUND', 'TEMPEST_API_KEY',
    'TEMPEST_STATION_ID', 'ALERTS_ENABLED', 'ALERTS_DRY_RUN', 'ALERTS_DISCORD_WEBHOOK', 'ALERTS_EMAIL_ENABLED',
    'ALERTS_EMAIL_TO', 'ALERTS_EMAIL_FROM', 'ALERTS_SMTP_SERVER', 'ALERTS_SMTP_PORT', 'ALERTS_SMTP_USERNAME',
    'ALERTS_SMTP_PASSWORD', 'ALERT_DEAD_AIR_THRESHOLD_MINUTES', 'ALERT_STREAM_DOWN_THRESHOLD_MINUTES',
    'ALERT_REPEAT_MINUTES', 'OAUTH_CLIENT_ID', 'OAUTH_CLIENT_SECRET', 'OAUTH_ALLOWED_DOMAIN',
    'DISCORD_OAUTH_CLIENT_ID', 'DISCORD_OAUTH_CLIENT_SECRET', 'DISCORD_ALLOWED_GUILD_ID',
    'ICECAST_STATUS_URL', 'ICECAST_LISTCLIENTS_URL', 'ICECAST_USERNAME', 'ICECAST_PASSWORD', 'ICECAST_MOUNT', 'ICECAST_IGNORED_IPS', 'SELF_HEAL_ENABLED',
    'MUSICBRAINZ_USER_AGENT', 'ICECAST_ANALYTICS_INTERVAL_MINUTES', 'SETTINGS_BACKUP_INTERVAL_HOURS',
    'SETTINGS_BACKUP_RETENTION', 'DATA_BACKUP_DIRNAME', 'DATA_BACKUP_RETENTION_DAYS', 'THEME_DEFAULT', 'INLINE_HELP_ENABLED', 'ARCHIVIST_DB_PATH', 'ARCHIVIST_UPLOAD_DIR',
    'RATE_LIMIT_ENABLED', 'RATE_LIMIT_REQUESTS', 'RATE_LIMIT_WINDOW_SECONDS', 'RATE_LIMIT_TRUSTED_IPS',
    'HIGH_CONTRAST_DEFAULT', 'FONT_SCALE_PERCENT', 'PSA_LIBRARY_PATH', 'IMAGING_LIBRARY_PATH',
    'DATA_ROOT', 'NAS_MUSIC_ROOT', 'RADIODJ_API_BASE_URL', 'RADIODJ_API_PASSWORD'
]


def _clean_optional(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
        return None
    return value


@main_bp.route('/settings', methods=['GET', 'POST'])
@permission_required({"settings:edit"})
def settings():
    """Route to update the application settings."""

    if request.method == 'POST':
        try:
            if request.form.get("archivist_import") == "1":
                file = request.files.get("archivist_file")
                if not file or file.filename == "":
                    flash("Please choose a CSV/TSV file to import.", "warning")
                    return redirect(url_for('main.settings'))
                try:
                    upload_dir = current_app.config.get("ARCHIVIST_UPLOAD_DIR")
                    count = import_archivist_csv(
                        file,
                        current_app.config.get("ARCHIVIST_DB_PATH"),
                        upload_dir,
                    )
                    flash(f"Imported {count} rows into the archivist database.", "success")
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"Failed to import archivist data: {exc}")
                    flash("Import failed. Please check the file format.", "danger")
                return redirect(url_for('main.settings'))
            updated_settings = {
                'ADMIN_USERNAME': request.form['admin_username'],
                'ADMIN_PASSWORD': request.form['admin_password'],
                'BIND_HOST': request.form.get('bind_host', current_app.config.get('BIND_HOST', '127.0.0.1')).strip(),
                'BIND_PORT': int(request.form.get('bind_port') or current_app.config.get('BIND_PORT', 5000)),
                'STREAM_URL': request.form['stream_url'],
                'OUTPUT_FOLDER': request.form['output_folder'],
                'DEFAULT_START_DATE': request.form['default_start_date'],
                'DEFAULT_END_DATE': request.form['default_end_date'],
                'AUTO_CREATE_SHOW_FOLDERS': 'auto_create_show_folders' in request.form,
                'STATION_NAME': request.form['station_name'],
                'STATION_SLOGAN': request.form['station_slogan'],
                'STATION_BACKGROUND': request.form.get('station_background', '').strip(),
                'TEMPEST_API_KEY': _clean_optional(request.form.get('tempest_api_key', '').strip()),
                'TEMPEST_STATION_ID': int(request.form.get('tempest_station_id') or current_app.config.get('TEMPEST_STATION_ID', 118392)),
                'ALERTS_ENABLED': 'alerts_enabled' in request.form,
                'ALERTS_DRY_RUN': 'alerts_dry_run' in request.form,
                'ALERTS_DISCORD_WEBHOOK': _clean_optional(request.form.get('alerts_discord_webhook', '').strip()),
                'ALERTS_EMAIL_ENABLED': 'alerts_email_enabled' in request.form,
                'ALERTS_EMAIL_TO': _clean_optional(request.form.get('alerts_email_to', '').strip()),
                'ALERTS_EMAIL_FROM': _clean_optional(request.form.get('alerts_email_from', '').strip()),
                'ALERTS_SMTP_SERVER': _clean_optional(request.form.get('alerts_smtp_server', '').strip()),
                'ALERTS_SMTP_PORT': int(request.form.get('alerts_smtp_port') or current_app.config.get('ALERTS_SMTP_PORT', 587)),
                'ALERTS_SMTP_USERNAME': _clean_optional(request.form.get('alerts_smtp_username', '').strip()),
                'ALERTS_SMTP_PASSWORD': _clean_optional(request.form.get('alerts_smtp_password', '').strip()),
                'ALERT_DEAD_AIR_THRESHOLD_MINUTES': int(request.form.get('alert_dead_air_threshold_minutes') or current_app.config.get('ALERT_DEAD_AIR_THRESHOLD_MINUTES', 5)),
                'ALERT_STREAM_DOWN_THRESHOLD_MINUTES': int(request.form.get('alert_stream_down_threshold_minutes') or current_app.config.get('ALERT_STREAM_DOWN_THRESHOLD_MINUTES', 1)),
                'ALERT_REPEAT_MINUTES': int(request.form.get('alert_repeat_minutes') or current_app.config.get('ALERT_REPEAT_MINUTES', 15)),
                'ICECAST_STATUS_URL': _clean_optional(request.form.get('icecast_status_url', '').strip()),
                'ICECAST_LISTCLIENTS_URL': _clean_optional(request.form.get('icecast_listclients_url', '').strip()),
                'ICECAST_USERNAME': _clean_optional(request.form.get('icecast_username', '').strip()),
                'ICECAST_PASSWORD': _clean_optional(request.form.get('icecast_password', '').strip()),
                'ICECAST_MOUNT': _clean_optional(request.form.get('icecast_mount', '').strip()),
                'ICECAST_ANALYTICS_INTERVAL_MINUTES': int(request.form.get('icecast_analytics_interval_minutes') or current_app.config.get('ICECAST_ANALYTICS_INTERVAL_MINUTES', 5)),
                'ICECAST_IGNORED_IPS': [ip.strip() for ip in (request.form.get('icecast_ignored_ips', '') or '').split(',') if ip.strip()],
                'SELF_HEAL_ENABLED': 'self_heal_enabled' in request.form,
                'MUSICBRAINZ_USER_AGENT': _clean_optional(request.form.get('musicbrainz_user_agent', '').strip()),
                'RATE_LIMIT_ENABLED': 'rate_limit_enabled' in request.form,
                'RATE_LIMIT_REQUESTS': int(request.form.get('rate_limit_requests') or current_app.config.get('RATE_LIMIT_REQUESTS', 120)),
                'RATE_LIMIT_WINDOW_SECONDS': int(request.form.get('rate_limit_window_seconds') or current_app.config.get('RATE_LIMIT_WINDOW_SECONDS', 60)),
                'RATE_LIMIT_TRUSTED_IPS': [ip.strip() for ip in (request.form.get('rate_limit_trusted_ips', '') or '').split(',') if ip.strip()],
                'OAUTH_CLIENT_ID': _clean_optional(request.form.get('oauth_client_id', '').strip()),
                'OAUTH_CLIENT_SECRET': _clean_optional(request.form.get('oauth_client_secret', '').strip()),
                'OAUTH_ALLOWED_DOMAIN': _clean_optional(request.form.get('oauth_allowed_domain', '').strip()),
                'DISCORD_OAUTH_CLIENT_ID': _clean_optional(request.form.get('discord_oauth_client_id', '').strip()),
                'DISCORD_OAUTH_CLIENT_SECRET': _clean_optional(request.form.get('discord_oauth_client_secret', '').strip()),
                'DISCORD_ALLOWED_GUILD_ID': _clean_optional(request.form.get('discord_allowed_guild_id', '').strip()),
                'OAUTH_ONLY': 'oauth_only' in request.form,
                'CUSTOM_ROLES': [r.strip() for r in request.form.get('custom_roles', '').split(',') if r.strip()],
                'SETTINGS_BACKUP_INTERVAL_HOURS': int(request.form.get('settings_backup_interval_hours') or current_app.config.get('SETTINGS_BACKUP_INTERVAL_HOURS', 12)),
                'SETTINGS_BACKUP_RETENTION': int(request.form.get('settings_backup_retention') or current_app.config.get('SETTINGS_BACKUP_RETENTION', 10)),
                'DATA_BACKUP_DIRNAME': request.form.get('data_backup_dirname', current_app.config.get('DATA_BACKUP_DIRNAME', 'data_backups')).strip(),
                'DATA_BACKUP_RETENTION_DAYS': int(request.form.get('data_backup_retention_days') or current_app.config.get('DATA_BACKUP_RETENTION_DAYS', 60)),
                'THEME_DEFAULT': request.form.get('theme_default', current_app.config.get('THEME_DEFAULT', 'system')),
                'INLINE_HELP_ENABLED': 'inline_help_enabled' in request.form,
                'HIGH_CONTRAST_DEFAULT': 'high_contrast_default' in request.form,
                'FONT_SCALE_PERCENT': int(request.form.get('font_scale_percent') or current_app.config.get('FONT_SCALE_PERCENT', 100)),
                'ARCHIVIST_DB_PATH': request.form.get('archivist_db_path', current_app.config.get('ARCHIVIST_DB_PATH', '')).strip(),
                'ARCHIVIST_UPLOAD_DIR': request.form.get('archivist_upload_dir', current_app.config.get('ARCHIVIST_UPLOAD_DIR', '')).strip(),
                'PSA_LIBRARY_PATH': request.form.get('psa_library_path', current_app.config.get('PSA_LIBRARY_PATH', '')).strip(),
                'IMAGING_LIBRARY_PATH': request.form.get('imaging_library_path', current_app.config.get('IMAGING_LIBRARY_PATH', '')).strip(),
                'DATA_ROOT': _clean_optional(request.form.get('data_root', '').strip()),
                'NAS_MUSIC_ROOT': _clean_optional(request.form.get('music_library_path', '').strip()),
                'RADIODJ_API_BASE_URL': _clean_optional(request.form.get('radiodj_api_base_url', '').strip()),
                'RADIODJ_API_PASSWORD': _clean_optional(request.form.get('radiodj_api_password', '').strip()),
                'AUDIO_HOST_UPLOAD_DIR': request.form.get('audio_host_upload_dir', current_app.config.get('AUDIO_HOST_UPLOAD_DIR')).strip(),
                'AUDIO_HOST_BACKDROP_DEFAULT': request.form.get('audio_host_backdrop_default', current_app.config.get('AUDIO_HOST_BACKDROP_DEFAULT', '')).strip(),
            }

            update_user_config(updated_settings)
            recording_periods_raw = request.form.get("recording_periods", "")
            recording_periods = [line.strip() for line in recording_periods_raw.splitlines() if line.strip()]
            current_period = request.form.get("current_recording_period") or None
            save_recording_periods(periods=recording_periods, current=current_period)
            # Re-register OAuth providers with new credentials without restart
            try:
                init_oauth(current_app)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed to reinitialize OAuth after settings update: {exc}")

            flash("Settings updated successfully!", "success")
            return redirect(url_for('main.shows'))

        except Exception as e:
            logger.error(f"An error occurred while updating settings: {e}")
            flash(f"An error occurred while updating settings: {e}", "danger")
            return redirect(url_for('main.settings'))

    config = current_app.config
    settings_data = {
        'admin_username': config['ADMIN_USERNAME'],
        'admin_password': config['ADMIN_PASSWORD'],
        'bind_host': config.get('BIND_HOST', '127.0.0.1'),
        'bind_port': config.get('BIND_PORT', 5000),
        'stream_url': config['STREAM_URL'],
        'output_folder': config['OUTPUT_FOLDER'],
        'default_start_date': config['DEFAULT_START_DATE'],
        'default_end_date': config['DEFAULT_END_DATE'],
        'auto_create_show_folders': config['AUTO_CREATE_SHOW_FOLDERS'],
        'station_name': config.get('STATION_NAME', ''),
        'station_slogan': config.get('STATION_SLOGAN', ''),
        'station_background': config.get('STATION_BACKGROUND', ''),
        'tempest_api_key': config.get('TEMPEST_API_KEY', ''),
        'tempest_station_id': config.get('TEMPEST_STATION_ID', 118392),
        'alerts_enabled': config.get('ALERTS_ENABLED', False),
        'alerts_dry_run': config.get('ALERTS_DRY_RUN', True),
        'alerts_discord_webhook': config.get('ALERTS_DISCORD_WEBHOOK', ''),
        'alerts_email_enabled': config.get('ALERTS_EMAIL_ENABLED', False),
        'alerts_email_to': config.get('ALERTS_EMAIL_TO', ''),
        'alerts_email_from': config.get('ALERTS_EMAIL_FROM', ''),
        'alerts_smtp_server': config.get('ALERTS_SMTP_SERVER', ''),
        'alerts_smtp_port': config.get('ALERTS_SMTP_PORT', 587),
        'alerts_smtp_username': config.get('ALERTS_SMTP_USERNAME', ''),
        'alerts_smtp_password': config.get('ALERTS_SMTP_PASSWORD', ''),
        'alert_dead_air_threshold_minutes': config.get('ALERT_DEAD_AIR_THRESHOLD_MINUTES', 5),
        'alert_stream_down_threshold_minutes': config.get('ALERT_STREAM_DOWN_THRESHOLD_MINUTES', 1),
        'alert_repeat_minutes': config.get('ALERT_REPEAT_MINUTES', 15),
        'icecast_status_url': _clean_optional(config.get('ICECAST_STATUS_URL', '')) or '',
        'icecast_listclients_url': _clean_optional(config.get('ICECAST_LISTCLIENTS_URL', '')) or '',
        'icecast_username': _clean_optional(config.get('ICECAST_USERNAME', '')) or '',
        'icecast_password': _clean_optional(config.get('ICECAST_PASSWORD', '')) or '',
        'icecast_mount': _clean_optional(config.get('ICECAST_MOUNT', '')) or '',
        'icecast_analytics_interval_minutes': config.get('ICECAST_ANALYTICS_INTERVAL_MINUTES', 5),
        'icecast_ignored_ips': ", ".join(config.get('ICECAST_IGNORED_IPS', [])),
        'data_root': _clean_optional(config.get('DATA_ROOT', '')) or '',
        'music_library_path': _clean_optional(config.get('NAS_MUSIC_ROOT', '')) or '',
        'radiodj_api_base_url': _clean_optional(config.get('RADIODJ_API_BASE_URL', '')) or '',
        'radiodj_api_password': _clean_optional(config.get('RADIODJ_API_PASSWORD', '')) or '',
        'self_heal_enabled': config.get('SELF_HEAL_ENABLED', True),
        'musicbrainz_user_agent': _clean_optional(config.get('MUSICBRAINZ_USER_AGENT', '')) or '',
        'rate_limit_enabled': config.get('RATE_LIMIT_ENABLED', True),
        'rate_limit_requests': config.get('RATE_LIMIT_REQUESTS', 120),
        'rate_limit_window_seconds': config.get('RATE_LIMIT_WINDOW_SECONDS', 60),
        'rate_limit_trusted_ips': ", ".join(config.get('RATE_LIMIT_TRUSTED_IPS', [])),
        'oauth_client_id': _clean_optional(config.get('OAUTH_CLIENT_ID', '')) or '',
        'oauth_client_secret': _clean_optional(config.get('OAUTH_CLIENT_SECRET', '')) or '',
        'oauth_allowed_domain': _clean_optional(config.get('OAUTH_ALLOWED_DOMAIN', '')) or '',
        'discord_oauth_client_id': _clean_optional(config.get('DISCORD_OAUTH_CLIENT_ID', '')) or '',
        'discord_oauth_client_secret': _clean_optional(config.get('DISCORD_OAUTH_CLIENT_SECRET', '')) or '',
        'discord_allowed_guild_id': _clean_optional(config.get('DISCORD_ALLOWED_GUILD_ID', '')) or '',
        'oauth_only': config.get('OAUTH_ONLY', False),
        'custom_roles': ", ".join(config.get('CUSTOM_ROLES', [])),
        'settings_backup_interval_hours': config.get('SETTINGS_BACKUP_INTERVAL_HOURS', 12),
        'settings_backup_retention': config.get('SETTINGS_BACKUP_RETENTION', 10),
        'data_backup_dirname': config.get('DATA_BACKUP_DIRNAME', 'data_backups'),
        'data_backup_retention_days': config.get('DATA_BACKUP_RETENTION_DAYS', 60),
        'theme_default': config.get('THEME_DEFAULT', 'system'),
        'inline_help_enabled': config.get('INLINE_HELP_ENABLED', True),
        'high_contrast_default': config.get('HIGH_CONTRAST_DEFAULT', False),
        'font_scale_percent': config.get('FONT_SCALE_PERCENT', 100),
        'archivist_db_path': config.get('ARCHIVIST_DB_PATH', ''),
        'archivist_upload_dir': config.get('ARCHIVIST_UPLOAD_DIR', ''),
        'psa_library_path': config.get('PSA_LIBRARY_PATH', ''),
        'imaging_library_path': config.get('IMAGING_LIBRARY_PATH', ''),
        'pause_shows_recording': config.get('PAUSE_SHOWS_RECORDING', False),
        'audio_host_upload_dir': config.get('AUDIO_HOST_UPLOAD_DIR', ''),
        'audio_host_backdrop_default': config.get('AUDIO_HOST_BACKDROP_DEFAULT', ''),
        'recording_periods': load_recording_periods().get("periods", []),
        'current_recording_period': current_recording_period(),
    }

    logger.info(f'Rendering settings page.')
    return render_template('settings.html', **settings_data)


@main_bp.route('/settings/logs')
@admin_required
def view_system_log():
    logs_dir = current_app.config.get("LOGS_DIR") or os.path.join(current_app.instance_path, "logs")
    log_path = os.path.join(logs_dir, "ShowRecorder.log")
    entries = []
    error = None
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        if not os.path.exists(log_path):
            # ensure the log file exists so the viewer can open it without raising
            open(log_path, 'a', encoding='utf-8').close()
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as fh:
            raw_lines = fh.readlines()
        for line in raw_lines[-800:]:
            level = 'info'
            if ' - ERROR - ' in line:
                level = 'error'
            elif ' - WARNING - ' in line:
                level = 'warning'
            entries.append({'text': line.rstrip('\n'), 'level': level})
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    return render_template('log_file.html', log_entries=entries, log_error=error, log_path=log_path)


@main_bp.route('/settings/export', methods=['GET'])
@admin_required
def export_settings():
    """Export current settings as JSON for backup/transfer."""
    payload = {key: current_app.config.get(key) for key in ALLOWED_SETTINGS_KEYS}
    return current_app.response_class(
        json.dumps(payload, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename="rams-settings.json"'}
    )


@main_bp.route('/settings/import', methods=['POST'])
@admin_required
def import_settings():
    """Import settings from an uploaded JSON file."""
    file = request.files.get('settings_file')
    if not file or file.filename == '':
        flash('Please choose a settings JSON file to import.', 'warning')
        return redirect(url_for('main.settings'))

    try:
        data = json.load(file)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to parse settings JSON: {exc}")
        flash('Could not read the uploaded settings file. Please check the JSON.', 'danger')
        return redirect(url_for('main.settings'))

    filtered = {k: _clean_optional(v) if k in {
        'OAUTH_CLIENT_ID', 'OAUTH_CLIENT_SECRET', 'OAUTH_ALLOWED_DOMAIN',
        'DISCORD_OAUTH_CLIENT_ID', 'DISCORD_OAUTH_CLIENT_SECRET', 'DISCORD_ALLOWED_GUILD_ID',
        'TEMPEST_API_KEY', 'ALERTS_DISCORD_WEBHOOK', 'ALERTS_EMAIL_TO', 'ALERTS_EMAIL_FROM',
        'ALERTS_SMTP_SERVER', 'ALERTS_SMTP_USERNAME', 'ALERTS_SMTP_PASSWORD', 'STATION_BACKGROUND',
        'ICECAST_STATUS_URL', 'ICECAST_LISTCLIENTS_URL', 'ICECAST_USERNAME', 'ICECAST_PASSWORD', 'ICECAST_MOUNT', 'MUSICBRAINZ_USER_AGENT',
        'DATA_ROOT', 'NAS_MUSIC_ROOT', 'RADIODJ_API_BASE_URL', 'RADIODJ_API_PASSWORD'
    } else v for k, v in data.items() if k in ALLOWED_SETTINGS_KEYS}

    if not filtered:
        flash('No recognized settings were found in the uploaded file.', 'warning')
        return redirect(url_for('main.settings'))

    try:
        update_user_config(filtered)
        try:
            init_oauth(current_app)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to reinitialize OAuth after settings import: {exc}")
        flash('Settings imported successfully.', 'success')
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to import settings: {exc}")
        flash('Import failed. Please try again with a valid settings file.', 'danger')

    return redirect(url_for('main.settings'))


@main_bp.route('/settings/backup-now', methods=['POST'])
@admin_required
def backup_settings_now():
    dest = backup_settings()
    if dest:
        flash(f"Settings backed up to {dest}", "success")
    else:
        flash("No user_config.json found to back up.", "warning")
    return redirect(url_for('main.settings'))


@main_bp.route('/settings/backup-data', methods=['POST'])
@admin_required
def backup_data_now():
    dest = backup_data_snapshot()
    if dest:
        flash(f"Data backup written to {dest}", "success")
    else:
        flash("Data backup did not run; check logs for details.", "warning")
    return redirect(url_for('main.settings'))

@main_bp.route('/update_schedule', methods=['POST'])
@admin_required
def update_schedule():
    """Route to refresh the schedule."""

    try:
        refresh_schedule()
        logger.info("Schedule updated successfully.")
        flash("Schedule updated successfully!", "success")
        return redirect(url_for('main.shows'))
    except Exception as e:
        logger.error(f"Error updating schedule: {e}")
        flash(f"Error updating schedule: {e}", "danger")
        return redirect(url_for('main.shows'))

@main_bp.route('/show/delete/<int:id>', methods=['POST'])
@admin_required
def delete_show(id):
    """Route to delete a show."""

    try:
        show = Show.query.get_or_404(id)
        db.session.delete(show)
        db.session.commit()
        refresh_schedule()
        logger.info("Show deleted successfully.")
        flash("Show deleted successfully!", "success")
        return redirect(url_for('main.shows'))
    except Exception as e:
        logger.error(f"Error deleting show: {e}")
        flash(f"Error deleting show: {e}", "danger")
        return redirect(url_for('main.shows'))

@main_bp.route('/clear_all', methods=['POST'])
@admin_required
def clear_all():
    """Route to clear all shows."""

    try:
        db.session.query(Show).delete()
        db.session.commit()
        refresh_schedule()
        logger.info("All shows have been deleted.")
        flash("All shows have been deleted.", "info")
        return redirect(url_for('main.shows'))
    except Exception as e:
        logger.error(f"Error deleting shows: {e}")
        flash(f"Error deleting shows: {e}", "danger")
        return redirect(url_for('main.shows'))

@main_bp.route('/pause', methods=['POST'])
@admin_required
def pause():
    """Pause the recordings until the specified end date or indefinitely."""

    try:
        pause_end_date = request.form.get('pause_end_date')
        if pause_end_date:
            pause_end_date = datetime.strptime(pause_end_date, '%Y-%m-%d')
            pause_shows_until(pause_end_date)
            update_user_config({"PAUSE_SHOW_END_DATE": pause_end_date.strftime('%Y-%m-%d')})

        update_user_config({"PAUSE_SHOWS_RECORDING": True})

        flash(f"Recordings paused{' until ' + pause_end_date.strftime('%d-%m-%y') if pause_end_date else ' indefinitely'}.", "warning")
        logger.info(f"Recordings paused{' until ' + pause_end_date.strftime('%d-%m-%y') if pause_end_date else ' indefinitely'}.")
    except Exception as e:
        logger.error(f"Error pausing recordings: {e}")
        flash(f"Error pausing recordings: {e}", "danger")

    return redirect(url_for('main.settings'))

@main_bp.route('/resume', methods=['POST'])
@admin_required
def resume():
    """Resume the recordings."""

    try:
        update_user_config({"PAUSE_SHOWS_RECORDING": False, "PAUSE_SHOW_END_DATE": None})
        flash("Recordings resumed.", "success")
        logger.info("Recordings resumed.")
    except Exception as e:
        logger.error(f"Error resuming recordings: {e}")
        flash(f"Error resuming recordings: {e}", "danger")
  
    return redirect(url_for('main.settings'))
