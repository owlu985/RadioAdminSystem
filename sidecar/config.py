import os

from config import Config as BaseConfig

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DEFAULT_DATA_ROOT = os.path.join(INSTANCE_DIR, "data")


class Config(BaseConfig):
    RAMS_NAME = "RAMS Sidecar"
    SECRET_KEY = BaseConfig.SECRET_KEY
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(INSTANCE_DIR, 'sidecar.db')}"
    DATA_ROOT = os.getenv("RAMS_SIDECAR_DATA_ROOT") or DEFAULT_DATA_ROOT
    LOGS_DIR = os.path.join(DATA_ROOT, "logs")
    OUTPUT_FOLDER = os.path.join(DATA_ROOT, "recordings")

    NAS_ROOT = os.getenv("RAMS_SIDECAR_NAS_ROOT") or os.path.join(DATA_ROOT, "nas")
    NAS_MUSIC_ROOT = os.getenv("RAMS_SIDECAR_MUSIC_LIBRARY") or os.path.join(NAS_ROOT, "music")
    PSA_LIBRARY_PATH = os.path.join(NAS_ROOT, "psa")
    IMAGING_LIBRARY_PATH = os.path.join(NAS_ROOT, "imaging")
    MEDIA_ASSETS_ROOT = os.path.join(NAS_ROOT, "assets")
    VOICE_TRACKS_ROOT = os.path.join(NAS_ROOT, "voice_tracks")
    AUDIO_HOST_UPLOAD_DIR = os.path.join(DATA_ROOT, "hosted_audio")

    ARCHIVIST_DB_PATH = os.path.join(DATA_ROOT, "archivist_db.json")
    ARCHIVIST_UPLOAD_DIR = os.path.join(DATA_ROOT, "archivist_uploads")

    MONEYMUSIC_SPREADSHEET_PATH = os.path.join(DATA_ROOT, "moneymusic.csv")
