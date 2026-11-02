from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from flask import Flask
import pytest

from app import scheduler as scheduler_module
from app.utils import (
    normalize_timezone_name, localize_datetime, format_datetime_zone,
    get_config_timezone_name, timezone_label,
)
from test_dj_recording_portal import portal_app
from test_airing_audit import login


@pytest.mark.parametrize('code,zone', [
    ('ET', 'America/New_York'), ('EST', 'America/New_York'),
    ('edt', 'America/New_York'), (' PT ', 'America/Los_Angeles'),
    ('PST', 'America/Los_Angeles'), ('PDT', 'America/Los_Angeles'),
    ('CT', 'America/Chicago'), ('MT', 'America/Denver'),
    ('America/Phoenix', 'America/Phoenix'), ('Europe/Paris', 'Europe/Paris'),
    ('UTC-5', 'Etc/GMT+5'), ('Etc/GMT+4', 'Etc/GMT+4'),
    ('UTC', 'Etc/GMT'), ('', 'America/New_York'), (None, 'America/New_York'),
])
def test_timezone_inputs(code, zone):
    assert normalize_timezone_name(code) == zone


def test_regional_labels_and_machine_context(portal_app):
    app, _ = portal_app
    app.config['SCHEDULE_TIMEZONE'] = 'ET'
    with app.test_request_context('/'):
        context = {}
        app.update_template_context(context)
        assert context['app_timezone'] == 'America/New_York'
        assert 'EST/EDT' in context['app_timezone_label']
        assert 'PST/PDT' in timezone_label('PT')
        rendered = app.jinja_env.from_string('{{ value|datetime_zone }}').render(
            value=datetime(2026, 1, 15, 18))
        assert rendered == '2026-01-15 1:00 PM EST'
        # Compile every template changed by the display-label update.
        for name in ('absence_manage.html', 'dj_discipline.html', 'dj_handoff.html',
                     'dj_profile.html', 'dj_tools.html', 'legacy_recordings_import.html',
                     'recordings_manage.html', 'settings.html', 'station_log.html'):
            app.jinja_env.get_template(name)


@pytest.mark.parametrize('utc,clock,code', [
    (datetime(2026, 3, 8, 6, 59), '01:59', 'EST'),
    (datetime(2026, 3, 8, 7), '03:00', 'EDT'),
    (datetime(2026, 11, 1, 5, 59), '01:59', 'EDT'),
    (datetime(2026, 11, 1, 6), '01:00', 'EST'),
])
def test_eastern_switches_at_transition_and_labels_record_date(utc, clock, code):
    app = Flask(__name__)
    app.config['SCHEDULE_TIMEZONE'] = 'ET'
    with app.app_context():
        local = localize_datetime(utc)
        assert local.strftime('%H:%M') == clock
        assert local.tzname() == code
        assert format_datetime_zone(utc).endswith(' ' + code)
        assert get_config_timezone_name() == 'America/New_York'


@pytest.mark.parametrize('zone,winter,summer', [
    ('PT', 'PST', 'PDT'), ('America/Phoenix', 'MST', 'MST'),
    ('Pacific/Honolulu', 'HST', 'HST'), ('UTC-5', '-05', '-05'),
])
def test_regional_and_fixed_zone_seasons(zone, winter, summer):
    app = Flask(__name__)
    app.config['SCHEDULE_TIMEZONE'] = zone
    with app.app_context():
        assert localize_datetime(datetime(2026, 1, 15, 18)).tzname() == winter
        assert localize_datetime(datetime(2026, 7, 15, 18)).tzname() == summer


@pytest.mark.parametrize('zone', ['America/New_York', 'America/Los_Angeles'])
@pytest.mark.parametrize('month,day', [(3, 7), (10, 31)])
def test_recurring_job_keeps_local_hour_across_dst(zone, month, day):
    tz = ZoneInfo(zone)
    trigger = CronTrigger(hour=10, minute=0, timezone=tz)
    first = trigger.get_next_fire_time(None, datetime(2026, month, day, 9, tzinfo=tz))
    second = trigger.get_next_fire_time(first, first + timedelta(seconds=1))
    assert first.hour == second.hour == 10
    assert first.utcoffset() != second.utcoffset()


def test_settings_preserve_custom_zone_and_save_regional_choice(portal_app, monkeypatch):
    app, _ = portal_app
    from config import Config
    for key in ('ADMIN_USERNAME', 'ADMIN_PASSWORD', 'STREAM_URL', 'OUTPUT_FOLDER',
                'DEFAULT_START_DATE', 'DEFAULT_END_DATE', 'AUTO_CREATE_SHOW_FOLDERS'):
        app.config[key] = getattr(Config, key)
    app.config['SCHEDULE_TIMEZONE'] = 'Europe/Paris'
    client = app.test_client()
    login(client)
    html = client.get('/settings').get_data(as_text=True)
    assert 'value="Europe/Paris" selected' in html
    assert 'Eastern (EST/EDT)' in html and 'Pacific (PST/PDT)' in html
    observed = []
    def update(settings):
        app.config.update(settings)
        observed.append(settings['SCHEDULE_TIMEZONE'])
    monkeypatch.setattr('app.main_routes.update_user_config', update)
    monkeypatch.setattr('app.main_routes.refresh_schedule', lambda: observed.append('refresh'))
    monkeypatch.setattr('app.main_routes.save_recording_periods', lambda **kw: None)
    monkeypatch.setattr('app.main_routes.init_oauth', lambda app: None)
    data = {key.lower(): app.config[key] for key in (
        'ADMIN_USERNAME', 'ADMIN_PASSWORD', 'STREAM_URL', 'OUTPUT_FOLDER',
        'DEFAULT_START_DATE', 'DEFAULT_END_DATE')}
    data.update(station_name='Test', station_slogan='', schedule_timezone='PT')
    assert client.post('/settings', data=data).status_code == 302
    assert observed == ['America/Los_Angeles', 'refresh']
    assert get_config_timezone_name() == 'America/Los_Angeles'


def test_refresh_applies_new_zone_without_dropping_maintenance(monkeypatch):
    scheduler = scheduler_module._new_scheduler(timezone=ZoneInfo('Etc/GMT+5'))
    scheduler.start(paused=True)
    scheduler.add_job(lambda: None, 'interval', hours=1, id='maintenance')
    monkeypatch.setattr(scheduler_module, 'scheduler', scheduler)
    monkeypatch.setattr(scheduler_module, 'table_exists', lambda name: False)
    app = Flask(__name__)
    app.config['SCHEDULE_TIMEZONE'] = 'PT'
    try:
        with app.app_context():
            scheduler_module.refresh_schedule()
        assert str(scheduler.timezone) == 'America/Los_Angeles'
        assert scheduler.get_job('maintenance') is not None
    finally:
        scheduler.shutdown(wait=False)
