"""Evidence-based airing totals. File modification times are never airing dates."""
from collections import defaultdict
from datetime import date, datetime, timedelta
import csv
import os
import re

from app.models import DJ, LogSheet, Show, ShowRun
from app.services.legacy_recordings import split_dj_names
from app.services.log_export import read_log_csv, read_recording_metadata
from app.services.missed_airings import load_missed_statuses
from app.utils import get_config_timezone, localize_datetime, scheduled_window_for_date


def key(value):
    return " ".join((value or "").casefold().split())


def recording_family(path):
    return re.sub(r"_part\d+$", "", os.path.splitext(path)[0], flags=re.I)


def evidence_identity(path, metadata=None, fallback_show=""):
    metadata = read_recording_metadata(path) if metadata is None else metadata
    show = metadata.get("show_name") or fallback_show
    for field in ("show_date", "recorded_date", "show_start"):
        try:
            return key(show), date.fromisoformat(str(metadata[field])[:10])
        except (KeyError, ValueError, TypeError):
            pass
    match = re.search(r"(\d{2})[-_](\d{2})[-_](\d{2,4})_RAWDATA", os.path.basename(path), re.I)
    if match:
        month, day, year = map(int, match.groups())
        try:
            return key(show), date(year + 2000 if year < 100 else year, month, day)
        except ValueError:
            pass
    return None


def metadata_hosts(metadata, fallback=""):
    hosts = metadata.get("dj_names") or metadata.get("hosts") or metadata.get("dj") or fallback
    if isinstance(hosts, str):
        hosts = split_dj_names(hosts.replace(",", "&"))
    return {name.strip() for name in hosts if isinstance(name, str) and name.strip()}


def build_audit(root, recordings, week, *, now=None, period=None, active_period=None,
                missed_statuses=None):
    now = now or datetime.now(get_config_timezone()).replace(tzinfo=None)
    evidence = {}
    missed = set()
    families = defaultdict(list)
    skipped = 0

    def add(identity, show, hosts, source):
        if not identity or not identity[0] or identity[1] > now.date():
            return
        row = evidence.setdefault(identity, {"show": show, "hosts": set(), "sources": set()})
        row["hosts"].update(hosts)
        row["sources"].add(source)

    for rec in recordings:
        families[recording_family(rec.full_path)].append(rec)
        identity = evidence_identity(rec.full_path, fallback_show=rec.show_name)
        if rec.flagged_missed and identity:
            missed.add(identity)

    for family, entries in families.items():
        originals = [rec for rec in entries if os.path.splitext(rec.full_path)[0] == family]
        # A lone recovery part is usable; multiple orphaned parts are not evidence.
        eligible = originals or (entries if len(entries) == 1 else [])
        for rec in eligible:
            identity = evidence_identity(rec.full_path, fallback_show=rec.show_name)
            if not identity:
                skipped += 1
                continue
            if rec.size_bytes > 0:
                add(identity, rec.show_name, metadata_hosts(read_recording_metadata(rec.full_path), rec.dj_name), "Recording")

    # CSVs can exist with no audio after a recorder/network failure.
    for directory, _, files in os.walk(root):
        for filename in files:
            if not filename.lower().endswith(".csv"):
                continue
            path = os.path.join(directory, filename)
            metadata = read_recording_metadata(path)
            try:
                rows = read_log_csv(path)
            except (OSError, UnicodeError, ValueError, csv.Error):
                skipped += 1
                continue
            for row in rows:
                try:
                    identity = key(row.show_name), date.fromisoformat(row.show_date)
                except (ValueError, TypeError):
                    continue
                if metadata.get("flagged_missed") is True:
                    missed.add(identity)
                add(identity, row.show_name, metadata_hosts({}, row.dj), "Digital log")

    unassigned_logs = 0
    for log in LogSheet.query.all():
        # Legacy database rows have no period column. Never import another
        # period's logs by guessing from a show name or the current DJ roster.
        if period is not None and (key(log.show_name), log.show_date) not in evidence:
            unassigned_logs += 1
            continue
        add((key(log.show_name), log.show_date), log.show_name,
            {f"{log.dj_first_name} {log.dj_last_name}".strip()}, "Digital log")
    for run in ShowRun.query.filter_by(flagged_missed=True).all():
        missed.add((key(run.show_name), localize_datetime(run.start_time).date()))

    # An explicit clear must also override old sidecars or automatic run flags.
    statuses = load_missed_statuses(root) if missed_statuses is None else missed_statuses
    for identity, flagged in statuses.items():
        if flagged:
            missed.add(identity)
        else:
            missed.discard(identity)

    aired = {identity: row for identity, row in evidence.items() if identity not in missed}
    show_totals = defaultdict(int)
    dj_totals = {
        key(f"{dj.first_name} {dj.last_name}"): [f"{dj.first_name} {dj.last_name}", 0]
        for dj in DJ.query.all()
        if period is None or period in dj.period_list
        or (period == active_period and not dj.period_list and not dj.is_archived)
    }
    for (show, _), row in aired.items():
        show_totals[show] += 1
        for name in {key(name): name for name in row["hosts"]}.values():
            dj_totals.setdefault(key(name), [name, 0])[1] += 1

    days = [week + timedelta(days=i) for i in range(7)]
    calendar = []
    known = set()
    period_show_dates = defaultdict(set)
    for show_key, airing_date in evidence:
        period_show_dates[show_key].add(airing_date)
    for show in Show.query.order_by(Show.show_name, Show.start_time).all():
        title = show.show_name or f"{show.host_first_name} {show.host_last_name}"
        if period is not None:
            # Archived periods use their own files, including log-only shows.
            # Keep currently scheduled, not-yet-recorded shows in the active
            # period so missing airings still appear in its calendar.
            current_schedule = (period == active_period
                                and show.start_date <= now.date() <= show.end_date)
            evidence_schedule = any(show.start_date <= day <= show.end_date
                                    for day in period_show_dates[key(title)])
            if not evidence_schedule and not current_schedule:
                continue
        known.add(key(title))
        cells = []
        for day in days:
            identity = key(title), day
            window = scheduled_window_for_date(show, day)
            row = aired.get(identity)
            if window and window[1] > now:
                status = "Upcoming"
            elif identity in missed:
                status = "Missed"
            elif row:
                status = "Aired"
            elif window:
                status = "No evidence"
            else:
                status = "Unscheduled"
            cells.append({"status": status, "sources": ", ".join(sorted(row["sources"])) if row else ""})
        calendar.append({"name": title, "total": show_totals[key(title)], "cells": cells})
    # Keep historical/deleted shows visible, including log-only and imported shows.
    for show_key in sorted({identity[0] for identity in evidence} - known):
        name = next(row["show"] for identity, row in evidence.items() if identity[0] == show_key)
        cells = []
        for day in days:
            identity = show_key, day
            row = aired.get(identity)
            cells.append({"status": "Missed" if identity in missed else "Aired" if row else "Unscheduled",
                          "sources": ", ".join(sorted(row["sources"])) if row else ""})
        calendar.append({"name": name, "total": show_totals[show_key], "cells": cells})
    return {"days": days, "calendar": calendar, "dj_totals": sorted(dj_totals.values(), key=lambda row: row[0].casefold()),
            "total": len(aired), "skipped": skipped, "unassigned_logs": unassigned_logs}
