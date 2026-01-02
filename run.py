import os

from app import create_app
import os


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}



def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("RAMS_HOST") or app.config.get("BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("RAMS_PORT") or app.config.get("BIND_PORT", 5000))

    use_dev_ssl = _env_flag("RAMS_DEV_SSL", app.config.get("DEV_SSL_ENABLED", False))
    cert_path = os.environ.get("RAMS_DEV_SSL_CERT") or app.config.get("DEV_SSL_CERT_PATH")
    key_path = os.environ.get("RAMS_DEV_SSL_KEY") or app.config.get("DEV_SSL_KEY_PATH")
    openssl_bin = os.environ.get("RAMS_DEV_SSL_OPENSSL") or app.config.get("DEV_SSL_OPENSSL_BIN")
    ssl_context = None

    if use_dev_ssl:
        try:
            from app.services.ssl_utils import ensure_dev_ssl

            cert_path, key_path = ensure_dev_ssl(cert_path, key_path, openssl_bin=openssl_bin)
            ssl_context = (cert_path, key_path)
            print(f"[RAMS] Dev SSL enabled using cert={cert_path} key={key_path}")
        except Exception as exc:  # pragma: no cover - best effort for local dev
            print(f"[RAMS] Failed to enable dev SSL: {exc}")

    app.run(debug=False, host=host, port=port, ssl_context=ssl_context)
