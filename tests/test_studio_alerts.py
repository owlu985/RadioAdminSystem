from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import time

from flask import Flask
import pytest
import requests
from shapely.geometry import mapping
from shapely.geometry import box

from app.services import studio_monitor as monitor, weather_alerts as nws, barix
from app.services.barix import BarixRestartResult
from app.services.runtime_state import locked_state, read_state


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(DATA_ROOT=str(tmp_path), OUTPUT_FOLDER=str(tmp_path),
                      SELF_HEAL_ENABLED=True, BARIX_AUTO_RESTART_ENABLED=True,
                      STREAM_DOWN_RESTART_THRESHOLD=3, MONITOR_STORAGE_MIN_FREE_BYTES=0)
    with app.app_context():
        yield app


def feature(*, geometry=None, codes=None, **properties):
    props = dict(id='urn:alert:one', status='Actual', messageType='Alert',
                 event='Severe Thunderstorm Warning', severity='Severe',
                 expires=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                 geocode={'UGC': codes or []})
    props.update(properties)
    return {'id': props['id'], 'properties': props, 'geometry': geometry}


def test_polygon_names_only_intersected_towns_and_prioritizes():
    boundaries = {name: geometry for name, _, geometry in nws.town_boundaries()}
    assert 'Bellows Falls' in boundaries
    area = boundaries['Putney'].union(boundaries['Brattleboro']).union(boundaries['Keene'])
    result = nws.summarize(feature(geometry=mapping(area), codes=['VTC025', 'NHC005']))
    assert result['towns'] == ['Putney', 'Brattleboro', 'Keene']
    assert 'Dummerston' not in result['towns']
    assert 'description' not in result


def test_polygon_does_not_promote_entire_county():
    putney = next(g for name, _, g in nws.town_boundaries() if name == 'Putney')
    result = nws.summarize(feature(geometry=mapping(putney), codes=['VTC025']))
    assert result['towns'] == ['Putney']
    assert nws.summarize(feature(geometry=mapping(box(-71.99, 42.7, -71.98, 42.71)), codes=['NHC005'])) is None


def test_zone_geometry_and_county_only_watch():
    nh = nws.summarize(feature(codes=['NHC005'], event='Tornado Watch'))
    assert nh['towns'][0] == 'Keene'
    assert 'Putney' not in nh['towns']
    putney = next(g for name, _, g in nws.town_boundaries() if name == 'Putney')
    url = 'https://api.weather.gov/zones/forecast/VTZ015'
    alert = feature(codes=['VTZ015'], affectedZones=[url])
    assert nws.summarize(alert, {url: mapping(putney)})['towns'] == ['Putney']
    unknown = nws.summarize(alert)
    assert unknown['towns'] == []


@pytest.mark.parametrize('properties', [
    {'status': 'Test'}, {'messageType': 'Cancel'},
    {'expires': '2000-01-01T00:00:00Z'},
    {'ends': '2000-01-01T00:00:00Z'}, {'expires': 'invalid'},
])
def test_expired_cancelled_and_test_messages_excluded(properties):
    assert nws.summarize(feature(codes=['VTC025'], **properties)) is None


def test_collector_dedupes_uses_both_counties_and_throttles(app, monkeypatch):
    calls = []
    def get(url, headers, params=None):
        calls.append((url, headers, params))
        return {'type': 'FeatureCollection', 'features': [feature(codes=['VTC025'])] * 2}
    monkeypatch.setattr(nws, '_get', get)
    nws.refresh_weather_alerts(); nws.refresh_weather_alerts()
    assert len(calls) == 1
    assert calls[0][2] == {'zone': 'VTC025,NHC005'}
    assert calls[0][1]['User-Agent']
    assert len(nws.weather_alert_snapshot()['alerts']) == 1
    assert nws.weather_alert_snapshot()['available']


def test_failure_retains_only_unexpired_alerts_and_success_removes_cancelled(app, monkeypatch):
    item = nws.summarize(feature(codes=['VTC025']))
    expired = dict(item, id='expired', valid_until=0)
    with locked_state('nws-alerts') as state:
        state.update(alerts=[item, expired], last_success=time.time())
    def fail(*args, **kwargs):
        raise requests.ConnectionError()
    monkeypatch.setattr(nws, '_get', fail)
    nws.refresh_weather_alerts()
    snapshot = nws.weather_alert_snapshot()
    assert snapshot['message'] == 'NWS info unavailable'
    assert len(snapshot['alerts']) == 1 and snapshot['alerts'][0]['stale']
    with locked_state('nws-alerts') as state:
        state['last_attempt'] = 0
    monkeypatch.setattr(nws, '_get', lambda *args, **kwargs: {'type': 'FeatureCollection', 'features': []})
    nws.refresh_weather_alerts()
    assert nws.weather_alert_snapshot()['alerts'] == []
    assert nws.weather_alert_snapshot()['available']


@pytest.mark.parametrize('status', [301, 400, 401, 403, 404, 500, 503])
def test_http_errors_never_count_as_playable(app, monkeypatch, status):
    response = Mock(status_code=status, headers={})
    response.__enter__ = Mock(return_value=response); response.__exit__ = Mock()
    monkeypatch.setattr(monitor.requests, 'get', lambda *args, **kwargs: response)
    assert monitor.check_stream_http('https://stream.test')[0] is False
    response.__exit__.assert_called_once()


def test_consecutive_failures_reset_on_success_and_do_not_use_lifetime_totals(app):
    restart = Mock(return_value=BarixRestartResult(True, True, 'accepted', 'Requested'))
    healthy = SimpleNamespace(classification='live_show')
    monitor.record_stream_check(None, restart=restart)
    monitor.record_stream_check(None, restart=restart)
    assert not restart.called
    monitor.record_stream_check(healthy, restart=restart)
    monitor.record_stream_check(None, restart=restart)
    assert read_state('stream')['consecutive_failures'] == 1
    monitor.record_stream_check(None, restart=restart)
    monitor.record_stream_check(None, restart=restart)
    assert restart.call_count == 1
    assert read_state('stream')['recovery'] == 'verifying'
    monitor.record_stream_check(None, restart=restart)
    assert restart.call_count == 1  # cooldown spans successive calls / app workers
    monitor.record_stream_check(healthy, restart=restart)
    assert read_state('stream')['recovery'] == 'healthy'


@pytest.mark.parametrize('automatic', [True, False])
def test_manual_restart_notice_when_disabled_or_restart_failed(app, automatic):
    app.config['BARIX_AUTO_RESTART_ENABLED'] = automatic
    restart = Mock(return_value=BarixRestartResult(True, False, 'http_error', 'HTTP 500'))
    for _ in range(3):
        monitor.record_stream_check(None, restart=restart)
    assert restart.call_count == (1 if automatic else 0)
    alerts = monitor.monitor_snapshot()['alerts']
    assert any(a['code'] == '12' and a['message'] == 'Barix Error, Restart needed' for a in alerts)


def test_silent_but_playable_stream_does_not_restart_encoder(app):
    restart = Mock()
    monitor.record_stream_check(SimpleNamespace(classification='dead_air'), restart=restart)
    assert not restart.called
    assert any(a['message'] == 'NO INPUT DETECTED, CHECK BOARD' for a in monitor.monitor_snapshot()['alerts'])


def test_restart_limit_survives_new_app_context(app, monkeypatch):
    app.config.update(BARIX_RESTART_URL='http://barix/restart', BARIX_MAX_RESTARTS_PER_WINDOW=1)
    monkeypatch.setattr(barix.requests, 'get', lambda *args, **kwargs: SimpleNamespace(status_code=200))
    assert barix.restart_instreamer().accepted
    assert barix.restart_instreamer().status == 'lockout_started'
    assert barix.is_locked_out()


def test_missing_storage_or_mount_and_stale_monitor_labels(app, monkeypatch):
    app.config['OUTPUT_FOLDER'] = str(app.config['DATA_ROOT']) + '/missing'
    assert monitor.check_storage()
    app.config['OUTPUT_FOLDER'] = app.config['DATA_ROOT']
    app.config['MONITOR_STORAGE_MOUNT'] = app.config['DATA_ROOT']
    monkeypatch.setattr(monitor.os.path, 'ismount', lambda path: False)
    assert monitor.check_storage()
    codes = {a['code'] for a in monitor.monitor_snapshot()['alerts']}
    assert {'08', '09'} <= codes
    assert not ({'03', '05', '06', '10', '11'} & codes)


def test_handshake_uses_recognized_fields_and_does_not_create_nas(app, monkeypatch):
    from app.services.radiodj_client import RadioDJClient
    from pathlib import Path
    nas = Path(app.config['DATA_ROOT']) / 'missing-nas'
    app.config.update(NAS_ROOT=str(nas), RADIODJ_API_BASE_URL='http://radio.test', RADIODJ_API_PASSWORD='secret')
    monkeypatch.setattr(RadioDJClient, 'status', lambda self: {'message': 'Invalid password'})
    monitor.run_studio_checks()
    assert not nas.exists()
    assert not read_state('radiodj-status')['handshake_ok']
    assert any(a['code'] == '04' for a in monitor.monitor_snapshot()['alerts'])


def test_audio_levels_are_dbfs_and_silence_remains_input_alert(app, tmp_path):
    import math
    import struct
    import wave
    from app.services.detection import analyze_audio
    audio = tmp_path / 'tone.wav'
    for amplitude, expected in [(600, 'live_show'), (0, 'dead_air')]:
        with wave.open(str(audio), 'wb') as file:
            file.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            file.writeframes(b''.join(struct.pack('<h', int(amplitude * math.sin(i * 2 * math.pi * 440 / 8000))) for i in range(8000)))
        result = analyze_audio(str(audio), {})
        assert result.avg_db < 0
        assert result.classification == expected
