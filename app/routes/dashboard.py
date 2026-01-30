import random
from datetime import datetime, timedelta

from flask import current_app, redirect, render_template, session, url_for

from app.auth_utils import admin_required
from app.logger import init_logger
from app.main_routes import main_bp
from app.models import DJAbsence, Show
from app.services.health import get_health_snapshot
from app.services.stream_monitor import fetch_icecast_listeners
from app.utils import format_show_window, get_current_show

logger = init_logger()


@main_bp.route('/')
def index():
    """Redirect to login or dashboard depending on authentication."""

    if session.get('authenticated'):
        logger.info("Redirecting to dashboard.")
        return redirect(url_for('main.dashboard'))
    logger.info("Redirecting to login.")
    return redirect(url_for('main.login'))


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
