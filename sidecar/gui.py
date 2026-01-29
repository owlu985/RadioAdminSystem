import threading
import time
import webbrowser

import webview

from sidecar.app import create_app


def _run_flask(app, host: str, port: int) -> None:
    app.run(host=host, port=port, debug=False, use_reloader=False)


def main() -> None:
    app = create_app()
    host = app.config.get("BIND_HOST", "127.0.0.1")
    port = int(app.config.get("BIND_PORT", 5055))
    server = threading.Thread(target=_run_flask, args=(app, host, port), daemon=True)
    server.start()
    time.sleep(0.4)
    url = f"http://{host}:{port}/"
    try:
        window = webview.create_window("RAMS Sidecar", url, width=1280, height=860)
        webview.start(debug=False)
    except Exception:
        webbrowser.open(url)
        server.join()


if __name__ == "__main__":
    main()
