from datetime import datetime, timedelta
import base64
import io
import os
import zipfile

import pytest

from app import create_app
from app.models import DJ, DJRecordingAccessCode, Show, db
from app.services.dj_recording_access import create_access_code
from app.services.legacy_recordings import write_legacy_sidecar
from app.services.library.media_library import encode_media_token


class TestConfig:
    TESTING = True
    SECRET_KEY = "portal-test-secret"
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


@pytest.fixture()
def portal_app(tmp_path, monkeypatch):
    app = create_app(TestConfig)
    recordings = tmp_path / "recordings"
    monkeypatch.setattr("app.main_routes._recordings_root", lambda: str(recordings))
    monkeypatch.setattr(
        "app.main_routes.load_recording_periods",
        lambda: {"periods": ["Fall 2026", "Spring 2026"], "current": "Fall 2026"},
    )
    monkeypatch.setattr("app.main_routes.current_recording_period", lambda: "Fall 2026")
    with app.app_context():
        db.create_all()
        yield app, recordings
        db.session.remove()
        db.drop_all()


def _show(name, first_name="Alex", last_name="Smith"):
    return Show(
        host_first_name=first_name, host_last_name=last_name, show_name=name,
        start_date=datetime(2026, 1, 1).date(), end_date=datetime(2026, 12, 31).date(),
        start_time=datetime.strptime("10:00", "%H:%M").time(),
        end_time=datetime.strptime("12:00", "%H:%M").time(), days_of_week="fri",
    )


def _seed(app, recordings):
    with app.app_context():
        own_show = _show("Friday Night Radio")
        other_show = _show("Someone Else", "Other", "DJ")
        dj = DJ(first_name="Alex", last_name="Smith", shows=[own_show])
        db.session.add_all([dj, other_show])
        db.session.commit()
        _, code = create_access_code(dj.id)
        dj_id = dj.id
    own = recordings / "Fall 2026" / "Alex_Smith" / "Radio Shows" / "Friday_Night_Radio"
    other = recordings / "Fall 2026" / "Other_DJ" / "Radio Shows" / "Someone_Else"
    own.mkdir(parents=True)
    other.mkdir(parents=True)
    own_file = own / "Friday_Night_Radio_09-11-26_RAWDATA.mp3"
    other_file = other / "Someone_Else_09-11-26_RAWDATA.mp3"
    own_file.write_bytes(b"own audio")
    other_file.write_bytes(b"other audio")
    return dj_id, code, own_file, other_file


def test_codes_default_to_30_days_and_can_be_indefinite(portal_app):
    app, _ = portal_app
    with app.app_context():
        dj = DJ(first_name="Alex", last_name="Smith")
        db.session.add(dj)
        db.session.commit()
        default_record, code = create_access_code(dj.id)
        indefinite_record, indefinite_code = create_access_code(dj.id, indefinite=True)
        assert len(code) == 4 and code.isdigit()
        assert len(indefinite_code) == 4
        assert timedelta(days=29) < default_record.expires_at - datetime.utcnow() <= timedelta(days=30)
        assert indefinite_record.expires_at is None
        assert default_record.code_digest != code


def test_portal_only_lists_and_serves_assigned_show(portal_app):
    app, recordings = portal_app
    _, code, own_file, other_file = _seed(app, recordings)
    client = app.test_client()
    response = client.post("/dj-recordings", data={"code": code}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Friday Night Radio" in response.data
    assert b"Someone Else" not in response.data
    assert response.headers["Cache-Control"] == "private, no-store"

    import base64
    own_token = base64.urlsafe_b64encode(str(own_file).encode()).decode()
    other_token = base64.urlsafe_b64encode(str(other_file).encode()).decode()
    assert client.get(f"/dj-recordings/audio/{own_token}").status_code == 200
    assert client.get(f"/dj-recordings/audio/{other_token}").status_code == 404


def test_download_requires_disclaimer_acknowledgement_and_revocation_ends_session(portal_app):
    app, recordings = portal_app
    dj_id, code, own_file, _ = _seed(app, recordings)
    import base64
    token = base64.urlsafe_b64encode(str(own_file).encode()).decode()
    client = app.test_client()
    client.post("/dj-recordings", data={"code": code})
    assert client.post("/dj-recordings/download", data={"tokens": token, "single": "1"}).status_code == 400
    response = client.post("/dj-recordings/download", data={"tokens": token, "single": "1", "copyright_acknowledged": "1"})
    assert response.status_code == 200
    assert response.headers["Content-Disposition"].startswith("attachment;")

    with app.app_context():
        record = DJRecordingAccessCode.query.filter_by(dj_id=dj_id).one()
        record.revoked_at = datetime.utcnow()
        db.session.commit()
    revoked_response = client.get("/dj-recordings/archive")
    assert revoked_response.status_code == 403, revoked_response.data[:500]


def test_public_detail_omits_compliance_and_listener_information(portal_app):
    app, recordings = portal_app
    _, code, own_file, _ = _seed(app, recordings)
    import base64
    token = base64.urlsafe_b64encode(str(own_file).encode()).decode()
    client = app.test_client()
    client.post("/dj-recordings", data={"code": code})
    response = client.get(f"/dj-recordings/view/{token}")
    assert response.status_code == 200
    assert b"Copyright Notice" in response.data
    assert b"compliance" not in response.data.lower()
    assert b"peak listeners" not in response.data.lower()


def test_manager_can_create_indefinite_code_and_revoke_it(portal_app):
    app, _ = portal_app
    with app.app_context():
        dj = DJ(first_name="Alex", last_name="Smith")
        db.session.add(dj)
        db.session.commit()
        dj_id = dj.id
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["authenticated"] = True
        browser_session["role"] = "manager"
    created = client.post(
        f"/djs/{dj_id}/recording-access", data={"expiry": "never"}, follow_redirects=True
    )
    assert created.status_code == 200
    assert b"This code will only be shown once" in created.data
    assert b"/dj-recordings" in created.data
    with app.app_context():
        record = DJRecordingAccessCode.query.filter_by(dj_id=dj_id).one()
        assert record.expires_at is None
        code_id = record.id
    revoked = client.post(
        f"/djs/{dj_id}/recording-access/{code_id}/revoke", follow_redirects=True
    )
    assert revoked.status_code == 200
    with app.app_context():
        assert db.session.get(DJRecordingAccessCode, code_id).revoked_at is not None


@pytest.mark.parametrize("has_schedule", [False, True])
def test_legacy_recordings_use_individual_host_metadata_for_all_portal_access(portal_app, has_schedule):
    app, recordings = portal_app
    with app.app_context():
        dj = DJ(first_name="Alex", last_name="Smith")
        if has_schedule:
            dj.shows = [_show("Current Show")]
        db.session.add(dj)
        db.session.commit()
        _, code = create_access_code(dj.id)

    folder = recordings / "Spring 2026" / "Old_Folder"
    folder.mkdir(parents=True)
    own = folder / "legacy.mp3"
    other = folder / "other.mp3"
    partial = folder / "partial.mp3"
    for path, hosts in ((own, ["Pat Jones", " alex smith "]),
                        (other, ["Other DJ"]), (partial, ["Alex Smithson"])):
        path.write_bytes(b"legacy audio")
        write_legacy_sidecar(str(path), period="Spring 2026", show_name="Current Show",
                             dj_names=hosts, recorded_date="2026-03-01")

    client = app.test_client()
    client.post("/dj-recordings", data={"code": code})
    for period in ("Spring 2026", "__all__"):
        response = client.get("/dj-recordings/archive", query_string={"period": period})
        assert response.status_code == 200
        assert b"legacy.mp3" in response.data
        assert b"other.mp3" not in response.data
        assert b"partial.mp3" not in response.data
    assert b"legacy.mp3" not in client.get("/dj-recordings/archive").data

    for path, status in ((own, 200), (other, 404), (partial, 404)):
        token = encode_media_token(str(path))
        assert client.get(f"/dj-recordings/audio/{token}").status_code == status
        assert client.get(f"/dj-recordings/view/{token}").status_code == status
        assert client.post("/dj-recordings/download", data={
            "tokens": token, "single": "1", "copyright_acknowledged": "1",
        }).status_code == status

    response = client.post("/dj-recordings/download", data={
        "period": "Spring 2026", "copyright_acknowledged": "1",
    })
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.namelist() == ["Current_Show/legacy.mp3"]


def test_all_periods_renders_surrogate_filename_and_preserves_path(portal_app, monkeypatch):
    from app import main_routes

    app, recordings = portal_app
    _, code, own, _ = _seed(app, recordings)
    filename = "old_\udcfe\udcff.mp3"
    full = os.path.join(str(own.parent), filename)
    original_stat = os.stat
    original_isfile = os.path.isfile
    monkeypatch.setattr(main_routes.os, "walk", lambda root: [(str(own.parent), [], [filename])])
    monkeypatch.setattr(main_routes.os, "stat", lambda path, *a, **kw:
                        original_stat(own) if path == full else original_stat(path, *a, **kw))
    monkeypatch.setattr(main_routes.os.path, "isfile", lambda path:
                        True if os.path.normcase(path) == os.path.normcase(full) else original_isfile(path))
    client = app.test_client()
    client.post("/dj-recordings", data={"code": code})
    response = client.get("/dj-recordings/archive?period=__all__")
    assert response.status_code == 200
    assert b"old_??.mp3" in response.data
    entry = main_routes._collect_recordings(period="__all__")[0]
    assert entry.full_path == full
    assert main_routes._resolve_recording_path(entry.token) == os.path.normcase(os.path.abspath(full))
    assert client.get(f"/dj-recordings/view/{entry.token}").status_code == 200


def test_recording_tokens_keep_old_links_and_root_validation(portal_app, tmp_path):
    from app.main_routes import _resolve_recording_path

    app, recordings = portal_app
    _, _, own, _ = _seed(app, recordings)
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"private")
    old_token = base64.urlsafe_b64encode(str(own).encode()).decode()
    assert _resolve_recording_path(old_token) == os.path.normcase(os.path.abspath(own))
    assert _resolve_recording_path(encode_media_token(str(outside))) is None
    assert _resolve_recording_path("invalid token") is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX permits non-UTF-8 filename bytes")
def test_non_utf8_recording_can_be_listed_streamed_and_downloaded(portal_app):
    from app.main_routes import _collect_recordings, _resolve_recording_path

    app, recordings = portal_app
    _, code, own, _ = _seed(app, recordings)
    raw_path = os.fsencode(own.parent) + b"/legacy_\xfe\xff.mp3"
    with open(raw_path, "wb") as audio:
        audio.write(b"old audio")
    client = app.test_client()
    client.post("/dj-recordings", data={"code": code})
    response = client.get("/dj-recordings/archive?period=__all__")
    assert response.status_code == 200
    assert b"legacy_??.mp3" in response.data
    entry = next(item for item in _collect_recordings(period="__all__")
                 if os.fsencode(item.full_path) == raw_path)
    assert os.fsencode(_resolve_recording_path(entry.token)) == raw_path
    streamed = client.get(f"/dj-recordings/audio/{entry.token}")
    assert streamed.status_code == 200
    assert streamed.data == b"old audio"
    ranged = client.get(f"/dj-recordings/audio/{entry.token}", headers={"Range": "bytes=0-2"})
    assert ranged.status_code == 206
    assert ranged.data == b"old"
    downloaded = client.post("/dj-recordings/download", data={
        "tokens": entry.token, "single": "1", "copyright_acknowledged": "1",
    })
    assert downloaded.status_code == 200
    assert downloaded.data == b"old audio"
    zipped = client.post("/dj-recordings/download", data={
        "tokens": entry.token, "copyright_acknowledged": "1",
    })
    assert zipped.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zipped.data)) as archive:
        assert archive.read("Friday_Night_Radio/legacy_??.mp3") == b"old audio"
