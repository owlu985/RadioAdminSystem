import os
import json
import secrets
import traceback
from typing import Any
from flask import Flask, render_template, request
from config import Config
from .models import db, Show
from app.plugins import load_plugins
from .utils import init_utils, format_datetime_local, format_date_local, get_config_timezone_name
from .config_utils import normalize_optional_config
from .db_utils import ensure_schema
from .oauth import init_oauth, oauth
from .logger import init_logger
from flask_migrate import Migrate
from sqlalchemy import event
from datetime import datetime, timedelta
from .scheduler import init_scheduler, pause_shows_until
from .rate_limit import rate_limit_check





def _normalize_url_prefix(prefix: Any) -> str:
    if prefix is None:
        return ""
    prefix = str(prefix).strip()
    if not prefix or prefix == "/":
        return ""
    prefix = "/" + prefix.strip("/")
    return prefix


def _prefix_path(prefix: str, path: str) -> str:
    normalized = _normalize_url_prefix(prefix)
    if not path:
        return normalized or "/"
    if path.startswith(("http://", "https://", "//", "mailto:", "tel:", "javascript:")):
        return path
    if not path.startswith("/"):
        path = "/" + path
    if normalized and path.startswith(normalized + "/"):
        return path
    if normalized == path:
        return path
    return f"{normalized}{path}" if normalized else path


def _install_url_prefix_middleware(app: Flask) -> None:
    prefix = _normalize_url_prefix(app.config.get("ADMIN_URL_PREFIX", ""))
    app.config["ADMIN_URL_PREFIX"] = prefix
    app.config["APPLICATION_ROOT"] = prefix or "/"
    if prefix:
        app.config.setdefault("SESSION_COOKIE_PATH", prefix)

    original_wsgi_app = app.wsgi_app

    def _prefixed_wsgi_app(environ, start_response):
        if prefix:
            path_info = environ.get("PATH_INFO", "") or "/"
            script_name = environ.get("SCRIPT_NAME", "")
            if path_info == prefix or path_info.startswith(prefix + "/"):
                environ["SCRIPT_NAME"] = f"{script_name}{prefix}"
                environ["PATH_INFO"] = path_info[len(prefix):] or "/"
        return original_wsgi_app(environ, start_response)

    app.wsgi_app = _prefixed_wsgi_app

def _utf8_safe_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.encode("utf-8", "replace").decode("utf-8")


def _sanitize_db_params(value: Any) -> Any:
    if isinstance(value, str):
        return _utf8_safe_text(value)
    if isinstance(value, dict):
        return {k: _sanitize_db_params(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_sanitize_db_params(v) for v in value)
    if isinstance(value, list):
        return [_sanitize_db_params(v) for v in value]
    return value


def _install_utf8_query_safety(app: Flask) -> None:
    if app.extensions.get("utf8_query_safety_installed"):
        return

    @event.listens_for(db.engine, "before_cursor_execute", retval=True)
    def _sanitize_sql_params(conn, cursor, statement, parameters, context, executemany):
        try:
            sanitized_statement = _utf8_safe_text(statement)
            sanitized_parameters = _sanitize_db_params(parameters)
            return sanitized_statement, sanitized_parameters
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("UTF-8 query sanitization failed: %s", exc)
            return statement, parameters

    app.extensions["utf8_query_safety_installed"] = True


def _install_wsgi_utf8_safety(app: Flask) -> None:
    if app.extensions.get("utf8_wsgi_safety_installed"):
        return

    original_wsgi_app = app.wsgi_app

    def _safe_wsgi_app(environ, start_response):
        for key, value in list(environ.items()):
            if not isinstance(value, str):
                continue
            environ[key] = _utf8_safe_text(value)
        return original_wsgi_app(environ, start_response)

    app.wsgi_app = _safe_wsgi_app
    app.extensions["utf8_wsgi_safety_installed"] = True

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.jinja_env.filters["datetime_local"] = format_datetime_local
    app.jinja_env.filters["date_local"] = format_date_local
    _install_url_prefix_middleware(app)

    @app.context_processor
    def inject_runtime_settings():
        prefix = app.config.get("ADMIN_URL_PREFIX", "")
        return {
            "app_timezone": get_config_timezone_name(),
            "app_url_prefix": prefix,
            "app_prefixed_path": lambda path="/": _prefix_path(prefix, path),
            "app_request_path": _prefix_path(prefix, request.path),
        }

    def _startup_enabled(flag: str, default: bool = True) -> bool:
        value = app.config.get(flag, default)
        if not app.config.get("WSGI_SAFE_MODE", False):
            return value
        if flag in {
            "RUN_SCHEMA_SETUP_ON_STARTUP",
            "RUN_MIGRATIONS_ON_STARTUP",
            "RUN_CLEANUP_ON_STARTUP",
            "RUN_SCHEDULER_ON_STARTUP",
        }:
            return False
        return value
    
    user_config_path = os.path.join(app.instance_path, 'user_config.json')
    logs_dir = app.config.get("LOGS_DIR") or os.path.join(app.instance_path, 'logs')
    log_file_path = os.path.join(logs_dir, 'ShowRecorder.log')
    audio_host_dir = app.config.get("AUDIO_HOST_UPLOAD_DIR", Config.AUDIO_HOST_UPLOAD_DIR)
    data_root = app.config.get("DATA_ROOT")

    if not os.path.exists(app.instance_path):
        os.makedirs(app.instance_path, exist_ok=True)
    if data_root and not os.path.exists(data_root):
        os.makedirs(data_root, exist_ok=True)
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir, exist_ok=True)
    if audio_host_dir and not os.path.exists(audio_host_dir):
        os.makedirs(audio_host_dir, exist_ok=True)

    initial_logger = init_logger(log_file_path)
    initial_logger.info("Init logger initialized.")

    @app.before_request
    def _apply_rate_limit():
        response = rate_limit_check(app)
        if response:
            return response

#Load/Generate secret key and user config
    if not os.path.exists(user_config_path):
        try:
            secret_key = secrets.token_hex(16)
            default_config = {"SECRET_KEY": secret_key}
            try:
                fd = os.open(user_config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                fd = None
            if fd is not None:
                with os.fdopen(fd, "w") as f:
                    json.dump(default_config, f, indent=4)
                app.config['SECRET_KEY'] = secret_key
        except Exception as e:
            initial_logger.error(f"Error creating user config: {e}")
    else:
        try:
            with open(user_config_path, 'r') as f:
                user_config = json.load(f)

                app.config.update(normalize_optional_config(user_config))
        except Exception as e:
            initial_logger.error(f"Error loading user config: {e}")

#Init Database
    try:
        db.init_app(app)
        with app.app_context():
            _install_utf8_query_safety(app)

        if _startup_enabled("RUN_SCHEMA_SETUP_ON_STARTUP", True):
            with app.app_context():
                ensure_schema(app, initial_logger)

        Migrate(app, db)

        if _startup_enabled("RUN_MIGRATIONS_ON_STARTUP", True):
            with app.app_context():
                from flask_migrate import upgrade, init, migrate
                migrations_dir = os.path.join(app.instance_path, 'migrations')
                if not os.path.exists(migrations_dir):
                    try:
                        init(directory=migrations_dir)
                        migrate(message="Initial migration", directory=migrations_dir)
                        upgrade(directory=migrations_dir)
                    except Exception as e:
                        initial_logger.logger.error(f"Error during migrations: {e}")
    except Exception as e:
        initial_logger.error(f"Error initializing the database: {e}")

#Delete past shows
    if _startup_enabled("RUN_CLEANUP_ON_STARTUP", True):
        try:
            with app.app_context():
                past_shows = Show.query.filter(Show.end_date < datetime.now().date()).all()
                initial_logger.info(f"Past shows: {past_shows}")
                if not past_shows:
                    initial_logger.info("No past shows to delete on Init.")
                else:
                    for show in past_shows:
                        db.session.delete(show)
                    db.session.commit()
                    initial_logger.info(f"{past_shows} shows deleted on Init.")
        except Exception as e:
            initial_logger.error(f"Error deleting past shows on Init: {e}")

#Init Scheduler and Utils
    if _startup_enabled("RUN_SCHEDULER_ON_STARTUP", True):
        try:
            init_scheduler(app)
        except Exception as e:
            initial_logger.error(f"Error initializing scheduler: {e}")

    if _startup_enabled("RUN_UTILS_ON_STARTUP", True):
        try:
            init_utils()
        except Exception as e:
            initial_logger.error(f"Error initializing utils: {e}")

# Init OAuth (optional)
    if _startup_enabled("RUN_OAUTH_INIT_ON_STARTUP", True):
        try:
            init_oauth(app)
        except Exception as e:
            initial_logger.error(f"Error initializing OAuth: {e}")

#Init Show pausing restart roll over
    try:
        if app.config['PAUSE_SHOWS_RECORDING'] is True and app.config['PAUSE_SHOW_END_DATE'] is not None :
            pause_shows_until(app.config['PAUSE_SHOW_END_DATE'])
            initial_logger.info(f"Shows paused on startup until {app.config['PAUSE_SHOW_END_DATE']}")
        else:
            initial_logger.info("Shows not paused on startup.")
    except Exception as e:
        initial_logger.error(f"Error pausing shows on Init: {e}")
    
    from app.services.show_run_service import start_show_run, end_show_run  # noqa: F401
    from app.main_routes import main_bp
    from app.routes import auth as auth_routes  # noqa: F401
    from app.routes import dashboard as dashboard_routes  # noqa: F401
    from app.routes.api import api_bp
    from app.routes.logging_api import logs_bp
    from app.routes.news import news_bp

    if _startup_enabled("RUN_PLUGIN_LOAD_ON_STARTUP", True):
        with app.app_context():
            load_plugins(app)

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(news_bp)
    app.oauth_client = oauth

    @app.errorhandler(403)
    def forbidden(error):
        return (
            render_template(
                "error_403.html",
                error_description=getattr(error, "description", None),
            ),
            403,
        )

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error_404.html"), 404

    @app.errorhandler(500)
    def internal_error(error):
        message = getattr(error, "description", None) or str(error)
        details = traceback.format_exc()
        return (
            render_template(
                "error_500.html", error_message=message, error_details=details
            ),
            500,
        )

    _install_wsgi_utf8_safety(app)

    initial_logger.info("Application startup complete.")

    return app
