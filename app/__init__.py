import os
import json
import secrets
import traceback
from flask import Flask, render_template
from config import Config
from .models import db, Show
from app.plugins import load_plugins
from .utils import init_utils
from .config_utils import normalize_optional_config
from .db_utils import ensure_schema
from .oauth import init_oauth, oauth
from .logger import init_logger
from flask_migrate import Migrate
from datetime import datetime, timedelta
from .scheduler import init_scheduler, pause_shows_until
from .rate_limit import rate_limit_check

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    user_config_path = os.path.join(app.instance_path, 'user_config.json')
    logs_dir = app.config.get("LOGS_DIR") or os.path.join(app.instance_path, 'logs')
    log_file_path = os.path.join(logs_dir, 'ShowRecorder.log')
    audio_host_dir = app.config.get("AUDIO_HOST_UPLOAD_DIR", Config.AUDIO_HOST_UPLOAD_DIR)
    data_root = app.config.get("DATA_ROOT")

    if not os.path.exists(app.instance_path):
        os.mkdir(app.instance_path)
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
            with open(user_config_path, 'w') as f:
                secret_key = secrets.token_hex(16)
                default_config = {
                    "SECRET_KEY": secret_key
                }
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
            ensure_schema(app, initial_logger)

        Migrate(app, db)

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
    try:
        init_scheduler(app)
    except Exception as e:
        initial_logger.error(f"Error initializing scheduler: {e}")

    try:
        init_utils()
    except Exception as e:
        initial_logger.error(f"Error initializing utils: {e}")

# Init OAuth (optional)
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
    from app.routes.api import api_bp
    from app.routes.logging_api import logs_bp
    from app.routes.news import news_bp

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


    initial_logger.info("Application startup complete.")

    return app
