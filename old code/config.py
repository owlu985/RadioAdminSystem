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
	
	# Audio analysis
	ENABLE_AUDIO_ANALYSIS = True
	AUDIO_WINDOW_SECONDS = 5

	DEAD_AIR_DB = -72
	AUTOMATION_DB_MIN = -6
	AUTOMATION_DB_MAX = -3

	# Show detection
	MIN_LIVE_PERCENT = 0.25   # % of windows that must be "live" to count as DJ show
	MAX_DEAD_AIR_PERCENT = 0.40

	# Paths
	RECORDINGS_PATH = "/mnt/nas/wlmc_recordings"
	ANALYSIS_OUTPUT_PATH = "/mnt/nas/wlmc_analysis"
	
	# --- RadioDJ Integration ---
	RADIODJ_ENABLED = False  # Safe default
	RADIODJ_API_URL = "http://127.0.0.1:8080/api"  # example
	RADIODJ_API_KEY = None  # optional if API requires it
	RADIODJ_DB_PATH = None  # optional if using DB access
	RADIODJ_TIMEOUT_SECONDS = 3



