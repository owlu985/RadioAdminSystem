"""Attendance, evidence and potential slots for the selected recording period."""
from collections import defaultdict
from datetime import date, datetime, timedelta
import csv
import os

from app.models import DJ, DJAbsence, LogSheet, Show, ShowRun
from app.services.airing_audit import evidence_identity, key, metadata_hosts, recording_family
from app.services.airing_dates import log_airing_date, airing_date_for_start
from app.services.log_export import read_log_csv, read_recording_metadata
from app.services.missed_airings import load_missed_statuses, load_probe_clearances
from app.utils import get_config_timezone, localize_datetime, scheduled_window_for_date


def build_report(root, recordings, week, *, now=None, period=None, active_period=None,
                 missed_statuses=None, period_start=None, period_end=None, probe_clearances=None):
    now = now or datetime.now(get_config_timezone()).replace(tzinfo=None)
    shows = Show.query.order_by(Show.show_name, Show.start_time).all()
    def title(show):
        return show.show_name or f"{show.host_first_name} {show.host_last_name}"
    by_name = defaultdict(list)
    for show in shows:
        by_name[key(title(show))].append(show)
    evidence, observed = {}, {}
    missed, families = set(), defaultdict(list)
    skipped, repaired_logs = 0, 0
    diagnostics = defaultdict(list)

    def normalize(identity, clocks=()):
        return (identity[0], log_airing_date(by_name[identity[0]], identity[1], clocks)) if identity else None

    def in_period(day):
        return (not period_start or day >= period_start) and (not period_end or day <= period_end)

    def add(identity, name, hosts, source, detail="", token=None):
        if not identity or not identity[0] or identity[1] > now.date() or not in_period(identity[1]):
            return
        row = evidence.setdefault(identity, {"show": name, "hosts": set(), "sources": set(), "details": set(), "token": token})
        row["hosts"].update(hosts)
        row["sources"].add(source)
        if detail:
            row["details"].add(detail)

    for rec in recordings:
        identity = evidence_identity(rec.full_path, fallback_show=rec.show_name)
        if identity and in_period(identity[1]):
            observed[identity] = rec.show_name
            if rec.flagged_missed:
                missed.add(identity)
        families[recording_family(rec.full_path)].append(rec)
    for family, entries in families.items():
        originals = [rec for rec in entries if os.path.splitext(rec.full_path)[0] == family]
        # A failed zero-byte original must not hide 141 usable recovery parts.
        eligible = entries if originals or len(entries) == 1 else []
        usable = [rec for rec in eligible if rec.size_bytes > 0]
        if not usable:
            identity = evidence_identity(entries[0].full_path, fallback_show=entries[0].show_name)
            if identity:
                diagnostics[identity].append("Multiple orphaned parts require an original or a submitted log." if not eligible else "Recording files exist, but they are empty.")
            continue
        representative = next((rec for rec in originals if rec.size_bytes > 0), usable[0])
        identity = evidence_identity(originals[0].full_path if originals else representative.full_path,
                                     fallback_show=representative.show_name)
        if not identity:
            skipped += 1
            continue
        hosts = set().union(*(metadata_hosts(read_recording_metadata(rec.full_path), rec.dj_name) for rec in usable))
        add(identity, representative.show_name, hosts, "Recording",
            f"{len(usable)} nonempty recording file(s); counted once", representative.token)

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
            grouped = defaultdict(list)
            for row in rows:
                try:
                    grouped[(key(row.show_name), date.fromisoformat(row.show_date))].append(row)
                except (ValueError, TypeError):
                    continue
            for raw_identity, group in grouped.items():
                canonical = evidence_identity(path, metadata, group[0].show_name)
                identity = normalize(canonical if canonical and canonical[0] == raw_identity[0] else raw_identity,
                                     [row.time for row in group])
                repaired_logs += identity != raw_identity
                if metadata.get("flagged_missed") is True:
                    missed.add(identity)
                add(identity, group[0].show_name, set().union(*(metadata_hosts({}, row.dj) for row in group)),
                    "Digital log", "Log matched to the airing start date" if identity != raw_identity else "Submitted digital log")
    unassigned_logs = 0
    for log in LogSheet.query.all():
        identity = normalize((key(log.show_name), log.show_date))
        if period is not None and identity not in evidence:
            unassigned_logs += 1
            continue
        add(identity, log.show_name, {f"{log.dj_first_name} {log.dj_last_name}".strip()}, "Digital log")
    cleared_probes = load_probe_clearances(root) if probe_clearances is None else probe_clearances
    probe_runs = {}
    for run in ShowRun.query.filter_by(flagged_missed=True).all():
        local_start = localize_datetime(run.start_time).replace(tzinfo=None)
        identity = key(run.show_name), airing_date_for_start(by_name[key(run.show_name)], local_start)
        if identity not in cleared_probes:
            probe_runs[identity] = run.id
            missed.add(identity)
    statuses = load_missed_statuses(root) if missed_statuses is None else missed_statuses
    for identity, flagged in statuses.items():
        identity = identity if identity in observed or identity in evidence else normalize(identity)
        missed.add(identity) if flagged else missed.discard(identity)

    attendance = defaultdict(list)
    for absence in DJAbsence.query.filter(DJAbsence.status.in_(["pending", "approved", "resolved"])).all():
        identity = key(absence.show_name), absence.start_time.date()
        belongs = (period is None or (period_start and period_end) or identity in observed or identity in evidence
                   or (period == active_period and any(s.start_date <= now.date() <= s.end_date for s in by_name[identity[0]])))
        if in_period(identity[1]) and belongs:
            attendance[identity].append(absence)
    aired = {}
    for identity, row in evidence.items():
        excused = [a for a in attendance.get(identity, []) if a.status in {"approved", "resolved"}]
        for absence in excused:
            row["hosts"] = {name for name in row["hosts"] if key(name) != key(absence.dj_name)}
            if absence.replacement_name:
                row["hosts"].add(absence.replacement_name)
        if identity not in missed and not (excused and not row["hosts"]):
            aired[identity] = row

    period_dates = defaultdict(set)
    for identity in set(evidence) | set(observed) | set(attendance):
        period_dates[identity[0]].add(identity[1])
    selected = []
    for show in shows:
        overlap = (not period_start or show.end_date >= period_start) and (not period_end or show.start_date <= period_end)
        belongs = period is None or (period_start and period_end and overlap) or (
            period == active_period and show.start_date <= now.date() <= show.end_date) or any(
            show.start_date <= day <= show.end_date for day in period_dates.get(key(title(show)), ()))
        if overlap and belongs:
            selected.append(show)

    expected, expected_hosts = set(), defaultdict(set)
    for show in selected:
        hosts = {f"{dj.first_name} {dj.last_name}".strip() for dj in show.djs}
        hosts.add(f"{show.host_first_name} {show.host_last_name}".strip())
        day = max(show.start_date, period_start or show.start_date)
        end = min(show.end_date, period_end or show.end_date, now.date())
        while day <= end:
            window = scheduled_window_for_date(show, day)
            if window and window[1] <= now:
                identity = key(title(show)), day
                expected.add(identity)
                expected_hosts[identity].update(hosts)
                for absence in attendance.get(identity, []):
                    if absence.status in {"approved", "resolved"} and absence.replacement_name:
                        expected_hosts[identity].add(absence.replacement_name)
            day += timedelta(days=1)
    for identity in list(aired):
        matching = [scheduled_window_for_date(show, identity[1]) for show in selected if key(title(show)) == identity[0]]
        if any(window and window[1] > now for window in matching):
            aired.pop(identity)
    show_totals = defaultdict(int)
    dj_totals = {key(f"{dj.first_name} {dj.last_name}"): [f"{dj.first_name} {dj.last_name}", 0]
                 for dj in DJ.query.all() if period is None or period in dj.period_list
                 or (period == active_period and not dj.period_list and not dj.is_archived)}
    dj_possible = defaultdict(set)
    for identity, hosts in expected_hosts.items():
        for name in hosts:
            dj_totals.setdefault(key(name), [name, 0])
            dj_possible[key(name)].add(identity)
    unknown_djs = set()
    for identity, row in aired.items():
        show_totals[identity[0]] += 1
        for name in {key(name): name for name in row["hosts"]}.values():
            dj_totals.setdefault(key(name), [name, 0])[1] += 1
            if identity not in dj_possible[key(name)]:
                unknown_djs.add(key(name))
    days = [week + timedelta(days=i) for i in range(7)]

    def cell(identity, window):
        row = evidence.get(identity)
        notices = attendance.get(identity, [])
        confirmed = [a for a in notices if a.status in {"approved", "resolved"}]
        subs = [a.replacement_name for a in confirmed if a.replacement_name]
        if not in_period(identity[1]):
            status, code = "Outside period", "—"
        elif window and window[1] > now:
            status, code = "Upcoming", "U"
        elif confirmed:
            status, code = ("Substituted", "S") if subs and identity in aired else ("Absence — substitute arranged", "E/S") if subs else ("Recorded absence — aired by co-host", "E/A") if identity in aired else ("Recorded absence", "E")
        elif notices:
            status, code = ("Aired — absence pending", "A/P") if identity in aired else ("Absence pending", "P")
        elif identity in missed:
            status, code = ("Missed — automated probe flag", "M") if identity in probe_runs and statuses.get(identity) is not True else ("Missed — no notice", "M")
        elif identity in aired:
            status, code = "Aired", "A"
        elif window or identity in observed:
            status, code = "No evidence", "?"
        else:
            status, code = "Unscheduled", "—"
        details = (sorted(row["details"]) if row else []) + diagnostics[identity]
        for notice in notices:
            details.append(f"{notice.dj_name}: {notice.notes or 'No reason recorded'}" + (f"; substitute: {notice.replacement_name}" if notice.replacement_name else ""))
        if identity in missed and row:
            details.append("Evidence exists, but this airing is flagged Missed; clear the flag if it aired.")
        if identity in cleared_probes:
            details.append("Automated Missed flag was manually cleared for this airing.")
        return {"status": status, "code": code, "sources": ", ".join(sorted(row["sources"])) if row else "",
                "probe_run_id": probe_runs.get(identity) if identity in missed and statuses.get(identity) is not True else None,
                "details": details, "token": row.get("token") if row else None,
                "color": "success" if identity in aired else "warning" if notices else "danger" if code in {"M", "?"} else "secondary" if code == "U" else ""}

    calendar, known = [], set()
    for show in selected:
        name, name_key = title(show), key(title(show))
        if name_key in known:
            continue
        known.add(name_key)
        cells = []
        for day in days:
            windows = [scheduled_window_for_date(s, day) for s in selected if key(title(s)) == name_key]
            cells.append(cell((name_key, day), next((window for window in windows if window), None)))
        unknown_schedule = any(identity[0] == name_key and identity not in expected for identity in aired)
        calendar.append({"name": name, "total": show_totals[name_key],
                         "possible": None if unknown_schedule else sum(identity[0] == name_key for identity in expected), "cells": cells})
    for name_key in sorted((set(period_dates) | {i[0] for i in evidence}) - known):
        identity = next(i for i in set(evidence) | set(observed) | set(attendance) if i[0] == name_key)
        name = evidence[identity]["show"] if identity in evidence else observed.get(identity) or attendance[identity][0].show_name
        calendar.append({"name": name, "total": show_totals[name_key], "possible": None,
                         "cells": [cell((name_key, day), None) for day in days]})
    return {"days": days, "calendar": calendar, "dj_totals": sorted(dj_totals.values(), key=lambda row: row[0].casefold()),
            "dj_possible": {name: None if name_key in unknown_djs else len(dj_possible[name_key]) for name_key, (name, _) in dj_totals.items()},
            "total": len(aired), "possible": len(expected), "skipped": skipped, "unassigned_logs": unassigned_logs,
            "repaired_logs": repaired_logs, "inferred_period": not (period_start and period_end)}
