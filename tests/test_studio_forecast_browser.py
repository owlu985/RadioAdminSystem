from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import render_template

from app.routes import api
from test_dj_recording_portal import portal_app
from test_time_display_browser import ROOT, browser


def test_forecast_shows_upcoming_days_and_precipitation(browser, portal_app, monkeypatch, tmp_path):
    app, _ = portal_app
    zone = ZoneInfo('America/New_York')
    now = datetime(2026, 10, 28, 12, tzinfo=zone)
    daily = [{
        'day_start_local': (now.replace(hour=0) + timedelta(days=n)).timestamp(),
        'air_temp_high': 65 - n, 'air_temp_low': 43 - n,
        'conditions': ['Partly cloudy', 'Partly cloudy', 'Rain', 'Mostly sunny'][n],
        'precip_probability': [20, 0, 70, None][n],
    } for n in range(4)]

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'current_conditions': {'air_temperature': 63, 'conditions': 'Partly cloudy'}, 'forecast': {'daily': daily}}

    monkeypatch.setattr(api.requests, 'get', lambda *args, **kwargs: Response())
    monkeypatch.setattr(api.time, 'time', lambda: now.timestamp())
    app.config.update(TEMPEST_API_KEY='test', SCHEDULE_TIMEZONE='America/New_York')
    weather_failed = False
    with app.test_request_context('/dj/status'):
        weather = api.weather_tempest().get_json()
        html = render_template('dj_status.html', station_name='WLMC Radio: The Finn',
                               station_slogan='The Beat of Landmark College', app_timezone='America/New_York',
                               app_url_prefix='', station_background='/static/first-bkg-variant.jpg')
    page = browser.new_page(viewport={'width': 1920, 'height': 1080})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(request):
        path = urlparse(request.request.url).path
        if path == '/':
            request.fulfill(content_type='text/html', body=html)
        elif path == '/api/studio/status':
            request.fulfill(json={'now': {'status': 'off_air'}, 'monitor': {'alerts': []}, 'nws': {'available': True, 'alerts': []}})
        elif path == '/api/weather/tempest':
            request.fulfill(status=502, json={'status': 'error'}) if weather_failed else request.fulfill(json=weather)
        elif path.startswith('/static/'):
            file = ROOT / 'app/static' / path.removeprefix('/static/')
            request.fulfill(path=str(file)) if file.is_file() else request.fulfill(status=404)
        else:
            request.fulfill(json={'events': []})

    page.route('http://rams.test/**', route)
    page.goto('http://rams.test/')
    page.wait_for_function("document.querySelector('#temperature').textContent === '63°'")
    assert page.locator('#high').inner_text() == '↑ High 65°'
    assert page.locator('#low').inner_text() == '↓ Low 43°'
    assert page.locator('#currentPrecip').inner_text() == '20% precip.'
    assert page.locator('#forecast > div').evaluate_all('tiles => tiles.map(tile => tile.firstChild.textContent)') == ['Thu', 'Fri', 'Sat']
    assert page.locator('.forecast-precip').all_text_contents() == ['0% precip.', '70% precip.', 'Precip —']
    for width, height in [(1920, 1080), (1366, 768), (736, 1024)]:
        page.set_viewport_size({'width': width, 'height': height})
        page.screenshot(path=str(tmp_path / f'forecast-{width}.png'))
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert page.locator('.weather').evaluate('el => el.scrollHeight <= el.clientHeight + 2')
        assert page.locator('.forecast-precip').evaluate_all('els => els.every(el => el.scrollWidth <= el.clientWidth + 2)')
    weather['next_days'][0]['precip_probability'] = 100
    page.reload()
    page.wait_for_function("document.querySelector('.forecast-precip')?.textContent === '100% precip.'")
    weather_failed = True
    page.reload()
    page.wait_for_function("document.querySelector('#conditions').textContent === 'Weather unavailable'")
    assert page.locator('#currentPrecip').inner_text() == ''
    assert page.locator('#forecast > div').count() == 0
    assert not errors
    page.close()
