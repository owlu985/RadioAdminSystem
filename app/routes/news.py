import os
import shutil
from datetime import datetime, date
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from werkzeug.utils import secure_filename
from app.auth_utils import admin_required
from app.logger import init_logger

news_bp = Blueprint("news", __name__, url_prefix="/news")
logger = init_logger()


def _news_storage_dir():
    root = current_app.config["NAS_ROOT"]
    path = os.path.join(root, "news")
    os.makedirs(path, exist_ok=True)
    return path


def activate_news_for_date(target_date: date) -> bool:
    """
    Copy wlmc_news_<date>.mp3 into wlmc_news.mp3 for RadioDJ consumption.
    Returns True if a file was activated.
    """
    storage_dir = _news_storage_dir()
    dated_name = f"wlmc_news_{target_date.isoformat()}.mp3"
    dated_path = os.path.join(storage_dir, dated_name)
    if not os.path.exists(dated_path):
        return False

    dest = current_app.config["NAS_NEWS_FILE"]
    shutil.copy(dated_path, dest)
    logger.info("Activated news file for %s -> %s", target_date, dest)
    return True


@news_bp.route("/upload", methods=["GET", "POST"])
@admin_required
def upload_news():
    """
    Admin panel to upload dated newscasts.
    """
    if request.method == "POST":
        file = request.files.get("file")
        target_date_str = request.form.get("date") or date.today().isoformat()
        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid date.", "danger")
            return redirect(url_for("news.upload_news"))

        if not file or file.filename == "":
            flash("Please select a file to upload.", "danger")
            return redirect(url_for("news.upload_news"))

        storage_dir = _news_storage_dir()
        filename = f"wlmc_news_{target_date.isoformat()}.mp3"
        path = os.path.join(storage_dir, secure_filename(filename))
        file.save(path)
        flash(f"Uploaded news cast for {target_date}.", "success")
        logger.info("Uploaded news file to %s", path)

        # If it's for today, immediately activate.
        if target_date == date.today():
            activate_news_for_date(target_date)

        return redirect(url_for("news.upload_news"))

    return render_template("news_upload.html", today=date.today())
