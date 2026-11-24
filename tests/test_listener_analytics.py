import json
from datetime import datetime

from flask import Flask

from app.services.listener_analytics import load_listener_history, peak_listeners_for_show


def test_legacy_counts_import_once(tmp_path):
    history = tmp_path / "listeners.jsonl"
    history.write_text('{"listeners":3,"ts":"2026-01-01T00:00:00"}\nbad line\n', encoding="utf-8")
    app = Flask(__name__)
    app.config["ICECAST_ANALYTICS_FILE"] = str(history)
    with app.app_context():
        assert [row["listeners"] for row in load_listener_history()] == [3]
        assert len(load_listener_history()) == 1


def test_peak_listeners_for_show_filters_window_and_show(tmp_path):
    history = tmp_path / "listeners.jsonl"
    samples = [
        {"ts": "2026-07-27T10:00:00", "listeners": 4, "show": "Morning"},
        {"ts": "2026-07-27T10:15:00", "listeners": 12, "show": "Morning"},
        {"ts": "2026-07-27T10:30:00", "listeners": 30, "show": "Other"},
        {"ts": "2026-07-27T13:00:00", "listeners": 50, "show": "Morning"},
    ]
    history.write_text("\n".join(json.dumps(sample) for sample in samples), encoding="utf-8")
    app = Flask(__name__)
    app.config["ICECAST_ANALYTICS_FILE"] = str(history)

    with app.app_context():
        peak = peak_listeners_for_show(
            "Morning", datetime(2026, 7, 27, 10), datetime(2026, 7, 27, 12)
        )

    assert peak == 12
