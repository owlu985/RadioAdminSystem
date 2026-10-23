"""Resolve logs to a show's station-local start date, never submission time."""
from datetime import datetime, timedelta

from app.utils import scheduled_window_for_date


def airing_date_for_start(shows, local_start):
    """Use a timestamp to distinguish an overnight airing, including daily shows."""
    for day in (local_start.date(), local_start.date() - timedelta(days=1)):
        for show in shows:
            window = scheduled_window_for_date(show, day)
            if window and window[0] <= local_start < window[1]:
                return day
    return local_start.date()


def log_airing_date(shows, day, entry_times=()):
    """Repair an unambiguous next-day date for a midnight/overnight slot.

    Already-scheduled dates are left alone: daily shows need an explicit
    airing selection rather than silently guessing yesterday.
    """
    if any(scheduled_window_for_date(show, day) for show in shows):
        return day
    previous = day - timedelta(days=1) if day.year > 1 else day
    for show in shows:
        window = scheduled_window_for_date(show, previous)
        if not window or window[1].date() != day:
            continue
        valid = True
        for value in entry_times:
            if not value:
                continue
            try:
                clock = datetime.strptime(value, "%H:%M").time()
            except (ValueError, TypeError):
                continue
            occurred = datetime.combine(previous, clock)
            if occurred < window[0]:
                occurred += timedelta(days=1)
            if not window[0] <= occurred <= window[1]:
                valid = False
        if valid:
            return previous
    return day
