from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from app.main_routes import _collect_recordings
from app.models import DJ, DJAbsence, LogSheet, ShowRun, db
from app.services.airing_audit import build_audit
from app.services.log_export import LogCsvEntry, read_recording_metadata, write_log_csv, write_recording_metadata
from app.services.library.media_library import encode_media_token
from test_dj_recording_portal import portal_app, _show


def login(client, role="admin"):
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["role"] = role


def recording(root, day, suffix="", *, missed=False, hosts=None):
    path = root / f"Friday_Night_Radio_{day:%m-%d-%y}_RAWDATA{suffix}.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    write_recording_metadata(str(path), {"show_name": "Friday Night Radio", "show_date": day.isoformat(),
                                         "dj_names": hosts or ["Alex Smith"], "flagged_missed": missed})
    return path


def log(root, day, *, missed=False):
    path = root / f"Friday_Night_Radio_{day:%m-%d-%y}_RAWDATA.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_log_csv(csv_path=str(path), entries=[LogCsvEntry(day.isoformat(), "Friday Night Radio", "Alex Smith", "10:30", "music", "Song", "Artist", "")])
    if missed:
        metadata = read_recording_metadata(str(path))
        metadata["flagged_missed"] = True
        write_recording_metadata(str(path), metadata)
    return path


def report(app, root, week=date(2026, 9, 7)):
    with app.app_context():
        return build_audit(str(root), _collect_recordings(include_missed=True), week, now=datetime(2026, 10, 1))


def test_audit_deduplicates_recordings_logs_and_parts_and_excludes_missed(portal_app):
    app, root = portal_app
    with app.app_context():
        db.session.add_all([_show("Friday Night Radio"), DJ(first_name="Alex", last_name="Smith"), DJ(first_name="No", last_name="Shows")])
        db.session.commit()
    first = date(2026, 9, 4)
    recording(root, first)
    recording(root, first, "_part01")
    log(root, first)
    recording(root, first + timedelta(days=7), "_part01", hosts=["Alex Smith", "Pat Jones"])
    recording(root, first + timedelta(days=14), "_part01")
    recording(root, first + timedelta(days=14), "_part02")
    recording(root, first + timedelta(days=21), missed=True)
    log(root, first + timedelta(days=21))
    # A future upload cannot inflate totals.
    recording(root, date(2027, 1, 1))
    result = report(app, root)
    assert result["total"] == 2
    assert dict(result["dj_totals"]) == {"Alex Smith": 2, "No Shows": 0, "Pat Jones": 1}
    row = result["calendar"][0]
    assert row["total"] == 2
    assert row["cells"][4]["status"] == "Aired"
    # Multiple orphaned parts can still be supported by a submitted log.
    log(root, first + timedelta(days=14))
    assert report(app, root)["total"] == 3


def test_log_only_and_legacy_log_rows_count_once_but_missed_run_vetoes(portal_app):
    app, root = portal_app
    day = date(2026, 9, 11)
    log(root, day)
    with app.app_context():
        db.session.add(LogSheet(show_name="Friday Night Radio", dj_first_name="Alex", dj_last_name="Smith", show_date=day))
        db.session.commit()
    assert report(app, root)["total"] == 1
    with app.app_context():
        app.config["TIMEZONE"] = "UTC"
        db.session.add(ShowRun(show_name="Friday Night Radio", dj_first_name="Alex", dj_last_name="Smith", start_time=datetime(2026, 9, 11, 16), flagged_missed=True))
        db.session.commit()
    assert report(app, root)["total"] == 0


def test_calendar_red_for_missing_evidence_and_gray_for_future(portal_app):
    app, root = portal_app
    with app.app_context():
        db.session.add(_show("Friday Night Radio"))
        db.session.commit()
    result = report(app, root)
    assert result["calendar"][0]["cells"][4]["status"] == "No evidence"
    assert report(app, root, date(2026, 10, 5))["calendar"][0]["cells"][4]["status"] == "Upcoming"


def test_filename_date_fallback_does_not_use_mtime(portal_app):
    app, root = portal_app
    path = recording(root, date(2026, 9, 11))
    path.with_suffix(".json").unlink()
    unknown = root / "undated.mp3"
    unknown.write_bytes(b"audio")
    result = report(app, root)
    assert result["total"] == 1
    assert result["skipped"] == 1


def test_missed_control_filters_lists_exports_and_can_be_reversed(portal_app):
    app, root = portal_app
    path = recording(root, date(2026, 9, 11))
    part = recording(root, date(2026, 9, 11), "_part01")
    log(root, date(2026, 9, 11))
    token = encode_media_token(str(path))
    client = app.test_client()
    login(client, "viewer")
    assert client.post(f"/recordings/missed/{token}", data={"flagged_missed": "1"}).status_code == 403
    login(client)
    assert client.post(f"/recordings/missed/{token}", data={"flagged_missed": "1"}).status_code == 302
    assert read_recording_metadata(str(path))["flagged_missed"] is True
    assert read_recording_metadata(str(part))["flagged_missed"] is True
    assert path.name.encode() not in client.get("/recordings?period=__all__").data
    assert path.name.encode() in client.get("/recordings?period=__all__&show_missed=1").data
    assert b"Missed show" in client.get(f"/recordings/view/{token}").data
    assert report(app, root)["total"] == 0
    assert client.get("/recordings/download?period=__all__").status_code == 302
    assert client.get("/recordings/download?period=__all__&show_missed=1").status_code == 200
    assert client.post(f"/recordings/missed/{token}", data={"flagged_missed": "0"}).status_code == 302
    assert report(app, root)["total"] == 1
    assert path.name.encode() in client.get("/recordings?period=__all__").data


def test_audit_page_and_week_validation(portal_app):
    app, _ = portal_app
    client = app.test_client()
    assert client.get("/shows/audit").status_code == 403
    login(client)
    response = client.get("/shows/audit?week=2026-09-11")
    assert response.status_code == 200
    assert b'2026-09-07' in response.data
    for week in ("bad", "0001-01-01", "9999-12-31"):
        assert client.get(f"/shows/audit?week={week}").status_code == 400


def test_absence_specific_date_overnight_and_invalid_dates(portal_app, monkeypatch):
    app, _ = portal_app
    monkeypatch.setattr("app.main_routes.refresh_schedule", lambda: None)
    future = date.today() + timedelta(days=21)
    future += timedelta(days=(4-future.weekday()) % 7)
    with app.app_context():
        show = _show("Friday Night Radio")
        show.start_date, show.end_date = future - timedelta(days=30), future + timedelta(days=30)
        show.start_time, show.end_time = time(23), time(1)
        dj = DJ(first_name="Alex", last_name="Smith")
        db.session.add_all([show, dj])
        db.session.commit()
        payload = {"dj_id": dj.id, "show_id": show.id, "airing_date": future.isoformat()}
    client = app.test_client()
    assert client.post("/dj/absence", data=payload).status_code == 302
    with app.app_context():
        absence = DJAbsence.query.one()
        assert absence.start_time == datetime.combine(future, time(23))
        assert absence.end_time == datetime.combine(future + timedelta(days=1), time(1))
    for invalid in ("bad", (future+timedelta(days=1)).isoformat(), (future+timedelta(days=70)).isoformat(), "2020-01-03"):
        client.post("/dj/absence", data={**payload, "airing_date": invalid})
    with app.app_context():
        assert DJAbsence.query.count() == 1
    monkeypatch.setattr("app.main_routes.next_show_occurrence", lambda *args, **kwargs: (datetime.combine(future, time(23)), datetime.combine(future+timedelta(days=1), time(1))))
    client.post("/dj/absence", data={**payload, "airing_date": ""})
    with app.app_context():
        assert DJAbsence.query.count() == 2


def test_shared_navigation_loads_bootstrap_once_before_page_scripts(portal_app):
    app, _ = portal_app
    client = app.test_client()
    login(client)
    for route in ("/shows", "/recordings", "/shows/audit", "/dj/absence"):
        response = client.get(route)
        assert response.status_code == 200
        assert response.data.count(b'/dist/js/bootstrap.bundle.min.js') == 1
    templates = Path(__file__).parents[1] / "app/templates"
    for path in templates.glob("*.html"):
        source = path.read_text(encoding="utf-8")
        if "include '_nav.html'" in source:
            assert '/dist/js/bootstrap.bundle.min.js' not in source, path.name


def test_audit_defaults_to_active_period_and_preserves_period_in_week_links(portal_app):
    app, root = portal_app
    recording(root / "Fall 2026", date(2026, 9, 11))
    recording(root / "Spring 2026", date(2026, 3, 6))
    log(root / "Spring 2026", date(2026, 3, 13))
    client = app.test_client()
    login(client)
    response = client.get("/shows/audit?week=2026-09-07")
    assert response.status_code == 200
    assert b'<option value="Fall 2026" selected>' in response.data
    assert b'<strong>1</strong> airings in' in response.data
    response = client.get("/shows/audit?period=Spring+2026&week=2026-03-02")
    assert b'<option value="Spring 2026" selected>' in response.data
    assert b'<strong>2</strong> airings in' in response.data
    assert b'week=2026-02-23&amp;period=Spring+2026' in response.data
    assert b'week=2026-03-09&amp;period=Spring+2026' in response.data
    assert b'name="period" value="Spring 2026"' in response.data
    for invalid in ("__all__", "../outside"):
        response = client.get("/shows/audit", query_string={"period": invalid})
        assert b'<option value="Fall 2026" selected>' in response.data
        assert b'<strong>1</strong> airings in' in response.data


def test_period_audit_excludes_other_period_shows_and_unscoped_database_logs(portal_app):
    app, root = portal_app
    path = recording(root / "Spring 2026", date(2026, 3, 6))
    write_recording_metadata(str(path), {"show_name": "Spring Only", "show_date": "2026-03-06", "dj": "Spring Host"})
    with app.app_context():
        spring = _show("Spring Only", "Spring", "Host")
        spring.end_date = date(2026, 5, 31)
        fall = _show("Fall No Evidence", "Fall", "Host")
        fall.start_date = date(2026, 9, 1)
        dj = DJ(first_name="Spring", last_name="Host")
        dj.period_list = ["Spring 2026"]
        db.session.add_all([spring, fall, dj, LogSheet(show_name="Unrelated Database Show", dj_first_name="Other", dj_last_name="Host", show_date=date(2026, 3, 13))])
        db.session.commit()
        entries = _collect_recordings(period="Spring 2026", include_missed=True)
        result = build_audit(str(root / "Spring 2026"), entries, date(2026, 3, 2),
                             now=datetime(2026, 10, 18), period="Spring 2026", active_period="Fall 2026")
        assert [row["name"] for row in result["calendar"]] == ["Spring Only"]
        assert dict(result["dj_totals"]) == {"Spring Host": 1}
        assert result["total"] == 1
        assert result["unassigned_logs"] == 1
        result = build_audit(str(root / "Fall 2026"), [], date(2026, 10, 12),
                             now=datetime(2026, 10, 18), period="Fall 2026", active_period="Fall 2026")
        assert [row["name"] for row in result["calendar"]] == ["Fall No Evidence"]
        assert result["total"] == 0


def test_missed_save_does_not_scan_archive_read_logs_or_reload_analytics(portal_app, monkeypatch):
    from sqlalchemy import event
    app, root = portal_app
    path = recording(root / "Fall 2026" / "selected", date(2026, 9, 11))
    part = recording(path.parent, date(2026, 9, 11), "_part02")
    log(path.parent, date(2026, 9, 11))
    token = encode_media_token(str(path))
    client = app.test_client()
    login(client)
    def forbidden(*args, **kwargs):
        raise AssertionError("Missed saves must not scan the archive, read CSVs, or reload analytics")
    monkeypatch.setattr("app.main_routes._collect_recordings", forbidden)
    monkeypatch.setattr("app.main_routes.os.walk", forbidden)
    monkeypatch.setattr("app.main_routes.read_log_csv", forbidden)
    monkeypatch.setattr("app.main_routes.peak_listeners_for_show", forbidden)
    statements = []
    with app.app_context():
        engine = db.engine
    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FROM show_run" in statement:
            statements.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.post(f"/recordings/missed/{token}", data={"flagged_missed": "1"},
                               headers={"Accept": "application/json"})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert response.get_json() == {"flagged_missed": True}
    assert read_recording_metadata(str(part))["flagged_missed"] is True
    assert statements and all("WHERE" in sql and "start_time >=" in sql and "start_time <" in sql for sql in statements)


def test_missed_override_keeps_other_copies_and_logs_consistent_without_rewriting_them(portal_app):
    from app.services.missed_airings import load_missed_statuses
    app, root = portal_app
    day = date(2026, 9, 11)
    selected = recording(root / "Fall 2026" / "one", day)
    copy = recording(root / "Fall 2026" / "two", day)
    log_path = log(root / "Fall 2026" / "three", day)
    other = recording(root / "Fall 2026" / "two", date(2026, 9, 18))
    token = encode_media_token(str(selected))
    client = app.test_client()
    login(client)
    for value in ("1", "0"):
        response = client.post(f"/recordings/missed/{token}", data={"flagged_missed": value},
                               headers={"Accept": "application/json"})
        assert response.status_code == 200
        with app.app_context():
            entries = _collect_recordings(period="Fall 2026", include_missed=True)
            copied = next(entry for entry in entries if entry.full_path == str(copy))
            assert copied.flagged_missed is (value == "1")
            report = build_audit(str(root / "Fall 2026"), entries, date(2026, 9, 7),
                                 now=datetime(2026, 10, 19), period="Fall 2026", active_period="Fall 2026",
                                 missed_statuses=load_missed_statuses(str(root)))
            assert report["total"] == (1 if value == "1" else 2)
        detail = client.get(f"/recordings/view/{encode_media_token(str(copy))}")
        assert detail.status_code == 200
        assert (b'id="missedStatusAlert" class="alert alert-danger" hidden' in detail.data) is (value == "0")
        # Independent copies and separately named CSV evidence need no rewrite.
        assert read_recording_metadata(str(copy))["flagged_missed"] is False
        assert read_recording_metadata(str(log_path)) == {}
        assert read_recording_metadata(str(other))["flagged_missed"] is False


def test_explicit_clear_overrides_old_missed_flags_and_only_updates_matching_local_day(portal_app):
    from app.services.missed_airings import load_missed_statuses
    app, root = portal_app
    selected = recording(root / "Fall 2026" / "one", date(2026, 9, 11), missed=True)
    recording(root / "Fall 2026" / "two", date(2026, 9, 11), missed=True)
    log(root / "Fall 2026" / "three", date(2026, 9, 11), missed=True)
    with app.app_context():
        app.config["SCHEDULE_TIMEZONE"] = "America/New_York"
        # UTC midnight on Sept 12 still belongs to the Sept 11 local airing.
        matching = ShowRun(show_name="Friday Night Radio", dj_first_name="Alex", dj_last_name="Smith",
                           start_time=datetime(2026, 9, 12, 1), flagged_missed=True)
        unrelated = ShowRun(show_name="Friday Night Radio", dj_first_name="Alex", dj_last_name="Smith",
                            start_time=datetime(2026, 9, 12, 5), flagged_missed=True)
        db.session.add_all([matching, unrelated])
        db.session.commit()
        ids = matching.id, unrelated.id
    client = app.test_client()
    login(client)
    response = client.post(f"/recordings/missed/{encode_media_token(str(selected))}",
                           data={"flagged_missed": "0"}, headers={"Accept": "application/json"})
    assert response.status_code == 200
    with app.app_context():
        assert db.session.get(ShowRun, ids[0]).flagged_missed is False
        assert db.session.get(ShowRun, ids[1]).flagged_missed is True
        entries = _collect_recordings(period="Fall 2026", include_missed=True)
        result = build_audit(str(root / "Fall 2026"), entries, date(2026, 9, 7),
                             now=datetime(2026, 10, 19), missed_statuses=load_missed_statuses(str(root)))
        assert result["total"] == 1
