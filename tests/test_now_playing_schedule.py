from datetime import datetime
from zoneinfo import ZoneInfo

from flask import Flask

from app.routes import api as api_module


def test_now_playing_uses_station_wall_clock_for_schedule_lookups(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        SCHEDULE_TIMEZONE="Etc/GMT+4",
        DEFAULT_OFF_AIR_MESSAGE="Off air",
    )
    observed = {}

    def current_show(*, now):
        observed["current"] = now
        return None

    def current_absence(now):
        observed["absence"] = now
        return None, None

    def next_show(now):
        observed["next"] = now
        return None, None, None

    monkeypatch.setattr(api_module, "get_current_show", current_show)
    monkeypatch.setattr(api_module, "get_current_absent_show", current_absence)
    monkeypatch.setattr(api_module, "_find_next_show", next_show)
    monkeypatch.setattr(api_module, "_override_enabled", lambda: False)

    with app.test_request_context("/api/now"):
        response = api_module.now_playing()

    expected = datetime.now(ZoneInfo("Etc/GMT+4")).replace(tzinfo=None)
    assert response.get_json()["status"] == "off_air"
    assert observed["current"] == observed["absence"] == observed["next"]
    assert abs((observed["current"] - expected).total_seconds()) < 2
