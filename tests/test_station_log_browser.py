"""Optional browser checks for the master log's mounted paths and review workflow."""
from datetime import date
from pathlib import Path
import threading
from werkzeug.serving import make_server
import os

import pytest
from test_station_log import app, event

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def browser():
    with playwright.sync_playwright() as runner:
        executable = os.environ.get("RAMS_CHROMIUM_EXECUTABLE", runner.chromium.executable_path)
        if not Path(executable).is_file():
            pytest.skip("Install Playwright Chromium or set RAMS_CHROMIUM_EXECUTABLE")
        instance = runner.chromium.launch(executable_path=executable)
        yield instance
        instance.close()


@pytest.fixture
def live_app(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


def test_review_workflow_and_history_survive_mounted_paths(app, browser, live_app):
    event("2026-10-28 14:10:00", title="Automation behind show")
    event("2026-10-28 14:11:00", title="RadioDJ handshake failed", event_type="code_raised", code="04")
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(authenticated=True, role="admin")
    page = browser.new_page(viewport={"width":1440, "height":1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    cookie = client.get_cookie("session", path="/admin")
    page.context.add_cookies([dict(name="session", value=cookie.value, url=live_app + "/admin")])
    page.goto(live_app + "/admin/station-log?day=2026-10-28")
    assert page.get_by_role("heading", name="Master Station Log", exact=True).is_visible()
    assert page.get_by_text("Automation behind show", exact=True).is_visible()
    detail = page.locator("details").filter(has=page.locator("summary", has_text="Add a show window"))
    detail.locator("summary").click()
    detail.locator('[name="show_name"]').fill("Live show")
    # time-display.js preserves canonical native datetime fields behind visible controls.
    page.evaluate("""() => {
      const form = document.querySelector('form[action$="/airings"]');
      form.querySelector('[name="starts_at"]').value='2026-10-28T10:00';
      form.querySelector('[name="ends_at"]').value='2026-10-28T11:00';
    }""")
    detail.locator('[name="status"]').select_option("aired")
    detail.locator('[name="reason"]').fill("Confirmed from aircheck")
    detail.get_by_role("button", name="Add window").click()
    page.wait_for_load_state()
    assert page.get_by_text("Show aired — playlist log missing", exact=True).is_visible()
    assert page.get_by_text("Automation behind show", exact=True).count() == 0
    assert page.get_by_text("RadioDJ handshake failed", exact=True).is_visible()
    page.select_option('#raw', '1')
    page.get_by_role("button", name="Read log").click()
    page.wait_for_load_state()
    assert page.get_by_text("Automation behind show", exact=True).is_visible()
    assert not errors
    page.close()
