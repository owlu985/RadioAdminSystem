import os
import shutil
from uuid import uuid4

from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, send_from_directory, abort
from werkzeug.utils import secure_filename

from app.auth_utils import admin_required
from app.models import HostedAudio
from app.plugins import PluginInfo, ensure_plugin_record
from app import db
from config import Config

bp = Blueprint(
    "audio_host_plugin",
    __name__,
    template_folder="templates",
)


def _resolve_upload_dir():
    upload_dir = current_app.config.get("AUDIO_HOST_UPLOAD_DIR", Config.AUDIO_HOST_UPLOAD_DIR)
    if upload_dir:
        os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def _save_upload(file_storage, upload_dir):
    filename = f"{uuid4().hex}_{secure_filename(file_storage.filename)}"
    path = os.path.join(upload_dir, filename)
    file_storage.save(path)
    return filename


def _default_backdrop(upload_dir):
    fallback = current_app.config.get("AUDIO_HOST_BACKDROP_DEFAULT")
    if fallback and os.path.isfile(fallback):
        dest = os.path.join(upload_dir, os.path.basename(fallback))
        if not os.path.exists(dest):
            try:
                shutil.copyfile(fallback, dest)
            except Exception:
                pass
        return url_for("audio_host_plugin.serve_file", filename=os.path.basename(fallback), _external=True)
    if fallback and fallback.startswith("http"):
        return fallback
    return url_for('static', filename='logo.png', _external=True)


@bp.route("/", methods=["GET", "POST"])
@admin_required
def manage():
    plugin = ensure_plugin_record("audio_host")
    upload_dir = _resolve_upload_dir()
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip() or None

        file_url = (request.form.get("file_url") or "").strip()
        file_upload = request.files.get("file_upload")
        if file_upload and file_upload.filename:
            if upload_dir:
                filename = _save_upload(file_upload, upload_dir)
                file_url = url_for("audio_host_plugin.serve_file", filename=filename, _external=True)
        backdrop_url = (request.form.get("backdrop_url") or "").strip()
        backdrop_upload = request.files.get("backdrop_upload")
        if backdrop_upload and backdrop_upload.filename and upload_dir:
            bname = _save_upload(backdrop_upload, upload_dir)
            backdrop_url = url_for("audio_host_plugin.serve_file", filename=bname, _external=True)

        if not backdrop_url:
            backdrop_url = _default_backdrop(upload_dir) if upload_dir else url_for('static', filename='logo.png', _external=True)

        if not title or not file_url:
            flash("Title and an audio file or URL are required.", "danger")
        else:
            db.session.add(HostedAudio(title=title, description=description, file_url=file_url, backdrop_url=backdrop_url))
            db.session.commit()
            flash("Hosted audio saved.", "success")
        return redirect(url_for("audio_host_plugin.manage"))

    items = HostedAudio.query.order_by(HostedAudio.created_at.desc()).all()
    return render_template("plugin_audio_host.html", plugin=plugin, items=items, upload_dir=upload_dir)


@bp.route("/<int:item_id>/delete", methods=["POST"])
@admin_required
def delete(item_id: int):
    item = HostedAudio.query.get(item_id)
    if item:
        db.session.delete(item)
        db.session.commit()
        flash("Audio removed.", "info")
    return redirect(url_for("audio_host_plugin.manage"))


@bp.route('/files/<path:filename>')
def serve_file(filename: str):
    upload_dir = _resolve_upload_dir()
    if not upload_dir:
        abort(404)
    return send_from_directory(upload_dir, filename)


def register_plugin(app):
    with app.app_context():
        ensure_plugin_record("audio_host")
    app.register_blueprint(bp, url_prefix="/plugins/audio-host")
    return PluginInfo(
        name="audio_host",
        display_name="Hosted Audio & Embeds",
        blueprint=bp,
        url_prefix="/plugins/audio-host",
        manage_endpoint="audio_host_plugin.manage",
    )
