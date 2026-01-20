import gzip
import os
import shutil
from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, Response
from werkzeug.utils import secure_filename
from app.auth_utils import admin_required, permission_required
from app.logger import init_logger
from app.models import db, NewsType, NewsCast
from app.utils import update_user_config
from app.services.news_config import load_news_types

news_bp = Blueprint("news", __name__, url_prefix="/news")
logger = init_logger()

DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _news_storage_dir(news_type_key: str):
    root = current_app.config["NAS_ROOT"]
    path = os.path.join(root, "news", news_type_key)
    os.makedirs(path, exist_ok=True)
    return path


def _news_base_name(filename: str) -> str:
    return filename.replace(".mp3", "")


def _news_scripts_dir(news_type_key: str):
    base = _news_storage_dir(news_type_key)
    path = os.path.join(base, "scripts")
    os.makedirs(path, exist_ok=True)
    return path


def _ensure_news_types() -> list[NewsType]:
    types = NewsType.query.order_by(NewsType.label).all()
    if types:
        return types
    legacy = load_news_types()
    if not legacy:
        return []
    for item in legacy:
        db.session.add(
            NewsType(
                key=item.get("key"),
                label=item.get("label") or item.get("key"),
                filename=item.get("filename") or f"{item.get('key')}.mp3",
                frequency=(item.get("frequency") or "daily").lower(),
                rotation_day=item.get("rotation_day"),
                active_days=item.get("active_days"),
                artist=item.get("metadata", {}).get("artist"),
                album=item.get("metadata", {}).get("album"),
                title_template=item.get("metadata", {}).get("title_template"),
                date_format=item.get("metadata", {}).get("date_format"),
                is_active=True,
            )
        )
    db.session.commit()
    return NewsType.query.order_by(NewsType.label).all()


def _active_days_set(news_type: NewsType) -> set[int]:
    if news_type.frequency == "daily":
        return set(range(7))
    if news_type.frequency == "weekly":
        return {news_type.rotation_day} if news_type.rotation_day is not None else set()
    if news_type.active_days:
        try:
            return {int(day) for day in news_type.active_days.split(",") if day.strip() != ""}
        except ValueError:
            return set()
    return set()


def _is_air_day(news_type: NewsType, target_date: date) -> bool:
    return target_date.weekday() in _active_days_set(news_type)


def _next_air_dates(news_type: NewsType, count: int = 3) -> list[date]:
    dates: list[date] = []
    cursor = date.today()
    while len(dates) < count:
        if _is_air_day(news_type, cursor):
            dates.append(cursor)
        cursor = cursor + timedelta(days=1)
    return dates


def _get_cast(news_type: NewsType, target_date: date) -> NewsCast | None:
    return NewsCast.query.filter_by(news_type_id=news_type.id, air_date=target_date).first()


def _script_text(script_path: str) -> str:
    if not os.path.exists(script_path):
        return ""
    if script_path.endswith(".gz"):
        with gzip.open(script_path, "rt", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    with open(script_path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _script_preview(script_path: str, limit: int = 240) -> str:
    content = _script_text(script_path).strip().replace("\n", " ")
    return content[:limit] + ("…" if len(content) > limit else "")


def activate_news_for_date(target_date: date, news_key: str | None = None) -> bool:
    """
    Copy <base>_<date>.mp3 into its configured destination for RadioDJ consumption.
    Returns True if a file was activated.
    """
    news_types = _ensure_news_types()
    targets = [t for t in news_types if (news_key is None or t.key == news_key)]
    activated = False
    for nt in targets:
        if not _is_air_day(nt, target_date):
            continue
        cast = _get_cast(nt, target_date)
        if not cast or not cast.audio_filename:
            continue
        storage_dir = _news_storage_dir(nt.key)
        audio_path = os.path.join(storage_dir, cast.audio_filename)
        if not os.path.exists(audio_path):
            continue
        output_dir = nt.output_dir or current_app.config["NAS_ROOT"]
        os.makedirs(output_dir, exist_ok=True)
        dest = os.path.join(output_dir, nt.filename)
        shutil.copy(audio_path, dest)
        logger.info("Activated %s file for %s -> %s", nt.key, target_date, dest)
        activated = True
    return activated


def _build_news_entries(casts: list[NewsCast]) -> list[dict]:
    """Attach preview/download URLs for audio/scripts if they exist."""
    enhanced = []
    for cast in casts:
        nt = cast.news_type
        date_str = cast.air_date.isoformat()
        audio_url = None
        script_url = None
        preview = None

        if cast.audio_filename:
            audio_path = os.path.join(_news_storage_dir(nt.key), cast.audio_filename)
            if os.path.exists(audio_path):
                audio_url = url_for("news.get_audio", news_key=nt.key, date_str=date_str)

        if cast.script_filename:
            script_path = os.path.join(_news_scripts_dir(nt.key), cast.script_filename)
            if os.path.exists(script_path):
                script_url = url_for("news.get_script", news_key=nt.key, date_str=date_str)
                if cast.script_filename.endswith(".txt.gz"):
                    preview = _script_preview(script_path)

        enhanced.append(
            {
                "id": cast.id,
                "date": cast.air_date,
                "key": nt.key,
                "label": nt.label,
                "audio_url": audio_url,
                "script_url": script_url,
                "script_preview": preview,
                "audio_ready": bool(audio_url),
                "script_ready": bool(script_url),
            }
        )
    return enhanced


@news_bp.route("/upload", methods=["GET", "POST"])
@permission_required({"news:edit"})
def upload_news():
    news_types = _ensure_news_types()
    return render_template("news_upload.html", today=date.today(), news_types=news_types)


@news_bp.route("/upload_audio", methods=["POST"])
@permission_required({"news:edit"})
def upload_audio():
    news_type_id = request.form.get("news_type_id", type=int)
    target_date_str = request.form.get("date")
    file = request.files.get("file")
    news_type = NewsType.query.get(news_type_id) if news_type_id else None
    if not news_type:
        flash("Invalid news type.", "danger")
        return redirect(url_for("news.upload_news"))
    if not file or not file.filename:
        flash("No audio file selected.", "danger")
        return redirect(url_for("news.upload_news"))
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        flash("Invalid date for audio upload.", "danger")
        return redirect(url_for("news.upload_news"))

    base = _news_base_name(news_type.filename)
    storage_dir = _news_storage_dir(news_type.key)
    filename = secure_filename(f"{base}_{target_date.isoformat()}.mp3")
    path = os.path.join(storage_dir, filename)
    file.save(path)
    cast = _get_cast(news_type, target_date) or NewsCast(news_type_id=news_type.id, air_date=target_date)
    cast.audio_filename = filename
    cast.updated_at = datetime.utcnow()
    db.session.add(cast)
    db.session.commit()
    flash(f"Uploaded audio for {news_type.label} ({target_date}).", "success")
    if target_date == date.today():
        activate_news_for_date(target_date, news_key=news_type.key)
    return redirect(url_for("news.upload_news"))


@news_bp.route("/upload_script", methods=["POST"])
@permission_required({"news:edit"})
def upload_script():
    news_type_id = request.form.get("news_type_id", type=int)
    target_date_str = request.form.get("date")
    script_file = request.files.get("script_file")
    news_type = NewsType.query.get(news_type_id) if news_type_id else None
    if not news_type:
        flash("Invalid news type.", "danger")
        return redirect(url_for("news.upload_news"))
    if not script_file or not script_file.filename:
        flash("No script selected.", "danger")
        return redirect(url_for("news.upload_news"))
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        flash("Invalid date for script upload.", "danger")
        return redirect(url_for("news.upload_news"))

    base = _news_base_name(news_type.filename)
    scripts_dir = _news_scripts_dir(news_type.key)
    ext = os.path.splitext(script_file.filename)[1] or ".txt"
    script_name = secure_filename(f"{base}_{target_date.isoformat()}{ext}.gz")
    script_path = os.path.join(scripts_dir, script_name)
    with gzip.open(script_path, "wb") as gz:
        gz.write(script_file.read())
    cast = _get_cast(news_type, target_date) or NewsCast(news_type_id=news_type.id, air_date=target_date)
    cast.script_filename = script_name
    cast.updated_at = datetime.utcnow()
    db.session.add(cast)
    db.session.commit()
    flash(f"Uploaded script for {news_type.label} ({target_date}).", "success")
    return redirect(url_for("news.upload_news"))


@news_bp.route("/script/edit", methods=["GET", "POST"])
@permission_required({"news:edit"})
def edit_script():
    news_type_id = request.values.get("news_type_id", type=int)
    target_date_str = request.values.get("date") or date.today().isoformat()
    news_type = NewsType.query.get(news_type_id) if news_type_id else None
    if not news_type:
        flash("Invalid news type.", "danger")
        return redirect(url_for("news.upload_news"))
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        flash("Invalid date for script editor.", "danger")
        return redirect(url_for("news.upload_news"))

    base = _news_base_name(news_type.filename)
    scripts_dir = _news_scripts_dir(news_type.key)
    script_name = secure_filename(f"{base}_{target_date.isoformat()}.txt.gz")
    script_path = os.path.join(scripts_dir, script_name)

    if request.method == "POST":
        content = request.form.get("content", "")
        with gzip.open(script_path, "wt", encoding="utf-8") as handle:
            handle.write(content)
        cast = _get_cast(news_type, target_date) or NewsCast(news_type_id=news_type.id, air_date=target_date)
        cast.script_filename = script_name
        cast.updated_at = datetime.utcnow()
        db.session.add(cast)
        db.session.commit()
        flash(f"Saved script for {news_type.label} ({target_date}).", "success")
        return redirect(url_for("news.edit_script", news_type_id=news_type.id, date=target_date.isoformat()))

    content = _script_text(script_path) if os.path.exists(script_path) else ""
    return render_template(
        "news_editor.html",
        news_type=news_type,
        target_date=target_date,
        content=content,
    )


@news_bp.route("/settings", methods=["GET", "POST"])
@permission_required({"news:edit"})
def news_settings():
    types = _ensure_news_types()
    edit_id = request.args.get("edit_id", type=int)
    edit_type = NewsType.query.get(edit_id) if edit_id else None
    if request.method == "POST":
        action = request.form.get("action", "create")
        if action == "events_feed":
            feed_url = (request.form.get("events_feed_url") or "").strip() or None
            update_user_config({"LANDMARK_EVENTS_FEED_URL": feed_url})
            flash("Events feed URL updated.", "success")
            return redirect(url_for("news.news_settings"))
        if action == "delete":
            type_id = request.form.get("news_type_id", type=int)
            news_type = NewsType.query.get(type_id)
            if news_type:
                db.session.delete(news_type)
                db.session.commit()
                flash("News type deleted.", "warning")
            return redirect(url_for("news.news_settings"))

        key = (request.form.get("key") or "").strip().lower().replace(" ", "_")
        label = (request.form.get("label") or "").strip()
        filename = (request.form.get("filename") or "").strip()
        output_dir = (request.form.get("output_dir") or "").strip()
        frequency = (request.form.get("frequency") or "daily").lower()
        rotation_day = request.form.get("rotation_day", type=int)
        active_days = request.form.getlist("active_days")
        artist = (request.form.get("artist") or "").strip()
        album = (request.form.get("album") or "").strip()
        title_template = (request.form.get("title_template") or "").strip()
        date_format = (request.form.get("date_format") or "").strip()
        is_active = request.form.get("is_active") == "on"
        type_id = request.form.get("news_type_id", type=int)

        if not key or not label or not filename:
            flash("Key, label, and filename are required.", "danger")
            return redirect(url_for("news.news_settings"))

        if type_id:
            news_type = NewsType.query.get(type_id)
        else:
            news_type = None

        if not news_type:
            existing = NewsType.query.filter_by(key=key).first()
            if existing:
                flash("A news type with that key already exists.", "danger")
                return redirect(url_for("news.news_settings"))
            news_type = NewsType(key=key)

        news_type.key = key
        news_type.label = label
        news_type.filename = filename
        news_type.output_dir = output_dir or None
        news_type.frequency = frequency
        news_type.rotation_day = rotation_day if frequency == "weekly" else None
        news_type.active_days = ",".join(active_days) if frequency == "custom" else None
        news_type.artist = artist or None
        news_type.album = album or None
        news_type.title_template = title_template or None
        news_type.date_format = date_format or None
        news_type.is_active = is_active
        news_type.updated_at = datetime.utcnow()

        db.session.add(news_type)
        db.session.commit()
        flash("News type saved.", "success")
        return redirect(url_for("news.news_settings"))

    return render_template(
        "news_settings.html",
        types=types,
        day_labels=DAY_LABELS,
        events_feed_url=current_app.config.get("LANDMARK_EVENTS_FEED_URL"),
        edit_type=edit_type,
    )


@news_bp.route("/dashboard")
@permission_required({"news:view"})
def news_dashboard():
    """Dashboard overview for upcoming and archived newscasts with previews."""
    news_types = _ensure_news_types()
    casts = NewsCast.query.order_by(NewsCast.air_date.desc()).all()
    entries = _build_news_entries(casts)
    today = date.today()
    upcoming = [e for e in entries if e["date"] >= today]
    archive = [e for e in entries if e["date"] < today]
    status_cards = []
    for nt in [t for t in news_types if t.is_active]:
        upcoming_dates = _next_air_dates(nt)
        next_date = upcoming_dates[0] if upcoming_dates else None
        next_cast = _get_cast(nt, next_date) if next_date else None
        audio_ready = bool(next_cast and next_cast.audio_filename)
        script_ready = bool(next_cast and next_cast.script_filename)
        status_cards.append(
            {
                "type": nt,
                "next_date": next_date,
                "schedule_label": ", ".join(DAY_LABELS[d] for d in sorted(_active_days_set(nt))) or "Not scheduled",
                "audio_ready": audio_ready,
                "script_ready": script_ready,
            }
        )
    return render_template(
        "news_dashboard.html",
        upcoming=upcoming,
        archive=archive,
        news_types=news_types,
        status_cards=status_cards,
        day_labels=DAY_LABELS,
        events_feed_url=current_app.config.get("LANDMARK_EVENTS_FEED_URL"),
    )


@news_bp.route("/audio/<news_key>/<date_str>")
@admin_required
def get_audio(news_key, date_str):
    news_type = NewsType.query.filter_by(key=news_key).first()
    if not news_type:
        flash("Invalid news type.", "danger")
        return redirect(url_for("news.news_dashboard"))
    base = _news_base_name(news_type.filename)
    storage_dir = _news_storage_dir(news_key)
    filename = f"{base}_{date_str}.mp3"
    path = os.path.join(storage_dir, filename)
    if not os.path.exists(path):
        flash("Audio not found.", "warning")
        return redirect(url_for("news.news_dashboard"))
    return current_app.send_file(path)


@news_bp.route("/script/<news_key>/<date_str>")
@admin_required
def get_script(news_key, date_str):
    news_type = NewsType.query.filter_by(key=news_key).first()
    if not news_type:
        flash("Invalid news type.", "danger")
        return redirect(url_for("news.news_dashboard"))
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date.", "danger")
        return redirect(url_for("news.news_dashboard"))
    cast = _get_cast(news_type, target_date)
    if not cast or not cast.script_filename:
        flash("Script not found.", "warning")
        return redirect(url_for("news.news_dashboard"))
    scripts_dir = _news_scripts_dir(news_key)
    safe_name = secure_filename(cast.script_filename)
    path = os.path.join(scripts_dir, safe_name)
    if not os.path.exists(path):
        flash("Script not found.", "warning")
        return redirect(url_for("news.news_dashboard"))
    if safe_name.endswith(".gz"):
        content = _script_text(path)
        return Response(content, mimetype="text/plain")
    return current_app.send_file(path)
