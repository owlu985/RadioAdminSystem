from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.auth_utils import admin_required
from app.models import HostedAudio
from app.plugins import PluginInfo, ensure_plugin_record
from app import db

bp = Blueprint(
    "audio_host_plugin",
    __name__,
    template_folder="templates",
)


@bp.route("/", methods=["GET", "POST"])
@admin_required
def manage():
    plugin = ensure_plugin_record("audio_host")
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        description = (request.form.get("description") or "").strip() or None
        file_url = (request.form.get("file_url") or "").strip()
        backdrop_url = (request.form.get("backdrop_url") or "").strip() or None
        if not title or not file_url:
            flash("Title and audio URL are required.", "danger")
        else:
            db.session.add(HostedAudio(title=title, description=description, file_url=file_url, backdrop_url=backdrop_url))
            db.session.commit()
            flash("Hosted audio saved.", "success")
        return redirect(url_for("audio_host_plugin.manage"))

    items = HostedAudio.query.order_by(HostedAudio.created_at.desc()).all()
    return render_template("plugin_audio_host.html", plugin=plugin, items=items)


@bp.route("/<int:item_id>/delete", methods=["POST"])
@admin_required
def delete(item_id: int):
    item = HostedAudio.query.get(item_id)
    if item:
        db.session.delete(item)
        db.session.commit()
        flash("Audio removed.", "info")
    return redirect(url_for("audio_host_plugin.manage"))


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
