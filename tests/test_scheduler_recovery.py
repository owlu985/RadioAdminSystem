from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

from flask import Flask

from app import scheduler as scheduler_module


class FakeScheduler:
    running = True

    def __init__(self):
        self.jobs = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append((func, trigger, kwargs))


def _show(show_id=7):
    return SimpleNamespace(
        id=show_id,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        start_time=time(10, 0),
        end_time=time(12, 0),
        days_of_week="mon",
        show_name="Campus Hour",
        host_first_name="Alex",
        host_last_name="Smith",
        djs=[],
        is_temporary=False,
    )


def test_active_show_is_recorded_from_recovery_until_its_scheduled_end(monkeypatch, tmp_path):
    fake_scheduler = FakeScheduler()
    monkeypatch.setattr(scheduler_module, "scheduler", fake_scheduler)
    monkeypatch.setattr(scheduler_module, "recordings_period_root", lambda create: str(tmp_path))
    monkeypatch.setattr(scheduler_module, "is_show_preempted_by_absence", lambda *args: False)
    monkeypatch.setattr(scheduler_module, "logger", SimpleNamespace(warning=lambda *args: None))
    scheduler_module.CATCHED_UP_SHOW_WINDOWS.clear()

    app = Flask(__name__, instance_path=str(tmp_path / "instance"))
    app.config["STREAM_URL"] = "http://stream.example/live"
    now = datetime(2026, 9, 7, 10, 30)
    with app.app_context():
        assert scheduler_module.schedule_active_show_catchup(_show(), now) is True
        assert scheduler_module.schedule_active_show_catchup(_show(), now + timedelta(minutes=1)) is False

    assert len(fake_scheduler.jobs) == 1
    func, trigger, options = fake_scheduler.jobs[0]
    assert func is scheduler_module.record_stream
    assert trigger == "date"
    assert options["args"][1] == 90 * 60
    assert options["id"].startswith("show-catchup:7:")


def test_overnight_show_recovery_uses_original_occurrence_date(monkeypatch, tmp_path):
    fake_scheduler = FakeScheduler()
    monkeypatch.setattr(scheduler_module, "scheduler", fake_scheduler)
    monkeypatch.setattr(scheduler_module, "recordings_period_root", lambda create: str(tmp_path))
    monkeypatch.setattr(scheduler_module, "is_show_preempted_by_absence", lambda *args: False)
    monkeypatch.setattr(scheduler_module, "logger", SimpleNamespace(warning=lambda *args: None))
    scheduler_module.CATCHED_UP_SHOW_WINDOWS.clear()
    show = _show()
    show.start_time = time(23, 0)
    show.end_time = time(1, 0)

    app = Flask(__name__, instance_path=str(tmp_path / "instance"))
    app.config["STREAM_URL"] = "http://stream.example/live"
    with app.app_context():
        assert scheduler_module.schedule_active_show_catchup(
            show, datetime(2026, 9, 8, 0, 30)
        ) is True

    options = fake_scheduler.jobs[0][2]
    assert options["args"][1] == 30 * 60
    assert options["args"][-1] == date(2026, 9, 7)


def test_now_playing_metadata_job_runs_immediately(monkeypatch):
    fake_scheduler = FakeScheduler()
    monkeypatch.setattr(scheduler_module, "scheduler", fake_scheduler)
    scheduler_module.flask_app = Flask(__name__)
    scheduler_module.flask_app.config["SCHEDULE_TIMEZONE"] = "America/New_York"
    monkeypatch.setattr(scheduler_module, "logger", SimpleNamespace(info=lambda *args: None, error=lambda *args: None))

    with scheduler_module.flask_app.app_context():
        scheduler_module.schedule_radiodj_now_playing()

    _, trigger, options = fake_scheduler.jobs[0]
    assert trigger == "interval"
    assert options["seconds"] == 8
    assert options["next_run_time"].tzinfo is not None


def test_scheduler_uses_station_timezone_instead_of_host_timezone(monkeypatch):
    app = Flask(__name__)
    app.config["SCHEDULE_TIMEZONE"] = "America/New_York"
    monkeypatch.setattr(scheduler_module, "init_logger", lambda: SimpleNamespace(
        info=lambda *args: None,
        warning=lambda *args: None,
    ))
    for name in (
        "refresh_schedule", "schedule_stream_probe", "schedule_nas_watch",
        "schedule_news_rotation", "schedule_icecast_analytics", "schedule_settings_backup",
        "schedule_radiodj_now_playing", "schedule_library_index_job",
        "schedule_transcode_cache_cleanup", "schedule_schedule_refresh",
    ):
        monkeypatch.setattr(scheduler_module, name, lambda: None)
    scheduler_module.scheduler = scheduler_module.BackgroundScheduler()

    try:
        scheduler_module.init_scheduler(app)
        assert str(scheduler_module.scheduler.timezone) == "America/New_York"
    finally:
        if scheduler_module.scheduler.running:
            scheduler_module.scheduler.shutdown(wait=False)
