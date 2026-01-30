import os

from app import create_app

os.environ.setdefault("RAMS_WSGI_SAFE_MODE", "1")

application = create_app()
