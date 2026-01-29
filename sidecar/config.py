import importlib.util
import os
import secrets
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
DEFAULT_DATA_ROOT = os.path.join(INSTANCE_DIR, "data")
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))


def _load_base_config():
    config_path = os.path.join(ROOT_DIR, "config.py")
    if os.path.exists(config_path):
        spec = importlib.util.spec_from_file_location("rams_config", config_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load base config from {config_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.Config

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundled_config = os.path.join(bundle_root, "config.py")
        if os.path.exists(bundled_config):
            spec = importlib.util.spec_from_file_location("rams_config", bundled_config)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Unable to load base config from {bundled_config}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.Config

    class FallbackConfig:
        SECRET_KEY = secrets.token_hex(16)
        BIND_HOST = "127.0.0.1"
        BIND_PORT = 5055
        STATION_NAME = ""
        STATION_SLOGAN = ""
        THEME_DEFAULT = "system"
        FONT_SCALE_PERCENT = 100

    return FallbackConfig


BaseConfig = _load_base_config()


class Config:
    RAMS_NAME = "RAMS Sidecar"
    SECRET_KEY = BaseConfig.SECRET_KEY
    BIND_HOST = getattr(BaseConfig, "BIND_HOST", "127.0.0.1")
    BIND_PORT = getattr(BaseConfig, "BIND_PORT", 5055)
    STATION_NAME = getattr(BaseConfig, "STATION_NAME", "")
    STATION_SLOGAN = getattr(BaseConfig, "STATION_SLOGAN", "")
    THEME_DEFAULT = getattr(BaseConfig, "THEME_DEFAULT", "system")
    FONT_SCALE_PERCENT = getattr(BaseConfig, "FONT_SCALE_PERCENT", 100)

    DATA_ROOT = os.getenv("RAMS_SIDECAR_DATA_ROOT") or DEFAULT_DATA_ROOT
    NAS_ROOT = os.getenv("RAMS_SIDECAR_NAS_ROOT") or os.path.join(DATA_ROOT, "nas")
    NAS_MUSIC_ROOT = os.getenv("RAMS_SIDECAR_MUSIC_LIBRARY") or os.path.join(NAS_ROOT, "music")
    MONEYMUSIC_SPREADSHEET_PATH = os.path.join(DATA_ROOT, "moneymusic.csv")
