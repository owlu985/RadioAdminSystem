from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time

from flask import Flask, jsonify
import pytest

from app.routes import api
from app.services import studio_monitor, weather_alerts


@pytest.fixture
def app(monkeypatch, tmp_path):
    app = Flask(__name__)
    app.config.update(DATA_ROOT=str(tmp_path), SCHEDULE_TIMEZONE='America/New_York', TEMPEST_API_KEY='test')
    monkeypatch.setattr(studio_monitor, 'monitor_snapshot', lambda: {
        'alerts': [], 'radiodj': {'autodj': True, 'handshake_ok': True, 'playing': True, 'checked_at': time.time()}})
    monkeypatch.setattr(weather_alerts, 'weather_alert_snapshot', lambda: {'available': True, 'alerts': []})
    monkeypatch.setattr(api, '_RADIODJ_NOWPLAYING_CACHE', {})
    with app.app_context():
        yield app


def setup_automation(monkeypatch, item):
    api._RADIODJ_NOWPLAYING_CACHE.update(source_success_at=time.time(), current_item=item)
    monkeypatch.setattr(api, 'now_playing', lambda: jsonify(status='off_air'))
    monkeypatch.setattr(api, '_get_cached_radiodj_nowplaying', lambda **kwargs: None)
    monkeypatch.setattr(api, '_cover_url_for_track', lambda *args: '/cover')


def test_history_excludes_current_once_keeps_old_repeat(app, monkeypatch):
    item = {'artist': 'Artist', 'title': 'Current', 'is_music': True}
    setup_automation(monkeypatch, item)
    history = [dict(item, play_id=3), {'artist': 'Other', 'title': 'Previous', 'play_id': 2}, dict(item, play_id=1)]
    monkeypatch.setattr(api, 'recent_tracks', lambda: jsonify(tracks=history))
    result = api.studio_status().get_json()['now']
    assert result['status'] == 'automation'
    assert [item['play_id'] for item in result['history']] == [2, 1]


def test_non_music_does_not_show_previous_song_as_current(app, monkeypatch):
    setup_automation(monkeypatch, {'title': 'Station ID', 'is_music': False})
    monkeypatch.setattr(api, 'recent_tracks', lambda: jsonify(tracks=[{'title': 'Last Song', 'artist': 'Artist', 'play_id': 1}]))
    result = api.studio_status().get_json()['now']
    assert result['track']['title'] == 'Station ID'
    assert 'cover_url' not in result['track']
    assert len(result['history']) == 1


def test_stale_or_stopped_playback_hides_artwork(app, monkeypatch):
    setup_automation(monkeypatch, {'title': 'Old Song', 'is_music': True})
    api._RADIODJ_NOWPLAYING_CACHE['source_success_at'] = 0
    result = api.studio_status().get_json()['now']
    assert result['status'] == 'unknown' and result['track'] is None


def test_live_show_keeps_priority_and_tomorrow_is_not_next_show_today(app, monkeypatch):
    tomorrow = datetime.now(ZoneInfo('America/New_York')) + timedelta(days=1)
    monkeypatch.setattr(api, 'now_playing', lambda: jsonify(status='on_air', show={'name': 'Live Show'}, next_show={'window': {'start_at': tomorrow.isoformat()}}))
    monkeypatch.setattr(api, '_get_cached_radiodj_nowplaying', lambda **kwargs: pytest.fail('Live mode must not fetch music'))
    result = api.studio_status().get_json()['now']
    assert result['status'] == 'on_air' and result['next_show'] is None


def test_tempest_keeps_zero_degree_temperatures_and_conditions(app, monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'current_conditions': {'air_temperature': 0, 'conditions': 'Snow'},
                    'forecast': {'daily': [{'day': 'Fri', 'air_temp_high': 0, 'air_temp_low': -4, 'conditions': 'Light snow'}]}}
    monkeypatch.setattr(api.requests, 'get', lambda *args, **kwargs: Response())
    data = api.weather_tempest().get_json()
    assert data['current']['temp'] == 0
    assert data['next_days'][0] == {'day': 'Fri', 'high': 0, 'low': -4, 'condition': 'Light snow', 'icon': None}
