"""Supervised RAMS background-service entrypoint.

Run exactly one instance under systemd, Docker Compose, or another process
supervisor. Web workers intentionally do not own recurring jobs.
"""

from __future__ import annotations

import os
import signal
import threading
import fcntl

os.environ["RAMS_RUN_SCHEDULER_ON_STARTUP"] = "0"

from app import create_app  # noqa: E402
from app import scheduler as scheduler_module  # noqa: E402


def main() -> int:
    app = create_app()
    stop = threading.Event()
    lock_path = os.path.join(app.instance_path, "background-service.lock")
    lock_file = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        app.logger.error("Another RAMS background service already owns %s", lock_path)
        lock_file.close()
        return 1
    lock_file.write(str(os.getpid()))
    lock_file.flush()

    def request_stop(signum, _frame):
        app.logger.info("Background service received signal %s", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    scheduler_module.init_scheduler(app)
    app.logger.info("RAMS background service started pid=%s", os.getpid())
    stop.wait()
    if scheduler_module.scheduler.running:
        scheduler_module.scheduler.shutdown(wait=True)
    lock_file.close()
    app.logger.info("RAMS background service stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
