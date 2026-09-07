import importlib


def test_direct_run_enables_scheduler_by_default(monkeypatch):
    monkeypatch.delenv("RAMS_RUN_SCHEDULER_ON_STARTUP", raising=False)
    monkeypatch.delenv("RAMS_WSGI_SAFE_MODE", raising=False)

    import config

    reloaded = importlib.reload(config)
    assert reloaded.Config.RUN_SCHEDULER_ON_STARTUP is True


def test_scheduler_can_still_be_disabled_for_a_web_worker(monkeypatch):
    monkeypatch.setenv("RAMS_RUN_SCHEDULER_ON_STARTUP", "0")

    import config

    reloaded = importlib.reload(config)
    assert reloaded.Config.RUN_SCHEDULER_ON_STARTUP is False
    monkeypatch.delenv("RAMS_RUN_SCHEDULER_ON_STARTUP")
    importlib.reload(config)
