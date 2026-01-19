from datetime import datetime, time, timedelta
from flask import current_app as app
from .logger import init_logger
from .models import Show, DJAbsence
import threading
import json
import os

config_lock = threading.Lock()
logger = None

def init_utils():
    global logger
    logger = init_logger()
    logger.info("Utils logger initialized.")

def update_user_config(updates):
    """Update the user configuration file and Flask's configuration."""

    config_path = os.path.join(app.instance_path, 'user_config.json')

    def _normalize_optional(val):
        if val is None:
            return None
        if isinstance(val, str) and val.strip().lower() in {"", "none", "null"}:
            return None
        return val

    with config_lock:
        current_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    current_config = json.load(f)
            except Exception as e:
                raise f"Error reading user configuration: {e}"

        current_config.update(updates)

        optional_keys = {
            "TEMPEST_API_KEY",
            "OAUTH_CLIENT_ID",
            "OAUTH_CLIENT_SECRET",
            "OAUTH_ALLOWED_DOMAIN",
            "DISCORD_OAUTH_CLIENT_ID",
            "DISCORD_OAUTH_CLIENT_SECRET",
            "DISCORD_ALLOWED_GUILD_ID",
            "ALERTS_DISCORD_WEBHOOK",
            "ALERTS_EMAIL_TO",
            "ALERTS_EMAIL_FROM",
            "ALERTS_SMTP_SERVER",
            "ALERTS_SMTP_USERNAME",
            "ALERTS_SMTP_PASSWORD",
            "MUSICBRAINZ_USER_AGENT",
        }

        for key in optional_keys:
            if key in current_config:
                current_config[key] = _normalize_optional(current_config[key])

        try:
            with open(config_path, 'w') as f:
                json.dump(current_config, f, indent=4)
        except Exception as e:
            logger.error(f"Error writing user configuration: {e}")

        app.config.update(current_config)
        logger.info(f"User configuration updated successfully with {updates}.")


def _normalize_day(day: str) -> str:
    return day.lower()[:3]


DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def normalize_days_list(days: list[str]) -> str:
    """Normalize and order day strings into a comma-separated list of 3-letter keys."""
    cleaned = []
    for d in days:
        if not d:
            continue
        key = _normalize_day(d)
        if key not in cleaned:
            cleaned.append(key)
    # order by the standard week ordering
    cleaned.sort(key=lambda x: DAY_ORDER.index(x) if x in DAY_ORDER else 99)
    return ",".join(cleaned)


def get_current_show(now: datetime | None = None):
    """
    Return the Show scheduled right now, if any.
    Handles overnight shows where end_time is earlier than start_time.
    """
    if now is None:
        now = datetime.now()

    current_time = now.time()
    day_key = _normalize_day(now.strftime('%a'))
    yesterday_key = _normalize_day((now - timedelta(days=1)).strftime('%a'))

    today_shows = Show.query.all()

    def _has_day(show_obj: Show, key: str) -> bool:
        days = [d.strip() for d in (show_obj.days_of_week or "").split(',') if d.strip()]
        return key in days

    for show in today_shows:
        if not _has_day(show, day_key):
            continue
        if show.start_time <= show.end_time:
            if show.start_time <= current_time < show.end_time:
                return show
        else:
            # Overnight show into next day
            if current_time >= show.start_time or current_time < show.end_time:
                return show

    # Handle shows that began yesterday and end after midnight today
    for show in today_shows:
        if not _has_day(show, yesterday_key):
            continue
        if show.end_time < show.start_time and current_time < show.end_time:
            return show

    return None


def format_show_window(show: Show) -> dict:
    """Return formatted window info for API/UI responses."""
    return {
        "start_time": show.start_time.strftime("%H:%M"),
        "end_time": show.end_time.strftime("%H:%M"),
        "start_date": show.start_date.isoformat(),
        "end_date": show.end_date.isoformat(),
        "days_of_week": show.days_of_week,
    }


def show_host_names(show: Show) -> str:
    names = []
    primary = f"{show.host_first_name} {show.host_last_name}".strip()
    if primary:
        names.append(primary)
    if getattr(show, "djs", None):
        for dj in show.djs:
            cohost = f"{dj.first_name} {dj.last_name}".strip()
            if cohost and cohost not in names:
                names.append(cohost)
    return ", ".join(names)


def show_primary_host(show: Show) -> tuple[str, str]:
    if show.host_first_name or show.host_last_name:
        return show.host_first_name, show.host_last_name
    if getattr(show, "djs", None):
        for dj in show.djs:
            return dj.first_name, dj.last_name
    return "", ""


def show_display_title(show: Show) -> str:
    return show.show_name or show_host_names(show)


def active_absence_for_show(show: Show, *, now: datetime | None = None) -> DJAbsence | None:
    """Return an approved/pending absence covering ``now`` for the given show."""

    if now is None:
        now = datetime.utcnow()

    query = DJAbsence.query.filter(
        DJAbsence.start_time <= now,
        DJAbsence.end_time >= now,
        DJAbsence.status.in_(["approved", "pending"]),
    )
    if getattr(show, "id", None):
        query = query.filter((DJAbsence.show_id == show.id) | (DJAbsence.show_name == show.show_name))
    else:
        query = query.filter(DJAbsence.show_name == show.show_name)
    return query.order_by(DJAbsence.status.desc(), DJAbsence.start_time.desc()).first()


def next_show_occurrence(show: Show, *, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """Return the next scheduled start/end datetimes for a show based on its days_of_week.

    Picks the soonest occurrence on or after ``now`` (default: current time) within the next two weeks.
    Handles overnight shows whose end_time is earlier than start_time by rolling the end into the next day.
    """

    if now is None:
        now = datetime.now()

    days = [d.strip() for d in (show.days_of_week or "").split(',') if d.strip()]
    if not days:
        return None

    today = now.date()
    for offset in range(0, 14):
        candidate = today + timedelta(days=offset)
        key = _normalize_day(candidate.strftime('%a'))
        if key not in days:
            continue
        if show.start_date and candidate < show.start_date:
            continue
        if show.end_date and candidate > show.end_date:
            continue
        start_dt = datetime.combine(candidate, show.start_time)
        end_dt = datetime.combine(candidate, show.end_time)
        if show.end_time <= show.start_time:
            end_dt += timedelta(days=1)
        if start_dt >= now or offset > 0:
            return start_dt, end_dt
    return None
