import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from flask import Flask
from mutagen.id3 import APIC, ID3
import pytest

from app import scheduler
from app.routes import api, dashboard
from app.services import studio_monitor
from app.services.listener_report import listener_report, report_window
from app.services.listener_store import samples, save_sample
from app.services.stream_monitor import parse_snapshot
from app.utils import recordings_paused


@pytest.fixture
def station(tmp_path):
    app = Flask("app", instance_path=str(tmp_path))
    app.secret_key = "test"
    app.config.update(DATA_ROOT=str(tmp_path), SCHEDULE_TIMEZONE="America/New_York",
                      ICECAST_ANALYTICS_FILE=str(tmp_path / "history.jsonl"))
    with app.app_context():
        yield app


def test_mount_clients_and_ignored_ips():
    xml = """<icestats><source mount="/other"><Listeners>0</Listeners></source><source mount="/stream">
    <Listeners>5</Listeners>
    <listener><IP>172.16.1.5</IP><UserAgent>RadioGarden/4</UserAgent><Connected>342</Connected><ID>10</ID></listener>
    <listener><IP>192.168.1.2</IP><UserAgent>TuneIn Radio/42 Android</UserAgent></listener>
    <listener><IP>203.0.113.8</IP><UserAgent>Mozilla/5.0 ClaudeBot/1.0</UserAgent></listener>
    <listener><IP>172.16.0.99</IP><UserAgent>Lavf/60</UserAgent></listener>
    <listener><IP>2001:db8::1</IP><UserAgent>VLC/3</UserAgent></listener></source></icestats>"""
    count, clients = parse_snapshot(xml, "/stream", {"172.16.0.99"}, listclients=True)
    assert count == 4
    assert [row["client"] for row in clients] == ["Radio Garden", "TuneIn", "ClaudeBot (crawler)", "VLC"]
    assert [row["network"] for row in clients] == ["Internal", "Internal", "External", "External"]
    assert clients[0]["seconds"] == 342
    assert parse_snapshot(xml, "/other", set(), listclients=True) == (0, [])
    assert parse_snapshot(xml, "/missing", set()) is None
    assert parse_snapshot('<icestats><source mount="/stream"><listeners>4</listeners></source></icestats>',
                          "/stream", set()) == (4, None)


def test_sampled_sessions_and_csv(station):
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 5, 50, tzinfo=timezone.utc)
    ctx = dict(state="automation", show_name=None, classification="automation")
    client = dict(id="/stream/1", client="Firefox", network="Internal", seconds=180)
    save_sample(1, ctx, [client], start)
    save_sample(1, ctx, [client], start)
    save_sample(1, ctx, [dict(client, seconds=1080)], start + timedelta(minutes=15))
    save_sample(0, ctx, [], start + timedelta(minutes=30))
    save_sample(3, ctx, None, start + timedelta(minutes=45))
    show = dict(state="live_show", show_name="Breakfast", classification="live_show")
    save_sample(1, show, [client], start + timedelta(hours=1))
    anchor = date(2026, 11, 1)
    report = listener_report("day", anchor, zone)
    assert report["samples"] == 5 and report["detailed"] == 4
    assert report["average"] == 1.2 and report["peak"] == 3 and report["first_seen"] == 2
    assert report["groups"]["Client"]["Firefox"]["average"] == 0.75
    assert set(report["groups"]["Trend"]) == {"1 AM EDT", "1 AM EST"}
    assert listener_report("month", anchor, zone, "Breakfast")["samples"] == 1
    for period in ("year", "ytd"):
        assert listener_report(period, anchor, zone)["samples"] == 5
    assert report_window("day", anchor, zone)[1] - report_window("day", anchor, zone)[0] == 25*3600
    assert len(list(samples())) == 5
    station.add_url_rule("/report", view_func=dashboard.listeners_report)
    client = station.test_client()
    assert client.get("/report?format=csv").status_code == 403
    with client.session_transaction() as session:
        session.update(authenticated=True, role="admin")
    response = client.get("/report?format=csv&period=ytd&date=2026-11-01&program=Breakfast")
    rows = list(csv.reader(io.StringIO(response.get_data(as_text=True))))
    assert response.mimetype == "text/csv" and "attachment" in response.headers["Content-Disposition"]
    assert rows[1][4:9] == ["Summary", "All selected samples", "1", "1.0", "1"]
    assert client.get("/report?period=invalid").status_code == 400
    from jinja2 import ChoiceLoader, DictLoader
    station.jinja_loader = ChoiceLoader([DictLoader({"_nav.html": ""}), station.jinja_loader])
    page = client.get("/report?date=2026-11-01")
    assert page.status_code == 200 and b"1 AM EDT" in page.data and b"1 AM EST" in page.data


def test_pause_from_other_worker_stops_active_capture_and_blocks_metadata(station, monkeypatch):
    settings = Path(station.instance_path) / "user_config.json"
    def pause(_):
        settings.write_text(json.dumps({"PAUSE_SHOWS_RECORDING": True}), encoding="utf-8")
    proc = Mock()
    proc.poll.return_value = None
    with pytest.raises(scheduler.RecordingPaused):
        scheduler._wait_for_recorder(proc, str(settings), 45, paused=recordings_paused, sleep=pause)
    proc.terminate.assert_called_once()
    monkeypatch.setattr(scheduler, "flask_app", station)
    monkeypatch.setattr(scheduler, "logger", Mock())
    monkeypatch.setattr(scheduler.subprocess, "Popen", lambda *a, **kw: pytest.fail("Started while paused"))
    scheduler.record_stream("stream", 60, "file", str(settings))
    monkeypatch.setattr(api, "_override_enabled", lambda: False)
    monkeypatch.setattr(api, "_find_next_show", lambda now: (None, None, None))
    monkeypatch.setattr(api, "_get_cached_radiodj_nowplaying", lambda **kw: {"title": "RadioDJ song"})
    with station.test_request_context():
        now = api.now_playing().get_json()
    assert now["status"] == "automation" and now["track"]["title"] == "RadioDJ song"
    push = Mock()
    monkeypatch.setattr(api, "_push_icecast_metadata", push)
    scheduler.run_radiodj_now_playing_job()
    push.assert_not_called()


def test_monitor_retains_failed_stage_and_confirms_second_failure(station):
    failed = SimpleNamespace(classification="unknown", reason="FFmpeg capture timed out after 25 seconds")
    restart = Mock()
    studio_monitor.record_stream_check(failed, restart=restart)
    snap = studio_monitor.monitor_snapshot()
    assert snap["last_failure_reason"] == failed.reason
    assert all(a["code"] != "08" for a in snap["alerts"])
    studio_monitor.record_stream_check(failed, restart=restart)
    alert = next(a for a in studio_monitor.monitor_snapshot()["alerts"] if a["code"] == "08")
    assert alert["details"]["reason"] == failed.reason
    assert alert["details"]["consecutive_failures"] == 2
    studio_monitor.record_stream_check(SimpleNamespace(classification="live_show"), restart=restart)
    assert all(a["code"] != "08" for a in studio_monitor.monitor_snapshot()["alerts"])


def test_studio_cover_tries_duplicate_copies_and_returns_logo_on_miss(station, monkeypatch, tmp_path):
    station.config["NAS_MUSIC_ROOT"] = str(tmp_path)
    missing = tmp_path / "missing.mp3"
    track = tmp_path / "copy.mp3"
    track.write_bytes(b"audio")
    track.with_suffix(".png").write_bytes(b"cover")
    entries = [{"title": "Song", "artist": "Band", "path": str(path)} for path in (missing, track)]
    monkeypatch.setattr(api, "get_music_index", lambda: {"files": dict(enumerate(entries))})
    station.add_url_rule("/api/studio/cover", view_func=api.studio_cover)
    client = station.test_client()
    url = "/api/studio/cover?title=Song&artist=Band"
    with client.get(url) as response:
        assert response.status_code == 200 and response.data == b"cover"
    track.with_suffix(".png").unlink()
    tags = ID3()
    tags.add(APIC(mime="image/png", type=3, desc="Front cover", data=b"embedded"))
    tags.save(track)
    from app.services import cover_image
    # The fixture has a real ID3 tag but no MPEG frames.
    monkeypatch.setattr(cover_image, "File", lambda path, easy: SimpleNamespace(tags=ID3(path)))
    with client.get(url) as response:
        assert response.status_code == 200 and response.data == b"embedded"
    with client.get("/api/studio/cover?title=Absent&artist=Band") as response:
        assert response.status_code == 200 and response.data.startswith(b"\x89PNG")


def test_schedule_refresh_keeps_next_probe_deadline(station, monkeypatch):
    monkeypatch.setattr(scheduler, "flask_app", station)
    station.config["STREAM_PROBE_INTERVAL_MINUTES"] = 1
    jobs = Mock()
    jobs.get_job.return_value = SimpleNamespace(trigger=SimpleNamespace(interval=timedelta(minutes=1)))
    monkeypatch.setattr(scheduler, "scheduler", jobs)
    scheduler.schedule_stream_probe()
    jobs.add_job.assert_not_called()
