import os
import json

from sqlalchemy import inspect, text

from app.models import Plugin, db


def table_exists(table_name: str) -> bool:
    inspector = inspect(db.engine)
    return table_name in inspector.get_table_names()


def _playback_session_has_column(column_name: str) -> bool:
    inspector = inspect(db.engine)
    if "playback_session" not in inspector.get_table_names():
        return False
    try:
        columns = {col["name"] for col in inspector.get_columns("playback_session")}
    except Exception:  # noqa: BLE001
        columns = set()
    if column_name in columns:
        return True
    if db.engine.dialect.name != "sqlite":
        return False
    result = db.session.execute(text("PRAGMA table_info(playback_session)")).mappings().all()
    return any(row.get("name") == column_name for row in result)


def ensure_playback_session_schema(logger) -> None:
    inspector = inspect(db.engine)
    if "playback_session" not in inspector.get_table_names():
        return
    required = {
        "show_run_id": "INTEGER",
        "show_name": "VARCHAR(255)",
        "dj_name": "VARCHAR(255)",
        "notes": "TEXT",
        "started_at": "DATETIME",
        "ended_at": "DATETIME",
        "automation_mode": "VARCHAR(32)",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    }
    if db.engine.dialect.name != "sqlite":
        existing = {col["name"] for col in inspector.get_columns("playback_session")}
        missing = [name for name in required.keys() if name not in existing]
        if not missing:
            return
        logger.warning(
            "Missing playback_session columns but dialect is %s: %s",
            db.engine.dialect.name,
            missing,
        )
        return
    for name, column_type in required.items():
        if _playback_session_has_column(name):
            continue
        try:
            db.session.execute(text(f"ALTER TABLE playback_session ADD COLUMN {name} {column_type}"))
        except Exception as exc:  # noqa: BLE001
            if "duplicate column name" in str(exc).lower() or _playback_session_has_column(name):
                db.session.rollback()
                continue
            db.session.rollback()
            raise
    db.session.commit()


def ensure_schema(app, logger) -> None:
    db.create_all()

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
        if "news_type" in insp.get_table_names():
            news_type_cols = {c["name"] for c in insp.get_columns("news_type")}
            if "output_dir" not in news_type_cols:
                conn.execute(text("ALTER TABLE news_type ADD COLUMN output_dir VARCHAR(255) DEFAULT ''"))
                conn.execute(text("UPDATE news_type SET output_dir = '' WHERE output_dir IS NULL"))
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
            cue_cols = {c['name'] for c in insp.get_columns('music_cue')}
            for name in ["cue_out", "hook_in", "hook_out", "start_next", "loop_in", "loop_out"]:
                if name not in cue_cols:
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

    if not Plugin.query.filter_by(name="website_content").first():
        db.session.add(Plugin(name="website_content", enabled=True))
        db.session.commit()

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

    os.makedirs(
        app.config.get("SOCIAL_UPLOAD_DIR", os.path.join(app.instance_path, "social_uploads")),
        exist_ok=True,
    )

    logger.info("Database schema ensure complete.")
