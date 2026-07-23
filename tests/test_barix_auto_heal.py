from flask import Flask

from app.models import JobHealth, db
from app.services.barix import BarixRestartResult
from app.services import detection


class _TestConfig:
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    STREAM_URL = "https://example.test/stream"
    SELF_HEAL_ENABLED = False
    BARIX_AUTO_RESTART_ENABLED = True
    STREAM_DOWN_RESTART_THRESHOLD = 3
    TESTING = True


def _app():
    app = Flask(__name__)
    app.config.from_object(_TestConfig)
    db.init_app(app)
    with app.app_context():
        db.create_all()
    return app


def _health_reason(name="barix_auto_heal"):
    return JobHealth.query.filter_by(name=name).first().last_failure_reason


def test_probe_below_restart_threshold_reports_waiting_not_lockout(monkeypatch):
    app = _app()
    with app.app_context():
        monkeypatch.setattr(detection, "probe_stream", lambda _url: None)
        monkeypatch.setattr(detection, "process_probe_alerts", lambda *_args, **_kwargs: None)
        called = False

        def fake_restart(*_args, **_kwargs):
            nonlocal called
            called = True
            return BarixRestartResult(True, True, "accepted", "should not happen")

        monkeypatch.setattr(detection, "restart_instreamer", fake_restart)

        detection.probe_and_record()

        assert called is False
        assert _health_reason() == "Stream probe failed; waiting for restart threshold (0/3)"


def test_probe_at_restart_threshold_records_restart_result(monkeypatch):
    app = _app()
    with app.app_context():
        db.session.add(JobHealth(name="stream_probe", failure_count=3, restart_count=0))
        db.session.commit()
        monkeypatch.setattr(detection, "probe_stream", lambda _url: None)

        monkeypatch.setattr(
            detection,
            "restart_instreamer",
            lambda **_kwargs: BarixRestartResult(
                attempted=True,
                accepted=False,
                status="request_failed",
                message="Barix restart request failed: timed out",
            ),
        )
        monkeypatch.setattr(detection, "process_probe_alerts", lambda *_args, **_kwargs: None)

        detection.probe_and_record()

        assert _health_reason() == (
            "Stream probe restart: request_failed: Barix restart request failed: timed out"
        )
        assert JobHealth.query.filter_by(name="barix_auto_heal").first().restart_count == 0
