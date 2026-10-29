from datetime import date, datetime, timedelta, timezone
import csv
import io
import json
from pathlib import Path

import pytest
from flask import Flask

from app import create_app
from app.models import (db, StationLogEvent, StationLogImport, StationLogAiring,
                        StationLogSubmission, StationLogArchive, Show, DJ, ShowRun)
from app.services import station_log as logs, radiodj_history as history
from app.services.barix import restart_instreamer


@pytest.fixture
def app(tmp_path):
    class Config:
        TESTING = True
        SECRET_KEY = "station-log-test"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'station.db'}"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        RUN_SCHEMA_SETUP_ON_STARTUP = False
        RUN_MIGRATIONS_ON_STARTUP = False
        RUN_CLEANUP_ON_STARTUP = False
        RUN_SCHEDULER_ON_STARTUP = False
        RUN_UTILS_ON_STARTUP = False
        RUN_OAUTH_INIT_ON_STARTUP = False
        RUN_PLUGIN_LOAD_ON_STARTUP = False
        RATE_LIMIT_ENABLED = False
        DATA_ROOT = str(tmp_path / "data")
        LOGS_DIR = str(tmp_path / "logs")
        OUTPUT_FOLDER = str(tmp_path / "recordings")
        RECORDING_PERIODS_PATH = str(tmp_path / "periods.json")
        SCHEDULE_TIMEZONE = "America/New_York"
        ADMIN_URL_PREFIX = "/admin"
        RADIODJ_SQL_ENABLED = True
        RADIODJ_SQL_SOURCE = "radiodj-test"
        RADIODJ_SQL_TIMEZONE = "America/New_York"
        RADIODJ_SQL_INITIAL_DAYS = 4000
        RADIODJ_SQL_BATCH_SIZE = 2
        RADIODJ_SQL_MAX_BATCHES = 10
        STATION_LOG_ARCHIVE_DIR = str(tmp_path / "nas")
    created = create_app(Config)
    created.instance_path = str(tmp_path / "instance")
    Path(created.instance_path).mkdir()
    Path(created.config["STATION_LOG_ARCHIVE_DIR"]).mkdir()
    with created.app_context():
        db.create_all()
        # Keep startup evidence separate from assertions on test fixture events.
        for path in (Path(created.config["DATA_ROOT"]) / "station_log_pending").glob("*.json"):
            path.unlink()
        yield created
        db.session.remove()


def row(identity, when="2026-10-28 10:00:00", **updates):
    value = {field: "" for field in history.FIELDS}
    value.update(ID=identity, trackID=101, date_played=datetime.fromisoformat(when),
        duration=123.4, song_type=0, id_subcat=2, id_genre=3,
        title="Same Song", artist="Artist", isrc="USABC2600001")
    value.update(updates)
    return value


class FakeSQL:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []
        self.closed = False
        self.fail_after = None
    def cursor(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def close(self):
        self.closed = True
    def execute(self, query, params=()):
        assert query.startswith("SELECT "), "Remote database must never be mutated"
        self.queries.append((query, params))
        if self.fail_after is not None and len(self.queries) > self.fail_after:
            raise OSError("secret-password-do-not-log")
        if "MIN(" in query:
            self.result = [{"oldest": min((r["date_played"] for r in self.rows), default=None),
                            "max_id": max((r["ID"] for r in self.rows), default=None)}]
        elif "WHERE `ID` =" in query:
            self.result = [r for r in self.rows if r["ID"] == params[0]]
        else:
            selected = [r for r in self.rows if r["ID"] > params[0]]
            if "`date_played` >=" in query:
                selected = [r for r in selected if r["date_played"] >= params[1]]
            if "`date_played` <" in query:
                selected = [r for r in selected if r["date_played"] < params[2]]
            self.result = sorted(selected, key=lambda r: r["ID"])[:params[-1]]
    def fetchone(self):
        return dict(self.result[0]) if self.result else None
    def fetchall(self):
        return [dict(r) for r in self.result]


def connect(monkeypatch, rows):
    sql = FakeSQL(rows)
    monkeypatch.setattr(history, "connect_history", lambda: sql)
    return sql


def event(when, title="Source item", **updates):
    value = dict(source="test-source", source_id=title + when,
        event_type="playback", title=title, occurred_at=datetime.fromisoformat(when), details={})
    value.update(updates)
    result = StationLogEvent(**value)
    db.session.add(result)
    db.session.commit()
    return result


def airing(**updates):
    value = dict(show_name="Test Show", dj="Alex", starts_at=datetime(2026, 10, 28, 14),
        ends_at=datetime(2026, 10, 28, 15), status="aired", actor="operator", reason="Confirmed from aircheck")
    value.update(updates)
    return logs.review_airing(**value)


def test_all_types_repeats_isrc_and_metadata_survive_idempotent_import(app, monkeypatch):
    sql = connect(monkeypatch, [row(1), row(2, song_type=3, title="PSA", isrc=""),
        row(3, "2026-10-28 10:01:00"), row(4, song_type=8, title="Legal ID", duration=2)])
    assert history.import_history()["imported"] == 4
    assert history.import_history()["imported"] == 0
    records = StationLogEvent.query.order_by(StationLogEvent.source_id).all()
    assert len(records) == 4
    assert records[0].occurred_at == datetime(2026, 10, 28, 14)
    assert records[0].isrc == "USABC2600001"
    assert records[0].reported_duration == 123.4
    assert records[1].details["song_type"] == 3
    assert records[1].isrc == ""
    assert "listeners" not in records[0].details
    assert sql.closed
    assert db.session.get(StationLogImport, "radiodj-test").last_id == 4


def test_disconnect_after_committed_batch_resumes_without_loss_or_secret_leak(app, monkeypatch):
    sql = connect(monkeypatch, [row(n) for n in range(1, 6)])
    sql.fail_after = 2  # bounds and first batch succeed, second batch fails
    with pytest.raises(history.HistoryImportError, match="History import failed"):
        history.import_history()
    state = db.session.get(StationLogImport, "radiodj-test")
    assert state.last_id == 2
    assert StationLogEvent.query.count() == 2
    logs.drain_events()
    assert "secret-password" not in json.dumps([e.details for e in StationLogEvent.query.all()])
    sql.fail_after = None
    assert history.import_history()["imported"] == 3
    assert StationLogEvent.query.filter_by(event_type="playback").count() == 5
    assert state.last_error is None


def test_bad_row_rolls_back_whole_batch_and_checkpoint(app, monkeypatch):
    connect(monkeypatch, [row(1), row(2, duration="invalid")])
    with pytest.raises(history.HistoryImportError):
        history.import_history()
    assert StationLogEvent.query.count() == 0
    assert db.session.get(StationLogImport, "radiodj-test").last_id == 0


def test_bounded_catchup_reports_backlog(app, monkeypatch):
    app.config["RADIODJ_SQL_MAX_BATCHES"] = 1
    connect(monkeypatch, [row(n) for n in range(1, 6)])
    assert history.import_history()["backlog"]
    assert history.import_history()["imported"] == 2
    assert history.import_history()["backlog"] is False
    assert StationLogEvent.query.count() == 5


def test_daily_backfill_uses_exclusive_end_and_does_not_advance_live_checkpoint(app, monkeypatch):
    sql = connect(monkeypatch, [row(1, "2026-10-28 00:00:00"), row(2, "2026-10-28 23:59:59"), row(3, "2026-10-29 00:00:00")])
    result = history.import_history(day=date(2026, 10, 28))
    assert result["imported"] == 2
    assert db.session.get(StationLogImport, "radiodj-test").last_id == 0
    assert history.import_history(day=date(2026, 10, 28))["imported"] == 0
    assert any("`date_played` < %s" in q for q, _ in sql.queries)


def test_id_reset_stops_capture_without_erasing_evidence(app, monkeypatch):
    sql = connect(monkeypatch, [row(10)])
    history.import_history()
    sql.rows = [row(1)]
    with pytest.raises(history.HistoryImportError, match="moved backwards"):
        history.import_history()
    assert db.session.get(StationLogImport, "radiodj-test").needs_reset
    assert StationLogEvent.query.count() == 1
    history.reset_generation()
    assert db.session.get(StationLogImport, "radiodj-test").generation == 2
    assert StationLogEvent.query.count() == 1


def test_reused_checkpoint_is_detected(app, monkeypatch):
    sql = connect(monkeypatch, [row(1)])
    history.import_history()
    sql.rows = [row(1, "2026-10-29 10:00:00"), row(2)]
    with pytest.raises(history.HistoryImportError, match="checkpoint changed"):
        history.import_history()


def test_disabled_import_never_opens_connection(app, monkeypatch):
    app.config["RADIODJ_SQL_ENABLED"] = False
    monkeypatch.setattr(history, "connect_history", lambda: pytest.fail("must not connect"))
    assert history.import_history()["status"] == "disabled"


def test_connection_options_are_bounded_and_read_only_workflow(app, monkeypatch):
    app.config.update(RADIODJ_SQL_HOST="studio", RADIODJ_SQL_USER="rams_read", RADIODJ_SQL_PASSWORD="private",
                      RADIODJ_SQL_SSL_CA="/test/ca.pem")
    captured = {}
    monkeypatch.setattr(history.pymysql, "connect", lambda **kw: captured.update(kw))
    history.connect_history()
    assert captured["connect_timeout"] == 5
    assert captured["read_timeout"] == 10
    assert captured["local_infile"] is False
    assert captured["ssl_verify_identity"] is True


def test_show_substitution_preserves_incidents_and_raw_records(app):
    event("2026-10-28 13:59:59", title="Before")
    event("2026-10-28 14:10:00", title="Behind show")
    event("2026-10-28 14:11:00", title="Error", event_type="job_failure")
    event("2026-10-28 15:00:00", title="After")
    logs.save_submission(entries=[dict(time="10:05", title="DJ song", entry_type="music")],
        show_name="Test Show", dj="Alex", starts_at=datetime(2026,10,28,14), ends_at=datetime(2026,10,28,15))
    saved = StationLogAiring.query.one()
    assert saved.status == "unknown"
    assert "Behind show" in [r["title"] for r in logs.daily_log(date(2026,10,28))]
    airing(airing=saved)
    titles = [r["title"] for r in logs.daily_log(date(2026,10,28))]
    assert "DJ song" in titles and "Error" in titles and "Before" in titles and "After" in titles
    assert "Behind show" not in titles
    assert "Behind show" in [r["title"] for r in logs.daily_log(date(2026,10,28), raw=True)]


def test_confirmed_show_without_log_is_explicit_and_absence_restores_radioDJ(app):
    event("2026-10-28 14:10:00")
    saved = airing()
    rows = logs.daily_log(date(2026,10,28))
    assert any("playlist log missing" in r["title"] for r in rows)
    assert not any(r["event_type"] == "playback" for r in rows)
    airing(airing=saved, status="missed", reason="DJ confirmed absence")
    assert any(r["event_type"] == "playback" for r in logs.daily_log(date(2026,10,28)))
    logs.drain_events()
    audit = StationLogEvent.query.filter_by(event_type="airing_reviewed").all()
    assert any(e.details["before"] and e.details["before"]["status"] == "aired" for e in audit)


def test_late_revision_replaces_view_but_preserves_submissions(app):
    args = dict(show_name="Test Show", dj="Alex", starts_at=datetime(2026,10,28,14), ends_at=datetime(2026,10,28,15))
    logs.save_submission(entries=[dict(time="10:05", title="Original")], **args)
    saved = StationLogAiring.query.one()
    airing(airing=saved)
    logs.save_submission(entries=[dict(time="10:06", title="Corrected")], **args)
    titles = [r["title"] for r in logs.daily_log(date(2026,10,28))]
    assert "Corrected" in titles and "Original" not in titles
    assert StationLogSubmission.query.count() == 2
    assert saved.status == "aired"


def test_overnight_log_splits_by_calendar_day(app):
    start, end = datetime(2026,10,29,3), datetime(2026,10,29,5)
    logs.save_submission(entries=[dict(time="23:30", title="Before midnight"), dict(time="00:30", title="After midnight")],
        show_name="Night", dj="Alex", starts_at=start, ends_at=end)
    saved = StationLogAiring.query.one()
    airing(airing=saved, show_name="Night", starts_at=start, ends_at=end)
    assert "Before midnight" in [r["title"] for r in logs.daily_log(date(2026,10,28))]
    assert "After midnight" not in [r["title"] for r in logs.daily_log(date(2026,10,28))]
    assert "After midnight" in [r["title"] for r in logs.daily_log(date(2026,10,29))]


def test_overlapping_confirmed_windows_are_rejected(app):
    airing()
    with pytest.raises(ValueError, match="overlap"):
        airing(show_name="Other show")


def test_untimed_and_outside_entries_are_retained_as_unconfirmed(app):
    logs.save_submission(entries=[dict(time="", title="Untimed"), dict(time="17:00", title="Wrong time")],
        show_name="Test Show", dj="Alex", starts_at=datetime(2026,10,28,14), ends_at=datetime(2026,10,28,15))
    airing(airing=StationLogAiring.query.one())
    rows = logs.daily_log(date(2026,10,28))
    assert next(r for r in rows if r["title"] == "Untimed")["time_accuracy"] == "unreported"
    assert "review required" in next(r for r in rows if r["title"] == "Wrong time")["time_accuracy"]


def test_station_day_handles_dst_and_sql_time_ambiguity_is_flagged(app, monkeypatch):
    start, end = logs.day_bounds(date(2026,11,1))
    assert end - start == timedelta(hours=25)
    start, end = logs.day_bounds(date(2026,3,8))
    assert end - start == timedelta(hours=23)
    with pytest.raises(ValueError, match="Ambiguous"):
        logs.local_to_utc(datetime(2026,11,1,1,30))
    connect(monkeypatch, [row(1, "2026-11-01 01:30:00")])
    history.import_history()
    assert StationLogEvent.query.one().details["timestamp_ambiguous"]


def test_spool_survives_missing_tables_and_never_commits_caller_work(app):
    pending = StationLogAiring(identity="not-committed", show_name="Pending", dj="", starts_at=datetime(2026,10,28), ends_at=datetime(2026,10,29))
    db.session.add(pending)
    assert logs.emit_event("test_event", "queued")
    db.session.rollback()
    assert StationLogAiring.query.count() == 0
    assert logs.drain_events() == 1
    assert logs.drain_events() == 0
    assert StationLogEvent.query.one().title == "queued"


def test_alert_transitions_deduplicate_and_recovery_is_distinct(app):
    alert = dict(code="02", message="NO INPUT DETECTED", severity="critical")
    logs.record_alert_transitions([alert], stream_up=True)
    logs.record_alert_transitions([alert], stream_up=True)
    logs.record_alert_transitions([], stream_up=False)
    logs.record_alert_transitions([], stream_up=True)
    logs.drain_events()
    assert StationLogEvent.query.filter_by(event_type="code_raised").count() == 1
    assert StationLogEvent.query.filter_by(event_type="code_cleared").count() == 1
    assert StationLogEvent.query.filter_by(event_type="stream_unavailable").count() == 1
    assert StationLogEvent.query.filter_by(event_type="stream_recovered").count() == 2


def test_barix_request_and_result_are_not_claimed_as_recovery(app, monkeypatch):
    from types import SimpleNamespace
    from app.services import barix
    app.config.update(BARIX_RESTART_URL="http://barix/setup.cgi?c=99")
    monkeypatch.setattr(barix.requests, "get", lambda *a, **kw: SimpleNamespace(status_code=200))
    restart_instreamer(reason="manual", force=True)
    logs.drain_events()
    types = {e.event_type for e in StationLogEvent.query.all()}
    assert types == {"barix_restart_requested", "barix_restart_result"}
    assert StationLogEvent.query.filter_by(event_type="barix_restart_result").one().details["accepted"]


def test_archive_is_verified_versioned_and_keeps_database(app):
    event("2026-10-28 14:00:00", title="First")
    first = logs.archive_day(date(2026,10,28))
    first_data = Path(first.csv_path).read_bytes()
    assert first.revision == 1 and first_data.startswith(b"\xef\xbb\xbf")
    assert logs.archive_day(date(2026,10,28)).id == first.id
    event("2026-10-28 15:00:00", title="Late")
    second = logs.archive_day(date(2026,10,28))
    assert second.revision == 2
    assert Path(first.csv_path).read_bytes() == first_data
    assert StationLogEvent.query.count() == 2
    assert StationLogArchive.query.count() == 2


def test_nas_failure_does_not_mark_export_complete_or_delete_evidence(app):
    event("2026-10-28 14:00:00")
    app.config["STATION_LOG_ARCHIVE_DIR"] += "/missing"
    with pytest.raises(OSError, match="unavailable"):
        logs.archive_day(date(2026,10,28))
    assert StationLogArchive.query.count() == 0
    assert StationLogEvent.query.count() == 1


def test_mount_check_prevents_local_disk_fallback(app, monkeypatch):
    app.config["STATION_LOG_ARCHIVE_MOUNT"] = "/not-mounted"
    monkeypatch.setattr(logs.os.path, "ismount", lambda path: False)
    with pytest.raises(OSError, match="mount"):
        logs.archive_day(date(2026,10,28))


def test_csv_formula_characters_are_neutralized(app):
    event("2026-10-28 14:00:00", title="=HYPERLINK(unsafe)")
    text = logs.csv_bytes(logs.daily_log(date(2026,10,28))).decode("utf-8-sig")
    assert list(csv.DictReader(io.StringIO(text)))[0]["title"].startswith("'=")


def test_view_and_mutations_require_permissions_and_csrf(app):
    client = app.test_client()
    assert client.get("/admin/station-log").status_code == 403
    assert client.post("/admin/station-log/note").status_code == 403
    with client.session_transaction() as s:
        s.update(authenticated=True, role="viewer")
    assert client.get("/admin/station-log?day=2026-10-28").status_code == 200
    assert client.post("/admin/station-log/note").status_code == 403
    with client.session_transaction() as s:
        s.update(role="admin")
    assert client.post("/admin/station-log/note", data={"title":"note"}).status_code == 400
    client.get("/admin/station-log")
    with client.session_transaction() as s:
        token = s["station_log_csrf"]
    assert client.post("/admin/station-log/note", data={"title":"note", "csrf_token":token, "day":"2026-10-28"}).status_code == 302
    response = client.get("/admin/station-log/events?day=2026-10-28")
    assert response.status_code == 200


def test_connection_page_never_renders_password(app):
    app.config["RADIODJ_SQL_PASSWORD"] = "secret-do-not-render"
    client = app.test_client()
    with client.session_transaction() as s:
        s.update(authenticated=True, role="admin")
    response = client.get("/admin/station-log/connection")
    assert response.status_code == 200
    assert b"secret-do-not-render" not in response.data


def test_existing_public_submission_creates_revision_without_fake_show_run(app):
    show = Show(show_name="Campus", host_first_name="Alex", host_last_name="Smith",
        start_date=date(2026,1,1), end_date=date(2026,12,31), days_of_week="wed",
        start_time=datetime(2026,10,28,10).time(), end_time=datetime(2026,10,28,11).time())
    dj = DJ(first_name="Alex", last_name="Smith")
    db.session.add_all([show, dj])
    db.session.commit()
    response = app.test_client().post("/admin/logs/submit", data={
        "show_id":show.id, "dj_id":dj.id, "show_date":"2026-10-28", "row_index":"0",
        "time_0":"10:05", "type_0":"music", "title_0":"DJ song", "artist_0":"Artist"})
    assert response.status_code == 302
    assert StationLogSubmission.query.count() == 1
    assert StationLogAiring.query.one().status == "unknown"
    assert ShowRun.query.count() == 0


def test_init_db_cli_creates_tables_without_touching_existing_data(app):
    event("2026-10-28 14:00:00")
    result = app.test_cli_runner().invoke(args=["station-log", "init-db"])
    assert result.exit_code == 0
    assert StationLogEvent.query.count() == 1


def test_manual_window_created_before_submission_links_to_same_airing(app):
    saved = airing()
    logs.save_submission(entries=[dict(time="10:05", title="Late song", isrc="USABC2600002")],
        show_name="Test Show", dj="Alex", starts_at=saved.starts_at, ends_at=saved.ends_at)
    assert StationLogAiring.query.count() == 1
    assert next(r for r in logs.daily_log(date(2026,10,28)) if r["title"] == "Late song")["isrc"] == "USABC2600002"


def test_archive_only_due_days_and_late_revisions_are_reexported(app):
    event("2026-10-20 14:00:00", title="Old")
    event("2026-10-28 14:00:00", title="Recent")
    logs.archive_due_days(now=datetime(2026,10,29,12,tzinfo=timezone.utc))
    assert [a.day for a in StationLogArchive.query.all()] == [date(2026,10,20)]
    event("2026-10-20 15:00:00", title="Late old event")
    logs.archive_due_days(now=datetime(2026,10,29,13,tzinfo=timezone.utc))
    assert StationLogArchive.query.count() == 2
    assert StationLogArchive.query.order_by(StationLogArchive.revision.desc()).first().revision == 2


def test_error_capture_redacts_configured_credentials(app):
    import logging
    app.config["RADIODJ_SQL_PASSWORD"] = "unique-password"
    handler = logs.StationErrorHandler()
    handler.emit(logging.LogRecord("ShowRecorder", logging.ERROR, "test", 1,
        "Failed: unique-password at http://user:password@barix/setup", (), None))
    logs.drain_events()
    title = StationLogEvent.query.one().title
    assert "unique-password" not in title and "user:password" not in title
    assert "[redacted]" in title


def test_connection_settings_preserve_blank_password_and_never_log_it(app):
    app.config["RADIODJ_SQL_PASSWORD"] = "retain-this-password"
    client = app.test_client()
    with client.session_transaction() as s:
        s.update(authenticated=True, role="admin")
    client.get("/admin/station-log/connection")
    with client.session_transaction() as s:
        token = s["station_log_csrf"]
    response = client.post("/admin/station-log/connection", data=dict(
        csrf_token=token, enabled="1", host="database.test", port="3306", database="radiodj",
        username="rams_read", password="", source="radiodj-test", timezone="America/New_York"))
    assert response.status_code == 302
    assert app.config["RADIODJ_SQL_PASSWORD"] == "retain-this-password"
    config_path = Path(app.instance_path) / "user_config.json"
    assert json.loads(config_path.read_text())["RADIODJ_SQL_HOST"] == "database.test"
    assert config_path.stat().st_mode & 0o777 == 0o600
    logs.drain_events()
    assert "retain-this-password" not in json.dumps([e.details for e in StationLogEvent.query.all()])


def test_public_submission_isrc_is_saved_and_old_csvs_still_read(app, tmp_path):
    from app.services.log_export import read_log_csv, write_log_csv, LogCsvEntry
    path = tmp_path / "playlist.csv"
    path.write_text("Show Date,Show Name,DJ,Time,Type,Title,Artist,Message\n2026-10-28,Show,DJ,10:00,music,Song,Artist,\n")
    entries = read_log_csv(str(path))
    assert entries[0].isrc == ""
    entries[0].isrc = "USABC2600001"
    write_log_csv(csv_path=str(path), entries=entries)
    assert read_log_csv(str(path))[0].isrc == "USABC2600001"


def test_viewer_renders_success_and_error_states_after_sql_import(app, monkeypatch):
    sql = connect(monkeypatch, [row(1)])
    history.import_history()
    client = app.test_client()
    with client.session_transaction() as s:
        s.update(authenticated=True, role="admin")
    response = client.get("/admin/station-log?day=2026-10-28", environ_overrides={"SCRIPT_NAME":""})
    assert response.status_code == 200
    assert b"Last successful check" in response.data
    assert b"Same Song" in response.data
    sql.fail_after = 0
    with pytest.raises(history.HistoryImportError):
        history.import_history()
    response = client.get("/admin/station-log?day=2026-10-28", environ_overrides={"SCRIPT_NAME":""})
    assert response.status_code == 200
    assert b"Capture may be incomplete" in response.data


def test_scheduled_show_without_submission_stays_unconfirmed_and_links_late_log(app):
    db.session.add(Show(show_name="Campus", host_first_name="Alex", host_last_name="Smith",
        start_date=date(2026,1,1), end_date=date(2026,12,31), days_of_week="wed",
        start_time=datetime(2026,10,28,10).time(), end_time=datetime(2026,10,28,11).time()))
    db.session.commit()
    logs.register_scheduled_airings(date(2026,10,28))
    logs.register_scheduled_airings(date(2026,10,28))
    saved = StationLogAiring.query.one()
    assert saved.status == "unknown"
    event("2026-10-28 14:10:00", title="Automation evidence")
    assert "Automation evidence" in [r["title"] for r in logs.daily_log(date(2026,10,28))]
    logs.save_submission(entries=[dict(time="10:20", title="Late log")], show_name="Campus", dj="Alex",
        starts_at=datetime(2026,10,28,14,5), ends_at=datetime(2026,10,28,15,5), show_run_id=32)
    assert StationLogAiring.query.count() == 1
    assert saved.show_run_id == 32
    assert StationLogSubmission.query.one().airing_id == saved.id


def test_api_observer_records_stalled_monitor_code_once(app):
    from app.services.studio_monitor import monitor_snapshot
    monitor_snapshot()
    monitor_snapshot()
    logs.drain_events()
    assert StationLogEvent.query.filter_by(event_type="code_raised", code="09").count() == 1
