import os
import shutil
from datetime import datetime, date
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from werkzeug.utils import secure_filename
from app.auth_utils import admin_required, permission_required
from app.logger import init_logger
from app.services.news_config import load_news_types, get_news_type

news_bp = Blueprint("news", __name__, url_prefix="/news")
logger = init_logger()


def _news_storage_dir(news_type_key: str):
    root = current_app.config["NAS_ROOT"]
    path = os.path.join(root, "news", news_type_key)
    os.makedirs(path, exist_ok=True)
    return path


def _news_scripts_dir(news_type_key: str):
    base = _news_storage_dir(news_type_key)
    path = os.path.join(base, "scripts")
    os.makedirs(path, exist_ok=True)
    return path


def _list_news_queue(news_types):
    queue = []
    for nt in news_types:
        storage_dir = _news_storage_dir(nt["key"])
        base = nt["filename"].replace(".mp3", "")
        entries = []
        for fname in os.listdir(storage_dir):
            if fname.startswith(base + "_") and fname.endswith(".mp3"):
                try:
                    date_str = fname.replace(base + "_", "").replace(".mp3", "")
                    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                script_dir = _news_scripts_dir(nt["key"])
                scripts = [f for f in os.listdir(script_dir) if f.startswith(base + "_" + date_str)]
                entries.append(
                    {
                        "date": dt,
                        "audio": os.path.join(storage_dir, fname),
                        "scripts": [os.path.join(script_dir, s) for s in scripts],
                        "key": nt["key"],
                        "label": nt.get("label", nt["key"]),
                        "base": base,
                    }
                )
        entries.sort(key=lambda e: e["date"])
        queue.extend(entries)
    queue.sort(key=lambda e: (e["date"], e["label"]))
    return queue


def activate_news_for_date(target_date: date, news_key: str | None = None) -> bool:
    """
    Copy <base>_<date>.mp3 into its configured destination for RadioDJ consumption.
    Returns True if a file was activated.
    """
    news_types = load_news_types()
    targets = [t for t in news_types if (news_key is None or t["key"] == news_key)]
    activated = False
    for nt in targets:
        rotation_day = nt.get("rotation_day")
        frequency = (nt.get("frequency") or "daily").lower()
        if rotation_day is not None and target_date.weekday() != rotation_day:
            # Only rotate on the configured weekday
            continue
        storage_dir = _news_storage_dir(nt["key"])
        base = nt["filename"].replace(".mp3", "")
        dated_path = None

        if frequency == "weekly":
            candidates = []
            for fname in os.listdir(storage_dir):
                if not fname.startswith(f"{base}_") or not fname.endswith(".mp3"):
                    continue
                try:
                    dt = datetime.strptime(fname.replace(base + "_", "").replace(".mp3", ""), "%Y-%m-%d").date()
                except ValueError:
                    continue
                if rotation_day is not None and dt.weekday() != rotation_day:
                    continue
                if dt <= target_date:
                    candidates.append((dt, os.path.join(storage_dir, fname)))
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                dated_path = candidates[0][1]
        else:
            dated_name = f"{base}_{target_date.isoformat()}.mp3"
            candidate = os.path.join(storage_dir, dated_name)
            if os.path.exists(candidate):
                dated_path = candidate

        if not dated_path:
            continue

        dest = os.path.join(current_app.config["NAS_ROOT"], nt["filename"])
        shutil.copy(dated_path, dest)
        logger.info("Activated %s file for %s -> %s", nt["key"], target_date, dest)
        activated = True
    return activated


def _build_news_entry_urls(entries):
    """Attach preview/download URLs for audio/scripts if they exist."""
    enhanced = []
    for item in entries:
        date_str = item["date"].isoformat()
        audio_url = None
        if item.get("audio") and os.path.exists(item["audio"]):
            audio_url = url_for("news.get_audio", news_key=item["key"], date_str=date_str)

        scripts = []
        for script_path in item.get("scripts", []):
            if os.path.exists(script_path):
                scripts.append(
                    {
                        "name": os.path.basename(script_path),
                        "url": url_for(
                            "news.get_script",
                            news_key=item["key"],
                            date_str=date_str,
                            filename=os.path.basename(script_path),
                        ),
                    }
                )

        enhanced.append({**item, "audio_url": audio_url, "scripts": scripts})
    return enhanced


@news_bp.route("/upload", methods=["GET", "POST"])
@permission_required({"news:edit"})
def upload_news():
    """
    Admin panel to upload dated newscasts.
    """
    news_types = load_news_types()

    if request.method == "POST":
        file = request.files.get("file")
        script_file = request.files.get("script_file")
        target_date_str = request.form.get("date") or date.today().isoformat()
        news_key = request.form.get("news_type") or (news_types[0]["key"] if news_types else None)
        nt = get_news_type(news_key) if news_key else None
        if not nt:
            flash("Invalid news type.", "danger")
            return redirect(url_for("news.upload_news"))
        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid date.", "danger")
            return redirect(url_for("news.upload_news"))

        if not file and not script_file:
            flash("Please select at least one file to upload.", "danger")
            return redirect(url_for("news.upload_news"))

        base = nt["filename"].replace(".mp3", "")
        if file and file.filename:
            storage_dir = _news_storage_dir(nt["key"])
            filename = f"{base}_{target_date.isoformat()}.mp3"
            path = os.path.join(storage_dir, secure_filename(filename))
            file.save(path)
            flash(f"Uploaded {nt['label']} cast for {target_date}.", "success")
            logger.info("Uploaded %s file to %s", nt["key"], path)
            if target_date == date.today():
                activate_news_for_date(target_date, news_key=nt["key"])

        if script_file and script_file.filename:
            scripts_dir = _news_scripts_dir(nt["key"])
            script_name = f"{base}_{target_date.isoformat()}{os.path.splitext(script_file.filename)[1] or '.txt'}"
            script_path = os.path.join(scripts_dir, secure_filename(script_name))
            script_file.save(script_path)
            flash(f"Uploaded script for {nt['label']} ({target_date}).", "success")

        return redirect(url_for("news.upload_news"))

    queue = _list_news_queue(news_types)
    queue = _build_news_entry_urls(queue)
    return render_template("news_upload.html", today=date.today(), news_types=news_types, queue=queue)


@news_bp.route("/upload_script_only", methods=["POST"])
@permission_required({"news:edit"})
def upload_script_only():
    news_key = request.form.get("news_type")
    target_date_str = request.form.get("date")
    script_file = request.files.get("script_file")
    news_types = load_news_types()
    nt = get_news_type(news_key) if news_key else None
    if not nt:
        flash("Invalid news type.", "danger")
        return redirect(url_for("news.news_dashboard"))
    if not script_file or not script_file.filename:
        flash("No script selected.", "danger")
        return redirect(url_for("news.news_dashboard"))
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        flash("Invalid date for script upload.", "danger")
        return redirect(url_for("news.news_dashboard"))

    base = nt["filename"].replace(".mp3", "")
    scripts_dir = _news_scripts_dir(nt["key"])
    script_name = f"{base}_{target_date.isoformat()}{os.path.splitext(script_file.filename)[1] or '.txt'}"
    script_path = os.path.join(scripts_dir, secure_filename(script_name))
    script_file.save(script_path)
    flash(f"Uploaded script for {nt['label']} ({target_date}).", "success")
    return redirect(url_for("news.news_dashboard"))


@news_bp.route("/dashboard")
@permission_required({"news:view"})
def news_dashboard():
    """Dashboard overview for upcoming and archived newscasts with previews."""
    news_types = load_news_types()
    entries = _build_news_entry_urls(_list_news_queue(news_types))
    today = date.today()
    upcoming = [e for e in entries if e["date"] >= today]
    archive = [e for e in entries if e["date"] < today]
    return render_template(
        "news_dashboard.html",
        upcoming=upcoming,
        archive=archive,
        news_types=news_types,
    )


@news_bp.route("/audio/<news_key>/<date_str>")
@admin_required
def get_audio(news_key, date_str):
    nt = get_news_type(news_key)
    if not nt:
        flash("Invalid news type.", "danger")
        return redirect(url_for("news.news_dashboard"))
    base = nt["filename"].replace(".mp3", "")
    storage_dir = _news_storage_dir(news_key)
    filename = f"{base}_{date_str}.mp3"
    path = os.path.join(storage_dir, filename)
    if not os.path.exists(path):
        flash("Audio not found.", "warning")
        return redirect(url_for("news.news_dashboard"))
    return current_app.send_file(path)


@news_bp.route("/script/<news_key>/<date_str>/<path:filename>")
@admin_required
def get_script(news_key, date_str, filename):
    nt = get_news_type(news_key)
    if not nt:
        flash("Invalid news type.", "danger")
        return redirect(url_for("news.news_dashboard"))
    scripts_dir = _news_scripts_dir(news_key)
    safe_name = secure_filename(filename)
    path = os.path.join(scripts_dir, safe_name)
    if not os.path.exists(path):
        flash("Script not found.", "warning")
        return redirect(url_for("news.news_dashboard"))
    return current_app.send_file(path)
