import os

from app import create_app


if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("RAMS_HOST") or app.config.get("BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("RAMS_PORT") or app.config.get("BIND_PORT", 5000))

    app.run(debug=False, host=host, port=port)
