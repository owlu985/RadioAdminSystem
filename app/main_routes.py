from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_file, abort
import json
import os
from .scheduler import refresh_schedule, pause_shows_until
from .utils import update_user_config, get_current_show, format_show_window
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
)
from sqlalchemy import case, func
from functools import wraps
from .logger import init_logger
from app.auth_utils import admin_required
from app.routes.logging_api import logs_bp
from app.services.music_search import search_music, get_track, load_cue, save_cue
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
@admin_required
def shows():
    """Render the shows database page sorted and paginated."""

    day_order = case(
        (Show.days_of_week == 'mon', 1),
        (Show.days_of_week == 'tue', 2),
        (Show.days_of_week == 'wed', 3),
        (Show.days_of_week == 'thu', 4),
        (Show.days_of_week == 'fri', 5),
        (Show.days_of_week == 'sat', 6),
        (Show.days_of_week == 'sun', 7)
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

        return render_template(
                'dashboard.html',
                current_show=current_show,
                current_run=current_run,
                window=window,
                absences=absences,
                health=health,
                listeners=listeners,
        )


@main_bp.route("/api-docs")
@admin_required
def api_docs_page():
    return render_template("api_docs.html")


@main_bp.route("/dj/status")
def dj_status_page():
    """Public DJ status screen."""
    return render_template("dj_status.html")


@main_bp.route("/djs")
@admin_required
def list_djs():
        djs = DJ.query.order_by(DJ.last_name, DJ.first_name).all()
        return render_template("djs_list.html", djs=djs)


@main_bp.route("/djs/discipline", methods=["GET", "POST"])
@admin_required
def manage_dj_discipline():
        djs = DJ.query.order_by(DJ.last_name, DJ.first_name).all()
        if request.method == "POST":
                dj_id = request.form.get("dj_id", type=int)
                severity = request.form.get("severity") or None
                notes = request.form.get("notes") or None
                action_taken = request.form.get("action_taken") or None
                resolved = bool(request.form.get("resolved"))
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
@admin_required
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
                user.permissions = request.form.get('permissions') or None

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
        return render_template("users_manage.html", users=users, role_choices=_role_choices())


@main_bp.route("/djs/add", methods=["GET", "POST"])
@admin_required
def add_dj():
    from app.models import Show
    if request.method == "POST":
        dj = DJ(
            first_name=request.form.get("first_name").strip(),
            last_name=request.form.get("last_name").strip(),
            bio=request.form.get("bio"),
            photo_url=request.form.get("photo_url"),
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
        dj.first_name = request.form.get("first_name").strip()
        dj.last_name = request.form.get("last_name").strip()
        dj.bio = request.form.get("bio")
        dj.photo_url = request.form.get("photo_url")
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
                start_date = request.form.get("start_date")
                start_time = request.form.get("start_time")
                end_date = request.form.get("end_date") or start_date
                end_time = request.form.get("end_time")
                replacement = request.form.get("replacement") or None
                notes = request.form.get("notes") or None

                dj_obj = DJ.query.get(dj_id) if dj_id else None
                if dj_obj:
                        dj_name = f"{dj_obj.first_name} {dj_obj.last_name}"

                show_obj = Show.query.get(show_id) if show_id else None
                if show_obj:
                        show_name = show_obj.show_name or f"{show_obj.host_first_name} {show_obj.host_last_name}"

                if not all([dj_name, show_name, start_date, start_time, end_time]):
                        flash("All fields except replacement/notes are required.", "danger")
                        return redirect(url_for("main.dj_absence_submit"))
                try:
                        start_dt = datetime.fromisoformat(f"{start_date}T{start_time}")
                        end_dt = datetime.fromisoformat(f"{end_date}T{end_time}")
                except Exception:
                        flash("Invalid date or time.", "danger")
                        return redirect(url_for("main.dj_absence_submit"))

                absence = DJAbsence(
                        dj_name=dj_name,
                        show_name=show_name,
                        show_id=int(show_id) if show_id else None,
                        start_time=start_dt,
                        end_time=end_dt,
                        replacement_name=replacement,
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
@admin_required
def manage_absences():
        if request.method == "POST":
                abs_id = request.form.get("absence_id", type=int)
                action = request.form.get("action", "approve")
                absence = DJAbsence.query.get_or_404(abs_id)
                if action == "approve":
                        absence.status = "approved"
                elif action == "reject":
                        absence.status = "rejected"
                elif action == "resolve":
                        absence.status = "resolved"
                db.session.commit()
                flash("Absence updated.", "success")
                return redirect(url_for("main.manage_absences"))

        pending = DJAbsence.query.order_by(DJAbsence.start_time).all()
        return render_template("absence_manage.html", absences=pending)


@main_bp.route("/music/search")
@admin_required
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
@admin_required
def music_stream():
    path = request.args.get("path")
    if not path:
        abort(404)
    safe_path = _safe_music_path(path)
    return send_file(safe_path, conditional=True)


@main_bp.route("/music/detail")
@admin_required
def music_detail_page():
    path = request.args.get("path")
    track = get_track(path) if path else None
    return render_template("music_detail.html", track=track)


@main_bp.route("/music/edit", methods=["GET", "POST"])
@admin_required
def music_edit_page():
    path = request.values.get("path")
    if not path:
        return render_template("music_edit.html", track=None, error="Missing path")
    track = get_track(path)
    if request.method == "POST":
        if not track:
            return render_template("music_edit.html", track=None, error="Track not found")
        if not search_music.__globals__.get("mutagen"):
            return render_template("music_edit.html", track=track, error="Metadata editing requires mutagen installed.")
        try:
            audio = search_music.__globals__["mutagen"].File(path, easy=True)
            if not audio:
                return render_template("music_edit.html", track=track, error="Unsupported file format.")
            for field in ["title","artist","album","composer","isrc","year","track","disc","copyright"]:
                val = request.form.get(field) or None
                if val:
                    audio[field] = [val]
                elif field in audio:
                    del audio[field]
            audio.save()
            track = get_track(path)
            flash("Metadata updated.", "success")
            return redirect(url_for("main.music_detail_page", path=path))
        except Exception as exc:  # noqa: BLE001
            return render_template("music_edit.html", track=track, error=str(exc))
    return render_template("music_edit.html", track=track, error=None)


@main_bp.route("/music/cue", methods=["GET", "POST"])
@admin_required
def music_cue_page():
    path = request.values.get("path")
    if not path:
        return render_template("music_edit.html", track=None, error="Missing path for cue editor")
    track = get_track(path)
    cue_obj = load_cue(path)
    cue = {
        "cue_in": cue_obj.cue_in if cue_obj else None,
        "intro": cue_obj.intro if cue_obj else None,
        "outro": cue_obj.outro if cue_obj else None,
        "cue_out": cue_obj.cue_out if cue_obj else None,
        "hook_in": cue_obj.hook_in if cue_obj else None,
        "hook_out": cue_obj.hook_out if cue_obj else None,
        "start_next": cue_obj.start_next if cue_obj else None,
        "fade_in": cue_obj.fade_in if cue_obj else None,
        "fade_out": cue_obj.fade_out if cue_obj else None,
    }
    if request.method == "POST":
        payload = {}
        for field in ["cue_in", "intro", "outro", "cue_out", "hook_in", "hook_out", "start_next", "fade_in", "fade_out"]:
            val = request.form.get(field)
            payload[field] = float(val) if val else None
        cue_obj = save_cue(path, payload)
        cue.update(payload)
        flash("CUE points saved.", "success")
        return redirect(url_for("main.music_cue_page", path=path))
    return render_template("music_cue.html", track=track, cue=cue)


@main_bp.route("/audit", methods=["GET", "POST"])
@admin_required
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
@admin_required
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
@admin_required
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
@admin_required
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


@main_bp.route("/social/post", methods=["GET", "POST"])
@admin_required
def social_post_page():
    if request.method == "POST":
        content = (request.form.get("content") or "").strip()
        image_url = (request.form.get("image_url") or "").strip() or None
        platforms = request.form.getlist("platforms")
        if not content:
            flash("Please enter content for the post.", "warning")
            return redirect(url_for("main.social_post_page"))
        if not platforms:
            platforms = ["facebook", "instagram", "twitter", "bluesky"]
        post = SocialPost(content=content, image_url=image_url, platforms=json.dumps(platforms))
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
    return render_template("social_post.html", posts=posts, config=current_app.config)

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
        return client.authorize_redirect(redirect_uri)


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
                userinfo = client.parse_id_token(token)
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
@admin_required
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

            #if end_time_obj == time(0, 0) and start_time_obj != time(0, 0):
            #    pass
            #elif end_time_obj <= start_time_obj:
            #    flash("End time cannot be before start time!", "danger")
            #    return redirect(url_for('main.add_show'))

            short_day_name = request.form['days_of_week'].lower()[:3]

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
                days_of_week=short_day_name
            )
            db.session.add(show)
            db.session.commit()
            refresh_schedule()
            logger.info("Show added successfully.")
            flash("Show added successfully!", "success")
            return redirect(url_for('main.shows'))

        logger.info("Rendering add show page.")
        return render_template('add_show.html', config=current_app.config)
    except Exception as e:
        logger.error(f"Error adding show: {e}")
        flash(f"Error adding show: {e}", "danger")
        return redirect(url_for('main.shows'))

@main_bp.route('/show/edit/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_show(id):
    """Route to edit an existing show."""

    show = Show.query.get_or_404(id)
    try:
        if request.method == 'POST':
            short_day_name = request.form['days_of_week'].lower()[:3]

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
            show.days_of_week = short_day_name

            db.session.commit()
            refresh_schedule()
            logger.info("Show updated successfully.")
            flash("Show updated successfully!", "success")

            return redirect(url_for('main.shows'))

        logger.info(f'Rendering edit show page for show {id}.')
        return render_template('edit_show.html', show=show)
    except Exception as e:
        logger.error(f"Error editing show: {e}")
        flash(f"Error editing show: {e}", "danger")
        return redirect(url_for('main.shows'))

ALLOWED_SETTINGS_KEYS = [
    'ADMIN_USERNAME', 'ADMIN_PASSWORD', 'STREAM_URL', 'OUTPUT_FOLDER', 'DEFAULT_START_DATE', 'DEFAULT_END_DATE',
    'AUTO_CREATE_SHOW_FOLDERS', 'STATION_NAME', 'STATION_SLOGAN', 'STATION_BACKGROUND', 'TEMPEST_API_KEY',
    'TEMPEST_STATION_ID', 'ALERTS_ENABLED', 'ALERTS_DRY_RUN', 'ALERTS_DISCORD_WEBHOOK', 'ALERTS_EMAIL_ENABLED',
    'ALERTS_EMAIL_TO', 'ALERTS_EMAIL_FROM', 'ALERTS_SMTP_SERVER', 'ALERTS_SMTP_PORT', 'ALERTS_SMTP_USERNAME',
    'ALERTS_SMTP_PASSWORD', 'ALERT_DEAD_AIR_THRESHOLD_MINUTES', 'ALERT_STREAM_DOWN_THRESHOLD_MINUTES',
    'ALERT_REPEAT_MINUTES', 'OAUTH_CLIENT_ID', 'OAUTH_CLIENT_SECRET', 'OAUTH_ALLOWED_DOMAIN',
    'DISCORD_OAUTH_CLIENT_ID', 'DISCORD_OAUTH_CLIENT_SECRET', 'DISCORD_ALLOWED_GUILD_ID',
    'ICECAST_STATUS_URL', 'ICECAST_USERNAME', 'ICECAST_PASSWORD', 'ICECAST_MOUNT', 'SELF_HEAL_ENABLED',
    'MUSICBRAINZ_USER_AGENT', 'ICECAST_ANALYTICS_INTERVAL_MINUTES', 'SETTINGS_BACKUP_INTERVAL_HOURS',
    'SETTINGS_BACKUP_RETENTION', 'DATA_BACKUP_DIRNAME', 'DATA_BACKUP_RETENTION_DAYS', 'THEME_DEFAULT', 'INLINE_HELP_ENABLED', 'ARCHIVIST_DB_PATH', 'ARCHIVIST_UPLOAD_DIR',
    'SOCIAL_SEND_ENABLED', 'SOCIAL_DRY_RUN', 'SOCIAL_FACEBOOK_PAGE_TOKEN', 'SOCIAL_INSTAGRAM_TOKEN',
    'SOCIAL_TWITTER_BEARER_TOKEN', 'SOCIAL_BLUESKY_HANDLE', 'SOCIAL_BLUESKY_PASSWORD',
    'RATE_LIMIT_ENABLED', 'RATE_LIMIT_REQUESTS', 'RATE_LIMIT_WINDOW_SECONDS', 'RATE_LIMIT_TRUSTED_IPS'
]


def _clean_optional(value):
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"", "none", "null"}:
        return None
    return value


@main_bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings():
    """Route to update the application settings."""

    if request.method == 'POST':
        try:
            updated_settings = {
                'ADMIN_USERNAME': request.form['admin_username'],
                'ADMIN_PASSWORD': request.form['admin_password'],
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
                'ICECAST_USERNAME': _clean_optional(request.form.get('icecast_username', '').strip()),
                'ICECAST_PASSWORD': _clean_optional(request.form.get('icecast_password', '').strip()),
                'ICECAST_MOUNT': _clean_optional(request.form.get('icecast_mount', '').strip()),
                'ICECAST_ANALYTICS_INTERVAL_MINUTES': int(request.form.get('icecast_analytics_interval_minutes') or current_app.config.get('ICECAST_ANALYTICS_INTERVAL_MINUTES', 5)),
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
                'ARCHIVIST_DB_PATH': request.form.get('archivist_db_path', current_app.config.get('ARCHIVIST_DB_PATH', '')).strip(),
                'ARCHIVIST_UPLOAD_DIR': request.form.get('archivist_upload_dir', current_app.config.get('ARCHIVIST_UPLOAD_DIR', '')).strip(),
                'SOCIAL_SEND_ENABLED': 'social_send_enabled' in request.form,
                'SOCIAL_DRY_RUN': 'social_dry_run' in request.form,
                'SOCIAL_FACEBOOK_PAGE_TOKEN': _clean_optional(request.form.get('social_facebook_page_token', '').strip()),
                'SOCIAL_INSTAGRAM_TOKEN': _clean_optional(request.form.get('social_instagram_token', '').strip()),
                'SOCIAL_TWITTER_BEARER_TOKEN': _clean_optional(request.form.get('social_twitter_bearer_token', '').strip()),
                'SOCIAL_BLUESKY_HANDLE': _clean_optional(request.form.get('social_bluesky_handle', '').strip()),
                'SOCIAL_BLUESKY_PASSWORD': _clean_optional(request.form.get('social_bluesky_password', '').strip()),
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
        'icecast_username': _clean_optional(config.get('ICECAST_USERNAME', '')) or '',
        'icecast_password': _clean_optional(config.get('ICECAST_PASSWORD', '')) or '',
        'icecast_mount': _clean_optional(config.get('ICECAST_MOUNT', '')) or '',
        'icecast_analytics_interval_minutes': config.get('ICECAST_ANALYTICS_INTERVAL_MINUTES', 5),
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
        'archivist_db_path': config.get('ARCHIVIST_DB_PATH', ''),
        'archivist_upload_dir': config.get('ARCHIVIST_UPLOAD_DIR', ''),
        'social_send_enabled': config.get('SOCIAL_SEND_ENABLED', False),
        'social_dry_run': config.get('SOCIAL_DRY_RUN', True),
        'social_facebook_page_token': _clean_optional(config.get('SOCIAL_FACEBOOK_PAGE_TOKEN', '')) or '',
        'social_instagram_token': _clean_optional(config.get('SOCIAL_INSTAGRAM_TOKEN', '')) or '',
        'social_twitter_bearer_token': _clean_optional(config.get('SOCIAL_TWITTER_BEARER_TOKEN', '')) or '',
        'social_bluesky_handle': _clean_optional(config.get('SOCIAL_BLUESKY_HANDLE', '')) or '',
        'social_bluesky_password': _clean_optional(config.get('SOCIAL_BLUESKY_PASSWORD', '')) or '',
    }

    logger.info(f'Rendering settings page.')
    return render_template('settings.html', **settings_data)


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
        'ICECAST_STATUS_URL', 'ICECAST_USERNAME', 'ICECAST_PASSWORD', 'ICECAST_MOUNT', 'MUSICBRAINZ_USER_AGENT'
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
