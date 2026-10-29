"""Permanent source evidence, reviewed daily views, and versioned NAS CSVs.

Source evidence is append-only. Only confirmed show windows replace playback in
compiled views. Engineering events are never substituted. DB times are UTC.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, is_dataclass
from datetime import datetime, time, timedelta, timezone
import hashlib
import io
import json
import logging
import os
import re
from pathlib import Path
import tempfile
import uuid
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models import (db, StationLogEvent, StationLogAiring, StationLogSubmission,
                        StationLogArchive, StationLogImport)
from app.services.runtime_state import locked_state

UTC = timezone.utc

STATION_LOG_MODELS = (StationLogEvent, StationLogImport, StationLogAiring,
                      StationLogSubmission, StationLogArchive)


def missing_tables():
    """Readiness check only: web requests never implicitly modify the schema."""
    existing = set(inspect(db.engine).get_table_names())
    return [model.__tablename__ for model in STATION_LOG_MODELS if model.__tablename__ not in existing]


def station_zone():
    return ZoneInfo(current_app.config.get("SCHEDULE_TIMEZONE", "America/New_York"))


def utc_naive(value):
    if value.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return value.astimezone(UTC).replace(tzinfo=None)


def local_to_utc(value):
    """Reject ambiguous/nonexistent wall times instead of inventing airtime."""
    if value.tzinfo is not None:
        return utc_naive(value)
    zone = station_zone()
    first, second = value.replace(tzinfo=zone, fold=0), value.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError("Ambiguous or nonexistent local time; supply an ISO timestamp with UTC offset")
    return utc_naive(first)


def day_bounds(day):
    start = datetime.combine(day, time.min).replace(tzinfo=station_zone())
    end = datetime.combine(day + timedelta(days=1), time.min).replace(tzinfo=station_zone())
    return utc_naive(start), utc_naive(end)


def local_day(value):
    return value.replace(tzinfo=UTC).astimezone(station_zone()).date()


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False).encode("utf-8")


def _atomic_bytes(path, data):
    fd, temporary = tempfile.mkstemp(prefix=".station-log-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def emit_event(event_type, title, *, source="rams", severity="info", code=None,
               details=None, occurred_at=None, source_id=None):
    """Queue durably without committing/rolling back a caller's DB transaction."""
    if not has_app_context() or "sqlalchemy" not in current_app.extensions:
        return False
    try:
        root = Path(current_app.config.get("DATA_ROOT") or current_app.instance_path) / "station_log_pending"
        root.mkdir(parents=True, exist_ok=True)
        payload = dict(source=source, source_id=source_id or uuid.uuid4().hex,
                       occurred_at=(occurred_at or datetime.utcnow()).isoformat(),
                       received_at=datetime.utcnow().isoformat(), event_type=event_type,
                       title=str(title), severity=severity, code=code, details=details or {})
        _atomic_bytes(root / (uuid.uuid4().hex + ".json"), _json_bytes(payload))
        return True
    except Exception:
        current_app.logger.exception("Unable to queue station log event")
        return False


def drain_events(limit=1000):
    root = Path(current_app.config.get("DATA_ROOT") or current_app.instance_path) / "station_log_pending"
    if not root.exists():
        return 0
    count = 0
    with locked_state("station-log-spool"):
        with Session(db.engine) as session:
            for path in sorted(root.glob("*.json"))[:limit]:
                payload = json.loads(path.read_text(encoding="utf-8"))
                existing = session.scalar(select(StationLogEvent.id).where(
                    StationLogEvent.source == payload["source"],
                    StationLogEvent.source_id == payload["source_id"]))
                if not existing:
                    payload["occurred_at"] = datetime.fromisoformat(payload["occurred_at"])
                    payload["received_at"] = datetime.fromisoformat(payload["received_at"])
                    session.add(StationLogEvent(**payload))
                session.commit()
                path.unlink()  # delete only after the successful insert
                count += 1
    return count


class StationErrorHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            message = record.getMessage()
            if has_app_context():
                for key, value in current_app.config.items():
                    if any(part in key for part in ("PASSWORD", "SECRET", "API_KEY")) and isinstance(value, str) and value:
                        message = message.replace(value, "[redacted]")
            message = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", message)
            emit_event("application_error", message, severity="error",
                       details={"logger": record.name, "module": record.module})


def install_error_capture():
    logger = logging.getLogger("ShowRecorder")
    if not any(isinstance(h, StationErrorHandler) for h in logger.handlers):
        logger.addHandler(StationErrorHandler(level=logging.ERROR))


def record_alert_transitions(alerts, *, stream_up=None):
    """One raised/cleared pair per code, rather than duplicating each poll."""
    with locked_state("station-log-alerts") as state:
        previous = state.get("alerts", {})
        active = {str(item["code"]): item for item in alerts}
        for code, alert in active.items():
            if code not in previous:
                emit_event("code_raised", alert["message"], code=code,
                           severity=alert.get("severity", "warning"),
                           details={"timing": "sampled detection"})
        for code, alert in previous.items():
            if code not in active:
                emit_event("code_cleared", "Cleared: " + alert["message"], code=code,
                           details={"timing": "sampled detection"})
        if stream_up is not None and stream_up != state.get("stream_up"):
            emit_event("stream_recovered" if stream_up else "stream_unavailable",
                       "Stream audio decoded" if stream_up else "Stream audio unavailable",
                       severity="info" if stream_up else "error",
                       details={"timing": "sampled detection; failing device unconfirmed"})
            state["stream_up"] = stream_up
        state["alerts"] = active


def airing_identity(show_name, start):
    return hashlib.sha256(_json_bytes([show_name.strip().casefold(), start.isoformat()])).hexdigest()


def register_scheduled_airings(day):
    """Keep pending show windows visible; schedules alone NEVER confirm airtime."""
    from app.models import Show
    from app.utils import scheduled_window_for_date, show_display_title, show_primary_host
    with locked_state("station-log-airings"):
        for show in Show.query.all():
            window = scheduled_window_for_date(show, day)
            if not window:
                continue
            name = show_display_title(show)
            try:
                start, end = local_to_utc(window[0]), local_to_utc(window[1])
            except ValueError:
                # Explicit offset review is required at a repeated/missing wall time.
                emit_event("airing_time_unconfirmed", f"Review DST show window: {name}", severity="warning")
                continue
            identity = airing_identity(name, start)
            existing = StationLogAiring.query.filter_by(identity=identity).first()
            if not existing:
                existing = StationLogAiring.query.filter(StationLogAiring.show_name == name,
                    StationLogAiring.starts_at < end, StationLogAiring.ends_at > start).first()
            if existing:
                continue
            host = show_primary_host(show)
            db.session.add(StationLogAiring(identity=identity, show_name=name,
                dj=" ".join(host) if host else "", starts_at=start, ends_at=end, status="unknown"))
        db.session.commit()


def save_submission(*, entries, show_name, dj, starts_at, ends_at, recording_file=None,
                    actor="DJ submission (unverified)", show_run_id=None):
    """Retain revisions. Submission alone never proves that a show aired."""
    if ends_at <= starts_at or ends_at - starts_at > timedelta(days=2):
        raise ValueError("Invalid show window")
    identity = airing_identity(show_name, starts_at)
    with locked_state("station-log-airings"):
        airing = StationLogAiring.query.filter_by(identity=identity).first()
        if not airing and show_run_id:
            airing = StationLogAiring.query.filter_by(show_run_id=show_run_id).first()
        if not airing:
            candidates = StationLogAiring.query.filter(StationLogAiring.show_name == show_name,
                StationLogAiring.starts_at < ends_at, StationLogAiring.ends_at > starts_at).all()
            if len(candidates) == 1:
                airing = candidates[0]
        if not airing:
            airing = StationLogAiring(identity=identity, show_name=show_name, dj=dj,
                starts_at=starts_at, ends_at=ends_at, status="unknown", show_run_id=show_run_id)
            db.session.add(airing)
            db.session.flush()
        elif show_run_id and not airing.show_run_id:
            airing.show_run_id = show_run_id
        previous = StationLogSubmission.query.filter_by(airing_id=airing.id).order_by(
            StationLogSubmission.revision.desc()).first()
        serialized = [asdict(entry) if is_dataclass(entry) else dict(entry) for entry in entries]
        submission = StationLogSubmission(airing_id=airing.id,
            revision=previous.revision + 1 if previous else 1, actor=actor,
            entries=serialized, recording_file=recording_file)
        airing.updated_at = datetime.utcnow()
        db.session.add(submission)
        db.session.commit()
    emit_event("dj_log_submitted", f"Playlist submitted: {show_name}",
        details={"airing_id": airing.id, "revision": submission.revision, "actor": actor})
    return submission


def review_airing(*, airing=None, show_name, dj, starts_at, ends_at, status, actor, reason):
    if status not in {"aired", "missed", "unknown"} or ends_at <= starts_at:
        raise ValueError("Choose a valid status and an end time after the start")
    if ends_at - starts_at > timedelta(days=2):
        raise ValueError("A show window cannot exceed 48 hours")
    if not show_name.strip() or not reason.strip():
        raise ValueError("Show name and correction/confirmation reason are required")
    with locked_state("station-log-airings"):
        if airing is None:
            airing = StationLogAiring.query.filter_by(identity=airing_identity(show_name, starts_at)).first()
        if status == "aired":
            overlap = StationLogAiring.query.filter(
                StationLogAiring.status == "aired", StationLogAiring.starts_at < ends_at,
                StationLogAiring.ends_at > starts_at)
            if airing:
                overlap = overlap.filter(StationLogAiring.id != airing.id)
            if overlap.first():
                raise ValueError("Confirmed show windows cannot overlap")
        old = None if airing is None else dict(show_name=airing.show_name, dj=airing.dj,
            starts_at=airing.starts_at.isoformat(), ends_at=airing.ends_at.isoformat(), status=airing.status)
        if airing is None:
            airing = StationLogAiring(identity=airing_identity(show_name, starts_at))
            db.session.add(airing)
        airing.show_name, airing.dj = show_name.strip(), dj.strip()
        airing.starts_at, airing.ends_at, airing.status = starts_at, ends_at, status
        airing.updated_at = datetime.utcnow()
        db.session.commit()
        new = dict(show_name=airing.show_name, dj=airing.dj, starts_at=starts_at.isoformat(),
                   ends_at=ends_at.isoformat(), status=status)
        emit_event("airing_reviewed", f"Airing reviewed: {airing.show_name}",
                   details={"airing_id": airing.id, "actor": actor, "reason": reason,
                            "before": old, "after": new})
    return airing


def serialize_event(event):
    return dict(id=f"event:{event.id}", occurred_at=event.occurred_at.isoformat() + "Z",
        received_at=event.received_at.isoformat() + "Z", source=event.source,
        source_id=event.source_id, event_type=event.event_type, severity=event.severity,
        code=event.code or "", title=event.title or "", artist=event.artist or "",
        album=event.album or "", isrc=event.isrc or "", track_id=event.track_id,
        reported_duration=event.reported_duration, show_name="", dj="", airing_id=None,
        time_accuracy="ambiguous database timestamp" if (event.details or {}).get("timestamp_ambiguous") else
            "reported" if event.event_type == "playback" else "observed",
        details=event.details or {})


def _dj_entries(airing, submission, start, end):
    for index, entry in enumerate(submission.entries):
        clock = entry.get("time", "")
        occurred, accuracy = airing.starts_at, "unreported"
        if clock:
            local_start = airing.starts_at.replace(tzinfo=UTC).astimezone(station_zone())
            try:
                value = datetime.combine(local_start.date(), time.fromisoformat(clock))
                if value.time() < local_start.time().replace(tzinfo=None) and local_day(airing.ends_at) > local_start.date():
                    value += timedelta(days=1)
                occurred, accuracy = local_to_utc(value), "DJ reported (minute precision)"
            except ValueError:
                accuracy = "ambiguous/invalid DJ time"
        if not airing.starts_at <= occurred < airing.ends_at:
            occurred, accuracy = airing.starts_at, "outside confirmed window; review required"
        if not start <= occurred < end:
            continue
        yield dict(id=f"submission:{submission.id}:{index}", occurred_at=occurred.isoformat() + "Z",
            received_at=submission.submitted_at.isoformat() + "Z", source="dj_submission",
            source_id=f"{submission.id}:{index}", event_type=entry.get("entry_type") or "content",
            severity="warning" if accuracy != "DJ reported (minute precision)" else "info",
            code="", title=entry.get("title") or entry.get("message") or "Untitled entry",
            artist=entry.get("artist", ""), album=entry.get("album", ""),
            isrc=entry.get("isrc", ""), track_id=None,
            reported_duration=entry.get("reported_duration"),
            show_name=airing.show_name, dj=airing.dj, airing_id=airing.id,
            time_accuracy=accuracy, details={"revision": submission.revision,
                "submitted_time": clock, "recording_file": submission.recording_file})


def daily_log(day, *, raw=False):
    start, end = day_bounds(day)
    events = StationLogEvent.query.filter(StationLogEvent.occurred_at >= start,
        StationLogEvent.occurred_at < end).order_by(StationLogEvent.occurred_at, StationLogEvent.id).all()
    if raw:
        return [serialize_event(event) for event in events]
    airings = StationLogAiring.query.filter(StationLogAiring.starts_at < end,
        StationLogAiring.ends_at > start).order_by(StationLogAiring.starts_at).all()
    confirmed = [a for a in airings if a.status == "aired"]
    rows = [serialize_event(event) for event in events if not (
        event.event_type == "playback" and any(
            a.starts_at <= event.occurred_at < a.ends_at for a in confirmed))]
    for airing in airings:
        submission = StationLogSubmission.query.filter_by(airing_id=airing.id).order_by(
            StationLogSubmission.revision.desc()).first()
        label = {"aired": "Show aired", "missed": "Show missed; automation retained",
                 "unknown": "Airtime unconfirmed; RadioDJ evidence retained"}[airing.status]
        if airing.status == "aired" and (not submission or not submission.entries):
            label += " — playlist log missing"
        rows.append(dict(id=f"airing:{airing.id}", occurred_at=max(start, airing.starts_at).isoformat() + "Z",
            received_at=airing.updated_at.isoformat() + "Z", source="airing_review",
            source_id=str(airing.id), event_type="show_window", severity="info" if airing.status == "missed" or
            (airing.status == "aired" and submission and submission.entries) else "warning",
            code="", title=label, artist="", album="", isrc="", track_id=None,
            reported_duration=None, show_name=airing.show_name, dj=airing.dj, airing_id=airing.id,
            time_accuracy="confirmed window" if airing.status != "unknown" else "unconfirmed",
            details={"starts_at": airing.starts_at.isoformat() + "Z",
                "ends_at": airing.ends_at.isoformat() + "Z", "status": airing.status,
                "latest_submission_revision": submission.revision if submission else None,
                "submitted_entries": submission.entries if submission else []}))
        if airing.status == "aired" and submission:
            rows.extend(_dj_entries(airing, submission, start, end))
    return sorted(rows, key=lambda row: (row["occurred_at"], row["id"]))


CSV_FIELDS = ["id", "occurred_at", "local_time", "received_at", "source", "source_id",
    "event_type", "severity", "code", "title", "artist", "album", "isrc", "track_id",
    "reported_duration", "show_name", "dj", "airing_id", "time_accuracy", "details"]


def csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        value = dict(row)
        value["local_time"] = datetime.fromisoformat(row["occurred_at"]).astimezone(station_zone()).isoformat()
        value["details"] = json.dumps(row["details"], ensure_ascii=False, sort_keys=True)
        for key, cell in value.items():
            if isinstance(cell, str) and cell.lstrip().startswith(("=", "+", "-", "@")):
                value[key] = "'" + cell
        writer.writerow(value)
    return output.getvalue().encode("utf-8-sig")


def archive_day(day):
    directory = current_app.config.get("STATION_LOG_ARCHIVE_DIR")
    if not directory:
        return None
    mount = current_app.config.get("STATION_LOG_ARCHIVE_MOUNT")
    if mount and not os.path.ismount(mount):
        raise OSError("Station log NAS mount is unavailable")
    root = Path(directory)
    if not root.is_dir():
        raise OSError("Station log archive directory is unavailable; create it on the NAS first")
    with locked_state("station-log-archive"):
        rows, sources = daily_log(day), daily_log(day, raw=True)
        data, source_data = csv_bytes(rows), csv_bytes(sources)
        fingerprint = hashlib.sha256(data + b"\0" + source_data).hexdigest()
        latest = StationLogArchive.query.filter_by(day=day).order_by(StationLogArchive.revision.desc()).first()
        if latest and latest.fingerprint == fingerprint:
            if Path(latest.csv_path).is_file() and Path(latest.sources_path).is_file():
                return latest
        revision = latest.revision + 1 if latest else 1
        path = root / f"{day.isoformat()}.r{revision:04d}.csv"
        source_path = root / f"{day.isoformat()}.r{revision:04d}.sources.csv"
        _atomic_bytes(path, data)
        _atomic_bytes(source_path, source_data)
        if path.read_bytes() != data or source_path.read_bytes() != source_data:
            raise OSError("Station log archive verification failed")
        archive = StationLogArchive(day=day, revision=revision, fingerprint=fingerprint,
            csv_path=str(path), sources_path=str(source_path))
        db.session.add(archive)
        db.session.commit()
        return archive


def archive_due_days(now=None):
    if not current_app.config.get("STATION_LOG_ARCHIVE_DIR"):
        return 0
    now = now or datetime.now(station_zone())
    cutoff = now.date() - timedelta(days=max(1, int(current_app.config.get("STATION_LOG_ARCHIVE_AFTER_DAYS", 5))))
    # Group in SQL instead of loading every playback timestamp on each hourly run.
    # Each UTC calendar group spans at most two station days in normal station zones.
    candidates = {}
    groups = db.session.query(db.func.min(StationLogEvent.occurred_at),
        db.func.max(StationLogEvent.occurred_at), db.func.max(StationLogEvent.received_at)).group_by(
            db.func.date(StationLogEvent.occurred_at)).all()
    for first, last, changed in groups:
        day, last_day = local_day(first), local_day(last)
        while day <= last_day:
            candidates[day] = max(candidates.get(day, datetime.min), changed)
            day += timedelta(days=1)
    for airing in StationLogAiring.query.all():
        day, last = local_day(airing.starts_at), local_day(airing.ends_at - timedelta(microseconds=1))
        while day <= last:
            candidates[day] = max(candidates.get(day, datetime.min), airing.updated_at)
            day += timedelta(days=1)
    existing = {a.day: a for a in StationLogArchive.query.order_by(StationLogArchive.revision).all()}
    for day in sorted(candidates):
        if day <= cutoff:
            latest = existing.get(day)
            if latest and latest.created_at >= candidates[day] and Path(latest.csv_path).is_file() and Path(latest.sources_path).is_file():
                continue
            archive_day(day)
    return len([day for day in candidates if day <= cutoff])
