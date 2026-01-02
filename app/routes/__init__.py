# app/routes/__init__.py

from flask import Blueprint

main_bp = Blueprint("main", __name__)

# Import route modules so they register with Flask
from app import main_routes  # noqa: F401
from . import logging_api  # noqa: F401
from . import api  # noqa: F401
