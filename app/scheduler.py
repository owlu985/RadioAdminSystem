from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, time, timedelta
from sqlalchemy import inspect
from flask import current_app
from .logger import init_logger
from .models import db, Show
from app.services.detection import probe_and_record
from app.services.radiodj_client import import_news_or_calendar
from .utils import update_user_config
import ffmpeg
import json
import os

scheduler = BackgroundScheduler()
logger = None
flask_app = None

def init_scheduler(app):
    """Initialize and start the scheduler with the Flask app context."""

    global logger, flask_app
    flask_app = app
    logger = init_logger()
    logger.info("Scheduler logger initialized.")

    if not scheduler.running:
        scheduler.start()
        with app.app_context():
            logger.info("Scheduler initialized and started.")
            refresh_schedule()
            schedule_stream_probe()
            schedule_nas_watch()

def refresh_schedule():
    """Refresh the scheduler with the latest shows from the database."""
    try:
        if 'show' in inspect(db.engine).get_table_names():
            scheduler.remove_all_jobs()
            for show in Show.query.all():
                schedule_recording(show)
            logger.info("Schedule refreshed with latest shows.")
            schedule_stream_probe()
    except Exception as e:
        logger.error(f"Error refreshing schedule: {e}")

def pause_shows_until(date):
    """Pause all recordings until a specified date."""

    try:
        scheduler.add_job(
            update_user_config, 'date',
            run_date=date,
            args=[{"PAUSE_SHOWS_RECORDING": False, "PAUSE_END_DATE": None}]
        )
        logger.info(f"Recordings resume job added.")
    except Exception as e:
        logger.error(f"Error adding resume jobs: {e}")

def record_stream(stream_url, duration, output_file, config_file_path):
    """Records the stream using FFmpeg."""

    with open(config_file_path, 'r') as file:
        config = json.load(file)
    if config['PAUSE_SHOWS_RECORDING'] is True:
        logger.info("Recording paused. Skipping recording.")
        return


    output_file = f"{output_file}_{datetime.now().strftime('%m-%d-%y')}_RAWDATA.mp3"
    start_time = datetime.now().strftime('%H-%M-%S')
    try:
        (
            ffmpeg
            .input(stream_url, t=duration)
            .output(output_file, acodec='copy')
            .overwrite_output()
            .run()
        )
        logger.info(f"Recording started for {output_file}.")
        logger.info(f"Start time:{start_time}.")
    except ffmpeg.Error as e:
        logger.error(f"FFFmpeg error: {e.stderr.decode()}")

def delete_show(show_id):
    """Delete a show from the database."""

    try:
        with db.app_context():
            show = Show.query.get(show_id)
            if show:
                db.session.delete(show)
                db.session.commit()
        logger.info(f"Show with ID {show_id} deleted.")
        refresh_schedule()
    except Exception as e:
        logger.error(f"Error deleting show {show_id}: {e}")

def schedule_recording(show):
    """Schedules the recurring recording and deletion of a show."""

    start_time = datetime.combine(show.start_date, show.start_time)
    end_time = datetime.combine(show.start_date, show.end_time)

    if show.end_time <= show.start_time:
        end_time += timedelta(days=1)

    duration = (end_time - start_time).total_seconds()
    stream_url = current_app.config['STREAM_URL']

    display_name = show.show_name or f"{show.host_first_name} {show.host_last_name}"
    safe_name = display_name.replace(" ", "_")

    if current_app.config['AUTO_CREATE_SHOW_FOLDERS']:
        show_folder = os.path.join(current_app.config['OUTPUT_FOLDER'], display_name)
        if not os.path.exists(show_folder):
            os.mkdir(show_folder)
    else:
        show_folder = current_app.config['OUTPUT_FOLDER']

    output_file = os.path.join(show_folder, safe_name)
    user_config_path = os.path.join(current_app.instance_path, 'user_config.json')

    try:
        scheduler.add_job(
            record_stream, 'cron',
            day_of_week=show.days_of_week, hour=show.start_time.hour, minute=show.start_time.minute,
            args=[stream_url, duration, output_file, user_config_path],
            start_date=start_time,
            end_date=show.end_date,
        )
        logger.info(f"Recording scheduled for show {show.id}.")

        scheduler.add_job(
            delete_show, 'date',
            run_date=show.end_date + timedelta(days=1),
            args=[show.id]
        )
        logger.info(f"Deletion scheduled for show {show.id} after last airing.")
    except Exception as e:
        logger.error(f"Error scheduling recording for show {show.id}: {e}")


def schedule_stream_probe():
    """Schedule periodic stream probing for silence/automation detection."""
    if flask_app is None:
        return

    interval_minutes = flask_app.config.get("STREAM_PROBE_INTERVAL_MINUTES", 5)

    try:
        scheduler.add_job(
            run_stream_probe_job,
            "interval",
            minutes=interval_minutes,
            id="stream_probe_job",
            replace_existing=True,
        )
        logger.info("Stream probe job scheduled.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error scheduling stream probe job: {e}")


def schedule_nas_watch():
    """Monitor NAS news/calendar files and import to RadioDJ folder."""
    if flask_app is None:
        return
    interval = 5  # minutes
    try:
        scheduler.add_job(
            run_nas_watch_job,
            "interval",
            minutes=interval,
            id="nas_watch_job",
            replace_existing=True,
        )
        logger.info("NAS watch job scheduled.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error scheduling NAS watch job: {e}")


def run_nas_watch_job():
    if flask_app is None:
        return
    with flask_app.app_context():
        for kind in ("news", "community_calendar"):
            try:
                import_news_or_calendar(kind)
            except Exception as exc:  # noqa: BLE001
                logger.warning("NAS watch import failed for %s: %s", kind, exc)


def run_stream_probe_job():
    if flask_app is None:
        return

    with flask_app.app_context():
        probe_and_record()
