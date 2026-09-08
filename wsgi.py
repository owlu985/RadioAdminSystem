import os

# This deployment intentionally uses exactly one WSGI worker as the scheduler
# owner.  These values must be set before importing app/config, whose class
# attributes are evaluated at import time.
os.environ["RAMS_WSGI_SAFE_MODE"] = "1"
os.environ["RAMS_RUN_SCHEDULER_ON_STARTUP"] = "1"

from app import create_app  # noqa: E402

application = create_app()
