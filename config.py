import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DEFAULT_DATA_ROOT = os.path.join(INSTANCE_DIR, "data")


class Config:
    SECRET_KEY = "a_not_so_secure_fallback_key"
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(INSTANCE_DIR, "app.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    STREAM_URL = "https://wlmc.landmark.edu:8880/stream"
    DATA_ROOT = os.getenv("RAMS_DATA_ROOT") or DEFAULT_DATA_ROOT
    LOGS_DIR = os.path.join(DATA_ROOT, "logs")
    OUTPUT_FOLDER = os.path.join(DATA_ROOT, "recordings")
    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD = "admin"
    ADMIN_PASSWORD_HASH = None
    DEFAULT_START_DATE = "2024-01-01"
    DEFAULT_END_DATE = "2024-01-01"
    AUTO_CREATE_SHOW_FOLDERS = False
    PAUSE_SHOWS_RECORDING = False
    PAUSE_SHOW_END_DATE = None
    TEST_MODE = False

    # Server binding
    BIND_HOST = "127.0.0.1"
    BIND_PORT = 5000

    # Silence / automation detection
    DEAD_AIR_DB = -72
    DEAD_AIR_SOFT_DB = -60
    SILENCE_RATIO_DEAD_AIR = 0.35
    AUTOMATION_MIN_DB = -12
    AUTOMATION_MAX_DB = -2
    AUTOMATION_AVG_MIN_DB = -18
    AUTOMATION_AVG_MAX_DB = -2
    AUTOMATION_DYNAMIC_RANGE_MAX = 5.0
    AUTOMATION_RATIO_THRESHOLD = 0.65
    SILENCE_CHUNK_MS = 500
    STREAM_PROBE_SECONDS = 8
    STREAM_PROBE_INTERVAL_MINUTES = 1

    # REST/API defaults
    DEFAULT_OFF_AIR_MESSAGE = "WLMC is currently off-air"

    # Weather / Tempest
    TEMPEST_API_KEY = None
    TEMPEST_STATION_ID = 118392
    TEMPEST_UNITS_TEMP = "f"
    TEMPEST_UNITS_WIND = "mph"

    # Accessibility / display
    HIGH_CONTRAST_DEFAULT = False
    FONT_SCALE_PERCENT = 100

    # Roles / auth
    CUSTOM_ROLES = []
    OAUTH_ONLY = False

    # Alerts
    ALERTS_ENABLED = False
    ALERTS_DRY_RUN = True
    ALERTS_DISCORD_WEBHOOK = None
    ALERTS_EMAIL_ENABLED = False
    ALERTS_EMAIL_TO = None
    ALERTS_EMAIL_FROM = None
    ALERTS_SMTP_SERVER = None
    ALERTS_SMTP_PORT = 587
    ALERTS_SMTP_USERNAME = None
    ALERTS_SMTP_PASSWORD = None
    ALERT_DEAD_AIR_THRESHOLD_MINUTES = 5
    AUTODJ_DEAD_AIR_MINUTES = 4
    AUTODJ_AUTO_RECOVER = True
    ALERT_STREAM_DOWN_THRESHOLD_MINUTES = 1
    ALERT_REPEAT_MINUTES = 15

    # Stream/Icecast monitoring
    ICECAST_STATUS_URL = None
    ICECAST_LISTCLIENTS_URL = None
    ICECAST_USERNAME = None
    ICECAST_PASSWORD = None
    ICECAST_MOUNT = None
    ICECAST_ANALYTICS_INTERVAL_MINUTES = 15
    ICECAST_ANALYTICS_FILE = None  # defaults to instance/analytics/icecast_listener_history.jsonl
    ICECAST_IGNORED_IPS = []

    # Self-heal / health reporting defaults
    SELF_HEAL_ENABLED = True

    # Settings backup
    SETTINGS_BACKUP_INTERVAL_HOURS = 12
    SETTINGS_BACKUP_RETENTION = 10
    SETTINGS_BACKUP_DIRNAME = "settings_backups"
    DATA_BACKUP_DIRNAME = "data_backups"
    DATA_BACKUP_RETENTION_DAYS = 60

    # NAS / RadioDJ integration
    NAS_ROOT = os.getenv("RAMS_NAS_ROOT") or os.path.join(DATA_ROOT, "nas")
    NAS_NEWS_FILE = os.path.join(NAS_ROOT, "wlmc_news.mp3")
    NAS_COMMUNITY_CALENDAR_FILE = os.path.join(NAS_ROOT, "wlmc_comm_calendar.mp3")
    TEST_SAMPLE_AUDIO = os.path.join(NAS_ROOT, "sample_probe.mp3")
    NEWS_TYPES_CONFIG = os.path.join(NAS_ROOT, "news_types.json")
    NAS_MUSIC_ROOT = os.getenv("RAMS_MUSIC_LIBRARY") or os.path.join(NAS_ROOT, "music")
    MUSIC_INDEX_TTL = 60
    MEDIA_INDEX_TTL = 60
    PSA_LIBRARY_PATH = os.path.join(NAS_ROOT, "psa")
    IMAGING_LIBRARY_PATH = os.path.join(NAS_ROOT, "imaging")
    MEDIA_ASSETS_ROOT = os.path.join(NAS_ROOT, "assets")
    VOICE_TRACKS_ROOT = os.path.join(NAS_ROOT, "voice_tracks")
    TRANSCODE_ALAC_TO_MP3 = True
    AUDIO_HOST_UPLOAD_DIR = os.path.join(DATA_ROOT, "hosted_audio")
    AUDIO_HOST_BACKDROP_DEFAULT = os.path.join(DATA_ROOT, "hosted_audio_default.jpg")
    RADIODJ_IMPORT_FOLDER = os.path.join(DATA_ROOT, "radiodj_imports")
    RADIODJ_API_BASE_URL = os.getenv("RADIODJ_API_BASE_URL")
    RADIODJ_API_PASSWORD = os.getenv("RADIODJ_API_PASSWORD")
    RADIODJ_API_KEY = None
    NOW_PLAYING_OVERRIDE_ENABLED = False

    # Audits
    AUDIT_ITUNES_RATE_LIMIT_SECONDS = 3.1
    AUDIT_MUSIC_MAX_FILES = 500
    AUDIT_LYRICS_CHECK_ENABLED = False

    # Branding
    STATION_NAME = "WLMC"
    STATION_SLOGAN = "The Voice of Landmark College"
    STATION_BACKGROUND = "first-bkg-variant.jpg"  # filename in static/ or a full URL
    SCHEDULE_TIMEZONE = "America/New_York"
    THEME_DEFAULT = "system"
    INLINE_HELP_ENABLED = True
    DJ_PHOTO_UPLOAD_DIR = os.path.join(DATA_ROOT, "dj_photos")

    # Production / archivist
    ARCHIVIST_DB_PATH = os.path.join(DATA_ROOT, "archivist_db.json")
    ARCHIVIST_UPLOAD_DIR = os.path.join(DATA_ROOT, "archivist_uploads")

    # Metadata enrichment
    MUSICBRAINZ_USER_AGENT = "RAMS/1.0 (support@example.com)"

    # Rate limiting
    RATE_LIMIT_ENABLED = True
    RATE_LIMIT_REQUESTS = 120
    RATE_LIMIT_WINDOW_SECONDS = 60
    RATE_LIMIT_TRUSTED_IPS = ["127.0.0.1", "::1"]

    # OAuth
    OAUTH_CLIENT_ID = None
    OAUTH_CLIENT_SECRET = None
    OAUTH_ALLOWED_DOMAIN = None  # e.g. "example.edu" to restrict logins
    DISCORD_OAUTH_CLIENT_ID = None
    DISCORD_OAUTH_CLIENT_SECRET = None
    DISCORD_ALLOWED_GUILD_ID = None  # optional: require membership in this guild
