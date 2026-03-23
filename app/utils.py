from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from functools import lru_cache
from flask import current_app as app
from .logger import init_logger
from .config_utils import normalize_optional_config
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

    with config_lock:
        current_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    current_config = json.load(f)
            except Exception as e:
                raise f"Error reading user configuration: {e}"

        current_config.update(updates)
        normalize_optional_config(current_config)

        try:
            with open(config_path, 'w') as f:
                json.dump(current_config, f, indent=4)
        except Exception as e:
            logger.error(f"Error writing user configuration: {e}")

        app.config.update(current_config)
        logger.info(f"User configuration updated successfully with {updates}.")


def _normalize_day(day: str) -> str:
    return day.lower()[:3]




@lru_cache(maxsize=32)
def _coerce_zoneinfo(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/New_York")


def normalize_timezone_name(name: str | None) -> str:
    if not name:
        return "America/New_York"
    candidate = str(name).strip()
    if not candidate:
        return "America/New_York"
    return getattr(_coerce_zoneinfo(candidate), "key", "America/New_York")


def get_config_timezone_name() -> str:
    name = app.config.get("SCHEDULE_TIMEZONE", "America/New_York") if app else "America/New_York"
    return normalize_timezone_name(name)


def get_config_timezone() -> ZoneInfo:
    return _coerce_zoneinfo(get_config_timezone_name())


def localize_datetime(value: datetime | None, tz: ZoneInfo | None = None) -> datetime | None:
    if value is None:
        return None
    tz = tz or get_config_timezone()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz)


def format_datetime_local(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    localized = localize_datetime(value)
    return localized.strftime(fmt) if localized else ""


def format_date_local(value: datetime | None, fmt: str = "%Y-%m-%d") -> str:
    localized = localize_datetime(value)
    return localized.strftime(fmt) if localized else ""


def datetime_iso_local(value: datetime | None) -> str | None:
    localized = localize_datetime(value)
    return localized.isoformat() if localized else None

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
    cleaned.sort(key=lambda x: DAY_ORDER.index(x) if x in DAY_ORDER else 99)
    return ",".join(cleaned)


def show_occurs_on_date(show: Show, show_date) -> bool:
    if show.start_date and show_date < show.start_date:
        return False
    if show.end_date and show_date > show.end_date:
        return False
    days = [d.strip() for d in (show.days_of_week or '').split(',') if d.strip()]
    return _normalize_day(show_date.strftime('%a')) in days


def scheduled_window_for_date(show: Show, show_date) -> tuple[datetime, datetime] | None:
    if not show_occurs_on_date(show, show_date):
        return None
    start_dt = datetime.combine(show_date, show.start_time)
    end_dt = datetime.combine(show_date, show.end_time)
    if show.end_time <= show.start_time:
        end_dt += timedelta(days=1)
    return start_dt, end_dt


def approved_absence_for_window(show: Show, start_dt: datetime, end_dt: datetime | None = None) -> DJAbsence | None:
    if not getattr(show, 'id', None):
        return None
    query = DJAbsence.query.filter(
        DJAbsence.show_id == show.id,
        DJAbsence.status == "approved",
        DJAbsence.start_time <= start_dt,
        DJAbsence.end_time >= (end_dt or start_dt),
    ).order_by(DJAbsence.start_time.desc())
    return query.first()


def uncovered_approved_absence_for_window(show: Show, start_dt: datetime, end_dt: datetime | None = None) -> DJAbsence | None:
    absence = approved_absence_for_window(show, start_dt, end_dt)
    if absence and not (absence.replacement_name or "").strip():
        return absence
    return None


def is_show_preempted_by_absence(show: Show, start_dt: datetime, end_dt: datetime | None = None) -> bool:
    return uncovered_approved_absence_for_window(show, start_dt, end_dt) is not None


def get_current_absent_show(now: datetime | None = None) -> tuple[Show, DJAbsence] | tuple[None, None]:
    if now is None:
        now = datetime.utcnow()
    for show in Show.query.all():
        for show_date in (now.date(), now.date() - timedelta(days=1)):
            window = scheduled_window_for_date(show, show_date)
            if not window:
                continue
            start_dt, end_dt = window
            if start_dt <= now < end_dt:
                absence = uncovered_approved_absence_for_window(show, start_dt, end_dt)
                if absence:
                    return show, absence
    return None, None


def get_current_show(now: datetime | None = None):
    """Return the show scheduled right now, excluding approved absences without substitutes."""
    if now is None:
        now = datetime.now()

    current_time = now.time()
    day_key = _normalize_day(now.strftime('%a'))
    yesterday_key = _normalize_day((now - timedelta(days=1)).strftime('%a'))

    today_shows = Show.query.all()

    def _has_day(show_obj: Show, key: str) -> bool:
        days = [d.strip() for d in (show_obj.days_of_week or '').split(',') if d.strip()]
        return key in days

    for show in today_shows:
        if not _has_day(show, day_key):
            continue
        window = scheduled_window_for_date(show, now.date())
        if not window:
            continue
        start_dt, end_dt = window
        if start_dt <= now < end_dt and not is_show_preempted_by_absence(show, start_dt, end_dt):
            return show
        if show.start_time > show.end_time and current_time >= show.start_time and not is_show_preempted_by_absence(show, start_dt, end_dt):
            return show

    for show in today_shows:
        if not _has_day(show, yesterday_key):
            continue
        show_date = now.date() - timedelta(days=1)
        window = scheduled_window_for_date(show, show_date)
        if not window:
            continue
        start_dt, end_dt = window
        if start_dt <= now < end_dt and not is_show_preempted_by_absence(show, start_dt, end_dt):
            return show

    return None


def format_show_window(show: Show) -> dict:
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


def next_show_occurrence(show: Show, *, now: datetime | None = None, include_uncovered_absence: bool = False) -> tuple[datetime, datetime] | None:
    """Return the next scheduled start/end datetimes for a show within the next two weeks."""
    if now is None:
        now = datetime.now()

    days = [d.strip() for d in (show.days_of_week or '').split(',') if d.strip()]
    if not days:
        return None

    today = now.date()
    for offset in range(0, 14):
        candidate = today + timedelta(days=offset)
        key = _normalize_day(candidate.strftime('%a'))
        if key not in days:
            continue
        window = scheduled_window_for_date(show, candidate)
        if not window:
            continue
        start_dt, end_dt = window
        if start_dt >= now or offset > 0:
            if include_uncovered_absence or not is_show_preempted_by_absence(show, start_dt, end_dt):
                return start_dt, end_dt
    return None
