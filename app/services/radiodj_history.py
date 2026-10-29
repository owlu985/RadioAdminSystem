"""Read-only MySQL/MariaDB history capture, independent of the RadioDJ REST API."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from zoneinfo import ZoneInfo

import pymysql
from flask import current_app

from app.models import db, StationLogEvent, StationLogImport
from app.services.runtime_state import locked_state
from app.services.station_log import day_bounds, emit_event, station_zone, utc_naive

FIELDS = ("ID", "trackID", "date_played", "song_type", "id_subcat", "id_genre", "duration",
          "artist", "original_artist", "title", "album", "composer", "label", "year",
          "track_no", "disc_no", "publisher", "copyright", "isrc")
SELECT_HISTORY = "SELECT " + ", ".join(f"`{field}`" for field in FIELDS) + " FROM `history`"


class HistoryImportError(RuntimeError):
    pass


def enabled():
    value = current_app.config.get("RADIODJ_SQL_ENABLED", False)
    return str(value).lower() in {"1", "true", "yes", "on"}


def source_name():
    return str(current_app.config.get("RADIODJ_SQL_SOURCE") or "radiodj-main")


def connect_history():
    cfg = current_app.config
    if not enabled():
        raise HistoryImportError("RadioDJ SQL capture is disabled")
    if not cfg.get("RADIODJ_SQL_HOST") or not cfg.get("RADIODJ_SQL_USER"):
        raise HistoryImportError("Set the RadioDJ SQL host and read-only username")
    options = dict(host=cfg["RADIODJ_SQL_HOST"], port=int(cfg.get("RADIODJ_SQL_PORT", 3306)),
        user=cfg["RADIODJ_SQL_USER"], password=cfg.get("RADIODJ_SQL_PASSWORD") or "",
        database=cfg.get("RADIODJ_SQL_DATABASE") or "radiodj", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, connect_timeout=5, read_timeout=10,
        write_timeout=10, autocommit=True, local_infile=False)
    if cfg.get("RADIODJ_SQL_SSL_CA"):
        options.update(ssl_ca=cfg["RADIODJ_SQL_SSL_CA"], ssl_verify_cert=True, ssl_verify_identity=True)
    return pymysql.connect(**options)


def safe_error(exc):
    # Driver exception messages can contain hostnames, usernames, or connection details.
    if isinstance(exc, HistoryImportError):
        return str(exc)
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    messages = {1045: "Database login rejected", 1044: "Database access denied",
                1142: "SELECT permission denied", 1146: "History table not found",
                1054: "History schema does not match the expected columns",
                2003: "Cannot reach the database", 2013: "Database connection interrupted"}
    return messages.get(code, "History import failed; check connection, schema, timezone and permissions")


def _fingerprint(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _convert(row, generation):
    original = row["date_played"]
    value = original if isinstance(original, datetime) else datetime.fromisoformat(str(original))
    zone = ZoneInfo(current_app.config.get("RADIODJ_SQL_TIMEZONE", "America/New_York"))
    ambiguous = False
    if value.tzinfo is None:
        ambiguous = value.replace(tzinfo=zone, fold=0).utcoffset() != value.replace(tzinfo=zone, fold=1).utcoffset()
        value = value.replace(tzinfo=zone, fold=0)
    duration = float(row["duration"]) if row.get("duration") is not None else None
    if duration is not None and (not math.isfinite(duration) or duration < 0):
        duration = None
    details = {key: val for key, val in row.items() if key not in {"listeners"}}
    details["date_played"] = str(original)
    details.update(duration_basis="RadioDJ reported item length; actual airtime unverified",
                   database_timezone=str(zone), timestamp_ambiguous=ambiguous)
    # Normalize Decimal/datetime values without discarding original metadata.
    details = json.loads(json.dumps(details, default=str))
    return StationLogEvent(source=source_name(), source_id=f"{generation}:{row['ID']}",
        occurred_at=utc_naive(value), event_type="playback", title=row.get("title"),
        artist=row.get("artist"), album=row.get("album"), isrc=row.get("isrc"),
        track_id=row.get("trackID"), reported_duration=duration, details=details,
        severity="warning" if ambiguous else "info")


def _insert_batch(rows, state, *, checkpoint):
    source_ids = [f"{state.generation}:{row['ID']}" for row in rows]
    existing = {e.source_id: e for e in StationLogEvent.query.filter(
        StationLogEvent.source == state.source, StationLogEvent.source_id.in_(source_ids)).all()}
    added = 0
    for row, source_id in zip(rows, source_ids):
        if source_id in existing:
            if existing[source_id].details.get("date_played") != str(row["date_played"]):
                raise HistoryImportError("History IDs were reused; reset the source generation before importing")
            continue
        db.session.add(_convert(row, state.generation))
        added += 1
    if checkpoint and rows:
        state.last_id = int(rows[-1]["ID"])
        state.last_fingerprint = _fingerprint(rows[-1])
    # A checkpoint advances in the SAME transaction as its records.
    db.session.commit()
    return added


def _state():
    state = db.session.get(StationLogImport, source_name())
    if state is None:
        days = max(1, int(current_app.config.get("RADIODJ_SQL_INITIAL_DAYS", 7)))
        start, _ = day_bounds(datetime.now(station_zone()).date() - timedelta(days=days))
        state = StationLogImport(source=source_name(), coverage_start=start, last_id=0, generation=1)
        db.session.add(state)
        db.session.commit()
    return state


def import_history(*, day=None):
    """Bounded incremental capture, or explicit full-day backfill/reconciliation."""
    if not enabled():
        return {"status": "disabled", "imported": 0}
    with locked_state("station-log-sql"):
        state = _state()
        connection = None
        try:
            if state.needs_reset:
                raise HistoryImportError("History source needs an explicit generation reset")
            connection = connect_history()
            with connection.cursor() as cursor:
                cursor.execute("SELECT MIN(`date_played`) AS oldest, MAX(`ID`) AS max_id FROM `history`")
                bounds = cursor.fetchone() or {}
                maximum = bounds.get("max_id")
                if maximum is not None and int(maximum) < state.last_id:
                    state.needs_reset = True
                    db.session.commit()
                    raise HistoryImportError("History IDs moved backwards; reset the source generation before importing")
                if state.last_id and state.last_fingerprint:
                    cursor.execute(SELECT_HISTORY + " WHERE `ID` = %s", (state.last_id,))
                    anchor = cursor.fetchone()
                    if anchor and _fingerprint(anchor) != state.last_fingerprint:
                        state.needs_reset = True
                        db.session.commit()
                        raise HistoryImportError("History checkpoint changed; review and reset the source generation")
                batch_size = max(1, min(5000, int(current_app.config.get("RADIODJ_SQL_BATCH_SIZE", 500))))
                max_batches = max(1, int(current_app.config.get("RADIODJ_SQL_MAX_BATCHES", 20)))
                zone = ZoneInfo(current_app.config.get("RADIODJ_SQL_TIMEZONE", "America/New_York"))
                if day is not None:
                    start, end = day_bounds(day)
                    start = start.replace(tzinfo=timezone.utc).astimezone(zone).replace(tzinfo=None)
                    end = end.replace(tzinfo=timezone.utc).astimezone(zone).replace(tzinfo=None)
                    after = 0
                else:
                    start = state.coverage_start.replace(tzinfo=timezone.utc).astimezone(zone).replace(tzinfo=None)
                    end, after = None, state.last_id
                state.oldest_available = str(bounds.get("oldest") or "unknown")
                oldest = bounds.get("oldest")
                if isinstance(oldest, str):
                    oldest = datetime.fromisoformat(oldest)
                warning = None
                if oldest is None:
                    warning = "RadioDJ history is empty; coverage cannot be verified"
                elif oldest > start and (day is not None or not state.last_id):
                    warning = "Requested period begins before the oldest retained RadioDJ record; earlier coverage is unverified"
                if warning and warning != state.coverage_warning:
                    state.coverage_warning = warning
                    emit_event("sql_coverage_warning", warning, severity="warning",
                               details={"requested_start": str(start), "oldest_available": state.oldest_available})
                imported, complete = 0, False
                for _ in range(max_batches):
                    if day is not None:
                        query = SELECT_HISTORY + " WHERE `ID` > %s AND `date_played` >= %s AND `date_played` < %s ORDER BY `ID` LIMIT %s"
                        args = (after, start, end, batch_size)
                    elif after:
                        query = SELECT_HISTORY + " WHERE `ID` > %s ORDER BY `ID` LIMIT %s"
                        args = (after, batch_size)
                    else:
                        query = SELECT_HISTORY + " WHERE `ID` > %s AND `date_played` >= %s ORDER BY `ID` LIMIT %s"
                        args = (after, start, batch_size)
                    cursor.execute(query, args)
                    rows = cursor.fetchall()
                    imported += _insert_batch(rows, state, checkpoint=day is None)
                    if rows:
                        after = int(rows[-1]["ID"])
                    if len(rows) < batch_size:
                        complete = True
                        break
                if day is not None and not complete:
                    # Reconciliation can be rerun with a higher batch limit; it must not be marked complete.
                    raise HistoryImportError("Day reconciliation exceeded the batch limit; increase RADIODJ_SQL_MAX_BATCHES")
                recovered = bool(state.last_error)
                state.last_success, state.last_error = datetime.utcnow(), None
                if day is None:
                    state.backlog = not complete
                else:
                    state.last_reconciled_day = day
                db.session.commit()
                if recovered:
                    emit_event("sql_import_recovered", "RadioDJ SQL history connection recovered")
                return {"status": "ok", "imported": imported, "backlog": not complete,
                        "oldest_available": str(bounds.get("oldest") or "unknown")}
        except Exception as exc:
            db.session.rollback()
            state = db.session.get(StationLogImport, source_name())
            message = safe_error(exc)
            if state.last_error != message:
                emit_event("sql_import_failed", message, severity="error")
            state.last_error = message
            db.session.commit()
            raise HistoryImportError(message) from None
        finally:
            if connection is not None:
                connection.close()


def reset_generation():
    with locked_state("station-log-sql"):
        state = _state()
        state.generation += 1
        state.last_id, state.last_fingerprint, state.needs_reset = 0, None, False
        state.last_error, state.last_success, state.last_reconciled_day = None, None, None
        # Avoid replaying an entire restored database as new broadcasts.
        state.coverage_start = datetime.utcnow()
        db.session.commit()
        emit_event("sql_source_reset", "RadioDJ history source generation reset", severity="warning",
                   details={"source": state.source, "generation": state.generation})


def history_status():
    # Connection settings must remain accessible before the local tables exist.
    from app.services.station_log import missing_tables
    missing = missing_tables()
    state = db.session.get(StationLogImport, source_name()) if StationLogImport.__tablename__ not in missing else None
    return dict(enabled=enabled(), source=source_name(), last_id=state.last_id if state else 0,
        setup_required=bool(missing),
        last_success=state.last_success.isoformat() + "Z" if state and state.last_success else None,
        last_error=state.last_error if state else None,
        coverage_start=state.coverage_start.isoformat() + "Z" if state and state.coverage_start else None,
        last_reconciled_day=str(state.last_reconciled_day) if state and state.last_reconciled_day else None,
        oldest_available=state.oldest_available if state else None,
        coverage_warning=state.coverage_warning if state else None,
        stale=bool(state and state.last_success and (datetime.utcnow() - state.last_success).total_seconds() >
                   max(180, int(current_app.config.get("RADIODJ_SQL_POLL_SECONDS", 60)) * 3)),
        backlog=state.backlog if state else False, needs_reset=state.needs_reset if state else False)
