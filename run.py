import os

# This entrypoint runs exactly one Flask process, so it can safely own the
# scheduler. Set this before importing app/config; callers may still explicitly
# disable it with RAMS_RUN_SCHEDULER_ON_STARTUP=0.
os.environ.setdefault("RAMS_RUN_SCHEDULER_ON_STARTUP", "1")

from app import create_app


if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("RAMS_HOST") or app.config.get("BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("RAMS_PORT") or app.config.get("BIND_PORT", 5000))

    app.run(debug=False, host=host, port=port)
