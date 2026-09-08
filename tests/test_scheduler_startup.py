import importlib
import os
import runpy
import sys
import types

from config import Config


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


def test_wsgi_opts_in_before_creating_single_worker_app(monkeypatch):
    observed = {}
    monkeypatch.setenv("RAMS_RUN_SCHEDULER_ON_STARTUP", "0")
    monkeypatch.delenv("RAMS_WSGI_SAFE_MODE", raising=False)
    fake_app = types.ModuleType("app")

    def create_app():
        observed["scheduler"] = os.environ.get("RAMS_RUN_SCHEDULER_ON_STARTUP")
        observed["safe_mode"] = os.environ.get("RAMS_WSGI_SAFE_MODE")
        return object()

    fake_app.create_app = create_app
    monkeypatch.setitem(sys.modules, "app", fake_app)

    module_globals = runpy.run_path("wsgi.py", run_name="wsgi_test")

    assert observed == {"scheduler": "1", "safe_mode": "1"}
    assert module_globals["application"] is not None


def test_safe_mode_allows_explicit_single_owner_scheduler(monkeypatch, tmp_path):
    import app as app_module

    scheduler_apps = []
    monkeypatch.setattr(app_module, "init_scheduler", scheduler_apps.append)

    class SingleWorkerConfig(Config):
        TESTING = True
        WSGI_SAFE_MODE = True
        RUN_SCHEDULER_ON_STARTUP = True
        RUN_UTILS_ON_STARTUP = False
        RUN_OAUTH_INIT_ON_STARTUP = False
        RUN_PLUGIN_LOAD_ON_STARTUP = False
        DATA_ROOT = str(tmp_path / "data")
        LOGS_DIR = str(tmp_path / "logs")
        AUDIO_HOST_UPLOAD_DIR = str(tmp_path / "audio")
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'app.db'}"

    created_app = app_module.create_app(SingleWorkerConfig)

    assert scheduler_apps == [created_app]
