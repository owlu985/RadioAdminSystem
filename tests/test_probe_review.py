from datetime import date, datetime, time

from app.models import DJ, ShowRun, db
from app.services.missed_airings import read_missed_status, load_probe_clearances, write_missed_status
from app.services.library.media_library import encode_media_token
from test_dj_recording_portal import portal_app, _show
from test_airing_audit import recording, login


def seed(app, root, *, audio=True):
    with app.app_context():
        app.config['SCHEDULE_TIMEZONE'] = 'America/New_York'
        app.config['OUTPUT_FOLDER'] = str(root)
        show = _show('Friday Night Radio')
        run = ShowRun(show_name=show.show_name, dj_first_name='Alex', dj_last_name='Smith',
                      start_time=datetime(2026, 9, 11, 14), flagged_missed=True,
                      classification='automation')
        db.session.add_all([show, run])
        db.session.commit()
        run_id = run.id
    path = recording(root / 'Fall 2026', date(2026, 9, 11)) if audio else None
    return run_id, path


def test_probe_clear_available_in_audit_and_detail_and_requires_edit_permission(portal_app):
    app, root = portal_app
    run_id, path = seed(app, root)
    client = app.test_client()
    login(client)
    audit = client.get('/shows/audit?period=Fall+2026&week=2026-09-07')
    assert b'Clear automated Missed flag' in audit.data
    assert b'Missed' in audit.data
    detail = client.get(f'/recordings/view/{encode_media_token(str(path))}')
    assert b'Clear automated Missed flag' in detail.data
    login(client, 'viewer')
    assert b'Clear automated Missed flag' not in client.get('/shows/audit?week=2026-09-07').data
    assert client.post(f'/shows/audit/clear-probe-missed/{run_id}').status_code == 403
    with app.app_context():
        assert db.session.get(ShowRun, run_id).flagged_missed is True


def test_clear_persists_across_later_probe_flags_and_is_scoped_to_one_airing(portal_app, monkeypatch):
    app, root = portal_app
    run_id, path = seed(app, root)
    with app.app_context():
        other = ShowRun(show_name='Friday Night Radio', dj_first_name='Alex', dj_last_name='Smith',
                        start_time=datetime(2026, 9, 18, 14), flagged_missed=True)
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    client = app.test_client()
    login(client)
    def forbidden(*args, **kwargs):
        raise AssertionError('Clear must not walk recordings or parse CSV logs')
    with monkeypatch.context() as patch:
        patch.setattr('app.main_routes._collect_recordings', forbidden)
        patch.setattr('app.main_routes.os.walk', forbidden)
        patch.setattr('app.main_routes.read_log_csv', forbidden)
        response = client.post(f'/shows/audit/clear-probe-missed/{run_id}', headers={'Accept': 'application/json'})
    assert response.status_code == 200
    assert response.get_json() == {'cleared': True}
    with app.app_context():
        assert db.session.get(ShowRun, run_id).flagged_missed is False
        assert db.session.get(ShowRun, other_id).flagged_missed is True
        # Simulate the unchanged probe flagging this airing again.
        db.session.get(ShowRun, run_id).flagged_missed = True
        db.session.commit()
        from app.routes.api import _serialize_show_run
        serialized = _serialize_show_run(db.session.get(ShowRun, run_id))
        assert serialized['flagged_missed'] is False
        assert serialized['classification'] == 'automation'
    assert ('friday night radio', date(2026, 9, 11)) in load_probe_clearances(str(root))
    assert read_missed_status(str(root), ('friday night radio', date(2026, 9, 11))) is None
    response = client.get('/shows/audit?period=Fall+2026&week=2026-09-07')
    assert b'<strong>1</strong> airings in' in response.data
    assert f'clear-probe-missed/{run_id}'.encode() not in response.data
    assert b'Automated Missed flag was manually cleared' in response.data
    assert b'Clear automated Missed flag' not in client.get(f'/recordings/view/{encode_media_token(str(path))}').data


def test_clear_without_recording_does_not_invent_airing_evidence(portal_app):
    app, root = portal_app
    run_id, _ = seed(app, root, audio=False)
    client = app.test_client()
    login(client)
    assert b'Clear automated Missed flag' in client.get('/shows/audit?week=2026-09-07').data
    response = client.post(f'/shows/audit/clear-probe-missed/{run_id}', data={'period': 'Fall 2026'}, follow_redirects=True)
    assert response.status_code == 200
    assert b'<strong>0</strong> airings in' in response.data
    assert b'No evidence' in response.data
    assert b'Clear automated Missed flag' not in response.data
    assert client.post('/shows/audit/clear-probe-missed/999999').status_code == 404


def test_clear_leaves_manual_missed_decision_unchanged(portal_app):
    app, root = portal_app
    run_id, _ = seed(app, root)
    identity = 'friday night radio', date(2026, 9, 11)
    write_missed_status(str(root), identity, True)
    client = app.test_client()
    login(client)
    assert client.post(f'/shows/audit/clear-probe-missed/{run_id}', headers={'Accept': 'application/json'}).status_code == 200
    assert read_missed_status(str(root), identity) is True
    with app.app_context():
        from app.services.probe_review import effective_run_missed
        assert effective_run_missed(db.session.get(ShowRun, run_id)) is True
    response = client.get('/shows/audit?week=2026-09-07')
    assert b'<strong>0</strong> airings in' in response.data
    assert b'Missed' in response.data


def test_overnight_probe_flags_clear_together_but_next_airing_is_untouched(portal_app):
    app, root = portal_app
    with app.app_context():
        app.config['SCHEDULE_TIMEZONE'] = 'America/New_York'
        show = _show('Friday Night Radio')
        show.days_of_week, show.start_time, show.end_time = 'fri,sat', time(22), time(1)
        runs = [ShowRun(show_name=show.show_name, dj_first_name='Alex', dj_last_name='Smith',
                        start_time=stamp, flagged_missed=True) for stamp in
                (datetime(2026, 9, 12, 2), datetime(2026, 9, 12, 4, 30), datetime(2026, 9, 13, 2))]
        db.session.add_all([show, *runs])
        db.session.commit()
        ids = [run.id for run in runs]
    client = app.test_client()
    login(client)
    assert client.post(f'/shows/audit/clear-probe-missed/{ids[1]}', headers={'Accept': 'application/json'}).status_code == 200
    with app.app_context():
        assert [db.session.get(ShowRun, run_id).flagged_missed for run_id in ids] == [False, False, True]
