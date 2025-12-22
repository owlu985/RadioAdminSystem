# app/routes/__init__.py

from flask import Blueprint

main_bp = Blueprint("main", __name__)

# Import route modules so they register with Flask
from . import auth
from . import shows
from . import dashboard
from . import logging_api
from . import api
