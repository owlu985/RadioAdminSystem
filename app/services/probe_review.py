"""Locate one airing's probe flags without walking the recording archive."""
from datetime import datetime, time, timedelta, timezone

from app.models import Show, ShowRun
from app.services.airing_audit import key
from app.services.airing_dates import airing_date_for_start
from app.services.missed_airings import read_missed_status, probe_status_cleared
from app.services.recording_periods import recordings_base_root
from app.utils import get_config_timezone, localize_datetime


def run_airing_identity(run, shows=None):
    shows = shows if shows is not None else Show.query.all()
    matching = [show for show in shows if key(show.show_name or f"{show.host_first_name} {show.host_last_name}") == key(run.show_name)]
    local_start = localize_datetime(run.start_time).replace(tzinfo=None)
    return key(run.show_name), airing_date_for_start(matching, local_start)


def flagged_runs_for_airing(identity):
    day_start = datetime.combine(identity[1], time.min).replace(tzinfo=get_config_timezone())
    # Catch recovery runs after midnight as well as the original evening run.
    day_end = datetime.combine(identity[1] + timedelta(days=2), time.min).replace(tzinfo=get_config_timezone())
    runs = ShowRun.query.filter(
        ShowRun.flagged_missed.is_(True),
        ShowRun.start_time >= day_start.astimezone(timezone.utc).replace(tzinfo=None),
        ShowRun.start_time < day_end.astimezone(timezone.utc).replace(tzinfo=None),
    ).all()
    shows = Show.query.all()
    return [run for run in runs if run_airing_identity(run, shows) == identity]


def effective_run_missed(run):
    identity = run_airing_identity(run)
    root = recordings_base_root(create=False)
    manual = read_missed_status(root, identity)
    if manual is not None:
        return manual
    return bool(run.flagged_missed and not probe_status_cleared(root, identity))
