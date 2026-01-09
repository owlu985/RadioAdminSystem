from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_file, abort, send_from_directory, make_response, jsonify
import json
import os
import secrets
import random
import base64
import hashlib
import requests
from urllib.parse import quote_plus
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
    SocialPost,
    LogSheet,
    Plugin,
    WebsiteContent,
    PodcastEpisode,
    MarathonEvent,
)
from app.plugins import ensure_plugin_record, plugin_display_name
from sqlalchemy import case, func
from functools import wraps
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
from app.routes.logging_api import logs_bp
from app.services.music_search import (
    search_music,
    get_track,
    load_cue,
    save_cue,
    update_metadata,
)
from app.services.audit import audit_recordings, audit_explicit_music
from app.services.health import get_health_snapshot
from app.services.stream_monitor import fetch_icecast_listeners, recent_icecast_stats
from app.services.settings_backup import backup_settings, backup_data_snapshot
from app.services.live_reads import upsert_cards, card_query, chunk_cards
from app.services.archivist_db import import_archivist_csv, search_archivist
from app.services.social_poster import send_social_post
from app.oauth import oauth, init_oauth, ensure_oauth_initialized
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


def _complete_login(user: User):
    user.last_login_at = datetime.utcnow()
    db.session.commit()
    session['authenticated'] = True
    session['user_email'] = user.email
    session['auth_provider'] = user.provider
    session['user_id'] = user.id
    session['display_name'] = user.display_name or user.email
    role = user.custom_role or user.role or 'viewer'
    session['role'] = role
    perms = []
    if user.permissions:
        perms = [p.strip() for p in user.permissions.split(',') if p.strip()]
    session['permissions'] = perms


def _parse_identities(user: User) -> list[dict]:
    try:
        return json.loads(user.identities) if user.identities else []
    except Exception:  # noqa: BLE001
        return []


def _add_identity(user: User, provider: str, external_id: str | None, email: str | None) -> None:
    identities = _parse_identities(user)
    exists = any(
        (i.get('provider') == provider and i.get('external_id') == str(external_id))
        or (email and i.get('email') and i.get('email').lower() == email.lower())
        for i in identities
    )
    if not exists:
        identities.append({
            'provider': provider,
            'external_id': str(external_id) if external_id else None,
            'email': email,
        })
        user.identities = json.dumps(identities)


def _serialize_oauth_token(token: dict | None, provider: str | None = None) -> dict | None:
    """Safely stash an OAuth token in the session for admin inspection.

    Tokens can contain datetime or other non-JSON values; coerce everything to
    JSON-safe types and track the provider and capture time. This is only used
    for the current session and exposed via an admin-only endpoint.
    """

    if token is None:
        return None

    def _coerce(value):
        try:
            json.dumps(value)
            return value
        except Exception:
            return str(value)

    if isinstance(token, dict):
        sanitized = {k: _coerce(v) for k, v in token.items()}
    else:
        sanitized = _coerce(token)

    return {
        "provider": provider,
        "token": sanitized,
        "captured_at": datetime.utcnow().isoformat(),
    }


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _find_user(provider: str, external_id: str | None, email: str | None, display_name: str | None = None):
    external_id = str(external_id) if external_id else None
    email_norm = email.lower() if email else None

    if external_id:
        user = User.query.filter_by(provider=provider, external_id=external_id).first()
        if user:
            return user
        user = User.query.filter(User.identities.ilike(f"%{provider}%"), User.identities.ilike(f"%{external_id}%")).first()
        if user:
            return user

    if email_norm:
        user = User.query.filter(db.func.lower(User.email) == email_norm).first()
        if user:
            return user
        user = User.query.filter(User.identities.ilike(f"%{email_norm}%")).first()
        if user:
            return user

    if display_name:
        user = User.query.filter(User.display_name.ilike(display_name)).first()
        if user:
            return user
    return None


def _redirect_pending(profile):
    session['pending_oauth'] = profile
    flash("Almost done! Please confirm your name to request access.", "info")
    return redirect(url_for('main.oauth_claim'))


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

@main_bp.route('/')
def index():
    """Redirect to login or dashboard depending on authentication."""

    if session.get('authenticated'):
        logger.info("Redirecting to dashboard.")
        return redirect(url_for('main.dashboard'))
    logger.info("Redirecting to login.")
    return redirect(url_for('main.login'))

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
    shows_column = Show.query.order_by(
        day_order,
        Show.start_time,
        Show.start_date
    ).paginate(page=page, per_page=15)

    logger.info("Rendering shows database page.")
    return render_template('shows_database.html', shows=shows_column)


@main_bp.route('/schedule/grid')
def schedule_grid():
        return render_template('schedule_grid.html')


@main_bp.route('/schedule/ical')
def schedule_ical():
        shows = Show.query.order_by(Show.days_of_week, Show.start_time).all()
        tz = current_app.config.get("SCHEDULE_TIMEZONE", "America/New_York")
        lines = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                f"X-WR-TIMEZONE:{tz}",
        ]
        for show in shows:
                days = [d.strip() for d in (show.days_of_week or "").split(',') if d.strip()]
                for day in days:
                        uid = f"show-{show.id}-{day}@rams"
                        dtstart = f"{show.start_time.strftime('%H%M%S')}"
                        dtend = f"{show.end_time.strftime('%H%M%S')}"
                        lines.extend([
                                "BEGIN:VEVENT",
                                f"UID:{uid}",
                                f"SUMMARY:{show.show_name or (show.host_first_name + ' ' + show.host_last_name)}",
                                f"RRULE:FREQ=WEEKLY;BYDAY={day.upper()};UNTIL={show.end_date.strftime('%Y%m%dT%H%M%SZ')}",
                                f"DTSTART;TZID={tz}:{show.start_date.strftime('%Y%m%d')}T{dtstart}",
                                f"DTEND;TZID={tz}:{show.start_date.strftime('%Y%m%d')}T{dtend}",
                                "END:VEVENT",
                        ])
        lines.append("END:VCALENDAR")
        ical = "\r\n".join(lines)
        return current_app.response_class(ical, mimetype="text/calendar")


@main_bp.route('/dashboard')
@admin_required
def dashboard():
        """Admin landing page with current show status and quick links."""

        current_show = get_current_show()
        current_run = None
        window = None
        if current_show:
                window = format_show_window(current_show)
                from app.services.show_run_service import get_or_create_active_run
                current_run = get_or_create_active_run(
                        show_name=current_show.show_name or f"{current_show.host_first_name} {current_show.host_last_name}",
                        dj_first_name=current_show.host_first_name,
                        dj_last_name=current_show.host_last_name,
                )

        absences = DJAbsence.query.filter(
                DJAbsence.end_time >= datetime.utcnow() - timedelta(days=1),
                DJAbsence.status.in_(["pending", "approved"])
        ).order_by(DJAbsence.start_time).limit(10).all()

        health = get_health_snapshot()
        listeners = fetch_icecast_listeners()

        greeting = random.choice(["Hello", "Bonjour", "Hola", "Howdy", "Greetings", "Salutations", "Welcome"])

        return render_template(
                'dashboard.html',
                current_show=current_show,
                current_run=current_run,
                window=window,
                absences=absences,
                health=health,
                listeners=listeners,
                greeting=greeting,
        )


@main_bp.route("/api-docs")
@admin_required
def api_docs_page():
        return render_template("api_docs.html")


@main_bp.route("/dj/status")
def dj_status_page():
    """Public DJ status screen."""
    return render_template("dj_status.html")


def _psa_library_root():
    root = current_app.config.get("PSA_LIBRARY_PATH") or os.path.join(current_app.instance_path, "psa")
    os.makedirs(root, exist_ok=True)
    return root


def _media_roots() -> list[tuple[str, str]]:
    roots: list[tuple[str, str]] = [("PSA", _psa_library_root())]
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


@main_bp.route("/psa/player")
def psa_player():
    psa_root = _psa_library_root()
    resp = make_response(render_template("psa_player.html", psa_root=psa_root))
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@main_bp.route("/media/file/<path:token>")
def media_file(token: str):
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
    except Exception:
        abort(404)
    full = os.path.abspath(decoded)
    allowed = False
    for _, root in _media_roots():
        root_abs = os.path.abspath(root)
        if full.startswith(root_abs):
            allowed = True
            break
    if not allowed or not os.path.isfile(full):
        abort(404)
<<<<<<< codex/integrate-advanced-features-into-show-recorder-180zs2
    resp = send_file(full, conditional=True)
=======
    resp = send_file(full)
>>>>>>> main
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


@main_bp.route("/djs")
@admin_required
def list_djs():
        djs = DJ.query.order_by(DJ.last_name, DJ.first_name).all()
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

        show_payload = [
                {
                        "id": s.id,
                        "name": s.show_name or f"{s.host_first_name} {s.host_last_name}",
                        "schedule": f"{s.days_of_week} {s.start_time}-{s.end_time}",
                        "hosts": [d.id for d in s.djs],
                }
                for s in shows
        ]
        dj_payload = [
                {"id": d.id, "first_name": d.first_name, "last_name": d.last_name}
                for d in djs
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


def _safe_music_path(path: str) -> str:
    root = current_app.config.get("NAS_MUSIC_ROOT") or ""
    if not root:
        abort(404)
    normalized = os.path.realpath(path)
    root_norm = os.path.realpath(root)
    if not normalized.startswith(root_norm):
        abort(403)
    if not os.path.exists(normalized):
        abort(404)
    return normalized


@main_bp.route("/music/stream")
@permission_required({"music:view"})
def music_stream():
    path = request.args.get("path")
    if not path:
        abort(404)
    safe_path = _safe_music_path(path)
    return send_file(safe_path, conditional=True)


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
            explicit_results = audit_explicit_music(rate_limit_s=rate, max_files=limit)
    return render_template(
        "audit.html",
        recordings_results=recordings_results,
        explicit_results=explicit_results,
        default_rate=current_app.config["AUDIT_ITUNES_RATE_LIMIT_SECONDS"],
        default_limit=current_app.config["AUDIT_MUSIC_MAX_FILES"],
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


@main_bp.route("/archivist", methods=["GET", "POST"])
@permission_required({"music:view"})
def archivist_page():
    import_count = None
    if request.method == "POST":
        file = request.files.get("archive_file")
        if not file or file.filename == "":
            flash("Please choose a CSV/TSV file to import.", "warning")
        else:
            try:
                upload_dir = current_app.config.get("ARCHIVIST_UPLOAD_DIR")
                count = import_archivist_csv(
                    file,
                    current_app.config.get("ARCHIVIST_DB_PATH"),
                    upload_dir,
                )
                import_count = count
                flash(f"Imported {count} rows into the archivist database.", "success")
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed to import archivist data: {exc}")
                flash("Import failed. Please check the file format.", "danger")

    query = (request.args.get("q") or "").strip()
    show_all = request.args.get("show_all") == "1" or query == "%"
    results = []
    if query or show_all:
        limit = 500 if show_all else 200
        results = search_archivist(query or "%", limit=limit)

    total = ArchivistEntry.query.count()
    return render_template(
        "archivist.html",
        results=results,
        query=query,
        total=total,
        import_count=import_count,
        show_all=show_all,
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


@main_bp.route("/social/uploads/<path:filename>")
def social_upload(filename: str):
    upload_dir = current_app.config.get("SOCIAL_UPLOAD_DIR") or os.path.join(current_app.instance_path, "social_uploads")
    return send_from_directory(upload_dir, filename, as_attachment=False)


@main_bp.route("/djs/photos/<path:filename>")
def dj_photo(filename: str):
    upload_dir = current_app.config.get("DJ_PHOTO_UPLOAD_DIR") or os.path.join(current_app.instance_path, "dj_photos")
    return send_from_directory(upload_dir, filename, as_attachment=False)


@main_bp.route("/social/post", methods=["GET", "POST"])
@permission_required({"social:post"})
def social_post_page():
    upload_dir = current_app.config.get("SOCIAL_UPLOAD_DIR") or os.path.join(current_app.instance_path, "social_uploads")
    os.makedirs(upload_dir, exist_ok=True)

    platform_tokens = {
        "facebook": bool(current_app.config.get("SOCIAL_FACEBOOK_PAGE_TOKEN")),
        "instagram": bool(current_app.config.get("SOCIAL_INSTAGRAM_TOKEN")),
        "twitter": bool(
            current_app.config.get("SOCIAL_TWITTER_BEARER_TOKEN")
            or (
                current_app.config.get("SOCIAL_TWITTER_CONSUMER_KEY")
                and current_app.config.get("SOCIAL_TWITTER_CONSUMER_SECRET")
                and current_app.config.get("SOCIAL_TWITTER_ACCESS_TOKEN")
                and current_app.config.get("SOCIAL_TWITTER_ACCESS_SECRET")
            )
        ),
        "bluesky": bool(current_app.config.get("SOCIAL_BLUESKY_HANDLE") and current_app.config.get("SOCIAL_BLUESKY_PASSWORD")),
    }

    if request.method == "POST":
        content = (request.form.get("content") or "").strip()
        image_url = (request.form.get("image_url") or "").strip() or None
        platforms = request.form.getlist("platforms")
        image_path = None

        if not content:
            flash("Please enter content for the post.", "warning")
            return redirect(url_for("main.social_post_page"))
        if not platforms:
            platforms = [p for p, enabled in platform_tokens.items() if enabled] or ["facebook", "instagram", "twitter", "bluesky"]

        file = request.files.get("image_file")
        if file and file.filename:
            fname = secure_filename(file.filename)
            timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
            stored_name = f"{timestamp}_{fname}"
            file.save(os.path.join(upload_dir, stored_name))
            image_path = stored_name
            image_url = url_for("main.social_upload", filename=stored_name, _external=True)

        post = SocialPost(content=content, image_url=image_url, image_path=image_path, platforms=json.dumps(platforms))
        db.session.add(post)
        db.session.commit()
        statuses = send_social_post(post, current_app.config)
        pretty = ", ".join(f"{k}:{v}" for k, v in statuses.items())
        flash(f"Post queued ({pretty}).", "success")
        return redirect(url_for("main.social_post_page"))

    posts = SocialPost.query.order_by(SocialPost.created_at.desc()).limit(25).all()
    for p in posts:
        try:
            p.status_map = json.loads(p.result_log) if p.result_log else {}
        except json.JSONDecodeError:
            p.status_map = {}
    return render_template("social_post.html", posts=posts, config=current_app.config, platform_tokens=platform_tokens)

@main_bp.route('/login', methods=['GET'])
def login():
    """OAuth-first login landing page with master admin link."""

    ensure_oauth_initialized(current_app)

    google_client = oauth.create_client("google")
    discord_client = oauth.create_client("discord")
    oauth_enabled = google_client is not None or discord_client is not None
    allowed_domain = _clean_optional(current_app.config.get("OAUTH_ALLOWED_DOMAIN"))

    return render_template(
        'login.html',
        oauth_enabled=oauth_enabled,
        oauth_google_enabled=google_client is not None,
        oauth_discord_enabled=discord_client is not None,
        oauth_allowed_domain=allowed_domain,
    )


@main_bp.route('/login/master', methods=['GET', 'POST'])
def master_login():
    """Password-only master admin login for emergency/owner access."""

    if request.method == 'POST':
        password = request.form.get('password')
        if password and password == current_app.config['ADMIN_PASSWORD']:
            session['authenticated'] = True
            session['role'] = 'admin'
            session['display_name'] = current_app.config.get('ADMIN_USERNAME', 'Master Admin')
            session['auth_provider'] = 'master'
            logger.info("Master admin logged in via password.")
            flash("You are now logged in as master admin.", "success")
            return redirect(url_for('main.dashboard'))
        flash("Invalid master admin password.", "danger")

    return render_template('master_login.html')


@main_bp.route("/login/oauth/google")
def login_oauth_google():
        """Start a Google OAuth login."""

        ensure_oauth_initialized(current_app)

        client = oauth.create_client("google")
        if client is None:
                flash("Google OAuth is not configured. Please add a client id/secret in Settings.", "danger")
                return redirect(url_for("main.login"))

        redirect_uri = url_for("main.oauth_callback_google", _external=True)
        nonce = secrets.token_urlsafe(16)
        session["oauth_google_nonce"] = nonce
        return client.authorize_redirect(redirect_uri, nonce=nonce)


@main_bp.route("/social/oauth/twitter/start")
@admin_required
def social_oauth_twitter_start():
        """Start a Twitter/X OAuth2 PKCE flow for social posting token capture (not login)."""

        client_id = _clean_optional(current_app.config.get("SOCIAL_TWITTER_CLIENT_ID"))
        if not client_id:
                flash("Twitter client ID is required to start OAuth.", "danger")
                return redirect(url_for("main.settings") + "#tab-social")

        redirect_uri = url_for("main.social_oauth_twitter_callback", _external=True)
        state = secrets.token_urlsafe(16)
        verifier, challenge = _pkce_pair()
        session["twitter_oauth_state"] = state
        session["twitter_code_verifier"] = verifier

        scopes = "tweet.read tweet.write offline.access"
        auth_url = (
            "https://twitter.com/i/oauth2/authorize?response_type=code"
            f"&client_id={quote_plus(client_id)}"
            f"&redirect_uri={quote_plus(redirect_uri)}"
            f"&scope={quote_plus(scopes)}"
            f"&state={quote_plus(state)}"
            f"&code_challenge={quote_plus(challenge)}"
            "&code_challenge_method=S256"
        )
        return redirect(auth_url)


@main_bp.route("/social/oauth/twitter/callback")
def social_oauth_twitter_callback():
        """Handle Twitter/X OAuth2 callback and persist the user bearer token for social posting."""

        state = request.args.get("state")
        code = request.args.get("code")
        expected_state = session.pop("twitter_oauth_state", None)
        verifier = session.pop("twitter_code_verifier", None)

        redirect_target = url_for("main.settings") + "#tab-social"

        if not code:
                flash("Missing OAuth code from Twitter.", "danger")
                return redirect(redirect_target)

        if not expected_state or state != expected_state:
                flash("Invalid OAuth state for Twitter flow.", "danger")
                return redirect(redirect_target)

        client_id = _clean_optional(current_app.config.get("SOCIAL_TWITTER_CLIENT_ID"))
        client_secret = _clean_optional(current_app.config.get("SOCIAL_TWITTER_CLIENT_SECRET"))
        if not client_id or not verifier:
                flash("Twitter OAuth is not configured correctly (client ID / PKCE).", "danger")
                return redirect(redirect_target)

        redirect_uri = url_for("main.social_oauth_twitter_callback", _external=True)
        data = {
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        # Twitter's token endpoint expects Basic auth even for PKCE; for public
        # clients the secret may be blank, but the header must still be present.
        basic_token = base64.b64encode(f"{client_id}:{client_secret or ''}".encode()).decode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic_token}",
        }
        try:
            resp = requests.post(
                "https://api.twitter.com/2/oauth2/token",
                data=data,
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            token = resp.json()
            session["last_oauth_token"] = _serialize_oauth_token(token, "twitter")
            session["last_oauth_token_twitter"] = session["last_oauth_token"]

            bearer = token.get("access_token")
            if bearer:
                update_user_config({"SOCIAL_TWITTER_BEARER_TOKEN": bearer})
                flash("Twitter/X OAuth token captured and saved to Social settings for posting.", "success")
            else:
                flash("Twitter/X OAuth token response did not include an access token.", "warning")

            return redirect(redirect_target)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Twitter OAuth exchange failed: {exc}")
            flash("Twitter OAuth token exchange failed. Check credentials and try again.", "danger")
            return redirect(redirect_target)


@main_bp.route("/login/oauth/google/callback")
def oauth_callback_google():
        """Handle Google OAuth callback and establish a session."""

        ensure_oauth_initialized(current_app)

        client = oauth.create_client("google")
        if client is None:
                flash("OAuth is not configured.", "danger")
                return redirect(url_for("main.login"))

        try:
                token = client.authorize_access_token()
                session["last_oauth_token"] = _serialize_oauth_token(token, "google")
                nonce = session.pop("oauth_google_nonce", None)
                userinfo = None
                try:
                        userinfo = client.parse_id_token(token, nonce=nonce)
                except Exception as parse_exc:  # noqa: BLE001
                        logger.warning(f"ID token parse failed ({parse_exc}); falling back to userinfo endpoint.")
                        userinfo = None
                if not userinfo:
                        resp = client.get("userinfo")
                        userinfo = resp.json() if resp else {}
        except Exception as exc:  # noqa: BLE001
                logger.error(f"OAuth login failed: {exc}")
                flash("OAuth login failed. Please verify the Google client credentials and redirect URI.", "danger")
                return redirect(url_for("main.login"))

        email = (userinfo or {}).get("email")
        if not email:
                flash("OAuth login failed: missing email.", "danger")
                return redirect(url_for("main.login"))

        allowed_domain = _clean_optional(current_app.config.get("OAUTH_ALLOWED_DOMAIN"))
        if allowed_domain and not email.lower().endswith(f"@{allowed_domain.lower()}"):
                logger.warning("OAuth login blocked due to domain restriction.")
                flash("Your account is not permitted to log in with this station.", "danger")
                return redirect(url_for("main.login"))

        external_id = (userinfo or {}).get("sub")
        suggested_name = (userinfo or {}).get("name") or email.split("@")[0]
        existing = _find_user("google", external_id, email, None)

        if existing:
                _add_identity(existing, "google", external_id, email)
                db.session.commit()
                if existing.rejected or existing.approval_status == 'rejected':
                        flash("Your account request was rejected. Please contact an administrator.", "danger")
                        return redirect(url_for('main.login'))
                if existing.approved or existing.approval_status == 'approved':
                        _complete_login(existing)
                        logger.info("Admin logged in via Google OAuth.")
                        flash("You are now logged in via Google.", "success")
                        return redirect(url_for('main.dashboard'))
                session['pending_user_id'] = existing.id
                flash("Your account is pending approval.", "info")
                return redirect(url_for('main.oauth_pending'))

        merge_candidate = None
        if suggested_name:
                merge_candidate = User.query.filter(User.display_name.ilike(suggested_name)).first()

        profile = {
                "provider": "google",
                "email": email,
                "external_id": external_id,
                "suggested_name": suggested_name,
        }
        if merge_candidate:
                profile["merge_candidate_id"] = merge_candidate.id
        return _redirect_pending(profile)


@main_bp.route("/login/oauth/discord")
def login_oauth_discord():
        """Start a Discord OAuth login."""

        ensure_oauth_initialized(current_app)

        client = oauth.create_client("discord")
        if client is None:
                flash("Discord OAuth is not configured. Please add the Discord client id/secret in Settings.", "danger")
                return redirect(url_for("main.login"))

        redirect_uri = url_for("main.oauth_callback_discord", _external=True)
        return client.authorize_redirect(redirect_uri)


@main_bp.route("/login/oauth/discord/callback")
def oauth_callback_discord():
        """Handle Discord OAuth callback and establish a session."""

        ensure_oauth_initialized(current_app)

        client = oauth.create_client("discord")
        if client is None:
                flash("Discord OAuth is not configured.", "danger")
                return redirect(url_for("main.login"))

        try:
                token = client.authorize_access_token()
                session["last_oauth_token"] = _serialize_oauth_token(token, "discord")
                userinfo_resp = client.get("users/@me")
                userinfo = userinfo_resp.json() if userinfo_resp else {}
        except Exception as exc:  # noqa: BLE001
                logger.error(f"Discord OAuth login failed: {exc}")
                flash("Discord login failed. Please try again or contact an admin.", "danger")
                return redirect(url_for("main.login"))

        if not userinfo:
                flash("Discord login failed: missing user info.", "danger")
                return redirect(url_for("main.login"))

        email = userinfo.get("email")
        if not email:
                logger.warning("Discord OAuth did not return an email; access denied.")
                flash("Discord account is missing an email address; cannot log in.", "danger")
                return redirect(url_for("main.login"))

        allowed_guild_id = _clean_optional(current_app.config.get("DISCORD_ALLOWED_GUILD_ID"))
        guild_member = True
        if allowed_guild_id:
                try:
                        guilds_resp = client.get("users/@me/guilds")
                        guilds = guilds_resp.json() if guilds_resp else []
                        guild_member = any(str(g.get("id")) == str(allowed_guild_id) for g in guilds)
                except Exception as exc:  # noqa: BLE001
                        logger.error(f"Discord guild lookup failed: {exc}")
                        flash("Discord login failed while checking permissions.", "danger")
                        return redirect(url_for("main.login"))

        external_id = (userinfo or {}).get("id")
        suggested_name = userinfo.get("global_name") or userinfo.get("username") or email.split("@")[0]
        existing = _find_user("discord", external_id, email, None)

        if existing:
                _add_identity(existing, "discord", external_id, email)
                db.session.commit()
                if existing.rejected or existing.approval_status == 'rejected':
                        flash("Your account request was rejected. Please contact an administrator.", "danger")
                        return redirect(url_for('main.login'))
                if existing.approved or existing.approval_status == 'approved':
                        _complete_login(existing)
                        logger.info("Admin logged in via Discord OAuth.")
                        flash("You are now logged in via Discord.", "success")
                        return redirect(url_for('main.dashboard'))
                session['pending_user_id'] = existing.id
                flash("Your account is pending approval.", "info")
                return redirect(url_for('main.oauth_pending'))

        if allowed_guild_id and not guild_member:
                flash("Please submit your name to request access; you aren't in the authorized Discord guild yet.", "info")

        merge_candidate = None
        if suggested_name:
                merge_candidate = User.query.filter(User.display_name.ilike(suggested_name)).first()

        profile = {
                "provider": "discord",
                "email": email,
                "external_id": external_id,
                "suggested_name": suggested_name,
        }
        if merge_candidate:
                profile["merge_candidate_id"] = merge_candidate.id
        return _redirect_pending(profile)


@main_bp.route("/api/oauth/last-token")
@admin_required
def last_oauth_token():
    """Return the most recent OAuth token captured in this session.

    This is intended for debugging/verification and is restricted to admin roles.
    """

    token = session.get("last_oauth_token")
    if not token:
        return jsonify({"status": "empty", "message": "No OAuth token captured in this session."}), 404
    return jsonify({"status": "ok", "token": token})


@main_bp.route("/api/oauth/x-token")
@admin_required
def twitter_oauth_token():
    token = session.get("last_oauth_token_twitter") or session.get("last_oauth_token")
    if not token or token.get("provider") != "twitter":
        return jsonify({"status": "empty", "message": "No Twitter/X OAuth token captured in this session."}), 404
    return jsonify({"status": "ok", "token": token})

@main_bp.route('/logout')
def logout():
        """Logout route to clear the session."""

        try:
                session.pop('authenticated', None)
                session.pop('user_email', None)
                session.pop('auth_provider', None)
                session.pop('role', None)
                session.pop('permissions', None)
                session.pop('display_name', None)
                session.pop('pending_user_id', None)
                session.pop('pending_oauth', None)
                logger.info("Admin logged out successfully.")
                flash("You have successfully logged out.", "success")
                return redirect(url_for('main.login'))  #Change to index if index ever exists as other than redirect
        except Exception as e:
                logger.error(f"Error logging out: {e}")
                flash(f"Error logging out: {e}", "danger")
                return redirect(url_for('main.shows'))


@main_bp.route("/login/claim", methods=["GET", "POST"])
def oauth_claim():
        pending = session.get("pending_oauth")
        if not pending:
                flash("No pending OAuth request found.", "warning")
                return redirect(url_for("main.login"))

        if request.method == "POST":
            display_name = request.form.get("display_name")
            if not display_name:
                flash("Please provide your name to continue.", "danger")
                return redirect(url_for("main.oauth_claim"))

            merge_candidate_id = pending.get("merge_candidate_id")
            target = None
            if merge_candidate_id:
                target = User.query.get(merge_candidate_id)
            if not target:
                target = _find_user(pending.get("provider"), pending.get("external_id"), pending.get("email"), None)

            if target:
                target.display_name = display_name or target.display_name
                if pending.get("email") and not target.email:
                    target.email = pending.get("email")
                if pending.get("provider") and not target.provider:
                    target.provider = pending.get("provider")
                if pending.get("external_id") and not target.external_id:
                    target.external_id = str(pending.get("external_id"))
                target.rejected = False
                target.approval_status = target.approval_status or 'pending'
                target.requested_at = datetime.utcnow()
                _add_identity(target, pending.get("provider", "oauth"), pending.get("external_id"), pending.get("email"))
                db.session.commit()
                if target.approved or target.approval_status == 'approved':
                    session.pop("pending_oauth", None)
                    _complete_login(target)
                    flash("Profile linked and logged in.", "success")
                    return redirect(url_for('main.dashboard'))
                session['pending_user_id'] = target.id
            else:
                new_user = User(
                    email=pending.get("email"),
                    provider=pending.get("provider", "oauth"),
                    external_id=str(pending.get("external_id")) if pending.get("external_id") else None,
                    display_name=display_name,
                    approved=False,
                    rejected=False,
                    approval_status='pending',
                    role=None,
                    requested_at=datetime.utcnow(),
                )
                _add_identity(new_user, pending.get("provider", "oauth"), pending.get("external_id"), pending.get("email"))
                db.session.add(new_user)
                db.session.commit()
                session['pending_user_id'] = new_user.id

            session.pop("pending_oauth", None)
            flash("Request submitted. An admin will approve your access soon.", "info")
            return redirect(url_for("main.oauth_pending"))

        return render_template("oauth_claim.html", pending=pending)


@main_bp.route("/login/pending")
def oauth_pending():
        pending_id = session.get("pending_user_id")
        user = None
        if pending_id:
                user = User.query.get(pending_id)
        return render_template("oauth_pending.html", user=user)

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
            selected_djs = request.form.getlist('dj_ids')
            dj_objs = DJ.query.filter(DJ.id.in_(selected_djs)).all() if selected_djs else []

            show = Show(
                host_first_name=request.form['host_first_name'],
                host_last_name=request.form['host_last_name'],
                show_name=request.form.get('show_name'),
                genre=request.form.get('genre'),
                description=request.form.get('description'),
                is_regular_host='is_regular_host' in request.form,
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
        all_djs = DJ.query.order_by(DJ.first_name, DJ.last_name).all()
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
            selected_djs = request.form.getlist('dj_ids')
            dj_objs = DJ.query.filter(DJ.id.in_(selected_djs)).all() if selected_djs else []

            show.host_first_name = request.form['host_first_name']
            show.host_last_name = request.form['host_last_name']
            show.show_name = request.form.get('show_name')
            show.genre = request.form.get('genre')
            show.description = request.form.get('description')
            show.is_regular_host = 'is_regular_host' in request.form
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
        all_djs = DJ.query.order_by(DJ.first_name, DJ.last_name).all()
        selected_ids = {dj.id for dj in show.djs}
        return render_template('edit_show.html', show=show, djs=all_djs, selected_ids=selected_ids)
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
    'SOCIAL_SEND_ENABLED', 'SOCIAL_DRY_RUN', 'SOCIAL_FACEBOOK_PAGE_TOKEN', 'SOCIAL_INSTAGRAM_TOKEN',
    'SOCIAL_TWITTER_BEARER_TOKEN', 'SOCIAL_TWITTER_CONSUMER_KEY', 'SOCIAL_TWITTER_CONSUMER_SECRET',
    'SOCIAL_TWITTER_ACCESS_TOKEN', 'SOCIAL_TWITTER_ACCESS_SECRET', 'SOCIAL_TWITTER_CLIENT_ID', 'SOCIAL_TWITTER_CLIENT_SECRET', 'SOCIAL_BLUESKY_HANDLE', 'SOCIAL_BLUESKY_PASSWORD', 'SOCIAL_UPLOAD_DIR',
    'RATE_LIMIT_ENABLED', 'RATE_LIMIT_REQUESTS', 'RATE_LIMIT_WINDOW_SECONDS', 'RATE_LIMIT_TRUSTED_IPS',
    'HIGH_CONTRAST_DEFAULT', 'FONT_SCALE_PERCENT', 'PSA_LIBRARY_PATH'
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
                'SOCIAL_SEND_ENABLED': 'social_send_enabled' in request.form,
                'SOCIAL_DRY_RUN': 'social_dry_run' in request.form,
                'SOCIAL_FACEBOOK_PAGE_TOKEN': _clean_optional(request.form.get('social_facebook_page_token', '').strip()),
                'SOCIAL_INSTAGRAM_TOKEN': _clean_optional(request.form.get('social_instagram_token', '').strip()),
                'SOCIAL_TWITTER_BEARER_TOKEN': _clean_optional(request.form.get('social_twitter_bearer_token', '').strip()),
                'SOCIAL_TWITTER_CONSUMER_KEY': _clean_optional(request.form.get('social_twitter_consumer_key', '').strip()),
                'SOCIAL_TWITTER_CONSUMER_SECRET': _clean_optional(request.form.get('social_twitter_consumer_secret', '').strip()),
                'SOCIAL_TWITTER_ACCESS_TOKEN': _clean_optional(request.form.get('social_twitter_access_token', '').strip()),
                'SOCIAL_TWITTER_ACCESS_SECRET': _clean_optional(request.form.get('social_twitter_access_secret', '').strip()),
                'SOCIAL_TWITTER_CLIENT_ID': _clean_optional(request.form.get('social_twitter_client_id', '').strip()),
                'SOCIAL_TWITTER_CLIENT_SECRET': _clean_optional(request.form.get('social_twitter_client_secret', '').strip()),
                'SOCIAL_BLUESKY_HANDLE': _clean_optional(request.form.get('social_bluesky_handle', '').strip()),
                'SOCIAL_BLUESKY_PASSWORD': _clean_optional(request.form.get('social_bluesky_password', '').strip()),
                'SOCIAL_UPLOAD_DIR': _clean_optional(request.form.get('social_upload_dir', '').strip()) or config.get('SOCIAL_UPLOAD_DIR'),
                'AUDIO_HOST_UPLOAD_DIR': request.form.get('audio_host_upload_dir', current_app.config.get('AUDIO_HOST_UPLOAD_DIR')).strip(),
                'AUDIO_HOST_BACKDROP_DEFAULT': request.form.get('audio_host_backdrop_default', current_app.config.get('AUDIO_HOST_BACKDROP_DEFAULT', '')).strip(),
            }

            update_user_config(updated_settings)
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
        'pause_shows_recording': config.get('PAUSE_SHOWS_RECORDING', False),
        'social_send_enabled': config.get('SOCIAL_SEND_ENABLED', False),
        'social_dry_run': config.get('SOCIAL_DRY_RUN', True),
        'social_facebook_page_token': _clean_optional(config.get('SOCIAL_FACEBOOK_PAGE_TOKEN', '')) or '',
        'social_instagram_token': _clean_optional(config.get('SOCIAL_INSTAGRAM_TOKEN', '')) or '',
        'social_twitter_bearer_token': _clean_optional(config.get('SOCIAL_TWITTER_BEARER_TOKEN', '')) or '',
        'social_twitter_consumer_key': _clean_optional(config.get('SOCIAL_TWITTER_CONSUMER_KEY', '')) or '',
        'social_twitter_consumer_secret': _clean_optional(config.get('SOCIAL_TWITTER_CONSUMER_SECRET', '')) or '',
        'social_twitter_access_token': _clean_optional(config.get('SOCIAL_TWITTER_ACCESS_TOKEN', '')) or '',
        'social_twitter_access_secret': _clean_optional(config.get('SOCIAL_TWITTER_ACCESS_SECRET', '')) or '',
        'social_twitter_client_id': _clean_optional(config.get('SOCIAL_TWITTER_CLIENT_ID', '')) or '',
        'social_twitter_client_secret': _clean_optional(config.get('SOCIAL_TWITTER_CLIENT_SECRET', '')) or '',
        'social_bluesky_handle': _clean_optional(config.get('SOCIAL_BLUESKY_HANDLE', '')) or '',
        'social_bluesky_password': _clean_optional(config.get('SOCIAL_BLUESKY_PASSWORD', '')) or '',
        'social_upload_dir': config.get('SOCIAL_UPLOAD_DIR', ''),
        'audio_host_upload_dir': config.get('AUDIO_HOST_UPLOAD_DIR', ''),
        'audio_host_backdrop_default': config.get('AUDIO_HOST_BACKDROP_DEFAULT', ''),
    }

    logger.info(f'Rendering settings page.')
    return render_template('settings.html', **settings_data)


@main_bp.route('/settings/logs')
@admin_required
def view_system_log():
    repo_root = os.path.abspath(os.path.join(current_app.root_path, os.pardir))
    log_path = os.path.join(repo_root, 'ShowRecorder.log')
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
        'ICECAST_STATUS_URL', 'ICECAST_LISTCLIENTS_URL', 'ICECAST_USERNAME', 'ICECAST_PASSWORD', 'ICECAST_MOUNT', 'MUSICBRAINZ_USER_AGENT'
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
