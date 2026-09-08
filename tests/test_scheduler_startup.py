import importlib
import os
import runpy
import sys
import types


def test_app_import_does_not_enable_scheduler_by_default(monkeypatch):
    monkeypatch.delenv("RAMS_RUN_SCHEDULER_ON_STARTUP", raising=False)
    monkeypatch.delenv("RAMS_WSGI_SAFE_MODE", raising=False)

    import config

    reloaded = importlib.reload(config)
    assert reloaded.Config.RUN_SCHEDULER_ON_STARTUP is False


def test_scheduler_can_still_be_disabled_for_a_web_worker(monkeypatch):
    monkeypatch.setenv("RAMS_RUN_SCHEDULER_ON_STARTUP", "0")

    import config

    reloaded = importlib.reload(config)
    assert reloaded.Config.RUN_SCHEDULER_ON_STARTUP is False
    monkeypatch.delenv("RAMS_RUN_SCHEDULER_ON_STARTUP")
    importlib.reload(config)


def test_direct_run_opts_into_single_process_scheduler(monkeypatch):
    monkeypatch.delenv("RAMS_RUN_SCHEDULER_ON_STARTUP", raising=False)
    fake_app = types.ModuleType("app")
    fake_app.create_app = lambda: None
    monkeypatch.setitem(sys.modules, "app", fake_app)

    runpy.run_path("run.py", run_name="run_test")

    assert os.environ["RAMS_RUN_SCHEDULER_ON_STARTUP"] == "1"
