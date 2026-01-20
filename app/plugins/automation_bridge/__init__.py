import datetime as dt
import json
from typing import List

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app import db
from app.auth_utils import effective_permissions, permission_required
from app.models import AutomationRule
from app.plugins import PluginInfo, ensure_plugin_record
from app.services.radiodj_client import RadioDJClient, insert_track_top, search_track_by_term

bp = Blueprint(
    "automation_bridge_plugin",
    __name__,
    template_folder="templates",
)


def _parse_days(raw: List[str]) -> str:
    cleaned = []
    for val in raw:
        v = (val or "").strip().lower()
        if v:
            cleaned.append(v)
    return ",".join(cleaned)


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@bp.route("/", methods=["GET", "POST"])
@permission_required({"plugins:automation", "RDJ:read", "RDJ:write"})
def manage():
    perms = effective_permissions()
    can_rules = "*" in perms or "plugins:automation" in perms
    can_rdj_read = "*" in perms or "RDJ:read" in perms or "RDJ:write" in perms
    can_rdj_write = "*" in perms or "RDJ:write" in perms
    plugin = ensure_plugin_record("automation_bridge")
    if request.method == "POST":
        if not can_rules:
            flash("You do not have permission to manage automation rules.", "danger")
            return redirect(url_for("automation_bridge_plugin.manage"))
        name = (request.form.get("name") or "").strip()
        match_tag = (request.form.get("match_tag") or "").strip() or None
        days = _parse_days(request.form.getlist("days")) or None
        start_time = request.form.get("start_time") or None
        end_time = request.form.get("end_time") or None
        radiodj_id = (request.form.get("radiodj_id") or "").strip() or None
        radiodj_title = (request.form.get("radiodj_title") or "").strip() or None

        if not name:
            flash("Rule name is required", "danger")
            return redirect(url_for("automation_bridge_plugin.manage"))

        rule = AutomationRule(
            name=name,
            match_tag=match_tag,
            days_of_week=days,
            start_time=dt.datetime.strptime(start_time, "%H:%M").time() if start_time else None,
            end_time=dt.datetime.strptime(end_time, "%H:%M").time() if end_time else None,
            radiodj_id=radiodj_id,
            radiodj_title=radiodj_title,
            enabled=(request.form.get("enabled") or "1") == "1",
        )
        db.session.add(rule)
        db.session.commit()
        flash("Automation rule saved", "success")
        return redirect(url_for("automation_bridge_plugin.manage"))

    rules = AutomationRule.query.order_by(AutomationRule.created_at.desc()).all()
    rdj = RadioDJClient()
    status = {}
    playlist = []
    playlists = []
    rotations = []
    categories = []
    genres = []
    track_results = []
    track_item = None
    track_query = (request.args.get("track_q") or "").strip()
    track_subcategory = (request.args.get("track_subcategory") or "").strip()
    track_genre = (request.args.get("track_genre") or "").strip()
    track_id = (request.args.get("track_id") or "").strip()

    if can_rdj_read and rdj.enabled:
        status = rdj.status()
        playlist = rdj.playlists_main()
        playlists = rdj.playlists_list()
        rotations = rdj.rotations_list()
        categories = rdj.categories()
        genres = rdj.genres()
        if track_query:
            payload = json.dumps(
                {
                    "Keyword": track_query,
                    "Subcategory": _safe_int(track_subcategory),
                    "Genre": _safe_int(track_genre),
                    "StartIndex": 1,
                    "ResultsToShow": 100,
                }
            )
            track_results = rdj.tracks_search(payload)
        if track_id:
            track_item = rdj.track_item(track_id)

    return render_template(
        "plugin_automation_bridge.html",
        rules=rules,
        plugin=plugin,
        rdj_enabled=rdj.enabled,
        can_rules=can_rules,
        can_rdj_read=can_rdj_read,
        can_rdj_write=can_rdj_write,
        rdj_status=status,
        rdj_playlist=playlist,
        rdj_playlists=playlists,
        rdj_rotations=rotations,
        rdj_categories=categories,
        rdj_genres=genres,
        rdj_track_results=track_results,
        rdj_track_item=track_item,
        track_query=track_query,
        track_subcategory=track_subcategory,
        track_genre=track_genre,
        track_id=track_id,
    )


@bp.route("/rules/<int:rule_id>/toggle", methods=["POST"])
@permission_required({"plugins:automation"})
def toggle_rule(rule_id: int):
    rule = AutomationRule.query.get_or_404(rule_id)
    rule.enabled = not rule.enabled
    db.session.commit()
    flash(f"Rule '{rule.name}' is now {'enabled' if rule.enabled else 'disabled'}.", "info")
    return redirect(url_for("automation_bridge_plugin.manage"))


@bp.route("/rules/<int:rule_id>/delete", methods=["POST"])
@permission_required({"plugins:automation"})
def delete_rule(rule_id: int):
    rule = AutomationRule.query.get_or_404(rule_id)
    db.session.delete(rule)
    db.session.commit()
    flash("Rule removed.", "info")
    return redirect(url_for("automation_bridge_plugin.manage"))


@bp.route("/radiodj/search")
@permission_required({"plugins:automation"})
def radiodj_search():
    term = request.args.get("q", "").strip()
    results = search_track_by_term(term) if term else []
    return {"results": results}


@bp.route("/radiodj/insert", methods=["POST"])
@permission_required({"plugins:automation"})
def radiodj_insert():
    track_id = request.form.get("track_id")
    title = request.form.get("title")
    try:
        insert_track_top(track_id)
        flash(f"Requested insert of RadioDJ track {title or track_id}.", "success")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("RadioDJ insert failed: %s", exc)
        flash("Unable to insert track into RadioDJ playlist. Check API credentials.", "danger")
    return redirect(url_for("automation_bridge_plugin.manage"))


@bp.route("/radiodj/set-item", methods=["POST"])
@permission_required({"RDJ:write"})
def radiodj_set_item():
    command = (request.form.get("command") or "").strip()
    arg = (request.form.get("arg") or "").strip() or None
    if not command:
        flash("Command is required.", "danger")
        return redirect(url_for("automation_bridge_plugin.manage"))
    client = RadioDJClient()
    try:
        client.set_item(command, arg)
        flash(f"RadioDJ command sent: {command}.", "success")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("RadioDJ command failed: %s", exc)
        flash("RadioDJ command failed. Check API settings.", "danger")
    return redirect(url_for("automation_bridge_plugin.manage"))


@bp.route("/radiodj/playlist/action", methods=["POST"])
@permission_required({"RDJ:write"})
def radiodj_playlist_action():
    action = (request.form.get("action") or "").strip()
    index = (request.form.get("index") or "").strip()
    track_id = (request.form.get("track_id") or "").strip()
    playlist_id = (request.form.get("playlist_id") or "").strip()
    client = RadioDJClient()
    try:
        if action == "play" and index:
            client.set_item("PlayPlaylistTrack", index)
        elif action == "remove" and index:
            client.set_item("RemovePlaylistTrack", index)
        elif action == "clear":
            client.set_item("ClearPlaylist")
        elif action == "load_top" and track_id:
            client.set_item("LoadTrackToTop", track_id)
        elif action == "load_bottom" and track_id:
            client.set_item("LoadTrackToBottom", track_id)
        elif action == "load_playlist" and playlist_id:
            client.set_item("LoadPlaylist", playlist_id)
        else:
            flash("Invalid playlist action or missing data.", "danger")
            return redirect(url_for("automation_bridge_plugin.manage"))
        flash("Playlist command sent.", "success")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("RadioDJ playlist action failed: %s", exc)
        flash("RadioDJ playlist command failed.", "danger")
    return redirect(url_for("automation_bridge_plugin.manage"))


@bp.route("/radiodj/playlists/save", methods=["POST"])
@permission_required({"RDJ:write"})
def radiodj_playlists_save():
    action = (request.form.get("action") or "").strip()
    payload = (request.form.get("payload") or "").strip()
    playlist_id = (request.form.get("playlist_id") or "").strip()
    client = RadioDJClient()
    try:
        if action == "insert" and payload:
            client.playlist_insert(payload)
        elif action == "update" and payload:
            client.playlist_update(payload)
        elif action == "delete" and playlist_id:
            client.playlist_delete(playlist_id)
        else:
            flash("Invalid playlist save action.", "danger")
            return redirect(url_for("automation_bridge_plugin.manage"))
        flash("Playlist update sent.", "success")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("RadioDJ playlist save failed: %s", exc)
        flash("RadioDJ playlist update failed.", "danger")
    return redirect(url_for("automation_bridge_plugin.manage"))


@bp.route("/radiodj/rotations/save", methods=["POST"])
@permission_required({"RDJ:write"})
def radiodj_rotations_save():
    action = (request.form.get("action") or "").strip()
    payload = (request.form.get("payload") or "").strip()
    rotation_id = (request.form.get("rotation_id") or "").strip()
    client = RadioDJClient()
    try:
        if action == "insert" and payload:
            client.rotation_insert(payload)
        elif action == "update" and payload:
            client.rotation_update(payload)
        elif action == "delete" and rotation_id:
            client.rotation_delete(rotation_id)
        elif action == "load" and rotation_id:
            client.rotation_load(rotation_id)
        else:
            flash("Invalid rotation action.", "danger")
            return redirect(url_for("automation_bridge_plugin.manage"))
        flash("Rotation command sent.", "success")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("RadioDJ rotation command failed: %s", exc)
        flash("RadioDJ rotation command failed.", "danger")
    return redirect(url_for("automation_bridge_plugin.manage"))


@bp.route("/radiodj/categories/save", methods=["POST"])
@permission_required({"RDJ:write"})
def radiodj_categories_save():
    action = (request.form.get("action") or "").strip()
    payload = (request.form.get("payload") or "").strip()
    client = RadioDJClient()
    try:
        if action == "insert" and payload:
            client.category_insert(payload)
        elif action == "update" and payload:
            client.category_update(payload)
        elif action == "delete" and payload:
            client.category_delete(payload)
        else:
            flash("Invalid category action.", "danger")
            return redirect(url_for("automation_bridge_plugin.manage"))
        flash("Category update sent.", "success")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("RadioDJ category command failed: %s", exc)
        flash("RadioDJ category command failed.", "danger")
    return redirect(url_for("automation_bridge_plugin.manage"))


@bp.route("/radiodj/genres/save", methods=["POST"])
@permission_required({"RDJ:write"})
def radiodj_genres_save():
    action = (request.form.get("action") or "").strip()
    payload = (request.form.get("payload") or "").strip()
    genre_id = (request.form.get("genre_id") or "").strip()
    client = RadioDJClient()
    try:
        if action == "insert" and payload:
            client.genre_insert(payload)
        elif action == "update" and payload:
            client.genre_update(payload)
        elif action == "delete" and genre_id:
            client.genre_delete(genre_id)
        else:
            flash("Invalid genre action.", "danger")
            return redirect(url_for("automation_bridge_plugin.manage"))
        flash("Genre update sent.", "success")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("RadioDJ genre command failed: %s", exc)
        flash("RadioDJ genre command failed.", "danger")
    return redirect(url_for("automation_bridge_plugin.manage"))


@bp.route("/radiodj/tracks/save", methods=["POST"])
@permission_required({"RDJ:write"})
def radiodj_tracks_save():
    action = (request.form.get("action") or "").strip()
    payload = (request.form.get("payload") or "").strip()
    track_id = (request.form.get("track_id") or "").strip()
    client = RadioDJClient()
    try:
        if action == "insert" and payload:
            client.track_insert(payload)
        elif action == "update" and payload:
            client.track_update(payload)
        elif action == "delete" and track_id:
            client.track_delete(track_id)
        else:
            flash("Invalid track action.", "danger")
            return redirect(url_for("automation_bridge_plugin.manage"))
        flash("Track update sent.", "success")
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error("RadioDJ track command failed: %s", exc)
        flash("RadioDJ track command failed.", "danger")
    return redirect(url_for("automation_bridge_plugin.manage"))


def register_plugin(app):
    with app.app_context():
        ensure_plugin_record("automation_bridge")
    app.register_blueprint(bp, url_prefix="/plugins/automation-bridge")
    return PluginInfo(
        name="automation_bridge",
        display_name="Automation Bridge + RadioDJ",
        blueprint=bp,
        url_prefix="/plugins/automation-bridge",
        manage_endpoint="automation_bridge_plugin.manage",
        description="Rule-based ingest and RadioDJ playlist inserts.",
    )
