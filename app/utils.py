from datetime import datetime, time, timedelta
from flask import current_app as app
from .logger import init_logger
from .models import Show
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

        try:
            with open(config_path, 'w') as f:
                json.dump(current_config, f, indent=4)
        except Exception as e:
            logger.error(f"Error writing user configuration: {e}")

        app.config.update(current_config)
        logger.info(f"User configuration updated successfully with {updates}.")


def _normalize_day(day: str) -> str:
    return day.lower()[:3]


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

    today_shows = Show.query.filter_by(days_of_week=day_key).all()
    for show in today_shows:
        if show.start_time <= show.end_time:
            if show.start_time <= current_time < show.end_time:
                return show
        else:
            # Overnight show into next day
            if current_time >= show.start_time or current_time < show.end_time:
                return show

    # Handle shows that began yesterday and end after midnight today
    yesterday_shows = Show.query.filter_by(days_of_week=yesterday_key).all()
    for show in yesterday_shows:
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
