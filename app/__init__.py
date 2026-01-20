import os
import json
import secrets
import traceback
from flask import Flask, render_template
from config import Config
from .models import db, Show, Plugin
from app.plugins import load_plugins
from .utils import init_utils
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

                def _normalize_optional(val):
                    if val is None:
                        return None
                    if isinstance(val, str) and val.strip().lower() in {"", "none", "null"}:
                        return None
                    return val

                optional_keys = {
                    "TEMPEST_API_KEY",
                    "OAUTH_CLIENT_ID",
                    "OAUTH_CLIENT_SECRET",
                    "OAUTH_ALLOWED_DOMAIN",
                    "DISCORD_OAUTH_CLIENT_ID",
                    "DISCORD_OAUTH_CLIENT_SECRET",
                    "DISCORD_ALLOWED_GUILD_ID",
                    "ALERTS_DISCORD_WEBHOOK",
                    "ALERTS_EMAIL_TO",
                    "ALERTS_EMAIL_FROM",
                    "ALERTS_SMTP_SERVER",
                    "ALERTS_SMTP_USERNAME",
                    "ALERTS_SMTP_PASSWORD",
                    "ICECAST_STATUS_URL",
                    "ICECAST_USERNAME",
                    "ICECAST_PASSWORD",
                    "ICECAST_MOUNT",
                    "SOCIAL_FACEBOOK_PAGE_TOKEN",
                    "SOCIAL_INSTAGRAM_TOKEN",
                    "SOCIAL_TWITTER_BEARER_TOKEN",
                    "SOCIAL_BLUESKY_HANDLE",
                    "SOCIAL_BLUESKY_PASSWORD",
                    "ARCHIVIST_DB_PATH",
                    "ARCHIVIST_UPLOAD_DIR",
                    "DATA_ROOT",
                    "NAS_MUSIC_ROOT",
                    "RADIODJ_API_BASE_URL",
                    "RADIODJ_API_PASSWORD",
                }

                for key in optional_keys:
                    if key in user_config:
                        user_config[key] = _normalize_optional(user_config[key])

                app.config.update(user_config)
        except Exception as e:
            initial_logger.error(f"Error loading user config: {e}")

#Init Database
    try:
        db.init_app(app)
        
        with app.app_context():
            db.create_all()

            # Lightweight compatibility patching for new columns without manual migrations.
            from sqlalchemy import inspect, text
            insp = inspect(db.engine)
            cols = {col["name"] for col in insp.get_columns("log_entry")}
            with db.engine.begin() as conn:
                if "log_sheet_id" not in cols:
                    conn.execute(text("ALTER TABLE log_entry ADD COLUMN log_sheet_id INTEGER"))
                if "entry_time" not in cols:
                    conn.execute(text("ALTER TABLE log_entry ADD COLUMN entry_time TIME"))
                if "dj" not in insp.get_table_names():
                    conn.execute(text(
                        "CREATE TABLE IF NOT EXISTS dj (id INTEGER PRIMARY KEY, first_name VARCHAR(64) NOT NULL, last_name VARCHAR(64) NOT NULL, bio TEXT, description TEXT, photo_url VARCHAR(255), is_public BOOLEAN NOT NULL DEFAULT 1)"
                    ))
                if "show_dj" not in insp.get_table_names():
                    conn.execute(text("CREATE TABLE IF NOT EXISTS show_dj (show_id INTEGER NOT NULL, dj_id INTEGER NOT NULL, PRIMARY KEY (show_id, dj_id))"))
                if "user" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS user (
                            id INTEGER PRIMARY KEY,
                            email VARCHAR(255) NOT NULL UNIQUE,
                            provider VARCHAR(50) NOT NULL,
                            external_id VARCHAR(255),
                            display_name VARCHAR(255),
                            role VARCHAR(50),
                            approved BOOLEAN NOT NULL DEFAULT 0,
                            requested_at DATETIME NOT NULL,
                            approved_at DATETIME,
                            last_login_at DATETIME,
                            created_at DATETIME NOT NULL
                        )
                        """
                    ))
                user_cols = {c["name"] for c in insp.get_columns("user")}
                if "custom_role" not in user_cols:
                    conn.execute(text("ALTER TABLE user ADD COLUMN custom_role VARCHAR(50)"))
                if "permissions" not in user_cols:
                    conn.execute(text("ALTER TABLE user ADD COLUMN permissions TEXT"))
                if "identities" not in user_cols:
                    conn.execute(text("ALTER TABLE user ADD COLUMN identities TEXT"))
                if "approval_status" not in user_cols:
                    conn.execute(text("ALTER TABLE user ADD COLUMN approval_status VARCHAR(32) DEFAULT 'pending'"))
                if "rejected" not in user_cols:
                    conn.execute(text("ALTER TABLE user ADD COLUMN rejected BOOLEAN DEFAULT 0"))
                if "notification_email" not in user_cols:
                    conn.execute(text("ALTER TABLE user ADD COLUMN notification_email VARCHAR(255)"))
                if "created_at" not in user_cols:
                    conn.execute(text("ALTER TABLE user ADD COLUMN created_at DATETIME"))
                    conn.execute(text("UPDATE user SET created_at = COALESCE(requested_at, datetime('now'))"))
                dj_cols = {c["name"] for c in insp.get_columns("dj")}
                if "description" not in dj_cols:
                    conn.execute(text("ALTER TABLE dj ADD COLUMN description TEXT"))
                if "is_public" not in dj_cols:
                    conn.execute(text("ALTER TABLE dj ADD COLUMN is_public BOOLEAN DEFAULT 1"))
                    conn.execute(text("UPDATE dj SET is_public = 1 WHERE is_public IS NULL"))
                if "dj_absence" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS dj_absence (
                            id INTEGER PRIMARY KEY,
                            dj_name VARCHAR(128) NOT NULL,
                            show_name VARCHAR(128) NOT NULL,
                            show_id INTEGER,
                            start_time DATETIME NOT NULL,
                            end_time DATETIME NOT NULL,
                            replacement_name VARCHAR(128),
                            notes TEXT,
                            status VARCHAR(32) NOT NULL DEFAULT 'pending',
                            created_at DATETIME NOT NULL
                        )
                        """
                    ))
                if "show" in insp.get_table_names():
                    show_cols = {c["name"] for c in insp.get_columns("show")}
                    if "is_temporary" not in show_cols:
                        conn.execute(text("ALTER TABLE show ADD COLUMN is_temporary BOOLEAN DEFAULT 0"))
                        conn.execute(text("UPDATE show SET is_temporary = 0 WHERE is_temporary IS NULL"))
                if "music_analysis" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS music_analysis (
                            id INTEGER PRIMARY KEY,
                            path VARCHAR(500) NOT NULL UNIQUE,
                            duration_seconds FLOAT,
                            peak_db FLOAT,
                            rms_db FLOAT,
                            peaks TEXT,
                            bitrate INTEGER,
                            hash VARCHAR(64),
                            missing_tags BOOLEAN NOT NULL DEFAULT 0,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL
                        )
                        """
                    ))
                if "music_cue" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS music_cue (
                            id INTEGER PRIMARY KEY,
                            path VARCHAR(500) NOT NULL UNIQUE,
                            cue_in FLOAT,
                            intro FLOAT,
                            outro FLOAT,
                            cue_out FLOAT,
                            loop_in FLOAT,
                            loop_out FLOAT,
                            hook_in FLOAT,
                            hook_out FLOAT,
                            start_next FLOAT,
                            fade_in FLOAT,
                            fade_out FLOAT,
                            updated_at DATETIME NOT NULL
                        )
                        """
                    ))
                else:
                    cols = {c['name'] for c in insp.get_columns('music_cue')}
                    for name in ["cue_out", "hook_in", "hook_out", "start_next", "loop_in", "loop_out"]:
                        if name not in cols:
                            conn.execute(text(f"ALTER TABLE music_cue ADD COLUMN {name} FLOAT"))
                if "job_health" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS job_health (
                            id INTEGER PRIMARY KEY,
                            name VARCHAR(64) NOT NULL UNIQUE,
                            failure_count INTEGER NOT NULL DEFAULT 0,
                            restart_count INTEGER NOT NULL DEFAULT 0,
                            last_failure_at DATETIME,
                            last_restart_at DATETIME,
                            last_failure_reason VARCHAR(255)
                        )
                        """
                    ))
                if "live_read_card" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS live_read_card (
                            id INTEGER PRIMARY KEY,
                            title VARCHAR(200) NOT NULL,
                            expires_on DATE,
                            copy TEXT NOT NULL,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL
                        )
                        """
                    ))
                if "archivist_entry" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS archivist_entry (
                            id INTEGER PRIMARY KEY,
                            title VARCHAR(255),
                            artist VARCHAR(255),
                            album VARCHAR(255),
                            catalog_number VARCHAR(128),
                            price_range VARCHAR(64),
                            notes TEXT,
                            extra TEXT,
                            created_at DATETIME NOT NULL
                        )
                        """
                    ))
                else:
                    archivist_cols = {c['name'] for c in insp.get_columns('archivist_entry')}
                    if "price_range" not in archivist_cols:
                        conn.execute(text("ALTER TABLE archivist_entry ADD COLUMN price_range VARCHAR(64)"))
                if "archivist_rip_result" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS archivist_rip_result (
                            id INTEGER PRIMARY KEY,
                            filename VARCHAR(255),
                            duration_ms INTEGER,
                            segments_json TEXT,
                            settings_json TEXT,
                            created_at DATETIME NOT NULL
                        )
                        """
                    ))
                if "audit_run" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS audit_run (
                            id INTEGER PRIMARY KEY,
                            action VARCHAR(32) NOT NULL,
                            status VARCHAR(32) NOT NULL,
                            params_json TEXT,
                            results_json TEXT,
                            created_at DATETIME NOT NULL,
                            completed_at DATETIME
                        )
                        """
                    ))
                if "social_post" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS social_post (
                            id INTEGER PRIMARY KEY,
                            content TEXT NOT NULL,
                            platforms TEXT,
                            image_url VARCHAR(500),
                            image_path VARCHAR(500),
                            status VARCHAR(32) NOT NULL DEFAULT 'pending',
                            result_log TEXT,
                            created_at DATETIME NOT NULL,
                            sent_at DATETIME
                        )
                        """
                    ))
                else:
                    social_cols = {c['name'] for c in insp.get_columns('social_post')}
                    if 'image_path' not in social_cols:
                        conn.execute(text("ALTER TABLE social_post ADD COLUMN image_path VARCHAR(500)"))
                if "plugin" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS plugin (
                            id INTEGER PRIMARY KEY,
                            name VARCHAR(100) NOT NULL UNIQUE,
                            enabled BOOLEAN NOT NULL DEFAULT 1,
                            config TEXT,
                            created_at DATETIME NOT NULL,
                            updated_at DATETIME NOT NULL
                        )
                        """
                    ))
                if "website_content" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS website_content (
                            id INTEGER PRIMARY KEY,
                            headline VARCHAR(255),
                            body TEXT,
                            image_url VARCHAR(500),
                            updated_at DATETIME NOT NULL
                        )
                        """
                    ))
                if "podcast_episode" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS podcast_episode (
                            id INTEGER PRIMARY KEY,
                            title VARCHAR(255) NOT NULL,
                            description TEXT,
                            embed_code TEXT NOT NULL,
                            created_at DATETIME NOT NULL
                        )
                        """
                    ))
                if "icecast_stat" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS icecast_stat (
                            id INTEGER PRIMARY KEY,
                            listeners INTEGER,
                            created_at DATETIME NOT NULL
                        )
                        """
                    ))
                if "now_playing_state" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS now_playing_state (
                            id INTEGER PRIMARY KEY,
                            session_id INTEGER,
                            queue_item_id INTEGER,
                            item_type VARCHAR(32),
                            kind VARCHAR(32),
                            title VARCHAR(255),
                            artist VARCHAR(255),
                            duration FLOAT,
                            metadata TEXT,
                            status VARCHAR(32),
                            started_at DATETIME,
                            cue_in FLOAT,
                            cue_out FLOAT,
                            fade_out FLOAT,
                            cues TEXT,
                            override_enabled BOOLEAN NOT NULL DEFAULT 0,
                            updated_at DATETIME NOT NULL
                        )
                        """
                    ))
                else:
                    now_playing_cols = {c["name"] for c in insp.get_columns("now_playing_state")}
                    now_playing_additions = {
                        "session_id": "INTEGER",
                        "queue_item_id": "INTEGER",
                        "item_type": "VARCHAR(32)",
                        "kind": "VARCHAR(32)",
                        "title": "VARCHAR(255)",
                        "artist": "VARCHAR(255)",
                        "duration": "FLOAT",
                        "metadata": "TEXT",
                        "status": "VARCHAR(32)",
                        "started_at": "DATETIME",
                        "cue_in": "FLOAT",
                        "cue_out": "FLOAT",
                        "fade_out": "FLOAT",
                        "cues": "TEXT",
                        "override_enabled": "BOOLEAN NOT NULL DEFAULT 0",
                        "updated_at": "DATETIME",
                    }
                    for col, col_type in now_playing_additions.items():
                        if col not in now_playing_cols:
                            conn.execute(text(f"ALTER TABLE now_playing_state ADD COLUMN {col} {col_type}"))
                if "marathon_event" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS marathon_event (
                            id INTEGER PRIMARY KEY,
                            name VARCHAR(128) NOT NULL,
                            safe_name VARCHAR(128) NOT NULL,
                            start_time DATETIME NOT NULL,
                            end_time DATETIME NOT NULL,
                            chunk_hours INTEGER NOT NULL DEFAULT 2,
                            status VARCHAR(32) NOT NULL DEFAULT 'pending',
                            job_ids TEXT,
                            created_at DATETIME NOT NULL,
                            canceled_at DATETIME
                        )
                        """
                    ))
                if "hosted_audio" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS hosted_audio (
                            id INTEGER PRIMARY KEY,
                            title VARCHAR(255) NOT NULL,
                            description TEXT,
                            file_url VARCHAR(512) NOT NULL,
                            backdrop_url VARCHAR(512),
                            created_at DATETIME NOT NULL
                        )
                        """
                    ))
                if "saved_search" not in insp.get_table_names():
                    conn.execute(text(
                        """
                        CREATE TABLE IF NOT EXISTS saved_search (
                            id INTEGER PRIMARY KEY,
                            name VARCHAR(128) NOT NULL,
                            query VARCHAR(255) NOT NULL,
                            filters TEXT,
                            created_by VARCHAR(255),
                            created_at DATETIME NOT NULL
                        )
                        """
                    ))
            if "dj_disciplinary" not in insp.get_table_names():
                conn.execute(text(
                    """
                        CREATE TABLE IF NOT EXISTS dj_disciplinary (
                            id INTEGER PRIMARY KEY,
                            dj_id INTEGER NOT NULL,
                            issued_at DATETIME NOT NULL,
                            severity VARCHAR(32),
                            notes TEXT,
                            action_taken TEXT,
                            resolved BOOLEAN NOT NULL DEFAULT 0,
                            created_by VARCHAR(255)
                        )
                        """
                    ))

            # Seed the first-party Website Content & Podcasts plugin so content and
            # podcast management stay unified under a single plugin entry.
            if not Plugin.query.filter_by(name="website_content").first():
                db.session.add(Plugin(name="website_content", enabled=True))
                db.session.commit()

            # Ensure news types config exists with defaults
            news_config_path = app.config["NEWS_TYPES_CONFIG"]
            if not os.path.exists(news_config_path):
                os.makedirs(os.path.dirname(news_config_path), exist_ok=True)
                with open(news_config_path, "w") as f:
                    json.dump(
                        [
                            {
                                "key": "news",
                                "label": "News",
                                "filename": "wlmc_news.mp3",
                                "frequency": "daily",
                                "metadata": {
                                    "artist": "WLMC Radio",
                                    "album": "WLMC News",
                                    "title_template": "WLMC NEWS {date}",
                                    "date_format": "%m-%d-%Y",
                                },
                            },
                            {
                                "key": "community_calendar",
                                "label": "Community Calendar",
                                "filename": "wlmc_comm_calendar.mp3",
                                "frequency": "weekly",
                                "rotation_day": 0,
                                "metadata": {
                                    "artist": "WLMC Radio",
                                    "album": "WLMC Community Calendar",
                                    "title_template": "WLMC COMM CAL {date}",
                                    "date_format": "%m-%d-%Y",
                                },
                            },
                        ],
                        f,
                        indent=2,
                    )

            # Ensure social upload directory exists for uploaded post images
            os.makedirs(app.config.get("SOCIAL_UPLOAD_DIR", os.path.join(app.instance_path, "social_uploads")), exist_ok=True)

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
