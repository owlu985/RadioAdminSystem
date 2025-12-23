import os

class Config:
    SECRET_KEY = "a_not_so_secure_fallback_key"
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance", "app.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    STREAM_URL = "https://wlmc.landmark.edu:8880/stream"
    OUTPUT_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance", "recordings")
    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD = "admin"
    DEFAULT_START_DATE = "2024-01-01"
    DEFAULT_END_DATE = "2024-01-01"
    AUTO_CREATE_SHOW_FOLDERS = False
    PAUSE_SHOWS_RECORDING = False
    PAUSE_SHOW_END_DATE = None
    TEST_MODE = False

    # Silence / automation detection
    DEAD_AIR_DB = -72
    AUTOMATION_MIN_DB = -6
    AUTOMATION_MAX_DB = -3
    AUTOMATION_RATIO_THRESHOLD = 0.65
    SILENCE_CHUNK_MS = 500
    STREAM_PROBE_SECONDS = 8
    STREAM_PROBE_INTERVAL_MINUTES = 5

    # REST/API defaults
    DEFAULT_OFF_AIR_MESSAGE = "WLMC is currently off-air"

    # NAS / RadioDJ integration
    NAS_ROOT = os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance", "nas_test")
    NAS_NEWS_FILE = os.path.join(NAS_ROOT, "wlmc_news.mp3")
    NAS_COMMUNITY_CALENDAR_FILE = os.path.join(NAS_ROOT, "wlmc_comm_calendar.mp3")
    TEST_SAMPLE_AUDIO = os.path.join(NAS_ROOT, "sample_probe.mp3")
    NEWS_TYPES_CONFIG = os.path.join(NAS_ROOT, "news_types.json")
    NAS_MUSIC_ROOT = os.path.join(NAS_ROOT, "music")
    RADIODJ_IMPORT_FOLDER = os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance", "radiodj_imports")
    RADIODJ_API_BASE_URL = None
    RADIODJ_API_KEY = None

    # Audits
    AUDIT_ITUNES_RATE_LIMIT_SECONDS = 0.5
    AUDIT_MUSIC_MAX_FILES = 500

    # Branding
    STATION_NAME = "WLMC"
    STATION_SLOGAN = "The Voice of Landmark College"
    STATION_BACKGROUND = "first-bkg-variant.jpg"  # filename in static/ or a full URL

    # OAuth
    OAUTH_CLIENT_ID = None
    OAUTH_CLIENT_SECRET = None
    OAUTH_ALLOWED_DOMAIN = None  # e.g. "example.edu" to restrict logins
