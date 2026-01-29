import json
import os
from datetime import datetime

from flask import Blueprint, Flask, flash, redirect, render_template, request, url_for

try:
    from sidecar.config import Config, INSTANCE_DIR
except ModuleNotFoundError:  # pragma: no cover - fallback for bundled script entrypoints
    from config import Config, INSTANCE_DIR


def _instance_config_path() -> str:
    return os.path.join(INSTANCE_DIR, "sidecar_config.json")


def _load_sidecar_config() -> dict:
    path = _instance_config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def _save_sidecar_config(payload: dict) -> None:
    path = _instance_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)


def _apply_sidecar_config(app: Flask, payload: dict) -> None:
    for key in ("NAS_MUSIC_ROOT", "MONEYMUSIC_SPREADSHEET_PATH"):
        if key in payload and payload[key]:
            app.config[key] = payload[key]


def create_app(config_class=Config) -> Flask:
    sidecar_templates = os.path.join(os.path.dirname(__file__), "templates")

    app = Flask("rams_sidecar", instance_path=INSTANCE_DIR, instance_relative_config=False)
    app.config.from_object(config_class)

    os.makedirs(app.instance_path, exist_ok=True)
    data_root = app.config.get("DATA_ROOT")
    if data_root:
        os.makedirs(data_root, exist_ok=True)

    app.template_folder = sidecar_templates

    stored = _load_sidecar_config()
    _apply_sidecar_config(app, stored)

    main_bp = Blueprint("main", __name__)

    @main_bp.route("/")
    def index():
        return redirect(url_for("main.options"))

    @main_bp.route("/options", methods=["GET", "POST"])
    def options():
        current = {
            "NAS_MUSIC_ROOT": app.config.get("NAS_MUSIC_ROOT", ""),
            "MONEYMUSIC_SPREADSHEET_PATH": app.config.get("MONEYMUSIC_SPREADSHEET_PATH", ""),
        }
        if request.method == "POST":
            music_root = (request.form.get("music_root") or "").strip()
            spreadsheet = (request.form.get("moneymusic_spreadsheet") or "").strip()
            payload = {
                "NAS_MUSIC_ROOT": music_root,
                "MONEYMUSIC_SPREADSHEET_PATH": spreadsheet,
            }
            _save_sidecar_config(payload)
            _apply_sidecar_config(app, payload)
            flash("Sidecar options saved.", "success")
            current.update(payload)
        return render_template("options.html", **current)

    app.register_blueprint(main_bp)

    @app.context_processor
    def inject_globals():
        return {
            "rams_name": app.config.get("RAMS_NAME", "RAMS Sidecar"),
            "station_name": app.config.get("STATION_NAME", ""),
            "station_slogan": app.config.get("STATION_SLOGAN", ""),
            "theme_default": app.config.get("THEME_DEFAULT", "system"),
            "font_scale_percent": app.config.get("FONT_SCALE_PERCENT", 100),
            "current_year": datetime.utcnow().year,
        }

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(
        host=application.config.get("BIND_HOST", "127.0.0.1"),
        port=application.config.get("BIND_PORT", 5055),
        debug=True,
    )
