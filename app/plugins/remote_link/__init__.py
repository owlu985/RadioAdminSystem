import secrets

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app import db
from app.auth_utils import permission_required
from app.models import RemoteLinkSession
from app.plugins import PluginInfo, ensure_plugin_record

bp = Blueprint(
    "remote_link_plugin",
    __name__,
    template_folder="templates",
)


@bp.route("/", methods=["GET", "POST"])
@permission_required({"plugins:remote"})
def manage():
    plugin = ensure_plugin_record("remote_link")
    if request.method == "POST":
        label = (request.form.get("label") or "").strip()
        passcode = (request.form.get("passcode") or "").strip() or secrets.token_hex(3)
        notes = (request.form.get("notes") or "").strip() or None
        if not label:
            flash("Label is required", "danger")
        else:
            session = RemoteLinkSession(label=label, passcode=passcode, notes=notes)
            db.session.add(session)
            db.session.commit()
            flash("Remote link created", "success")
        return redirect(url_for("remote_link_plugin.manage"))

    sessions = RemoteLinkSession.query.order_by(RemoteLinkSession.created_at.desc()).all()
    return render_template("plugin_remote_link.html", sessions=sessions, plugin=plugin)


@bp.route("/<int:session_id>/delete", methods=["POST"])
@permission_required({"plugins:remote"})
def delete_session(session_id: int):
    session = RemoteLinkSession.query.get_or_404(session_id)
    db.session.delete(session)
    db.session.commit()
    flash("Session removed", "info")
    return redirect(url_for("remote_link_plugin.manage"))


def register_plugin(app):
    with app.app_context():
        ensure_plugin_record("remote_link")
    app.register_blueprint(bp, url_prefix="/plugins/remote-link")
    return PluginInfo(
        name="remote_link",
        display_name="Remote Studio Link",
        blueprint=bp,
        url_prefix="/plugins/remote-link",
        manage_endpoint="remote_link_plugin.manage",
        description="Low-latency send/return coordination with push-to-mute.",
    )
