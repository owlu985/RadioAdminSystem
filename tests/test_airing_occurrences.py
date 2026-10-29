from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.models import DJ, DJAbsence, ShowRun, db
from app.main_routes import _collect_recordings
from app.services.airing_audit import build_audit, evidence_identity
from app.services.airing_dates import log_airing_date, airing_date_for_start
from app.services.log_export import LogCsvEntry, read_recording_metadata, write_log_csv
from app.services.recording_periods import load_recording_periods, save_recording_periods
from test_dj_recording_portal import portal_app, _show
from test_airing_audit import recording, log, login


def report(app, root, week=date(2026, 9, 7), **options):
    with app.app_context():
        return build_audit(str(root), _collect_recordings(include_missed=True), week,
                           now=datetime(2026, 10, 1), **options)


def night_show():
    show = _show("Friday Night Radio")
    show.start_time, show.end_time = time(22), time(0)
    return show


def test_after_midnight_log_and_recording_are_one_airing_on_start_date(portal_app):
    app, root = portal_app
    with app.app_context():
        db.session.add(night_show())
        db.session.commit()
    recording(root, date(2026, 9, 11))
    path = root / "Friday_Night_Radio_09-12-26_RAWDATA.csv"
    write_log_csv(csv_path=str(path), entries=[LogCsvEntry("2026-09-12", "Friday Night Radio", "Alex Smith", "23:00", "music", "Song", "Artist", "")])
    result = report(app, root)
    assert result["total"] == 1
    friday, saturday = result["calendar"][0]["cells"][4:6]
    assert friday["sources"] == "Digital log, Recording"
    assert saturday["status"] == "Unscheduled"
    assert result["repaired_logs"] == 1


def test_daily_show_dates_are_not_silently_moved_and_bad_entry_times_do_not_shift():
    show = night_show()
    show.days_of_week = "fri,sat"
    assert log_airing_date([show], date(2026, 9, 12), ["23:00"]) == date(2026, 9, 12)
    show.days_of_week = "fri"
    assert log_airing_date([show], date(2026, 9, 12), ["12:00"]) == date(2026, 9, 12)
    show.days_of_week, show.end_time = "fri,sat", time(1)
    assert airing_date_for_start([show], datetime(2026, 9, 12, 0, 30)) == date(2026, 9, 11)


def test_empty_original_with_141_parts_proves_exactly_one_airing(portal_app):
    app, root = portal_app
    with app.app_context():
        db.session.add(night_show())
        db.session.commit()
    original = recording(root, date(2026, 9, 11))
    original.write_bytes(b"")
    for number in range(1, 142):
        recording(root, date(2026, 9, 11), f"_part{number}")
    result = report(app, root)
    assert result["total"] == 1
    cell = result["calendar"][0]["cells"][4]
    assert cell["status"] == "Aired"
    assert "141 nonempty recording file(s); counted once" in cell["details"]


def test_filename_date_survives_old_sidecar_overwritten_with_submission_date():
    identity = evidence_identity("Friday_Night_Radio_09-11-26_RAWDATA_part141.mp3",
                                 {"show_name": "Friday Night Radio", "show_date": "2026-09-12"})
    assert identity == ("friday night radio", date(2026, 9, 11))


def test_actual_possible_counts_and_absence_substitute_missed_codes(portal_app):
    app, root = portal_app
    with app.app_context():
        show = _show("Friday Night Radio")
        db.session.add_all([show, DJ(first_name="Alex", last_name="Smith"), DJ(first_name="Pat", last_name="Jones")])
        db.session.flush()
        for day, replacement in [(date(2026, 9, 11), "Pat Jones"), (date(2026, 9, 18), None)]:
            db.session.add(DJAbsence(show_id=show.id, show_name=show.show_name, dj_name="Alex Smith",
                                    start_time=datetime.combine(day, time(10)), end_time=datetime.combine(day, time(12)),
                                    status="approved", replacement_name=replacement, notes="Travel"))
        db.session.commit()
    for day in (4, 11, 18):
        recording(root, date(2026, 9, day))
    recording(root, date(2026, 9, 25), missed=True)
    options = {"period_start": date(2026, 9, 1), "period_end": date(2026, 9, 30)}
    result = report(app, root, **options)
    assert result["calendar"][0]["total"] == 2
    assert result["calendar"][0]["possible"] == 4
    assert dict(result["dj_totals"]) == {"Alex Smith": 1, "Pat Jones": 1}
    assert result["dj_possible"] == {"Alex Smith": 4, "Pat Jones": 1}
    assert result["calendar"][0]["cells"][4]["code"] == "S"
    assert report(app, root, week=date(2026, 9, 14), **options)["calendar"][0]["cells"][4]["code"] == "E"
    assert report(app, root, week=date(2026, 9, 21), **options)["calendar"][0]["cells"][4]["code"] == "M"


def test_future_slots_excluded_and_unknown_legacy_denominator_is_not_invented(portal_app):
    app, root = portal_app
    recording(root, date(2026, 9, 11))
    result = report(app, root)
    assert result["calendar"][0]["possible"] is None
    assert result["dj_possible"]["Alex Smith"] is None
    with app.app_context():
        db.session.add(_show("Friday Night Radio"))
        db.session.commit()
        result = build_audit(str(root), _collect_recordings(include_missed=True), date(2026, 9, 7),
                             now=datetime(2026, 9, 11, 11), period_start=date(2026, 9, 1), period_end=date(2026, 9, 30))
    assert result["possible"] == 1  # Only Sept 4 has finished.
    assert result["total"] == 0


def test_arranged_substitute_without_proof_is_not_counted_as_aired(portal_app):
    app, root = portal_app
    with app.app_context():
        show = _show("Friday Night Radio")
        db.session.add(show)
        db.session.flush()
        db.session.add(DJAbsence(show_id=show.id, show_name=show.show_name, dj_name="Alex Smith",
                                start_time=datetime(2026, 9, 11, 10), end_time=datetime(2026, 9, 11, 12),
                                status="approved", replacement_name="Pat Jones", notes="Illness"))
        db.session.commit()
    recording(root, date(2026, 9, 11), missed=True)
    result = report(app, root)
    assert result["total"] == 0
    assert result["calendar"][0]["cells"][4]["code"] == "E/S"
    assert dict(result["dj_totals"])["Pat Jones"] == 0


def test_log_submission_uses_local_linked_run_date_and_repairs_unlinked_log(portal_app, monkeypatch):
    app, root = portal_app
    monkeypatch.setattr("app.routes.logging_api.recordings_period_root", lambda **kwargs: str(root))
    with app.app_context():
        app.config["SCHEDULE_TIMEZONE"] = "America/New_York"
        show = night_show()
        dj = DJ(first_name="Alex", last_name="Smith")
        run = ShowRun(show_name=show.show_name, dj_first_name="Alex", dj_last_name="Smith",
                      start_time=datetime(2026, 9, 12, 2), end_time=datetime(2026, 9, 12, 4))
        db.session.add_all([show, dj, run])
        db.session.commit()
        data = {"dj_id": dj.id, "show_id": show.id, "show_run_id": run.id, "show_date": "2026-09-12",
                "row_index": "0", "time_0": "23:00", "type_0": "music", "title_0": "Song"}
    client = app.test_client()
    assert client.post("/logs/submit", data=data).status_code == 302
    expected = root / "Friday_Night_Radio_09-11-26_RAWDATA.mp3"
    assert read_recording_metadata(str(expected))["airing_date"] == "2026-09-11"
    data.pop("show_run_id")
    assert client.post("/logs/submit", data=data).status_code == 302
    assert read_recording_metadata(str(expected))["airing_date"] == "2026-09-11"
    assert not (root / "Friday_Night_Radio_09-12-26_RAWDATA.csv").exists()


def test_management_adds_edits_and_validates_past_absence_with_reason(portal_app, monkeypatch):
    app, _ = portal_app
    def no_live_scheduler_changes():
        raise AssertionError("Historical absence edits must not refresh the live scheduler")
    monkeypatch.setattr("app.main_routes.refresh_schedule", no_live_scheduler_changes)
    with app.app_context():
        show = night_show()
        dj = DJ(first_name="Alex", last_name="Smith")
        db.session.add_all([show, dj])
        db.session.commit()
        data = {"action": "create", "show_id": show.id, "dj_id": dj.id,
                "airing_date": "2026-09-11", "notes": "Family commitment", "status": "approved"}
    client = app.test_client()
    login(client)
    assert client.post("/absences", data=data).status_code == 302
    with app.app_context():
        absence = DJAbsence.query.one()
        assert absence.start_time == datetime(2026, 9, 11, 22)
        assert absence.end_time == datetime(2026, 9, 12)
        assert absence.notes == "Family commitment"
        absence_id = absence.id
    for bad in ("2026-09-12", "2025-01-03", "invalid"):
        client.post("/absences", data={**data, "airing_date": bad})
    client.post("/absences", data=data)  # Duplicate is refused.
    client.post("/absences", data={**data, "airing_date": "2026-09-18", "notes": ""})
    with app.app_context():
        assert DJAbsence.query.count() == 1
    client.post("/absences", data={"absence_id": absence_id, "airing_date": "2026-09-18", "notes": "Updated reason", "status": "resolved"})
    with app.app_context():
        absence = db.session.get(DJAbsence, absence_id)
        assert absence.start_time == datetime(2026, 9, 18, 22)
        assert absence.notes == "Updated reason"
        assert absence.status == "resolved"


def test_period_date_settings_are_validated_and_survive_other_settings_saves(portal_app, monkeypatch, tmp_path):
    app, _ = portal_app
    app.config["RECORDING_PERIODS_PATH"] = str(tmp_path / "periods.json")
    with app.app_context():
        save_recording_periods(periods=["Fall 2026"], current="Fall 2026")
    monkeypatch.setattr("app.main_routes.load_recording_periods", load_recording_periods)
    client = app.test_client()
    data = {"period": "Fall 2026", "start": "2026-09-01", "end": "2026-12-15"}
    login(client, "viewer")
    assert client.post("/shows/audit/period-dates", data=data).status_code == 403
    login(client)
    assert client.post("/shows/audit/period-dates", data=data).status_code == 302
    assert client.post("/shows/audit/period-dates", data={**data, "end": "2026-01-01"}).status_code == 400
    with app.app_context():
        save_recording_periods(periods=["Fall 2026", "Spring 2027"], current="Fall 2026")
        assert load_recording_periods()["date_ranges"]["Fall 2026"] == {"start": "2026-09-01", "end": "2026-12-15"}
    response = client.get("/shows/audit?period=Fall+2026&week=2026-09-07")
    assert response.status_code == 200
    assert b"2026-09-01 to 2026-12-15" in response.data


def test_midnight_recorder_recovery_keeps_original_airing_date(portal_app, monkeypatch, tmp_path):
    from app import scheduler as scheduler_module
    app, _ = portal_app
    monkeypatch.setattr(scheduler_module, "flask_app", app)
    monkeypatch.setattr(scheduler_module, "_schedule_now", lambda: datetime(2026, 9, 12, 0, 30))
    monkeypatch.setattr(scheduler_module, "_active_marathon", lambda: None)
    monkeypatch.setattr(scheduler_module, "logger", SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None))
    monkeypatch.setattr(scheduler_module.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(returncode=0, poll=lambda: 0))
    monkeypatch.setattr(scheduler_module, "_wait_for_recorder", lambda *a, **kw: (b"", b"", False))
    base = tmp_path / "Friday_Night_Radio"
    scheduler_module.record_stream("http://example/stream", 1, str(base), str(tmp_path / "missing.json"),
                                   show_name="Friday Night Radio", hosts=["Alex Smith"], show_occurrence_date=date(2026, 9, 11))
    metadata = read_recording_metadata(str(tmp_path / "Friday_Night_Radio_09-11-26_RAWDATA.mp3"))
    assert metadata["airing_date"] == "2026-09-11"
