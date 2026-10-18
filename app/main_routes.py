from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_file, abort, send_from_directory, make_response, jsonify, Response
from io import BytesIO, StringIO
from dataclasses import dataclass
import mutagen  # type: ignore
from mutagen.id3 import ID3  # type: ignore
from mutagen.mp4 import MP4  # type: ignore
import ffmpeg
import json
import csv
import math
import os
import subprocess
import secrets
import base64
import hashlib
import zipfile
import re
import threading
import tempfile
import weakref
from collections import deque
from tempfile import NamedTemporaryFile
from .scheduler import refresh_schedule, pause_shows_until, schedule_marathon_event, cancel_marathon_event, stop_active_show_recording
from .utils import (
    update_user_config,
    get_current_show,
    format_show_window,
    normalize_days_list,
    show_host_names,
    show_display_title,
    show_primary_host,
    next_show_occurrence,
    scheduled_window_for_date,
    get_config_timezone,
    localize_datetime,
    active_absence_for_show,
    normalize_timezone_name,
    UTC_OFFSET_TIMEZONE_OPTIONS,
    timezone_label,
)
from datetime import datetime, time, timedelta, date
from .models import (
    db,
    Show,
    User,
    DJAbsence,
    SavedSearch,
    DJDisciplinary,
    DJDisciplinaryAudit,
    DJDisciplinaryLink,
    DJ,
    LiveReadCard,
    ArchivistEntry,
    ArchivistRipResult,
    LogSheet,
    ShowRun,
    DJHandoffNote,
    Plugin,
    MarathonEvent,
    MusicAnalysis,
    DJRecordingAccessCode,
)
from app.plugins import ensure_plugin_record
from sqlalchemy import case, func, tuple_, or_
from sqlalchemy.orm import load_only, selectinload
from .logger import init_logger
from app.auth_utils import (
    admin_required,
    login_required,
    permission_required,
    ROLE_PERMISSIONS,
    ALLOWED_ADMIN_ROLES,
    PERMISSION_GROUPS,
    effective_permissions,
)
from app.services.library.music_search import (
    search_music,
    get_track,
    load_cue,
    save_cue,
    update_metadata,
    get_library_editor_index,
    _read_tags,
)
from app.services.library.dj_library import (
    build_dj_library_index,
    search_dj_library,
    match_text_playlist,
    match_youtube_playlist,
)
from app.services.library.media_library import decode_media_token, encode_media_token
from app.services.log_export import build_docx, read_log_csv, read_recording_metadata, recording_csv_path, write_recording_metadata
from app.services.airing_audit import build_audit, evidence_identity, recording_family, key as airing_key
from app.services.legacy_recordings import (
    discover_legacy_recordings,
    ignore_legacy_recording,
    legacy_recording_details,
    split_dj_names,
    write_legacy_sidecar,
)
from app.services.radiodj_client import RadioDJClient
from app.services.recording_periods import (
    UNASSIGNED_PERIOD_LABEL,
    current_recording_period,
    load_recording_periods,
    save_recording_periods,
    period_folder_name,
    recordings_base_root,
)
from app.services.health import get_health_snapshot, reset_health_counts
from app.services.system_resources import get_memory_status
from app.services.listener_analytics import peak_listeners_for_show
from app.services.dj_recording_access import (
    PORTAL_SESSION_KEY,
    authenticate as authenticate_dj_recording_code,
    create_access_code,
    current_access,
    login_is_blocked,
    logout as logout_dj_recording_portal,
)
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
        "dj_status_overnight_screensaver_enabled": current_app.config.get("DJ_STATUS_OVERNIGHT_SCREENSAVER_ENABLED", True),
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
        .filter(DJ.is_archived.is_(False))
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
RECORDINGS_PAGE_SIZE = 15
LEGACY_IMPORT_PAGE_SIZE = 25


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
    is_legacy: bool
    pdf_log_path: str
    flagged_missed: bool = False


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
    metadata = read_recording_metadata(full)
    show_name = (metadata.get("show_name") or "").strip() or None
    metadata_djs = metadata.get("dj_names") or metadata.get("hosts") or metadata.get("dj") or []
    if isinstance(metadata_djs, str):
        metadata_djs = split_dj_names(metadata_djs)
    dj_name = " & ".join(str(name).strip() for name in metadata_djs if str(name).strip()) or None

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

    if dj_folder and not dj_name:
        dj_name = dj_folder.replace("_", " ").strip()

    if show_folder and not show_name:
        show_name = show_folder.replace("_", " ").strip()

    if not show_name:
        filename = os.path.basename(full)
        safe_label = _parse_recording_label(filename) or os.path.splitext(filename)[0]
        show_name = safe_label.replace("_", " ").strip()
        if not dj_name and safe_label:
            dj_name = safe_label.replace("_", " ").strip()

    if not show_name or not dj_name:
        tags = _read_recording_tags(full)
        if not show_name:
            show_name = _parse_id3_show_name(tags)
        if not dj_name:
            dj_name = (tags.get("artist") or "").strip() or None

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
    metadata = read_recording_metadata(full)
    pdf_log_path = os.path.splitext(full)[0] + ".pdf"
    has_log = os.path.isfile(log_path)
    return RecordingEntry(
        show_name=show_name.encode("utf-8", "replace").decode("utf-8"),
        dj_name=dj_name.encode("utf-8", "replace").decode("utf-8"),
        show_key=_normalize_key(show_name),
        dj_key=_normalize_key(dj_name),
        filename=filename.encode("utf-8", "replace").decode("utf-8"),
        full_path=full,
        log_csv_path=log_path,
        has_log=has_log,
        token=encode_media_token(full),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime),
        period_label=_period_label_for_path(full, base_root, period_map).encode("utf-8", "replace").decode("utf-8"),
        is_legacy=bool(metadata.get("legacy_import")),
        pdf_log_path=pdf_log_path if os.path.isfile(pdf_log_path) else "",
        flagged_missed=metadata.get("flagged_missed") is True,
    )


def _collect_recordings(
    show: str | None = None,
    dj: str | None = None,
    period: str | None = None,
    include_missed: bool = False,
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
            if entry.flagged_missed and not include_missed:
                continue
            if show_filter and entry.show_key != show_filter:
                continue
            if dj_filter and entry.dj_key != dj_filter:
                continue
            entries.append(entry)
    entries.sort(key=lambda item: item.modified_at, reverse=True)
    return entries


def _resolve_recording_path(token: str) -> str | None:
    try:
        decoded = decode_media_token(token)
    except Exception:
        return None
    full = os.path.normcase(os.path.abspath(os.path.normpath(decoded)))
    root = os.path.normcase(os.path.abspath(os.path.normpath(_recordings_root())))
    try:
        within_root = os.path.commonpath([full, root]) == root
    except ValueError:
        within_root = False
    if not within_root or not os.path.isfile(full):
        return None
    return full


def _portal_response(response):
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _portal_show_keys(dj: DJ) -> set[str]:
    shows = list(dj.shows or [])
    primary = Show.query.filter_by(host_first_name=dj.first_name, host_last_name=dj.last_name).all()
    by_id = {show.id: show for show in shows + primary}
    return {
        _normalize_key(show.show_name or f"{show.host_first_name} {show.host_last_name}")
        for show in by_id.values()
    }


def _portal_can_access_entry(dj: DJ, entry: RecordingEntry, allowed_shows: set[str]) -> bool:
    if entry.is_legacy:
        # Legacy imports have host metadata but no corresponding schedule rows.
        # Match individual hosts exactly, including recordings with co-hosts.
        dj_key = _normalize_key(f"{dj.first_name} {dj.last_name}")
        return dj_key in {_normalize_key(name) for name in split_dj_names(entry.dj_name)}
    return entry.show_key in allowed_shows


def _portal_entries(access, *, show=None, period=None) -> list[RecordingEntry]:
    allowed = _portal_show_keys(access.dj)
    return [
        entry for entry in _collect_recordings(show=show, period=period)
        if _portal_can_access_entry(access.dj, entry, allowed)
    ]


def _portal_entry(access, token: str) -> RecordingEntry | None:
    full = _resolve_recording_path(token)
    if not full:
        return None
    periods = load_recording_periods().get("periods", [])
    period_map = _period_folder_map(periods)
    entry = _build_recording_entry(
        full=full,
        base_root=_recordings_root(),
        period_map=period_map,
        period_folders=set(period_map),
        root=_recordings_root(),
    )
    return entry if _portal_can_access_entry(access.dj, entry, _portal_show_keys(access.dj)) else None


def _require_portal_access():
    access = current_access()
    if not access:
        abort(403, description="Enter a valid DJ recording access code.")
    return access


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
    show_missed = request.args.get("show_missed") == "1"
    show_filter = (request.args.get("show") or "").strip() or None
    dj_filter = (request.args.get("dj") or "").strip() or None
    period_param = request.args.get("period") or current_recording_period()
    group_by = _normalize_group_by(request.args.get("group_by"))
    page = max(request.args.get("page", 1, type=int), 1)
    periods_payload = load_recording_periods()
    periods = periods_payload.get("periods", [])
    if period_param not in periods and period_param != ALL_PERIODS_VALUE:
        period_param = current_recording_period()
    entries = _collect_recordings(show=show_filter, dj=dj_filter, period=period_param, include_missed=show_missed)
    available_entries = _collect_recordings(period=period_param, include_missed=show_missed)
    shows = sorted({entry.show_name for entry in available_entries})
    djs = sorted({entry.dj_name for entry in available_entries})
    total_entries = len(entries)
    total_pages = max(math.ceil(total_entries / RECORDINGS_PAGE_SIZE), 1)
    if page > total_pages:
        page = total_pages
    page_start = (page - 1) * RECORDINGS_PAGE_SIZE
    page_entries = entries[page_start: page_start + RECORDINGS_PAGE_SIZE]
    return render_template(
        "recordings_manage.html",
        recordings=page_entries,
        show_missed=show_missed,
        shows=shows,
        djs=djs,
        selected_show=show_filter or "",
        selected_dj=dj_filter or "",
        selected_period=period_param,
        selected_group_by=group_by,
        current_page=page,
        total_pages=total_pages,
        page_size=RECORDINGS_PAGE_SIZE,
        total_entries=total_entries,
        periods=periods,
        all_periods_value=ALL_PERIODS_VALUE,
    )


@main_bp.route("/shows/audit")
@permission_required({"logs:view"})
def airing_audit():
    now = datetime.now(get_config_timezone()).replace(tzinfo=None)
    try:
        week = date.fromisoformat(request.args.get("week", now.date().isoformat()))
    except ValueError:
        abort(400, "Invalid week date")
    week -= timedelta(days=week.weekday())
    if week < date.min + timedelta(days=7) or week > date.max - timedelta(days=14):
        abort(400, "Week is out of range")
    report = build_audit(_recordings_root(), _collect_recordings(include_missed=True), week, now=now)
    return render_template("airing_audit.html", **report, week=week,
                           previous=week-timedelta(days=7), following=week+timedelta(days=7))


@main_bp.post("/recordings/missed/<path:token>")
@permission_required({"logs:edit"})
def recording_missed(token):
    full = _resolve_recording_path(token)
    if not full or not full.lower().endswith((".mp3", ".wav", ".m4a", ".aac")):
        abort(404)
    value = request.form.get("flagged_missed")
    if value not in {"0", "1"}:
        abort(400)
    now = datetime.now(get_config_timezone()).replace(tzinfo=None)
    rec = next((entry for entry in _collect_recordings(include_missed=True) if os.path.normcase(entry.full_path) == os.path.normcase(full)), None)
    if not rec:
        abort(404)
    identity = evidence_identity(full, fallback_show=rec.show_name)
    if not identity or identity[1] > now.date():
        abort(400, "A past airing date is required before marking a recording.")
    flagged = value == "1"
    # Apply to the full recovery family and other evidence for the same airing.
    paths = {full}
    for entry in _collect_recordings(include_missed=True):
        if (recording_family(entry.full_path) == recording_family(full)
                or evidence_identity(entry.full_path, fallback_show=entry.show_name) == identity):
            paths.add(entry.full_path)
    for directory, _, files in os.walk(_recordings_root()):
        for filename in files:
            if filename.lower().endswith(".csv"):
                path = os.path.join(directory, filename)
                try:
                    rows = read_log_csv(path)
                except (OSError, UnicodeError, ValueError, csv.Error):
                    continue
                if any((airing_key(row.show_name), row.show_date) == (identity[0], identity[1].isoformat()) for row in rows):
                    paths.add(path)
    for path in paths:
        metadata = read_recording_metadata(path)
        metadata.setdefault("show_name", rec.show_name)
        metadata.setdefault("show_date", identity[1].isoformat())
        metadata.setdefault("dj", rec.dj_name)
        metadata["flagged_missed"] = flagged
        metadata["missed_updated_at"] = datetime.utcnow().isoformat()
        write_recording_metadata(path, metadata)
    for run in ShowRun.query.all():
        if (airing_key(run.show_name), localize_datetime(run.start_time).date()) == identity:
            run.flagged_missed = flagged
    db.session.commit()
    flash("Show marked as Missed." if flagged else "Missed status cleared.", "success")
    return redirect(url_for("main.recordings_view", token=token))


@main_bp.route("/recordings/legacy-import", methods=["GET", "POST"])
@permission_required({"logs:edit"})
def legacy_recordings_import():
    periods = load_recording_periods().get("periods", [])
    selected_period = request.values.get("period") or (periods[0] if periods else "")
    if selected_period not in periods:
        selected_period = periods[0] if periods else ""
    period_root = os.path.join(_recordings_root(), period_folder_name(selected_period)) if selected_period else ""

    if request.method == "POST":
        if request.form.get("action") == "ignore":
            ignored_tokens = []
            for token in request.form.getlist("tokens"):
                full = _resolve_recording_path(token)
                if (not full or not period_root
                        or os.path.commonpath([os.path.abspath(full), os.path.abspath(period_root)]) != os.path.abspath(period_root)):
                    continue
                ignore_legacy_recording(full)
                ignored_tokens.append(token)
            if request.form.get("response_format") == "json":
                return jsonify({"ignored": len(ignored_tokens), "tokens": ignored_tokens})
            flash(f"Ignored {len(ignored_tokens)} file{'s' if len(ignored_tokens) != 1 else ''}. They will not appear in future legacy scans.", "success")
            return redirect(url_for("main.legacy_recordings_import", period=selected_period,
                                    page=request.form.get("page", 1, type=int)))

        imported = 0
        for token in request.form.getlist("tokens"):
            full = _resolve_recording_path(token)
            if not full or not period_root or os.path.commonpath([os.path.abspath(full), os.path.abspath(period_root)]) != os.path.abspath(period_root):
                continue
            show_name = (request.form.get(f"show_{token}") or "").strip()
            dj_names = split_dj_names(request.form.get(f"djs_{token}") or "")
            recorded_date = (request.form.get(f"date_{token}") or "").strip() or None
            write_legacy_sidecar(full, period=selected_period, show_name=show_name, dj_names=dj_names, recorded_date=recorded_date)
            for name in dj_names:
                pieces = name.split(None, 1)
                first_name, last_name = pieces[0], pieces[1] if len(pieces) > 1 else "(Legacy)"
                dj = DJ.query.filter(func.lower(DJ.first_name) == first_name.lower(), func.lower(DJ.last_name) == last_name.lower()).first()
                if not dj:
                    dj = DJ(first_name=first_name, last_name=last_name, is_archived=True)
                    db.session.add(dj)
                dj.period_list = [*dj.period_list, selected_period]
            imported += 1
        db.session.commit()
        flash(f"Imported {imported} legacy recording{'s' if imported != 1 else ''}.", "success")
        return redirect(url_for("main.legacy_recordings_import", period=selected_period))

    # Audio duration probes can be slow on a NAS. Keep the initial scan to cheap
    # directory/sidecar checks and load durations asynchronously for this page.
    suggestions = discover_legacy_recordings(period_root, include_duration=False) if period_root else []
    page = max(request.args.get("page", 1, type=int), 1)
    total_pages = max(math.ceil(len(suggestions) / LEGACY_IMPORT_PAGE_SIZE), 1)
    page = min(page, total_pages)
    start = (page - 1) * LEGACY_IMPORT_PAGE_SIZE
    rows = []
    for suggestion in suggestions[start:start + LEGACY_IMPORT_PAGE_SIZE]:
        rows.append((suggestion, encode_media_token(suggestion.path)))
    return render_template("legacy_recordings_import.html", rows=rows, periods=periods,
                           selected_period=selected_period, page=page, total_pages=total_pages,
                           total=len(suggestions))


@main_bp.get("/recordings/legacy-details/<path:token>")
@permission_required({"logs:edit"})
def legacy_recording_file_details(token: str):
    full = _resolve_recording_path(token)
    if not full:
        abort(404)
    return jsonify(legacy_recording_details(full))


@main_bp.post("/recordings/legacy-log/<path:token>")
@permission_required({"logs:edit"})
def legacy_recording_log_upload(token: str):
    full = _resolve_recording_path(token)
    upload = request.files.get("log")
    if not full or not upload or not upload.filename.lower().endswith(".pdf"):
        abort(400, description="Select a PDF log.")
    destination = os.path.splitext(full)[0] + ".pdf"
    upload.save(destination)
    metadata = read_recording_metadata(full)
    metadata["log_file"] = os.path.basename(destination)
    write_recording_metadata(full, metadata)
    flash("PDF log attached.", "success")
    return redirect(request.referrer or url_for("main.recordings_manage"))


@main_bp.get("/recordings/legacy-log/<path:token>")
@permission_required({"logs:view"})
def legacy_recording_log_download(token: str):
    full = _resolve_recording_path(token)
    pdf_path = os.path.splitext(full)[0] + ".pdf" if full else ""
    if not pdf_path or not os.path.isfile(pdf_path):
        abort(404)
    return send_file(pdf_path, as_attachment=True, download_name=os.path.basename(pdf_path), mimetype="application/pdf")


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
    metadata = read_recording_metadata(full)
    window_start = window_end = None
    try:
        window_start = datetime.fromisoformat(metadata["show_start"])
        window_end = datetime.fromisoformat(metadata["show_end"])
    except (KeyError, TypeError, ValueError):
        pass
    hour_counts = []
    peak_listeners = None
    if window_start and window_end:
        hours = max(1, math.ceil((window_end - window_start).total_seconds() / 3600))
        hour_counts = [0] * hours
        for entry in entries:
            if entry.entry_type not in {"psa", "live_read"} or not entry.time:
                continue
            try:
                occurred = datetime.combine(window_start.date(), datetime.strptime(entry.time, "%H:%M").time())
                if occurred < window_start:
                    occurred += timedelta(days=1)
                index = int((occurred - window_start).total_seconds() // 3600)
                if 0 <= index < hours:
                    hour_counts[index] += 1
            except ValueError:
                continue
        peak_listeners = peak_listeners_for_show(metadata.get("show_name", ""), window_start, window_end)
    compliance = {
        "has_log": bool(entries),
        "hour_counts": hour_counts,
        "compliant": bool(entries) and bool(hour_counts) and all(count >= 2 for count in hour_counts),
    }
    return render_template(
        "recordings_view.html",
        entries=entries,
        recording_name=recording_name,
        token=token,
        compliance=compliance,
        peak_listeners=peak_listeners,
        flagged_missed=metadata.get("flagged_missed") is True,
    )


@main_bp.post('/settings/health/reset')
@permission_required({"settings:edit"})
def reset_health_counters():
    count = reset_health_counts()
    flash(f"Reset self-heal and job-health counts for {count} jobs.", "success")
    return redirect(url_for("main.settings"))


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
        entries = _collect_recordings(show=show_filter, dj=dj_filter, period=period_param,
                                      include_missed=request.values.get("show_missed") == "1")
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


@main_bp.route("/dj-recordings", methods=["GET", "POST"])
def dj_recordings_login():
    if current_access():
        return redirect(url_for("main.dj_recordings_archive"))
    error = None
    status = 200
    if request.method == "POST":
        if login_is_blocked():
            error = "Too many attempts. Please wait 15 minutes and try again."
            status = 429
        elif authenticate_dj_recording_code((request.form.get("code") or "").strip()):
            return redirect(url_for("main.dj_recordings_archive"))
        else:
            error = "That code is invalid or is no longer active."
            status = 401
    response = make_response(render_template("dj_recordings_login.html", error=error), status)
    return _portal_response(response)


@main_bp.post("/dj-recordings/logout")
def dj_recordings_logout():
    logout_dj_recording_portal()
    return redirect(url_for("main.dj_recordings_login"))


@main_bp.route("/dj-recordings/archive")
def dj_recordings_archive():
    access = _require_portal_access()
    show_filter = (request.args.get("show") or "").strip() or None
    period = request.args.get("period") or current_recording_period()
    periods = load_recording_periods().get("periods", [])
    if period not in periods and period != ALL_PERIODS_VALUE:
        period = current_recording_period()
    available = _portal_entries(access, period=period)
    shows = sorted({entry.show_name for entry in available})
    entries = [entry for entry in available if not show_filter or entry.show_key == _normalize_key(show_filter)]
    page = max(request.args.get("page", 1, type=int), 1)
    total_pages = max(math.ceil(len(entries) / RECORDINGS_PAGE_SIZE), 1)
    page = min(page, total_pages)
    start = (page - 1) * RECORDINGS_PAGE_SIZE
    response = make_response(render_template(
        "dj_recordings_archive.html",
        access=access,
        recordings=entries[start:start + RECORDINGS_PAGE_SIZE],
        total_entries=len(entries),
        current_page=page,
        total_pages=total_pages,
        shows=shows,
        periods=periods,
        selected_show=show_filter or "",
        selected_period=period,
        all_periods_value=ALL_PERIODS_VALUE,
    ))
    return _portal_response(response)


@main_bp.route("/dj-recordings/audio/<path:token>")
def dj_recordings_audio(token: str):
    access = _require_portal_access()
    entry = _portal_entry(access, token)
    if not entry:
        abort(404)
    # Werkzeug's automatic ETag strictly encodes the filesystem path as UTF-8.
    # Portal responses are private/no-store; range requests still work without it.
    return _portal_response(send_file(entry.full_path, conditional=True, download_name=entry.filename, etag=False))


@main_bp.route("/dj-recordings/view/<path:token>")
def dj_recordings_view(token: str):
    access = _require_portal_access()
    entry = _portal_entry(access, token)
    if not entry:
        abort(404)
    log_entries = read_log_csv(entry.log_csv_path) if entry.has_log else []
    response = make_response(render_template(
        "dj_recordings_view.html", access=access, recording=entry, entries=log_entries
    ))
    return _portal_response(response)


@main_bp.route("/dj-recordings/log/<fmt>/<path:token>")
def dj_recordings_log_download(fmt: str, token: str):
    access = _require_portal_access()
    entry = _portal_entry(access, token)
    if not entry or not entry.has_log or fmt not in {"csv", "docx"}:
        abort(404)
    stem = os.path.splitext(entry.filename)[0]
    if fmt == "csv":
        return _portal_response(send_file(entry.log_csv_path, as_attachment=True, download_name=f"{stem}.csv", etag=False))
    response = make_response(build_docx(read_log_csv(entry.log_csv_path)))
    response.headers["Content-Disposition"] = f"attachment; filename={stem}.docx"
    response.headers["Content-Type"] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return _portal_response(response)


@main_bp.post("/dj-recordings/download")
def dj_recordings_download():
    access = _require_portal_access()
    if request.form.get("copyright_acknowledged") != "1":
        abort(400, description="Copyright acknowledgement is required.")
    tokens = request.form.getlist("tokens")
    entries = []
    if tokens:
        for token in tokens:
            entry = _portal_entry(access, token)
            if not entry:
                abort(404)
            entries.append(entry)
    else:
        show = (request.form.get("show") or "").strip() or None
        period = request.form.get("period") or current_recording_period()
        periods = load_recording_periods().get("periods", [])
        if period not in periods and period != ALL_PERIODS_VALUE:
            abort(400, description="Invalid recording period.")
        entries = _portal_entries(access, show=show, period=period)
    if not entries:
        abort(404)
    if len(entries) == 1 and request.form.get("single") == "1":
        response = send_file(entries[0].full_path, as_attachment=True, download_name=entries[0].filename, etag=False)
        return _portal_response(response)

    tmp = NamedTemporaryFile(delete=False, suffix=".zip")
    tmp_path = tmp.name
    tmp.close()
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            folder = _safe_folder_value(entry.show_name)
            archive.write(entry.full_path, arcname=os.path.join(folder, entry.filename))
            if entry.has_log:
                archive.write(entry.log_csv_path, arcname=os.path.join(folder, os.path.basename(entry.log_csv_path)))
    response = send_file(tmp_path, mimetype="application/zip", as_attachment=True, download_name="my_radio_shows.zip")
    response.call_on_close(lambda: os.unlink(tmp_path))
    return _portal_response(response)


@main_bp.route("/media/file/<path:token>")
def media_file(token: str):
    try:
        decoded = decode_media_token(token)
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
        show_archived = request.args.get("show_archived") == "1"
        query = DJ.query
        if not show_archived:
            query = query.filter(DJ.is_archived.is_(False))
        djs = (
            query.options(
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

        return render_template("djs_list.html", djs=djs, show_archived=show_archived)


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

        can_manage_access = role in ALLOWED_ADMIN_ROLES or "*" in perms or "dj:manage" in perms
        access_codes = (
            DJRecordingAccessCode.query.filter_by(dj_id=dj.id)
            .order_by(DJRecordingAccessCode.created_at.desc()).all()
            if can_manage_access else []
        )
        return render_template(
            "dj_profile.html",
            dj=dj,
            can_view_discipline=can_view_discipline,
            disciplinary=disciplinary,
            absences=absences,
            log_sheets=log_sheets,
            allowed_roles=sorted(ALLOWED_ADMIN_ROLES),
            can_manage_access=can_manage_access,
            access_codes=access_codes,
            now=datetime.utcnow(),
        )


@main_bp.post("/djs/<int:dj_id>/recording-access")
@permission_required({"dj:manage"})
def create_dj_recording_access(dj_id: int):
    dj = DJ.query.get_or_404(dj_id)
    expiry = request.form.get("expiry", "30_days")
    expires_at = None
    if expiry == "30_days":
        expires_at = datetime.utcnow() + timedelta(days=30)
    elif expiry == "custom":
        try:
            expires_at = datetime.strptime(request.form.get("expires_at") or "", "%Y-%m-%d") + timedelta(days=1)
        except ValueError:
            flash("Choose a valid custom expiration date.", "danger")
            return redirect(url_for("main.dj_profile", dj_id=dj.id))
        if expires_at <= datetime.utcnow():
            flash("The expiration date must be in the future.", "danger")
            return redirect(url_for("main.dj_profile", dj_id=dj.id))
    elif expiry != "never":
        abort(400)
    try:
        _, code = create_access_code(dj.id, expires_at=expires_at, indefinite=expiry == "never")
    except RuntimeError as exc:
        flash(str(exc), "danger")
    else:
        portal_url = url_for("main.dj_recordings_login", _external=True)
        flash(
            f"Recording access code for {dj.first_name} {dj.last_name}: {code}. "
            f"Portal: {portal_url}. This code will only be shown once.",
            "success",
        )
    return redirect(url_for("main.dj_profile", dj_id=dj.id))


@main_bp.post("/djs/<int:dj_id>/recording-access/<int:code_id>/revoke")
@permission_required({"dj:manage"})
def revoke_dj_recording_access(dj_id: int, code_id: int):
    DJ.query.get_or_404(dj_id)
    record = DJRecordingAccessCode.query.filter_by(id=code_id, dj_id=dj_id).first_or_404()
    if record.revoked_at is None:
        record.revoked_at = datetime.utcnow()
        db.session.commit()
        if session.get(PORTAL_SESSION_KEY) == record.id:
            session.pop(PORTAL_SESSION_KEY, None)
        flash("Recording access code revoked.", "success")
    return redirect(url_for("main.dj_profile", dj_id=dj_id))


DISCIPLINE_PUNISHMENTS = {
    "informal": {"label": "Informal / Information", "prefix": "I"},
    "warning": {"label": "Warning", "prefix": "W"},
    "probation": {"label": "Probation", "prefix": "P"},
    "suspension": {"label": "Suspension", "prefix": "S"},
    "removal": {"label": "Removal", "prefix": "R"},
    "custom": {"label": "Custom Action", "prefix": "C"},
}
DISCIPLINE_SEVERITIES = ["info", "low", "moderate", "serious", "critical"]


def _discipline_actor():
        return session.get("display_name") or session.get("user_email") or session.get("username") or "system"


def _has_discipline_approval_permission():
        return "dj:discipline:approve" in set(session.get("permissions") or [])


def _can_discipline(permission):
        perms = effective_permissions()
        return "*" in perms or "dj:discipline" in perms or permission in perms


def _parse_date_field(name):
        value = (request.form.get(name) or "").strip()
        if not value:
                return None
        try:
                return datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
                return None


def _discipline_requires_approval(punishment_type):
        required = set(current_app.config.get("DISCIPLINE_APPROVAL_REQUIRED_TYPES", ["probation", "suspension", "removal"]) or [])
        if current_app.config.get("DISCIPLINE_CUSTOM_REQUIRES_APPROVAL", False):
                required.add("custom")
        return punishment_type in required


def _next_discipline_case_number(punishment_type):
        prefix = DISCIPLINE_PUNISHMENTS.get(punishment_type, DISCIPLINE_PUNISHMENTS["custom"])["prefix"]
        existing = DJDisciplinary.query.filter(DJDisciplinary.case_number.like(f"{prefix}%")).all()
        max_num = 0
        for rec in existing:
                suffix = (rec.case_number or "")[len(prefix):]
                if suffix.isdigit():
                        max_num = max(max_num, int(suffix))
        return f"{prefix}{max_num + 1}"


def _audit_discipline(rec, event_type, message=None):
        db.session.add(DJDisciplinaryAudit(record=rec, event_type=event_type, actor=_discipline_actor(), message=message))


def _sync_discipline_links(rec):
        DJDisciplinaryLink.query.filter_by(record_id=rec.id).delete(synchronize_session=False)
        show_id = request.form.get("linked_show_id", type=int)
        log_id = request.form.get("linked_log_id", type=int)
        manual = (request.form.get("manual_reference") or "").strip()
        if show_id:
                show = Show.query.get(show_id)
                db.session.add(DJDisciplinaryLink(record=rec, link_type="show", linked_id=show_id, label=show.show_name if show else f"Show #{show_id}"))
        if log_id:
                log = LogSheet.query.get(log_id)
                label = f"Log #{log_id}"
                if log:
                        label = f"{log.show_name} - {log.show_date}"
                db.session.add(DJDisciplinaryLink(record=rec, link_type="log_sheet", linked_id=log_id, label=label))
        if manual:
                db.session.add(DJDisciplinaryLink(record=rec, link_type="manual_reference", label=manual))


def _apply_discipline_form(rec, creating=False):
        punishment_type = request.form.get("punishment_type") or "informal"
        if punishment_type not in DISCIPLINE_PUNISHMENTS:
                punishment_type = "custom"
        effective_at = _parse_date_field("effective_at")
        duration_days = request.form.get("duration_days", type=int)
        expires_at = _parse_date_field("expires_at")
        if duration_days and effective_at and not expires_at and punishment_type in {"probation", "suspension"}:
                expires_at = effective_at + timedelta(days=duration_days)
        rec.dj_id = request.form.get("dj_id", type=int) or rec.dj_id
        rec.punishment_type = punishment_type
        rec.custom_punishment = (request.form.get("custom_punishment") or "").strip() or None
        rec.severity = request.form.get("severity") or None
        rec.notes = request.form.get("notes") or None
        rec.action_taken = request.form.get("action_taken") or None
        rec.effective_at = effective_at
        rec.duration_days = duration_days
        rec.expires_at = expires_at
        rec.resolved = bool(request.form.get("resolved"))
        if rec.resolved:
                rec.case_status = "resolved"
        elif rec.case_status in {None, "resolved"}:
                rec.case_status = "active"
        if creating and not rec.case_number:
                rec.case_number = _next_discipline_case_number(punishment_type)
        if _discipline_requires_approval(punishment_type):
                if _has_discipline_approval_permission():
                        rec.approval_status = "approved"
                        rec.approved_by = _discipline_actor()
                        rec.approved_at = datetime.utcnow()
                        rec.case_status = "active"
                else:
                        rec.approval_status = "pending"
                        rec.case_status = "draft"
        else:
                rec.approval_status = "not_required"


@main_bp.route("/djs/discipline", methods=["GET", "POST"])
@permission_required({"dj:discipline", "dj:discipline:view"})
def manage_dj_discipline():
        djs = DJ.query.filter(DJ.is_archived.is_(False)).order_by(DJ.last_name, DJ.first_name).all()
        shows = Show.query.order_by(Show.show_name).all()
        logs = LogSheet.query.order_by(LogSheet.created_at.desc()).limit(100).all()
        can_approve = _has_discipline_approval_permission()
        can_create = _can_discipline("dj:discipline:create")
        can_edit = _can_discipline("dj:discipline:edit")
        can_void = _can_discipline("dj:discipline:void")
        if request.method == "POST":
                action = request.form.get("action", "create")
                rec_id = request.form.get("record_id", type=int)
                rec = DJDisciplinary.query.get(rec_id) if rec_id else None
                if action == "create":
                        if not can_create:
                                abort(403)
                        dj_id = request.form.get("dj_id", type=int)
                        if dj_id:
                                rec = DJDisciplinary(dj_id=dj_id, created_by=_discipline_actor())
                                _apply_discipline_form(rec, creating=True)
                                db.session.add(rec)
                                db.session.flush()
                                _sync_discipline_links(rec)
                                _audit_discipline(rec, "created", f"Case {rec.case_number} created.")
                                db.session.commit()
                                flash("Discipline case saved." if rec.approval_status != "pending" else "Discipline case submitted for approval.", "success")
                        return redirect(url_for("main.manage_dj_discipline"))
                if not rec:
                        return redirect(url_for("main.manage_dj_discipline"))
                locked = rec.approval_status == "approved" and not can_approve
                if action == "update":
                        if locked or not can_edit:
                                abort(403)
                        _apply_discipline_form(rec)
                        _sync_discipline_links(rec)
                        _audit_discipline(rec, "updated", f"Case {rec.case_number or rec.id} updated.")
                        db.session.commit()
                        flash("Discipline case updated.", "success")
                elif action in {"approve", "reject"}:
                        if not can_approve:
                                abort(403)
                        rec.approval_status = "approved" if action == "approve" else "rejected"
                        rec.case_status = "active" if action == "approve" else "draft"
                        rec.approved_by = _discipline_actor() if action == "approve" else None
                        rec.approved_at = datetime.utcnow() if action == "approve" else None
                        rec.approval_notes = request.form.get("approval_notes") or rec.approval_notes
                        _audit_discipline(rec, action, f"Case {rec.case_number or rec.id} {action}d.")
                        db.session.commit()
                        flash(f"Discipline case {action}d.", "success")
                elif action == "void":
                        if not can_void:
                                abort(403)
                        rec.voided = True
                        rec.case_status = "voided"
                        rec.voided_by = _discipline_actor()
                        rec.voided_at = datetime.utcnow()
                        rec.void_reason = request.form.get("void_reason") or None
                        _audit_discipline(rec, "voided", rec.void_reason)
                        db.session.commit()
                        flash("Discipline case voided.", "success")
                elif action == "appeal":
                        if locked or not can_edit:
                                abort(403)
                        rec.case_status = "appealed"
                        rec.appealed_by = _discipline_actor()
                        rec.appealed_at = datetime.utcnow()
                        rec.appeal_notes = request.form.get("appeal_notes") or None
                        _audit_discipline(rec, "appealed", rec.appeal_notes)
                        db.session.commit()
                        flash("Discipline case marked as appealed; punishment canceled.", "success")
                return redirect(url_for("main.manage_dj_discipline"))

        include_voided = request.args.get("include_voided") == "1"
        query = DJDisciplinary.query.options(selectinload(DJDisciplinary.dj), selectinload(DJDisciplinary.case_links), selectinload(DJDisciplinary.audit_entries))
        if not include_voided:
                query = query.filter((DJDisciplinary.voided.is_(False)) | (DJDisciplinary.voided.is_(None)))
        search = (request.args.get("q") or "").strip()
        if search:
                like = f"%{search}%"
                query = query.join(DJ).filter(or_(DJDisciplinary.case_number.ilike(like), DJ.first_name.ilike(like), DJ.last_name.ilike(like), DJDisciplinary.notes.ilike(like), DJDisciplinary.action_taken.ilike(like)))
        punishment_filter = request.args.get("punishment_type") or ""
        status_filter = request.args.get("case_status") or ""
        approval_filter = request.args.get("approval_status") or ""
        if punishment_filter:
                query = query.filter(DJDisciplinary.punishment_type == punishment_filter)
        if status_filter:
                query = query.filter(DJDisciplinary.case_status == status_filter)
        if approval_filter:
                query = query.filter(DJDisciplinary.approval_status == approval_filter)
        records = query.order_by(DJDisciplinary.issued_at.desc()).all()
        pending_records = []
        pending_count = DJDisciplinary.query.filter_by(approval_status="pending", voided=False).count()
        if can_approve:
                pending_records = DJDisciplinary.query.options(selectinload(DJDisciplinary.dj)).filter_by(approval_status="pending", voided=False).order_by(DJDisciplinary.issued_at.asc()).all()
        return render_template(
                "dj_discipline.html",
                djs=djs,
                shows=shows,
                logs=logs,
                records=records,
                pending_records=pending_records,
                pending_count=pending_count,
                can_approve=can_approve,
                can_create=can_create,
                can_edit=can_edit,
                can_void=can_void,
                punishments=DISCIPLINE_PUNISHMENTS,
                severities=DISCIPLINE_SEVERITIES,
                search=search,
                filters={"punishment_type": punishment_filter, "case_status": status_filter, "approval_status": approval_filter, "include_voided": include_voided},
                discipline_contact_email=current_app.config.get("DISCIPLINE_CONTACT_EMAIL", ""),
        )


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
            is_archived=bool(request.form.get("is_archived")),
        )
        dj.period_list = request.form.getlist("periods")
        selected = request.form.getlist("show_ids")
        if selected:
            dj.shows = Show.query.filter(Show.id.in_(selected)).all()
        db.session.add(dj)
        db.session.commit()
        flash("DJ added.", "success")
        return redirect(url_for("main.list_djs"))

    shows = Show.query.order_by(Show.start_time).all()
    periods = load_recording_periods().get("periods", [])
    return render_template("dj_form.html", dj=None, shows=shows, periods=periods)


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
        "^\\s*(?P<name>[^/]+?)\\s*/\\s*(?P<start>\\d{1,2}:\\d{2})\\s*[-\\u2013\\u2014]\\s*(?P<end>\\d{1,2}:\\d{2})\\s*/\\s*(?P<day>M|T|W|TH|F|SA|SU|MON|MONDAY|TUE|TUES|TUESDAY|WED|WEDNESDAY|THU|THUR|THURSDAY|FRI|FRIDAY|SAT|SATURDAY|SUN|SUNDAY)\\s*$",
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
        dj.is_archived = bool(request.form.get("is_archived"))
        dj.period_list = request.form.getlist("periods")
        selected = request.form.getlist("show_ids")
        dj.shows = Show.query.filter(Show.id.in_(selected)).all() if selected else []
        db.session.commit()
        flash("DJ updated.", "success")
        return redirect(url_for("main.list_djs"))

    shows = Show.query.order_by(Show.start_time).all()
    periods = load_recording_periods().get("periods", [])
    return render_template("dj_form.html", dj=dj, shows=shows, periods=periods)


@main_bp.route("/djs/<int:dj_id>/archive", methods=["POST"])
@admin_required
def archive_dj(dj_id):
    dj = DJ.query.get_or_404(dj_id)
    archive = request.form.get("archive") == "1"
    dj.is_archived = archive
    db.session.commit()
    flash(f"{'Archived' if archive else 'Unarchived'} DJ: {dj.first_name} {dj.last_name}.", "success")
    return redirect(url_for("main.list_djs", show_archived="1" if archive else None))

@main_bp.route("/djs/<int:dj_id>/delete", methods=["POST"])
@admin_required
def delete_dj(dj_id):
    dj = DJ.query.get_or_404(dj_id)
    name = f"{dj.first_name} {dj.last_name}".strip()

    dj.shows = []
    DJDisciplinary.query.filter_by(dj_id=dj.id).delete(synchronize_session=False)
    DJRecordingAccessCode.query.filter_by(dj_id=dj.id).delete(synchronize_session=False)
    db.session.delete(dj)
    db.session.commit()

    flash(f"Removed DJ: {name}.", "success")
    return redirect(url_for("main.list_djs"))


@main_bp.route("/dj/absence", methods=["GET", "POST"])
def dj_absence_submit():
        shows = Show.query.order_by(Show.show_name).all()
        djs = DJ.query.filter(DJ.is_archived.is_(False)).order_by(DJ.last_name, DJ.first_name).all()
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

                airing_date = (request.form.get("airing_date") or "").strip()
                if airing_date:
                        try:
                                occurrence = scheduled_window_for_date(show_obj, date.fromisoformat(airing_date))
                        except ValueError:
                                occurrence = None
                        now = datetime.now(get_config_timezone()).replace(tzinfo=None)
                        if not occurrence or occurrence[0] < now:
                                flash("Choose a future scheduled airing date within this show's date range.", "danger")
                                return redirect(url_for("main.dj_absence_submit"))
                else:
                        occurrence = next_show_occurrence(show_obj, include_uncovered_absence=True)
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
                refresh_schedule()
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
        djs = DJ.query.filter(DJ.is_archived.is_(False)).order_by(DJ.first_name.asc().nulls_last(), DJ.last_name.asc().nulls_last()).all()
        shows = Show.query.order_by(Show.show_name, Show.start_time).all()

        if request.method == "POST":
                action = request.form.get("action") or "update"
                if action == "create":
                        dj_id = request.form.get("dj_id", type=int)
                        show_id = request.form.get("show_id", type=int)
                        replacement_id = request.form.get("replacement_id", type=int)
                        status_choice = request.form.get("status") or "approved"
                        notes = request.form.get("notes") or None
                        show_obj = Show.query.get(show_id) if show_id else None
                        dj_obj = DJ.query.get(dj_id) if dj_id else None
                        if not show_obj or not dj_obj:
                                flash("Select both a DJ and show to add an absence.", "danger")
                                return redirect(url_for("main.manage_absences"))
                        occurrence = next_show_occurrence(show_obj, include_uncovered_absence=True)
                        if not occurrence:
                                flash("Could not determine the next scheduled slot for this show.", "danger")
                                return redirect(url_for("main.manage_absences"))
                        replacement_name = None
                        if replacement_id:
                                rep_obj = DJ.query.get(replacement_id)
                                if rep_obj:
                                        replacement_name = f"{rep_obj.first_name} {rep_obj.last_name}".strip()
                        absence = DJAbsence(
                                dj_name=f"{dj_obj.first_name} {dj_obj.last_name}".strip(),
                                show_name=show_obj.show_name or f"{show_obj.host_first_name} {show_obj.host_last_name}",
                                show_id=show_obj.id,
                                start_time=occurrence[0],
                                end_time=occurrence[1],
                                replacement_name=replacement_name,
                                notes=notes,
                                status=status_choice if status_choice in {"pending", "approved", "rejected", "resolved"} else "approved",
                        )
                        db.session.add(absence)
                        db.session.commit()
                        if absence.status == "approved" and not (absence.replacement_name or "").strip() and absence.show_id:
                                now = datetime.utcnow()
                                if absence.start_time <= now < absence.end_time:
                                        stop_active_show_recording(show_name=absence.show_name, reason="approved_absence")
                                        try:
                                                client = RadioDJClient()
                                                if client.enabled:
                                                        client.set_autodj(True)
                                        except Exception as exc:  # noqa: BLE001
                                                logger.warning("Failed to enable AutoDJ for approved absence %s: %s", absence.id, exc)
                        refresh_schedule()
                        flash("Absence added.", "success")
                        return redirect(url_for("main.manage_absences"))

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
                if absence.status == "approved" and not (absence.replacement_name or "").strip() and absence.show_id:
                        now = datetime.utcnow()
                        if absence.start_time <= now < absence.end_time:
                                stop_active_show_recording(show_name=absence.show_name, reason="approved_absence")
                                try:
                                        client = RadioDJClient()
                                        if client.enabled:
                                                client.set_autodj(True)
                                except Exception as exc:  # noqa: BLE001
                                        logger.warning("Failed to enable AutoDJ for approved absence %s: %s", absence.id, exc)
                refresh_schedule()
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
                "approved": DJAbsence.query.filter_by(stat…61689 tokens truncated… character.</div>
                </div>
                <div class="col-6 col-lg-2"><label class="form-label" for="yearFilter">Year</label><select class="form-select" id="yearFilter"><option value="">All years</option></select></div>
                <div class="col-6 col-lg-2"><label class="form-label" for="genreFilter">Genre</label><select class="form-select" id="genreFilter"><option value="">All genres</option></select></div>
                <div class="col-6 col-lg-1"><div class="form-check mb-2"><input class="form-check-input" id="explicitFilter" type="checkbox"><label class="form-check-label" for="explicitFilter">Explicit</label></div></div>
                <div class="col-6 col-lg-2 d-flex gap-1">
                    <button class="btn btn-outline-success flex-fill" id="saveSearchBtn" type="button">Save</button>
                    <div class="dropdown flex-fill"><button class="btn btn-outline-secondary dropdown-toggle w-100" data-bs-toggle="dropdown" type="button">Saved</button><ul class="dropdown-menu dropdown-menu-end" id="savedList"></ul></div>
                </div>
            </div>
            <div class="alert alert-light border mt-3 mb-0 d-none" id="maintenanceResults" role="status"></div>
        </div>
    </div>

    <div class="row g-3">
        <div class="col-lg-3">
            <div class="card h-100">
                <div class="card-header">
                    <ul class="nav nav-pills card-header-pills" id="libraryTabs">
                        <li class="nav-item">
                            <button class="nav-link active" data-mode="music" type="button">Music</button>
                        </li>
                        <li class="nav-item">
                            <button class="nav-link" data-mode="psa" type="button">PSA / Imaging</button>
                        </li>
                    </ul>
                </div>
                <div class="card-body p-0">
                    <div class="library-controls border-bottom p-3" id="musicControls">
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <div class="fw-semibold small text-uppercase text-muted">Browse</div>
                            <div class="btn-group btn-group-sm" id="browseModeToggle" role="group" aria-label="Browse mode">
                                <button class="btn btn-outline-secondary active" data-browse="artist" type="button">Artist</button>
                                <button class="btn btn-outline-secondary" data-browse="genre" type="button">Genre</button>
                            </div>
                        </div>
                        <div class="small text-muted">The search and filters above narrow every browser view.</div>
                    </div>
                    <div class="library-pane p-3">
                        <div id="musicBrowser">
                            <div id="artistBrowser">
                                <div class="mb-3">
                                    <div class="fw-semibold small text-uppercase text-muted">Artists</div>
                                    <div class="list-group list-group-flush" id="artistList"></div>
                                </div>
                                <div>
                                    <div class="fw-semibold small text-uppercase text-muted">Albums</div>
                                    <div class="list-group list-group-flush" id="albumList"></div>
                                </div>
                            </div>
                            <div id="genreBrowser" class="d-none">
                                <div class="mb-3">
                                    <div class="fw-semibold small text-uppercase text-muted">Genres</div>
                                    <div class="list-group list-group-flush" id="genreList"></div>
                                </div>
                                <div>
                                    <div class="fw-semibold small text-uppercase text-muted">Artists</div>
                                    <div class="list-group list-group-flush" id="genreArtistList"></div>
                                </div>
                            </div>
                        </div>
                        <div id="psaBrowser" class="d-none">
                            <div class="mb-3">
                                <div class="fw-semibold small text-uppercase text-muted">Collections</div>
                                <div class="list-group list-group-flush" id="psaCategoryList"></div>
                            </div>
                            <div>
                                <div class="fw-semibold small text-uppercase text-muted">Items</div>
                                <div class="list-group list-group-flush" id="psaItemList"></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        <div class="col-lg-5">
            <div class="card h-100 workspace-card">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span>Library Items</span>
                    <div class="btn-group btn-group-sm" role="group">
                        <button class="btn btn-outline-secondary active" id="listToggle" type="button">List</button>
                        <button class="btn btn-outline-secondary" id="gridToggle" type="button">Grid</button>
                    </div>
                </div>
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-center mb-2">
                        <div class="text-muted small" id="selectionSummary">Select an artist, album, or PSA collection.</div>
                        <div class="text-muted small">Sort by: <span id="sortLabel">Artist</span></div>
                    </div>
                    <div class="btn-group btn-group-sm mb-2" id="trackFilter" role="group" aria-label="Track filters">
                        <button class="btn btn-outline-secondary active" data-filter="all" type="button">All</button>
                        <button class="btn btn-outline-secondary" data-filter="recent" type="button">Recent</button>
                        <button class="btn btn-outline-secondary" data-filter="missing_cues" type="button">Missing Cues</button>
                    </div>
                    <div class="table-responsive" id="listView">
                        <table class="table table-sm table-hover align-middle">
                            <thead>
                                <tr>
                                    <th><input class="form-check-input" id="selectAll" type="checkbox" aria-label="Select all shown tracks"></th>
                                    <th class="sort-header" data-sort="artist">Artist<span class="sort-indicator" id="sort-artist"></span></th>
                                    <th class="sort-header" data-sort="album">Album<span class="sort-indicator" id="sort-album"></span></th>
                                    <th>Title</th>
                                    <th class="sort-header" data-sort="year">Year<span class="sort-indicator" id="sort-year"></span></th>
                                    <th class="sort-header" data-sort="genre">Genre<span class="sort-indicator" id="sort-genre"></span></th>
                                </tr>
                            </thead>
                            <tbody id="trackTableBody"></tbody>
                        </table>
                    </div>
                    <div class="row g-2 d-none" id="gridView"></div>
                </div>
            </div>
        </div>
        <div class="col-lg-4 editor-column">
            <div class="card h-100">
                <div class="card-header d-flex justify-content-between"><span id="editorHeading">Metadata Editor</span><span class="badge bg-primary d-none" id="bulkCount"></span></div>
                <div class="card-body">
                    <form id="metadataForm">
                        <div class="mb-3">
                            <div class="d-flex align-items-center gap-3 flex-wrap">
                                <img class="cover-preview" id="previewCover" alt="Album cover preview">
                                <div class="flex-grow-1">
                                    <div class="small text-muted text-uppercase">Preview Player</div>
                                    <div class="fw-semibold" id="previewTitle">Select a track to preview.</div>
                                    <div class="text-muted small" id="previewSubtitle">Album art and audio preview will appear here.</div>
                                    <audio class="w-100 mt-2" id="previewPlayer" controls preload="none"></audio>
                                </div>
                            </div>
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Artist</label>
                            <input type="text" class="form-control" id="metaArtist" placeholder="Artist name">
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Album</label>
                            <input type="text" class="form-control" id="metaAlbum" placeholder="Album name">
                        </div>
                        <div class="mb-3">
                            <label class="form-label">Title</label>
                            <input type="text" class="form-control" id="metaTitle" placeholder="Song or spot title">
                        </div>
                        <div class="row g-2">
                            <div class="col-sm-6">
                                <label class="form-label">Year</label>
                                <input type="text" class="form-control" id="metaYear" placeholder="YYYY">
                            </div>
                            <div class="col-sm-6">
                                <label class="form-label">Genre</label>
                                <input type="text" class="form-control" id="metaGenre" placeholder="Genre">
                            </div>
                        </div>
                        <div class="form-check form-switch mt-3">
                            <input class="form-check-input" type="checkbox" id="metaExplicit">
                            <label class="form-check-label" for="metaExplicit">Explicit</label>
                        </div>
                        <hr>
                        <a class="text-decoration-none" id="cueEditorLink" href="#" target="_blank" rel="noopener">
                            <div class="cue-link-card">
                                <div class="d-flex justify-content-between align-items-center">
                                    <div>
                                        <div class="fw-semibold">Cues</div>
                                        <div class="text-muted small">Open the cue editor for this track.</div>
                                    </div>
                                    <span class="text-primary fw-semibold">Edit →</span>
                                </div>
                            </div>
                        </a>
                        <div class="mt-3">
                            <label class="form-label small text-muted">File Path</label>
                            <input type="text" class="form-control form-control-sm" id="metaPath" readonly>
                        </div>
                        <div class="text-muted small mt-2" id="saveStatus"></div>
                        <div class="d-flex justify-content-end gap-2 mt-3">
                            <button class="btn btn-outline-primary btn-sm" type="button" id="openDetail" disabled>Details</button>
                            <button class="btn btn-outline-secondary btn-sm" type="button" id="resetMetadata">Reset</button>
                            <button class="btn btn-primary btn-sm" type="button" id="saveMetadata" disabled>Save</button>
                        </div>
                    </form>
                    <hr>
                    <div class="d-flex justify-content-between align-items-center">
                        <div><div class="fw-semibold">Bulk edit</div><div class="text-muted small">Select rows, enter only fields to change, then apply.</div></div>
                        <button class="btn btn-outline-secondary btn-sm" type="button" data-bs-toggle="collapse" data-bs-target="#bulkEditor">Open</button>
                    </div>
                    <div class="collapse mt-3" id="bulkEditor">
                        <div class="row g-2">
                            <div class="col-6"><input class="form-control form-control-sm bulk-field" data-key="artist" placeholder="Artist"></div>
                            <div class="col-6"><input class="form-control form-control-sm bulk-field" data-key="album" placeholder="Album"></div>
                            <div class="col-6"><input class="form-control form-control-sm bulk-field" data-key="genre" placeholder="Genre"></div>
                            <div class="col-6"><input class="form-control form-control-sm bulk-field" data-key="year" placeholder="Year"></div>
                            <div class="col-6"><input class="form-control form-control-sm bulk-field" data-key="composer" placeholder="Composer"></div>
                            <div class="col-6"><input class="form-control form-control-sm bulk-field" data-key="isrc" placeholder="ISRC"></div>
                            <div class="col-12"><label class="form-label small">Cover artwork</label><input class="form-control form-control-sm" id="bulkCover" type="file" accept="image/*"></div>
                            <div class="col-12"><button class="btn btn-primary btn-sm w-100" id="applyBulk" type="button" disabled>Apply to selected</button></div>
                        </div>
                        <div class="small mt-2" id="bulkStatus"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
    const libraryData = {{ library_index | tojson }};
    let savedSearches = [];
    const normalizeText = (value) => (value ?? '').toString().toLowerCase();
    const state = {
        mode: 'music',
        artists: libraryData.music || [],
        genres: libraryData.genres || [],
        psa: libraryData.psa_imaging || [],
        musicBrowseMode: 'artist',
        searchTerm: '',
        artistIndex: new Map(),
        genreIndex: new Map(),
        artistSearch: new Map(),
        albumSearch: new Map(),
        genreSearch: new Map(),
        genreArtistSearch: new Map(),
        selectedArtist: null,
        selectedAlbum: null,
        selectedGenre: null,
        selectedGenreArtist: null,
        selectedCategory: null,
        selectedItem: null,
        tracks: [],
        trackFilter: 'all',
        view: 'list',
        sortKey: 'artist',
        sortDir: 'asc',
        year: '',
        genre: '',
        explicitOnly: false,
        selectedPaths: new Set()
    };

    const artistList = document.getElementById('artistList');
    const albumList = document.getElementById('albumList');
    const genreList = document.getElementById('genreList');
    const genreArtistList = document.getElementById('genreArtistList');
    const psaCategoryList = document.getElementById('psaCategoryList');
    const psaItemList = document.getElementById('psaItemList');
    const trackTableBody = document.getElementById('trackTableBody');
    const gridView = document.getElementById('gridView');
    const listView = document.getElementById('listView');
    const selectionSummary = document.getElementById('selectionSummary');
    const sortLabel = document.getElementById('sortLabel');
    const librarySearch = document.getElementById('librarySearch');
    const trackFilterGroup = document.getElementById('trackFilter');
    const musicControls = document.getElementById('musicControls');
    const selectAll = document.getElementById('selectAll');
    const applyBulk = document.getElementById('applyBulk');
    const bulkCount = document.getElementById('bulkCount');

    const formFields = {
        artist: document.getElementById('metaArtist'),
        album: document.getElementById('metaAlbum'),
        title: document.getElementById('metaTitle'),
        year: document.getElementById('metaYear'),
        genre: document.getElementById('metaGenre'),
        explicit: document.getElementById('metaExplicit'),
        path: document.getElementById('metaPath')
    };
    const previewCover = document.getElementById('previewCover');
    const previewPlayer = document.getElementById('previewPlayer');
    const previewTitle = document.getElementById('previewTitle');
    const previewSubtitle = document.getElementById('previewSubtitle');
    const cueEditorLink = document.getElementById('cueEditorLink');
    const saveMetadataBtn = document.getElementById('saveMetadata');
    const saveStatus = document.getElementById('saveStatus');
    const placeholderCover = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="100%25" height="100%25" fill="%23f8f9fa"/><text x="50%25" y="50%25" font-family="Arial" font-size="20" fill="%2399a0a7" dominant-baseline="middle" text-anchor="middle">No Cover</text></svg>';
    const formState = {
        baseline: null,
        saving: false
    };
    previewCover.src = placeholderCover;
    cueEditorLink.classList.add('disabled');

    const debounce = (callback, wait = 200) => {
        let timeout;
        return (...args) => {
            window.clearTimeout(timeout);
            timeout = window.setTimeout(() => callback(...args), wait);
        };
    };

    const allMusicTracks = () => state.artists.flatMap(artist =>
        (artist.albums || []).flatMap(album => album.tracks || [])
    );

    const showAllMatchingTracks = () => {
        if (state.mode !== 'music') return;
        state.tracks = allMusicTracks();
        state.selectedArtist = null;
        state.selectedAlbum = null;
        renderTracks();
    };

    const buildArtistCache = () => {
        const artistIndex = new Map();
        const artistSearch = new Map();
        const albumSearch = new Map();
        state.artists.forEach(artist => {
            const albumSearchMap = new Map();
            const artistParts = [artist.name];
            (artist.albums || []).forEach(album => {
                const albumParts = [album.name, album.genre, album.year];
                (album.tracks || []).forEach(track => {
                    albumParts.push(track.title, track.genre, track.artist, track.album, track.composer, track.isrc, track.folder, track.mood);
                });
                const albumBlob = normalizeText(albumParts.join(' '));
                albumSearchMap.set(album.name, albumBlob);
                artistParts.push(albumBlob);
            });
            artistIndex.set(artist.name, { ...artist, albumSearchMap });
            artistSearch.set(artist.name, normalizeText(artistParts.join(' ')));
            albumSearch.set(artist.name, albumSearchMap);
        });
        state.artistIndex = artistIndex;
        state.artistSearch = artistSearch;
        state.albumSearch = albumSearch;
    };

    const buildGenreCache = () => {
        const genreIndex = new Map();
        const genreSearch = new Map();
        const genreArtistSearch = new Map();
        state.genres.forEach(genre => {
            const artistSearchMap = new Map();
            const genreParts = [genre.name];
            (genre.tracks || []).forEach(track => {
                const artistName = track.artist || 'Unknown Artist';
                const artistBlob = artistSearchMap.get(artistName) || '';
                const nextBlob = `${artistBlob} ${track.title || ''} ${track.album || ''} ${track.genre || ''} ${track.composer || ''} ${track.isrc || ''} ${track.folder || ''} ${track.mood || ''}`.trim();
                artistSearchMap.set(artistName, normalizeText(nextBlob));
                genreParts.push(track.artist, track.album, track.title, track.genre);
            });
            genreIndex.set(genre.name, genre);
            genreSearch.set(genre.name, normalizeText(genreParts.join(' ')));
            genreArtistSearch.set(genre.name, artistSearchMap);
        });
        state.genreIndex = genreIndex;
        state.genreSearch = genreSearch;
        state.genreArtistSearch = genreArtistSearch;
    };

    const buildCaches = () => {
        buildArtistCache();
        buildGenreCache();
    };

    const setActiveListItem = (container, activeValue) => {
        container.querySelectorAll('.list-group-item').forEach(item => {
            item.classList.toggle('active', item.dataset.value === activeValue);
        });
    };

    const updateMetadataForm = (item) => {
        formFields.artist.value = item?.artist || '';
        formFields.album.value = item?.album || '';
        formFields.title.value = item?.title || '';
        formFields.year.value = item?.year || '';
        formFields.genre.value = item?.genre || '';
        formFields.explicit.checked = Boolean(item?.explicit);
        formFields.path.value = item?.path || '';
        const title = item?.title || 'Select a track to preview.';
        const artist = item?.artist || '';
        const album = item?.album || '';
        previewTitle.textContent = title;
        previewSubtitle.textContent = [artist, album].filter(Boolean).join(' · ') || 'Album art and audio preview will appear here.';
        const path = item?.path || '';
        previewPlayer.src = path ? `/music/stream?path=${encodeURIComponent(path)}` : '';
        previewCover.src = path ? `/music/cover?path=${encodeURIComponent(path)}` : placeholderCover;
        previewCover.onerror = () => {
            previewCover.src = placeholderCover;
        };
        cueEditorLink.href = path ? `/music/cue?path=${encodeURIComponent(path)}` : '#';
        cueEditorLink.classList.toggle('disabled', !path);
        cueEditorLink.setAttribute('aria-disabled', path ? 'false' : 'true');
        document.getElementById('openDetail').disabled = !path;
        saveStatus.textContent = '';
        formState.baseline = getFormState();
        updateSaveState();
    };

    const getFormState = () => ({
        artist: formFields.artist.value.trim(),
        album: formFields.album.value.trim(),
        title: formFields.title.value.trim(),
        year: formFields.year.value.trim(),
        genre: formFields.genre.value.trim(),
        explicit: formFields.explicit.checked,
        path: formFields.path.value
    });

    const hasFormChanges = () => {
        if (!formState.baseline) {
            return false;
        }
        const current = getFormState();
        return Object.keys(current).some(key => current[key] !== formState.baseline[key]);
    };

    const updateSaveState = () => {
        const allowSave = state.selectedItem && hasFormChanges() && !formState.saving;
        saveMetadataBtn.disabled = !allowSave;
    };

    const sortTracks = (tracks) => {
        const key = state.sortKey;
        const dir = state.sortDir === 'asc' ? 1 : -1;
        tracks.sort((a, b) => {
            const aVal = (a[key] ?? '').toString().toLowerCase();
            const bVal = (b[key] ?? '').toString().toLowerCase();
            if (key === 'year') {
                return dir * ((parseInt(aVal || '0', 10)) - (parseInt(bVal || '0', 10)));
            }
            if (aVal < bVal) return -1 * dir;
            if (aVal > bVal) return 1 * dir;
            return 0;
        });
    };

    const applyTrackFilters = (tracks) => {
        return tracks.filter(track => {
            const blob = normalizeText([track.title, track.artist, track.album, track.genre, track.year, track.composer, track.isrc, track.folder, track.mood].filter(Boolean).join(' '));
            if (!matchesSearch(blob)) return false;
            if (state.mode === 'music' && state.year && String(track.year || '') !== state.year) return false;
            if (state.mode === 'music' && state.genre && normalizeText(track.genre) !== normalizeText(state.genre)) return false;
            if (state.mode === 'music' && state.explicitOnly && !track.explicit) return false;
            if (state.mode === 'music' && state.trackFilter === 'recent' && !track.is_recent) return false;
            if (state.mode === 'music' && state.trackFilter === 'missing_cues' && !track.missing_cues) return false;
            return true;
        });
    };

    const updateBulkState = () => {
        const count = state.selectedPaths.size;
        bulkCount.textContent = `${count} selected`;
        bulkCount.classList.toggle('d-none', count === 0);
        applyBulk.disabled = count === 0;
    };

    const renderTracks = () => {
        const baseTracks = state.tracks;
        const filteredTracks = applyTrackFilters(baseTracks);
        const sortedTracks = [...filteredTracks];
        sortTracks(sortedTracks);
        trackTableBody.innerHTML = '';
        gridView.innerHTML = '';
        if (!baseTracks.length) {
            trackTableBody.innerHTML = '<tr><td colspan="6" class="text-muted">No items selected.</td></tr>';
            if (state.mode === 'psa') {
                selectionSummary.textContent = 'Select a PSA collection.';
            } else if (state.musicBrowseMode === 'genre') {
                selectionSummary.textContent = 'Select a genre to view tracks.';
            } else {
                selectionSummary.textContent = 'Select an artist or album.';
            }
            return;
        }
        if (!filteredTracks.length) {
            trackTableBody.innerHTML = '<tr><td colspan="6" class="text-muted">No items match the current filter.</td></tr>';
            selectionSummary.textContent = `0 of ${baseTracks.length} items shown`;
            return;
        }
        selectionSummary.textContent = filteredTracks.length === baseTracks.length
            ? `${filteredTracks.length} items shown`
            : `${filteredTracks.length} of ${baseTracks.length} items shown`;
        sortedTracks.forEach((track, idx) => {
            const row = document.createElement('tr');
            row.className = `track-row ${track.path === state.selectedItem?.path ? 'table-primary' : ''}`;
            row.innerHTML = `
                <td><input class="form-check-input track-select" type="checkbox" ${state.selectedPaths.has(track.path) ? 'checked' : ''} aria-label="Select ${track.title || 'track'}"></td>
                <td>${track.artist || '-'}</td>
                <td>${track.album || '-'}</td>
                <td>${track.title || '-'}</td>
                <td>${track.year || '-'}</td>
                <td>${track.genre || '-'}</td>
            `;
            row.querySelector('.track-select').addEventListener('click', event => {
                event.stopPropagation();
                if (event.currentTarget.checked) state.selectedPaths.add(track.path);
                else state.selectedPaths.delete(track.path);
                updateBulkState();
            });
            row.addEventListener('click', () => {
                state.selectedItem = track;
                updateMetadataForm(track);
                renderTracks();
            });
            trackTableBody.appendChild(row);

            const col = document.createElement('div');
            col.className = 'col-12';
            if (state.view === 'grid') {
                col.className = 'col-md-6';
            }
            const card = document.createElement('div');
            card.className = `grid-card ${track.path === state.selectedItem?.path ? 'active' : ''}`;
            card.innerHTML = `
                <div class="fw-semibold">${track.title || '-'}</div>
                <div class="text-muted small">${track.artist || 'Unknown Artist'} · ${track.album || 'Unknown Album'}</div>
                <div class="small mt-1">Year: ${track.year || '—'} · Genre: ${track.genre || '—'}</div>
            `;
            card.addEventListener('click', () => {
                state.selectedItem = track;
                updateMetadataForm(track);
                renderTracks();
            });
            col.appendChild(card);
            gridView.appendChild(col);
        });
        selectAll.checked = sortedTracks.length > 0 && sortedTracks.every(track => state.selectedPaths.has(track.path));
        selectAll.dataset.visiblePaths = JSON.stringify(sortedTracks.map(track => track.path));
        updateBulkState();
    };

    const matchesSearch = (blob) => {
        if (!state.searchTerm) {
            return true;
        }
        const haystack = normalizeText(blob);
        const terms = state.searchTerm.split(/\s+/).filter(Boolean);
        return terms.every(term => {
            if (!term.includes('*') && !term.includes('?')) return haystack.includes(term);
            const escaped = term.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.');
            return new RegExp(escaped, 'i').test(haystack);
        });
    };

    const getFilteredArtists = () => state.artists.filter(artist => {
        const blob = state.artistSearch.get(artist.name) || '';
        return matchesSearch(blob);
    });

    const getFilteredGenres = () => state.genres.filter(genre => {
        const blob = state.genreSearch.get(genre.name) || '';
        return matchesSearch(blob);
    });

    const renderArtists = () => {
        artistList.innerHTML = '';
        const filteredArtists = getFilteredArtists();
        if (!filteredArtists.length) {
            artistList.innerHTML = '<div class="text-muted small">No artists match the current search.</div>';
        }
        if (state.selectedArtist && !filteredArtists.find(artist => artist.name === state.selectedArtist)) {
            state.selectedArtist = null;
            state.selectedAlbum = null;
            state.tracks = [];
            renderTracks();
        }
        filteredArtists.forEach(artist => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'list-group-item list-group-item-action';
            btn.dataset.value = artist.name;
            btn.textContent = artist.name;
            btn.addEventListener('click', () => {
                state.selectedArtist = artist.name;
                state.selectedAlbum = null;
                setActiveListItem(artistList, artist.name);
                renderAlbums();
                state.tracks = artist.albums.flatMap(album => album.tracks);
                renderTracks();
            });
            artistList.appendChild(btn);
        });
    };

    const renderAlbums = () => {
        albumList.innerHTML = '';
        const artist = state.artists.find(a => a.name === state.selectedArtist);
        if (!artist) {
            albumList.innerHTML = '<div class="text-muted small">Select an artist to view albums.</div>';
            return;
        }
        const albumSearchMap = state.albumSearch.get(artist.name) || new Map();
        const filteredAlbums = (artist.albums || []).filter(album => {
            if (!state.searchTerm) {
                return true;
            }
            return matchesSearch(albumSearchMap.get(album.name) || '');
        });
        if (!filteredAlbums.length) {
            albumList.innerHTML = '<div class="text-muted small">No albums match the current search.</div>';
            return;
        }
        filteredAlbums.forEach(album => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'list-group-item list-group-item-action';
            btn.dataset.value = album.name;
            btn.innerHTML = `<div class="fw-semibold">${album.name}</div><div class="small text-muted">${album.year || 'Year n/a'} · ${album.genre || 'Genre n/a'}</div>`;
            btn.addEventListener('click', () => {
                state.selectedAlbum = album.name;
                setActiveListItem(albumList, album.name);
                state.tracks = album.tracks;
                renderTracks();
            });
            albumList.appendChild(btn);
        });
    };

    const renderGenres = () => {
        genreList.innerHTML = '';
        const filteredGenres = getFilteredGenres();
        if (!filteredGenres.length) {
            genreList.innerHTML = '<div class="text-muted small">No genres match the current search.</div>';
        }
        if (state.selectedGenre && !filteredGenres.find(genre => genre.name === state.selectedGenre)) {
            state.selectedGenre = null;
            state.selectedGenreArtist = null;
            state.tracks = [];
            renderTracks();
        }
        filteredGenres.forEach(genre => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'list-group-item list-group-item-action';
            btn.dataset.value = genre.name;
            btn.textContent = genre.name;
            btn.addEventListener('click', () => {
                state.selectedGenre = genre.name;
                state.selectedGenreArtist = null;
                setActiveListItem(genreList, genre.name);
                renderGenreArtists();
                state.tracks = genre.tracks || [];
                renderTracks();
            });
            genreList.appendChild(btn);
        });
    };

    const renderGenreArtists = () => {
        genreArtistList.innerHTML = '';
        const genre = state.genres.find(g => g.name === state.selectedGenre);
        if (!genre) {
            genreArtistList.innerHTML = '<div class="text-muted small">Select a genre to view artists.</div>';
            return;
        }
        const artistSearchMap = state.genreArtistSearch.get(genre.name) || new Map();
        const artists = (genre.artists || []).filter(name => {
            if (!state.searchTerm) {
                return true;
            }
            return matchesSearch(artistSearchMap.get(name) || '');
        });
        if (!artists.length) {
            genreArtistList.innerHTML = '<div class="text-muted small">No artists match the current search.</div>';
            return;
        }
        if (state.selectedGenreArtist && !artists.includes(state.selectedGenreArtist)) {
            state.selectedGenreArtist = null;
            state.tracks = genre.tracks || [];
            renderTracks();
        }
        artists.forEach(name => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'list-group-item list-group-item-action';
            btn.dataset.value = name;
            btn.textContent = name;
            btn.addEventListener('click', () => {
                state.selectedGenreArtist = name;
                setActiveListItem(genreArtistList, name);
                state.tracks = (genre.tracks || []).filter(track => track.artist === name);
                renderTracks();
            });
            genreArtistList.appendChild(btn);
        });
    };

    const renderPsaCategories = () => {
        psaCategoryList.innerHTML = '';
        state.psa.forEach(category => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'list-group-item list-group-item-action';
            btn.dataset.value = category.category;
            btn.textContent = category.category;
            btn.addEventListener('click', () => {
                state.selectedCategory = category.category;
                setActiveListItem(psaCategoryList, category.category);
                renderPsaItems();
            });
            psaCategoryList.appendChild(btn);
        });
    };

    const renderPsaItems = () => {
        psaItemList.innerHTML = '';
        const category = state.psa.find(c => c.category === state.selectedCategory);
        if (!category) {
            psaItemList.innerHTML = '<div class="text-muted small">Select a collection to see items.</div>';
            state.tracks = [];
            renderTracks();
            return;
        }
        category.items.forEach(item => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'list-group-item list-group-item-action';
            btn.dataset.value = item.path;
            btn.textContent = item.title;
            btn.addEventListener('click', () => {
                state.selectedItem = item;
                updateMetadataForm(item);
                setActiveListItem(psaItemList, item.path);
            });
            psaItemList.appendChild(btn);
        });
        state.tracks = category.items.map(item => ({
            ...item,
            artist: item.artist || category.category,
            album: item.album || item.folder || category.category
        }));
        renderTracks();
    };

    const renderMusicLists = () => {
        document.getElementById('artistBrowser').classList.toggle('d-none', state.musicBrowseMode !== 'artist');
        document.getElementById('genreBrowser').classList.toggle('d-none', state.musicBrowseMode !== 'genre');
        if (state.musicBrowseMode === 'artist') {
            renderArtists();
            renderAlbums();
        } else {
            renderGenres();
            renderGenreArtists();
        }
    };

    const setBrowseMode = (browseMode) => {
        state.musicBrowseMode = browseMode;
        state.selectedArtist = null;
        state.selectedAlbum = null;
        state.selectedGenre = null;
        state.selectedGenreArtist = null;
        state.tracks = [];
        renderMusicLists();
        renderTracks();
    };

    const setMode = (mode) => {
        state.mode = mode;
        musicControls.classList.toggle('d-none', mode !== 'music');
        document.getElementById('musicBrowser').classList.toggle('d-none', mode !== 'music');
        document.getElementById('psaBrowser').classList.toggle('d-none', mode !== 'psa');
        trackFilterGroup.classList.toggle('d-none', mode !== 'music');
        if (mode === 'music') {
            state.selectedCategory = null;
            state.tracks = [];
            renderMusicLists();
            renderTracks();
        } else {
            state.selectedArtist = null;
            state.selectedAlbum = null;
            state.selectedGenre = null;
            state.selectedGenreArtist = null;
            state.tracks = [];
            renderPsaCategories();
            renderPsaItems();
        }
    };

    document.querySelectorAll('#libraryTabs .nav-link').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('#libraryTabs .nav-link').forEach(btn => btn.classList.remove('active'));
            tab.classList.add('active');
            setMode(tab.dataset.mode);
        });
    });

    document.querySelectorAll('#browseModeToggle .btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#browseModeToggle .btn').forEach(toggle => toggle.classList.remove('active'));
            btn.classList.add('active');
            setBrowseMode(btn.dataset.browse);
        });
    });

    librarySearch.addEventListener('input', debounce(() => {
        state.searchTerm = normalizeText(librarySearch.value);
        renderMusicLists();
        showAllMatchingTracks();
    }, 250));

    const yearFilter = document.getElementById('yearFilter');
    const genreFilter = document.getElementById('genreFilter');
    const years = [...new Set(allMusicTracks().map(track => String(track.year || '')).filter(Boolean))].sort().reverse();
    const genres = [...new Set(allMusicTracks().map(track => track.genre).filter(Boolean))].sort((a, b) => a.localeCompare(b));
    years.forEach(year => yearFilter.add(new Option(year, year)));
    genres.forEach(genre => genreFilter.add(new Option(genre, genre)));
    yearFilter.addEventListener('change', () => { state.year = yearFilter.value; showAllMatchingTracks(); });
    genreFilter.addEventListener('change', () => { state.genre = genreFilter.value; showAllMatchingTracks(); });
    document.getElementById('explicitFilter').addEventListener('change', event => { state.explicitOnly = event.target.checked; showAllMatchingTracks(); });

    selectAll.addEventListener('change', () => {
        const visible = JSON.parse(selectAll.dataset.visiblePaths || '[]');
        visible.forEach(path => selectAll.checked ? state.selectedPaths.add(path) : state.selectedPaths.delete(path));
        renderTracks();
    });

    document.getElementById('openDetail').addEventListener('click', () => {
        if (state.selectedItem?.path) window.location.href = window.ramsUrl(`/music/detail?path=${encodeURIComponent(state.selectedItem.path)}`);
    });

    document.getElementById('listToggle').addEventListener('click', () => {
        state.view = 'list';
        listView.classList.remove('d-none');
        gridView.classList.add('d-none');
        document.getElementById('listToggle').classList.add('active');
        document.getElementById('gridToggle').classList.remove('active');
    });

    document.getElementById('gridToggle').addEventListener('click', () => {
        state.view = 'grid';
        listView.classList.add('d-none');
        gridView.classList.remove('d-none');
        document.getElementById('gridToggle').classList.add('active');
        document.getElementById('listToggle').classList.remove('active');
        renderTracks();
    });

    document.querySelectorAll('#trackFilter .btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#trackFilter .btn').forEach(filterBtn => filterBtn.classList.remove('active'));
            btn.classList.add('active');
            state.trackFilter = btn.dataset.filter || 'all';
            renderTracks();
        });
    });

    document.querySelectorAll('.sort-header').forEach(header => {
        header.addEventListener('click', () => {
            const key = header.dataset.sort;
            if (!key) return;
            if (state.sortKey === key) {
                state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
            } else {
                state.sortKey = key;
                state.sortDir = 'asc';
            }
            sortLabel.textContent = key.charAt(0).toUpperCase() + key.slice(1);
            document.querySelectorAll('.sort-indicator').forEach(indicator => indicator.textContent = '');
            const indicator = document.getElementById(`sort-${key}`);
            indicator.textContent = state.sortDir === 'asc' ? '▲' : '▼';
            renderTracks();
        });
    });

    document.getElementById('resetMetadata').addEventListener('click', () => {
        updateMetadataForm(state.selectedItem);
    });

    Object.values(formFields).forEach(field => {
        if (!field) return;
        const eventName = field.type === 'checkbox' ? 'change' : 'input';
        field.addEventListener(eventName, () => {
            updateSaveState();
        });
    });

    saveMetadataBtn.addEventListener('click', async () => {
        if (!state.selectedItem) return;
        const current = getFormState();
        const baseline = formState.baseline || {};
        if (!current.path) return;
        const updates = {};
        ['artist', 'album', 'title', 'year', 'genre', 'explicit'].forEach(key => {
            if (current[key] !== baseline[key]) {
                updates[key] = current[key];
            }
        });
        if (!Object.keys(updates).length) {
            updateSaveState();
            return;
        }
        formState.saving = true;
        updateSaveState();
        saveStatus.textContent = 'Saving changes...';
        saveStatus.classList.remove('text-danger');
        try {
            const res = await fetch(window.ramsUrl('/api/music/bulk-update'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paths: [current.path], updates })
            });
            const payload = await res.json();
            if (!res.ok || payload.status !== 'ok') {
                throw new Error(payload.message || 'Unable to save changes.');
            }
            Object.assign(state.selectedItem, updates);
            formState.baseline = getFormState();
            saveStatus.textContent = 'Saved successfully.';
            saveStatus.classList.remove('text-danger');
            renderTracks();
        } catch (error) {
            saveStatus.textContent = error?.message || 'Unable to save changes.';
            saveStatus.classList.add('text-danger');
        } finally {
            formState.saving = false;
            updateSaveState();
            setTimeout(() => {
                saveStatus.classList.remove('text-danger');
            }, 2000);
        }
    });

    const fileAsBase64 = file => new Promise((resolve, reject) => {
        if (!file) return resolve(null);
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });

    applyBulk.addEventListener('click', async () => {
        const updates = {};
        document.querySelectorAll('.bulk-field').forEach(input => {
            if (input.value.trim()) updates[input.dataset.key] = input.value.trim();
        });
        const coverFile = document.getElementById('bulkCover').files[0];
        if (!Object.keys(updates).length && !coverFile) {
            document.getElementById('bulkStatus').textContent = 'Enter at least one change.';
            return;
        }
        if (!window.confirm(`Apply these changes to ${state.selectedPaths.size} selected item(s)?`)) return;
        applyBulk.disabled = true;
        const status = document.getElementById('bulkStatus');
        status.textContent = 'Applying changes…';
        try {
            const res = await fetch(window.ramsUrl('/api/music/bulk-update'), {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({paths: [...state.selectedPaths], updates, cover_art: await fileAsBase64(coverFile)})
            });
            const payload = await res.json();
            if (!res.ok || payload.status !== 'ok') throw new Error(payload.message || 'Bulk update failed.');
            allMusicTracks().filter(track => state.selectedPaths.has(track.path)).forEach(track => Object.assign(track, updates));
            status.textContent = `Updated ${state.selectedPaths.size} item(s).`;
            status.classList.remove('text-danger');
            buildCaches(); renderMusicLists(); renderTracks();
        } catch (error) {
            status.textContent = error.message || 'Bulk update failed.';
            status.classList.add('text-danger');
        } finally { updateBulkState(); }
    });

    const savedList = document.getElementById('savedList');
    const renderSavedSearches = () => {
        savedList.innerHTML = savedSearches.length ? '' : '<li><span class="dropdown-item-text text-muted">No saved searches</span></li>';
        savedSearches.forEach(saved => {
            const li = document.createElement('li');
            li.innerHTML = `<div class="dropdown-item d-flex align-items-center gap-2"><button class="btn btn-link p-0 text-start flex-grow-1 saved-open" type="button"></button><button class="btn btn-link text-danger p-0 saved-delete" aria-label="Delete saved search" type="button">×</button></div>`;
            li.querySelector('.saved-open').textContent = saved.name;
            li.querySelector('.saved-open').addEventListener('click', () => {
                librarySearch.value = saved.query || '';
                const filters = saved.filters || {};
                yearFilter.value = filters.year || ''; genreFilter.value = filters.genre || '';
                document.getElementById('explicitFilter').checked = Boolean(filters.explicit);
                state.searchTerm = normalizeText(librarySearch.value); state.year = yearFilter.value; state.genre = genreFilter.value; state.explicitOnly = Boolean(filters.explicit);
                renderMusicLists(); showAllMatchingTracks();
            });
            li.querySelector('.saved-delete').addEventListener('click', async event => {
                event.stopPropagation();
                await fetch(window.ramsUrl(`/api/music/saved-searches?id=${saved.id}`), {method: 'DELETE'});
                savedSearches = savedSearches.filter(item => item.id !== saved.id); renderSavedSearches();
            });
            savedList.appendChild(li);
        });
    };
    const loadSavedSearches = async () => {
        const res = await fetch(window.ramsUrl('/api/music/saved-searches'));
        if (res.ok) savedSearches = await res.json();
        renderSavedSearches();
    };
    document.getElementById('saveSearchBtn').addEventListener('click', async () => {
        const name = window.prompt('Name this search:');
        if (!name) return;
        const res = await fetch(window.ramsUrl('/api/music/saved-searches'), {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, query: librarySearch.value, filters: {year: state.year, genre: state.genre, explicit: state.explicitOnly}})});
        if (res.ok) await loadSavedSearches();
    });

    const maintenanceResults = document.getElementById('maintenanceResults');
    const showMaintenance = html => { maintenanceResults.innerHTML = html; maintenanceResults.classList.remove('d-none'); };
    document.getElementById('queuesBtn').addEventListener('click', async () => {
        showMaintenance('Loading queues…');
        const res = await fetch(window.ramsUrl('/api/music/scan/library'));
        const data = await res.json();
        const metrics = data.metrics || {};
        const list = (items, label) => `<strong>${label}</strong>: ${items?.length ? items.slice(0, 5).map(item => item.title || item.path).join(', ') : 'None'}`;
        showMaintenance(`${list(metrics.low_bitrate, 'Low bitrate')}<br>${list(metrics.missing_art, 'Missing art')}<br>${list(metrics.needs_metadata, 'Needs metadata')}<br>${list(metrics.recently_added, 'Recently added')}`);
    });
    document.getElementById('scanBtn').addEventListener('click', async () => {
        showMaintenance('Running duplicate and quality scan…');
        const res = await fetch(window.ramsUrl('/api/music/scan/library?refresh=1'));
        const data = await res.json();
        const metrics = data.metrics || {};
        showMaintenance(`Scan complete. ${metrics.duplicates?.length || 0} duplicate pair(s), ${metrics.low_bitrate?.length || 0} low-bitrate item(s), ${metrics.missing_art?.length || 0} missing artwork item(s).`);
    });

    buildCaches();
    renderMusicLists();
    renderTracks();
    loadSavedSearches();
</script>
</body>
</html>
