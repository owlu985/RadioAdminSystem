from datetime import datetime, time
from io import BytesIO
from zipfile import ZipFile

from flask import Flask
import pytest

from app.models import Show, db
from app.services.log_export import LogCsvEntry, build_docx, read_log_csv, write_log_csv
from app.utils import format_datetime_local, format_log_line_time12, format_time12, parse_clock_time
from test_dj_recording_portal import portal_app
from test_airing_audit import login


@pytest.mark.parametrize(('value', 'expected'), [
    (None, ''), ('', ''), ('00:00', '12:00 AM'), ('12:00', '12:00 PM'),
    (time(13, 5), '1:05 PM'), ('23:59:59', '11:59 PM'),
    ('1:05 PM', '1:05 PM'), ('unavailable', 'unavailable'),
])
def test_wall_clock_display(value, expected):
    assert format_time12(value) == expected


def test_timestamps_keep_station_timezone_and_wall_clocks_do_not_shift():
    app = Flask(__name__)
    app.config['SCHEDULE_TIMEZONE'] = 'America/New_York'
    with app.app_context():
        assert format_datetime_local(datetime(2026, 10, 24, 3, 5)) == '2026-10-23 11:05 PM'
        assert format_datetime_local(datetime(2026, 10, 24, 16)) == '2026-10-24 12:00 PM'
        assert format_datetime_local(datetime(2026, 10, 24, 4)) == '2026-10-24 12:00 AM'
        assert format_datetime_local(datetime(2026, 10, 24, 4), '%H:%M') == '00:00'
        assert format_time12(datetime(2026, 10, 24, 13, 5)) == '1:05 PM'
        assert format_time12(time(0, 0, 1), seconds=True) == '12:00:01 AM'


def test_system_log_display_changes_only_clock_prefix():
    line = '2026-10-23 13:05:01,123 INFO scheduled input 18:00'
    assert format_log_line_time12(line) == '2026-10-23 1:05:01,123 PM INFO scheduled input 18:00'
    assert format_log_line_time12('Traceback: input 18:00') == 'Traceback: input 18:00'


def test_docx_is_readable_but_csv_remains_machine_readable(tmp_path):
    entry = LogCsvEntry('2026-10-23', 'Night Show', 'DJ', '18:00', 'music', 'Song', 'Artist', '')
    path = tmp_path / 'log.csv'
    write_log_csv(csv_path=str(path), entries=[entry])
    assert read_log_csv(str(path))[0].time == '18:00'
    with ZipFile(BytesIO(build_docx([entry]))) as document:
        assert '6:00 PM' in document.read('word/document.xml').decode()
    assert entry.time == '18:00'


@pytest.mark.parametrize(('value', 'expected'), [('12:00 AM', time(0)), ('12:00 PM', time(12)), ('6:00pm', time(18)), ('18:00', time(18))])
def test_bulk_import_supports_ampm_and_existing_files(value, expected):
    assert parse_clock_time(value) == expected


def test_bulk_import_rejects_invalid_ampm():
    with pytest.raises(ValueError):
        parse_clock_time('13:00 PM')


def test_bulk_import_route_and_schedule_display(portal_app, monkeypatch):
    app, _ = portal_app
    app.config.update(DEFAULT_START_DATE='2026-01-01', DEFAULT_END_DATE='2026-12-31')
    monkeypatch.setattr('app.main_routes.refresh_schedule', lambda: None)
    client = app.test_client()
    login(client)
    response = client.post('/shows/bulk-add', data={
        'bulk_shows': 'Night Show / 6:00 PM - 12:00 AM / TH\nOld Import / 18:00 - 20:00 / F',
        'bulk_shows_csv': (BytesIO(b'ShowName,StartTime,EndTime,DayOfWeek\nCSV Show,12:00 PM,1:00 PM,M\n'), 'shows.csv'),
    })
    assert response.status_code == 302
    shows = {show.show_name: show for show in Show.query.all()}
    assert (shows['Night Show'].start_time, shows['Night Show'].end_time) == (time(18), time(0))
    assert shows['Old Import'].start_time == time(18)
    assert shows['CSV Show'].start_time == time(12)
    html = client.get('/shows').get_data(as_text=True)
    assert '06:00 PM' in html and '12:00 AM' in html
