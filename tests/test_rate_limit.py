from flask import Flask

from app import rate_limit


def _app():
    app = Flask(__name__)
    app.config.update(
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_REQUESTS=1,
        RATE_LIMIT_WINDOW_SECONDS=60,
        RATE_LIMIT_TRUSTED_IPS=[],
        RATE_LIMIT_TRUSTED_PROXIES=[],
    )
    return app


def test_forwarded_for_is_ignored_from_untrusted_peer():
    app = _app()
    with app.test_request_context("/", headers={"X-Forwarded-For": "203.0.113.7"}, environ_base={"REMOTE_ADDR": "198.51.100.2"}):
        assert rate_limit._client_ip([]) == "198.51.100.2"


def test_forwarded_for_is_used_from_trusted_proxy():
    app = _app()
    with app.test_request_context("/", headers={"X-Forwarded-For": "203.0.113.7"}, environ_base={"REMOTE_ADDR": "198.51.100.2"}):
        assert rate_limit._client_ip(["198.51.100.2"]) == "203.0.113.7"


def test_rate_limit_uses_constant_size_counter(monkeypatch):
    app = _app()
    cache = rate_limit.SimpleCache(default_timeout=0)
    monkeypatch.setattr(rate_limit, "_cache", cache)
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "198.51.100.9"}):
        assert rate_limit.rate_limit_check(app) is None
        response = rate_limit.rate_limit_check(app)
        assert response.status_code == 429
        assert cache.get("rl:198.51.100.9")["count"] == 1
