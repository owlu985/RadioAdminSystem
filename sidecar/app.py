import json
import os
from datetime import datetime

from flask import (
    Blueprint,
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from jinja2 import ChoiceLoader, FileSystemLoader

from app.db_utils import ensure_schema, ensure_playback_session_schema
from app.logger import init_logger
from app.models import ArchivistEntry, ArchivistRipResult, db
from app.routes.api import api_bp
from app.services.archivist_db import search_archivist
from app.services.audit import audit_recordings, audit_explicit_music

from sidecar.config import Config, INSTANCE_DIR

LOGGER = init_logger()


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


def _psa_library_root(app: Flask) -> str:
    root = app.config.get("PSA_LIBRARY_PATH") or os.path.join(app.instance_path, "psa")
    os.makedirs(root, exist_ok=True)
    return root


def _imaging_library_root(app: Flask) -> str:
    root = app.config.get("IMAGING_LIBRARY_PATH") or os.path.join(app.instance_path, "imaging")
    os.makedirs(root, exist_ok=True)
    return root


def create_app(config_class=Config) -> Flask:
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    template_root = os.path.join(base_dir, "app", "templates")
    static_root = os.path.join(base_dir, "app", "static")
    sidecar_templates = os.path.join(os.path.dirname(__file__), "templates")

    app = Flask(
        "rams_sidecar",
        instance_path=INSTANCE_DIR,
        instance_relative_config=False,
        static_folder=static_root,
    )
    app.config.from_object(config_class)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config.get("DATA_ROOT"), exist_ok=True)

    app.jinja_loader = ChoiceLoader([
        FileSystemLoader(sidecar_templates),
        FileSystemLoader(template_root),
    ])

    stored = _load_sidecar_config()
    _apply_sidecar_config(app, stored)

    db.init_app(app)
    with app.app_context():
        ensure_schema(app, LOGGER)
        ensure_playback_session_schema(LOGGER)

    main_bp = Blueprint("main", __name__)
    sidecar_api = Blueprint("sidecar_api", __name__, url_prefix="/sidecar-api")

    @main_bp.route("/")
    def index():
        return redirect(url_for("main.archivist_page"))

    @main_bp.route("/dashboard")
    def dashboard():
        return redirect(url_for("main.archivist_page"))

    @main_bp.route("/settings")
    def settings():
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

    @sidecar_api.route("/options", methods=["GET", "POST"])
    def options_api():
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            music_root = (payload.get("NAS_MUSIC_ROOT") or "").strip()
            spreadsheet = (payload.get("MONEYMUSIC_SPREADSHEET_PATH") or "").strip()
            new_config = {
                "NAS_MUSIC_ROOT": music_root,
                "MONEYMUSIC_SPREADSHEET_PATH": spreadsheet,
            }
            _save_sidecar_config(new_config)
            _apply_sidecar_config(app, new_config)
            return jsonify({"status": "ok", **new_config})
        return jsonify({
            "NAS_MUSIC_ROOT": app.config.get("NAS_MUSIC_ROOT", ""),
            "MONEYMUSIC_SPREADSHEET_PATH": app.config.get("MONEYMUSIC_SPREADSHEET_PATH", ""),
        })

    @main_bp.route("/archivist")
    def archivist_page():
        query = (request.args.get("q") or "").strip()
        show_all = request.args.get("show_all") == "1" or query == "%"
        results = []
        if query or show_all:
            limit = 500 if show_all else 200
            results = search_archivist(query or "%", limit=limit)

        total = ArchivistEntry.query.count()
        raw_results = ArchivistRipResult.query.order_by(ArchivistRipResult.created_at.desc()).limit(10).all()
        rip_results = []
        for item in raw_results:
            segments = []
            if item.segments_json:
                try:
                    segments = json.loads(item.segments_json)
                except json.JSONDecodeError:
                    segments = []
            rip_results.append(
                {
                    "id": item.id,
                    "filename": item.filename,
                    "duration_ms": item.duration_ms or 0,
                    "settings_json": item.settings_json or "",
                    "segments": segments,
                    "track_count": len(segments),
                }
            )
        return render_template(
            "archivist.html",
            results=results,
            query=query,
            total=total,
            show_all=show_all,
            rip_results=rip_results,
        )

    @sidecar_api.route("/archivist/search")
    def archivist_search():
        query = (request.args.get("q") or "").strip()
        show_all = request.args.get("show_all") == "1" or query == "%"
        results = []
        if query or show_all:
            limit = 500 if show_all else 200
            results = search_archivist(query or "%", limit=limit)
        payload = [
            {
                "artist": row.artist,
                "title": row.title,
                "album": row.album,
                "catalog_number": row.catalog_number,
                "price_range": row.price_range,
                "notes": row.notes,
            }
            for row in results
        ]
        return jsonify({"results": payload})

    @sidecar_api.route("/archivist/rips")
    def archivist_rips():
        raw_results = ArchivistRipResult.query.order_by(ArchivistRipResult.created_at.desc()).limit(10).all()
        payload = []
        for item in raw_results:
            segments = []
            if item.segments_json:
                try:
                    segments = json.loads(item.segments_json)
                except json.JSONDecodeError:
                    segments = []
            payload.append(
                {
                    "id": item.id,
                    "filename": item.filename,
                    "duration_ms": item.duration_ms or 0,
                    "settings_json": item.settings_json or "",
                    "segments": segments,
                }
            )
        return jsonify({"results": payload})

    @main_bp.route("/audit", methods=["GET", "POST"])
    def audit_page():
        recordings_results = None
        explicit_results = None
        if request.method == "POST":
            action = request.form.get("action")
            if action == "recordings":
                folder = request.form.get("recordings_folder") or None
                recordings_results = audit_recordings(folder)
            if action == "explicit":
                rate = float(request.form.get("rate_limit") or app.config["AUDIT_ITUNES_RATE_LIMIT_SECONDS"])
                limit = int(request.form.get("max_files") or app.config["AUDIT_MUSIC_MAX_FILES"])
                lyrics_check = request.form.get("lyrics_check") == "1"
                explicit_results = audit_explicit_music(
                    rate_limit_s=rate,
                    max_files=limit,
                    lyrics_check=lyrics_check,
                )
        return render_template(
            "audit.html",
            recordings_results=recordings_results,
            explicit_results=explicit_results,
            default_rate=app.config["AUDIT_ITUNES_RATE_LIMIT_SECONDS"],
            default_limit=app.config["AUDIT_MUSIC_MAX_FILES"],
            default_lyrics=app.config.get("AUDIT_LYRICS_CHECK_ENABLED", False),
        )

    @main_bp.route("/dj/show-automator")
    def show_automator():
        psa_root = _psa_library_root(app)
        imaging_root = _imaging_library_root(app)
        return render_template("show_automating_player.html", psa_root=psa_root, imaging_root=imaging_root)

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(sidecar_api)

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
