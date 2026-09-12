"""Phase 1 characterization tests for RAMS safety boundaries.

Passing tests preserve intentional public access and existing authorization guards.
Strict xfails describe security or data-safety contracts that Phase 2 should satisfy;
they must become ordinary passing tests when the corresponding guard is added.
"""

from datetime import date, time, timedelta
import json
from pathlib import Path

import pytest

from app import create_app
from app.models import Show, db
from app.services.settings_backup import backup_data_snapshot
from config import Config as DefaultConfig


class SafetyTestConfig:
    TESTING = True
    SECRET_KEY = "phase-one-test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RUN_SCHEMA_SETUP_ON_STARTUP = False
    RUN_MIGRATIONS_ON_STARTUP = False
    RUN_CLEANUP_ON_STARTUP = False
    RUN_SCHEDULER_ON_STARTUP = False
    RUN_UTILS_ON_STARTUP = False
    RUN_OAUTH_INIT_ON_STARTUP = False
    RUN_PLUGIN_LOAD_ON_STARTUP = False
    RATE_LIMIT_ENABLED = False
    ADMIN_URL_PREFIX = ""
    RADIODJ_API_BASE_URL = None
    RADIODJ_API_PASSWORD = None


@pytest.fixture()
def safety_app(tmp_path):
    class Config(SafetyTestConfig):
        DATA_ROOT = str(tmp_path / "data")
        LOGS_DIR = str(tmp_path / "logs")
        NEWS_TYPES_CONFIG = str(tmp_path / "nas" / "news_types.json")
        DATA_BACKUP_DIRNAME = "data_backups"

    app = create_app(Config)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/settings/import"),
        ("post", "/settings/backup-now"),
        ("post", "/settings/backup-data"),
        ("post", "/show/delete/1"),
        ("post", "/clear_all"),
        ("post", "/pause"),
        ("post", "/resume"),
    ],
)
def test_high_impact_admin_routes_reject_anonymous_requests(safety_app, method, path):
    response = getattr(safety_app.test_client(), method)(path)
    assert response.status_code == 403


def test_intentionally_public_routes_remain_available(safety_app):
    client = safety_app.test_client()

    assert client.get("/logs/submit").status_code == 200
    assert client.get("/api/schedule").status_code == 200


PHASE_2_AUTH_REASON = "Phase 2 must restrict this operation to master control"


@pytest.mark.xfail(strict=True, reason=PHASE_2_AUTH_REASON)
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/radiodj/queue", {"items": [{"token": "invalid"}]}),
        ("post", "/api/radiodj/import/news", {}),
        ("patch", "/api/radiodj/psas/1/metadata", {"title": "Changed"}),
        ("post", "/api/radiodj/psas/1/enable", {}),
        ("post", "/api/radiodj/psas/1/disable", {}),
        ("delete", "/api/radiodj/psas/1", None),
        ("post", "/api/radiodj/autodj", {"enabled": True}),
    ],
)
def test_radiodj_mutations_require_master_control(
    safety_app, monkeypatch, method, path, payload
):
    monkeypatch.setattr(
        "app.routes.api.import_news_or_calendar", lambda _kind: Path("imported.mp3")
    )
    response = safety_app.test_client().open(path, method=method.upper(), json=payload)
    assert response.status_code == 403


@pytest.mark.xfail(
    strict=True,
    reason="Phase 2 must add the appropriate operational or music permission guard",
)
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/api/now/override", {"enabled": True}),
        (
            "post",
            "/api/music/bulk-update",
            {"paths": ["/not/a/library/file.mp3"], "updates": {}},
        ),
        ("post", "/api/music/cover-art", {"path": "/not/a/library/file.mp3"}),
        ("post", "/api/library/index/refresh", {}),
    ],
)
def test_operational_and_music_mutations_require_permission(
    safety_app, monkeypatch, method, path, payload
):
    monkeypatch.setattr("app.routes.api.start_library_index_job", lambda: False)
    response = safety_app.test_client().open(path, method=method.upper(), json=payload)
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("post", "/dj/library/import", {"name": "Test", "text": "Artist - Title"}),
        ("post", "/dj/library/youtube", {"playlist_url": "https://example.invalid/list"}),
        (
            "post",
            "/dj/library/playlist/save",
            {"name": "Test", "content": "Artist - Title"},
        ),
    ],
)
def test_dj_library_server_work_remains_available_without_login(
    safety_app, monkeypatch, method, path, payload
):
    monkeypatch.setattr("app.main_routes.match_youtube_playlist", lambda _url: {"items": []})
    response = safety_app.test_client().open(path, method=method.upper(), json=payload)
    assert response.status_code != 403


@pytest.mark.xfail(
    strict=True,
    reason="Phase 2 should require CSRF tokens on authenticated browser mutations",
)
def test_authenticated_admin_post_without_csrf_token_is_rejected(safety_app):
    client = safety_app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["authenticated"] = True
        browser_session["role"] = "admin"

    response = client.post("/settings/backup-data")
    assert response.status_code in {400, 403}


@pytest.mark.xfail(
    strict=True,
    reason="Phase 2 should remove usable fallback credentials and secrets",
)
def test_default_configuration_has_no_usable_fallback_credentials():
    assert DefaultConfig.ADMIN_PASSWORD in {None, ""}
    assert DefaultConfig.SECRET_KEY in {None, ""}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Phase 2 should archive expired shows immediately and retain them for one year"
    ),
)
def test_startup_cleanup_retains_recently_expired_show_definitions(tmp_path):
    database_path = tmp_path / "cleanup.db"

    class SeedConfig(SafetyTestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path}"
        DATA_ROOT = str(tmp_path / "data")
        LOGS_DIR = str(tmp_path / "logs")
        NEWS_TYPES_CONFIG = str(tmp_path / "nas" / "news_types.json")

    seed_app = create_app(SeedConfig)
    with seed_app.app_context():
        db.create_all()
        db.session.add(
            Show(
                host_first_name="Archive",
                host_last_name="Candidate",
                show_name="Past Show",
                start_date=date.today() - timedelta(days=180),
                end_date=date.today() - timedelta(days=1),
                start_time=time(10),
                end_time=time(11),
                days_of_week="mon",
            )
        )
        db.session.commit()

    class CleanupConfig(SeedConfig):
        RUN_CLEANUP_ON_STARTUP = True

    cleanup_app = create_app(CleanupConfig)
    with cleanup_app.app_context():
        assert Show.query.filter_by(show_name="Past Show").one_or_none() is not None


@pytest.mark.xfail(
    strict=True,
    reason="Phase 2 should provide a complete, versioned operational backup",
)
def test_data_backup_covers_all_operational_domains(safety_app):
    expected_domains = {
        "users",
        "djs",
        "shows",
        "show_runs",
        "absences",
        "disciplinary",
        "plugins",
        "automation_rules",
        "radio_dj_config",
    }

    with safety_app.app_context():
        backup_path = Path(backup_data_snapshot())
        payload = json.loads(backup_path.read_text(encoding="utf-8"))

    assert payload.get("schema_version") is not None
    assert expected_domains <= payload.keys()


def test_current_data_backup_is_valid_json_with_core_domains(safety_app):
    with safety_app.app_context():
        backup_path = Path(backup_data_snapshot())
        payload = json.loads(backup_path.read_text(encoding="utf-8"))

    assert backup_path.name.startswith("rams_data_")
    assert {"generated_at", "djs", "shows", "disciplinary"} <= payload.keys()
    assert all(isinstance(payload[key], list) for key in ("djs", "shows", "disciplinary"))
