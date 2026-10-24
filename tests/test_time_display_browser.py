"""Optional browser regression checks; set RAMS_CHROMIUM_EXECUTABLE if needed."""
import os
from pathlib import Path
from urllib.parse import urlparse

from flask import render_template
import pytest

from test_dj_recording_portal import portal_app

playwright = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def browser():
    with playwright.sync_playwright() as runner:
        executable = os.environ.get('RAMS_CHROMIUM_EXECUTABLE', runner.chromium.executable_path)
        if not Path(executable).is_file():
            pytest.skip('Install Playwright Chromium or set RAMS_CHROMIUM_EXECUTABLE')
        instance = runner.chromium.launch(executable_path=executable)
        yield instance
        instance.close()


def test_ampm_controls_preserve_canonical_form_values(browser):
    page = browser.new_page(locale='en-GB')
    page.set_content('''<style>.form-control {display:block}</style><form><label for="start">Start time</label>
        <input type="time" id="start" name="start" class="form-control" required value="13:05">
        <input type="datetime-local" id="end" name="end" required value="2026-10-24T23:30">
        <input type="time" id="seconds" name="seconds" step="1" value="00:00:01">
        <input type="time" id="bounded" name="bounded" min="09:00" max="17:00">
        <div id="rows"></div><button type="reset">Reset</button></form>''')
    script = (ROOT / 'app/static/js/time-display.js').read_text()
    page.add_script_tag(content=script)
    assert page.locator('#start').is_hidden()
    assert page.locator('#start-display').input_value() == '1:05 PM'
    assert page.locator('#end-display').input_value() == '11:30 PM'
    assert page.locator('#seconds-display').input_value() == '12:00:01 AM'
    assert page.locator('label[for="start-display"]').count() == 1
    for value, canonical in [('12:00 AM', '00:00'), ('12:00 PM', '12:00')]:
        page.locator('#start-display').fill(value)
        assert page.locator('#start').input_value() == canonical
    page.locator('#start-display').fill('13:00 PM')
    assert not page.locator('#start-display').evaluate('(el) => el.checkValidity()')
    page.locator('#start-display').fill('1:30 pm')
    page.locator('#end-display').fill('12:00 AM')
    assert page.evaluate("Object.fromEntries(new FormData(document.querySelector('form')))") == {
        'start': '13:30', 'end': '2026-10-24T00:00', 'seconds': '00:00:01', 'bounded': '',
    }
    page.locator('#bounded-display').fill('8:00 AM')
    assert not page.locator('#bounded-display').evaluate('(el) => el.checkValidity()')
    page.locator('#bounded-display').fill('9:00 AM')
    assert page.locator('#bounded-display').evaluate('(el) => el.checkValidity()')
    # The log Now button and restored drafts assign canonical .value directly.
    page.evaluate("document.querySelector('#start').value = '00:01'")
    assert page.locator('#start-display').input_value() == '12:01 AM'
    page.evaluate("document.querySelector('#rows').innerHTML = '<input type=time name=time_4 value=18:30 required>'")
    page.wait_for_function("document.querySelector('[name=time_4]').dataset.timeSource === 'time'")
    assert page.locator('#rows input:not([hidden])').input_value() == '6:30 PM'
    page.add_script_tag(content=script)
    assert page.locator('#start-display').count() == 1
    page.locator('button').click()
    page.wait_for_function("document.querySelector('#start-display').value === '1:05 PM'")
    assert page.locator('#end-display').input_value() == '11:30 PM'
    page.locator('#start-display').fill('')
    assert not page.locator('#start-display').evaluate('(el) => el.checkValidity()')
    page.close()


@pytest.mark.parametrize(('instant', 'digits', 'period'), [
    ('2026-10-24T04:00:00Z', '12:00:00', 'AM'),
    ('2026-10-24T16:00:00Z', '12:00:00', 'PM'),
    ('2026-10-24T17:05:01Z', '1:05:01', 'PM'),
    ('2026-01-24T05:00:00Z', '12:00:00', 'AM'),
])
def test_studio_clock_uses_station_timezone_and_ampm(browser, portal_app, instant, digits, period):
    app, _ = portal_app
    with app.test_request_context('/dj/status'):
        html = render_template('dj_status.html', station_name='WLMC Radio: The Finn',
                               station_slogan='The Beat of Landmark College', app_timezone='America/New_York',
                               app_url_prefix='', station_background='/static/first-bkg-variant.jpg')
    page = browser.new_page(locale='en-GB', viewport={'width': 1920, 'height': 1080})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script(f"""{{const NativeDate=Date;class FixedDate extends NativeDate{{
        constructor(...args){{super(...(args.length ? args : ['{instant}']));}}
        static now(){{return new NativeDate('{instant}').valueOf();}}
    }}globalThis.Date=FixedDate;}}""")

    def route(request):
        path = urlparse(request.request.url).path
        if path == '/':
            request.fulfill(content_type='text/html', body=html)
        elif path == '/api/studio/status':
            request.fulfill(json={'now': {'status': 'off_air'}, 'monitor': {'alerts': []}, 'nws': {'available': True, 'alerts': []}})
        elif path == '/api/weather/tempest':
            request.fulfill(json={'status': 'ok', 'current': {'temp': 63, 'condition': 'Sunny'}, 'next_days': []})
        elif path.startswith('/static/'):
            file = ROOT / 'app/static' / path.removeprefix('/static/')
            request.fulfill(path=str(file)) if file.is_file() else request.fulfill(status=404)
        else:
            request.fulfill(json={'events': []})

    page.route('http://rams.test/**', route)
    page.goto('http://rams.test/')
    assert page.locator('#clockDigits').inner_text() == digits
    assert page.locator('#clockPeriod').inner_text() == period
    for width, height in [(1920, 1080), (1366, 768), (736, 1024)]:
        page.set_viewport_size({'width': width, 'height': height})
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    assert not errors
    page.close()
