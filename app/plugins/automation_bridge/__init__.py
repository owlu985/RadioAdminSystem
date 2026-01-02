import datetime as dt
from typing import List

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app import db
from app.auth_utils import permission_required
from app.models import AutomationRule
from app.plugins import PluginInfo, ensure_plugin_record
from app.services.radiodj_client import search_track_by_term, insert_track_top

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


@bp.route("/", methods=["GET", "POST"])
@permission_required({"plugins:automation"})
def manage():
    plugin = ensure_plugin_record("automation_bridge")
    if request.method == "POST":
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
        )
        db.session.add(rule)
        db.session.commit()
        flash("Automation rule saved", "success")
        return redirect(url_for("automation_bridge_plugin.manage"))

    rules = AutomationRule.query.order_by(AutomationRule.created_at.desc()).all()
    return render_template("plugin_automation_bridge.html", rules=rules, plugin=plugin)


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
