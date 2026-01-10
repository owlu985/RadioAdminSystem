import json
import secrets
import time

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app import db
from app.auth_utils import permission_required
from app.models import RemoteLinkSession, RemoteLinkSignal
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


@bp.route("/join", methods=["GET", "POST"])
def join():
    if request.method == "POST":
        code = (request.form.get("passcode") or "").strip()
        name = (request.form.get("display_name") or "Guest").strip() or "Guest"
        session_row = RemoteLinkSession.query.filter_by(passcode=code).first()
        if not session_row:
            flash("Invalid code", "danger")
        else:
            allowed = session.setdefault("remote_link_access", {})
            allowed[str(session_row.id)] = {"passcode": code, "name": name}
            session.modified = True
            return redirect(url_for("remote_link_plugin.room", session_id=session_row.id))

    return render_template("plugin_remote_link_join.html")


@bp.route("/session/<int:session_id>")
def room(session_id: int):
    allowed = session.get("remote_link_access", {})
    if str(session_id) not in allowed:
        flash("Join with your session code to continue", "warning")
        return redirect(url_for("remote_link_plugin.join"))
    session_row = RemoteLinkSession.query.get_or_404(session_id)
    return render_template(
        "plugin_remote_link_session.html",
        session_row=session_row,
        display_name=allowed[str(session_id)].get("name") or "Guest",
    )


@bp.route("/<int:session_id>/delete", methods=["POST"])
@permission_required({"plugins:remote"})
def delete_session(session_id: int):
    session = RemoteLinkSession.query.get_or_404(session_id)
    db.session.delete(session)
    db.session.commit()
    flash("Session removed", "info")
    return redirect(url_for("remote_link_plugin.manage"))


def _ensure_access(session_id: int):
    allowed = session.get("remote_link_access", {})
    if str(session_id) not in allowed:
        return None
    return allowed[str(session_id)]


@bp.route("/api/signals", methods=["POST"])
def post_signal():
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get("session_id")
    sender_id = (data.get("sender_id") or "").strip()
    signal_type = (data.get("type") or "").strip()
    payload = data.get("payload")
    sender_label = (data.get("sender_label") or "").strip() or None

    if not session_id or not sender_id or not signal_type or payload is None:
        return jsonify({"error": "missing required fields"}), 400

    access = _ensure_access(int(session_id))
    if not access:
        return jsonify({"error": "unauthorized"}), 403

    session_row = RemoteLinkSession.query.get(session_id)
    if not session_row:
        return jsonify({"error": "invalid session"}), 404

    as_json = json.dumps(payload)
    signal = RemoteLinkSignal(
        session_id=session_id,
        sender_id=sender_id,
        sender_label=sender_label,
        signal_type=signal_type,
        payload=as_json,
    )
    db.session.add(signal)

    cutoff = time.time() - 3600
    db.session.query(RemoteLinkSignal).filter(
        RemoteLinkSignal.created_at < db.func.to_timestamp(cutoff)
    ).delete()

    db.session.commit()
    return jsonify({"id": signal.id})


@bp.route("/api/signals", methods=["GET"])
def get_signals():
    session_id = request.args.get("session_id", type=int)
    since_id = request.args.get("since_id", type=int, default=0)
    self_id = request.args.get("self_id")
    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    access = _ensure_access(int(session_id))
    if not access:
        return jsonify({"error": "unauthorized"}), 403

    q = (
        RemoteLinkSignal.query.filter(RemoteLinkSignal.session_id == session_id)
        .filter(RemoteLinkSignal.id > since_id)
        .order_by(RemoteLinkSignal.id.asc())
    )
    results = []
    for sig in q.all():
        if self_id and sig.sender_id == self_id:
            continue
        results.append(
            {
                "id": sig.id,
                "type": sig.signal_type,
                "sender_id": sig.sender_id,
                "sender_label": sig.sender_label,
                "payload": json.loads(sig.payload),
            }
        )
    return jsonify({"signals": results})


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
