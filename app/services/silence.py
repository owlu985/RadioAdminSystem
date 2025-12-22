import os
import json
import secrets
from flask import Flask
from config import Config
from .models import db, Show
from .utils import init_utils
from .logger import init_logger
from flask_migrate import Migrate
from datetime import datetime, timedelta
from .scheduler import init_scheduler, pause_shows_until

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    user_config_path = os.path.join(app.instance_path, 'user_config.json')
    logs_dir = os.path.join(app.instance_path, 'logs')
    log_file_path = os.path.join(logs_dir, 'ShowRecorder.log')
    
    if not os.path.exists(app.instance_path):
        os.mkdir(app.instance_path)
    if not os.path.exists(logs_dir):
        os.mkdir(logs_dir)

    initial_logger = init_logger(log_file_path)
    initial_logger.info("Init logger initialized.")

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
                app.config.update(user_config)
        except Exception as e:
            initial_logger.error(f"Error loading user config: {e}")

#Init Database
    try:
        db.init_app(app)
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

#Init Show pausing restart roll over
    try:
        if app.config['PAUSE_SHOWS_RECORDING'] is True and app.config['PAUSE_SHOW_END_DATE'] is not None :
            pause_shows_until(app.config['PAUSE_SHOW_END_DATE'])
            initial_logger.info(f"Shows paused on startup until {app.config['PAUSE_SHOW_END_DATE']}")
        else:
            initial_logger.info("Shows not paused on startup.")
    except Exception as e:
        initial_logger.error(f"Error pausing shows on Init: {e}")
    
    from .routes import main_bp
    app.register_blueprint(main_bp)
    
    initial_logger.info("Application startup complete.")

    return app