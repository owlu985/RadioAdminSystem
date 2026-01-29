from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, time, timedelta
import time
from app.db_utils import table_exists
from flask import current_app
from .logger import init_logger
from .models import db, Show, MarathonEvent
from app.services.detection import probe_and_record
from app.services.radiodj_client import import_news_or_calendar
from app.services.health import record_failure
from app.services.settings_backup import backup_settings, backup_data_snapshot
from app.services.stream_monitor import record_icecast_stat
from app.services import api_cache
from app.services.library_index import start_library_index_job
from .utils import update_user_config, show_display_title, show_primary_host
from datetime import date as date_cls
import ffmpeg
import mutagen
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
            schedule_news_rotation()
            schedule_icecast_analytics()
            schedule_settings_backup()
            schedule_radiodj_now_playing()
            schedule_library_index_job()

def refresh_schedule():
    """Refresh the scheduler with the latest shows from the database."""
    try:
        if table_exists("show"):
            scheduler.remove_all_jobs()
            for show in Show.query.all():
                schedule_recording(show)
            logger.info("Schedule refreshed with latest shows.")
            schedule_stream_probe()
            now = datetime.utcnow()
            for event in MarathonEvent.query.filter(
                MarathonEvent.end_time >= now, MarathonEvent.canceled_at.is_(None)
            ).all():
                _schedule_marathon_jobs(event)
            api_cache.invalidate("schedule")
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

def _active_marathon():
    now = datetime.utcnow()
    return MarathonEvent.query.filter(
        MarathonEvent.status.in_(["pending", "running"]),
        MarathonEvent.start_time <= now,
        MarathonEvent.end_time > now,
        MarathonEvent.canceled_at.is_(None),
    ).first()


def _update_marathon_status(event_id: int, status: str, job_ids=None, canceled=False):
    event = MarathonEvent.query.get(event_id)
    if not event:
        return
    event.status = status
    if job_ids is not None:
        event.job_ids = job_ids
    if canceled:
        event.canceled_at = datetime.utcnow()
    db.session.commit()


def _format_host_list(hosts):
    cleaned = [host.strip() for host in hosts if host and host.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} & {cleaned[1]}"
    return f"{', '.join(cleaned[:-1])}, and {cleaned[-1]}"


def _apply_recording_tags(path, show_name, hosts, recorded_at):
    try:
        audio = mutagen.File(path, easy=True)
        if not audio:
            logger.warning("Unable to tag recording %s (unsupported format).", path)
            return
        if audio.tags is None:
            audio.add_tags()
        host_text = _format_host_list(hosts)
        title = f"{show_name} ({recorded_at.strftime('%m-%d-%Y')})"
        comment = (
            f"Official Logger File for {show_name}"
            f"{' with ' + host_text if host_text else ''} on WLMC Radio. "
            "The log for this show, if applicable, is available through the show/log management portal."
        )
        audio["title"] = [title]
        audio["artist"] = [host_text] if host_text else []
        audio["album"] = [show_name]
        audio["comment"] = [comment]
        audio.save()
        logger.info("Applied metadata tags to recording %s.", path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to tag recording %s: %s", path, exc)


def record_stream(stream_url, duration, output_file, config_file_path, marathon_event_id=None, chunk_end=None,
                  label=None, show_name=None, hosts=None, show_start_date=None, show_end_date=None):
    """Records the stream using FFmpeg."""

    ctx = flask_app.app_context() if flask_app else None
    if ctx:
        ctx.push()

    try:
        with open(config_file_path, 'r') as file:
            config = json.load(file)
    except FileNotFoundError:
        config = {}
    paused = config.get('PAUSE_SHOWS_RECORDING', False)
    if paused is True:
        logger.info("Recording paused. Skipping recording.")
        return

    if marathon_event_id is None:
        active_marathon = _active_marathon()
        if active_marathon:
            logger.info("Skipping show recording due to active marathon %s", active_marathon.name)
            return

    if show_start_date or show_end_date:
        today = datetime.now().date()
        if show_start_date and today < show_start_date:
            logger.info("Skipping recording before show start date %s.", show_start_date)
            return
        if show_end_date and today > show_end_date:
            logger.info("Skipping recording after show end date %s.", show_end_date)
            return


    recorded_at = datetime.now()
    output_file = f"{output_file}_{recorded_at.strftime('%m-%d-%y')}_RAWDATA.mp3"
    start_time = recorded_at.strftime('%H-%M-%S')
    try:
        if marathon_event_id:
            _update_marathon_status(marathon_event_id, "running")
        for attempt in (1, 2):
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
                if show_name:
                    _apply_recording_tags(output_file, show_name, hosts or [], recorded_at)
                if attempt == 2:
                    record_failure("recorder", reason="retry_success", restarted=True)
                return
            except ffmpeg.Error as e:
                err_msg = e.stderr.decode() if getattr(e, "stderr", None) else str(e)
                record_failure("recorder", reason=err_msg, restarted=False)
                logger.error(f"FFmpeg error (attempt {attempt}): {err_msg}")
            except Exception as exc:  # noqa: BLE001
                record_failure("recorder", reason=str(exc), restarted=False)
                logger.error(f"Recording error (attempt {attempt}): {exc}")

            if attempt == 1 and (flask_app and flask_app.config.get("SELF_HEAL_ENABLED", True)):
                logger.warning("Retrying recording after failure...")
                time.sleep(1)
                continue
            break
        if marathon_event_id:
            event = _active_marathon()
            if event and chunk_end and chunk_end >= event.end_time:
                _update_marathon_status(marathon_event_id, "completed")
    finally:
        if ctx:
            ctx.pop()

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

    if show.start_date and show.end_date and show.start_date > show.end_date:
        logger.warning("Skipping show %s due to start date after end date.", show.id)
        return

    start_time = datetime.combine(show.start_date, show.start_time)
    end_time = datetime.combine(show.start_date, show.end_time)

    if show.end_time <= show.start_time:
        end_time += timedelta(days=1)

    duration = (end_time - start_time).total_seconds()
    stream_url = current_app.config['STREAM_URL']

    display_name = show_display_title(show)
    safe_name = display_name.replace(" ", "_")
    hosts = []
    if show.djs:
        hosts = [f"{dj.first_name} {dj.last_name}".strip() for dj in show.djs]
    elif show.host_first_name or show.host_last_name:
        hosts = [f"{show.host_first_name} {show.host_last_name}".strip()]

    if current_app.config['AUTO_CREATE_SHOW_FOLDERS']:
        show_folder = os.path.join(current_app.config['OUTPUT_FOLDER'], display_name)
        if not os.path.exists(show_folder):
            os.mkdir(show_folder)
    else:
        show_folder = current_app.config['OUTPUT_FOLDER']

    output_file = os.path.join(show_folder, safe_name)
    user_config_path = os.path.join(current_app.instance_path, 'user_config.json')
    schedule_end = datetime.combine(show.end_date, show.end_time)
    if show.end_time <= show.start_time:
        schedule_end += timedelta(days=1)

    try:
        scheduler.add_job(
            record_stream, 'cron',
            day_of_week=show.days_of_week, hour=show.start_time.hour, minute=show.start_time.minute,
            args=[
                stream_url,
                duration,
                output_file,
                user_config_path,
                None,
                None,
                None,
                display_name,
                hosts,
                show.start_date,
                show.end_date,
            ],
            start_date=start_time,
            end_date=schedule_end,
        )
        logger.info(f"Recording scheduled for show {show.id}.")

        if show.is_temporary:
            scheduler.add_job(
                delete_show, 'date',
                run_date=schedule_end + timedelta(hours=1),
                args=[show.id]
            )
            logger.info(f"Deletion scheduled for temporary show {show.id} after last airing.")
    except Exception as e:
        logger.error(f"Error scheduling recording for show {show.id}: {e}")


def _schedule_marathon_jobs(event: MarathonEvent):
    stream_url = flask_app.config["STREAM_URL"]
    base_folder = os.path.join(flask_app.config["OUTPUT_FOLDER"], "Marathons", event.safe_name)
    os.makedirs(base_folder, exist_ok=True)
    user_config_path = os.path.join(flask_app.instance_path, "user_config.json")

    # Clear existing jobs for this event before rescheduling
    for job_id in (event.job_ids or "").split(','):
        if job_id:
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass

    job_ids: list[str] = []
    current = event.start_time
    while current < event.end_time:
        chunk_end = min(current + timedelta(hours=event.chunk_hours), event.end_time)
        label = f"{event.safe_name}_{current.strftime('%a_%I%p').lstrip('0')}_{chunk_end.strftime('%I%p').lstrip('0')}"
        output_file = os.path.join(base_folder, label)
        job_id = f"marathon_{event.id}_{int(current.timestamp())}"
        try:
            scheduler.add_job(
                record_stream,
                "date",
                id=job_id,
                run_date=current,
                args=[
                    stream_url,
                    (chunk_end - current).total_seconds(),
                    output_file,
                    user_config_path,
                    event.id,
                    chunk_end,
                    None,
                    None,
                    None,
                    None,
                    None,
                ],
                replace_existing=True,
            )
            job_ids.append(job_id)
            logger.info("Scheduled marathon chunk %s from %s to %s", job_id, current, chunk_end)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to schedule marathon chunk %s: %s", job_id, exc)
        current = chunk_end

    event.job_ids = ",".join(job_ids)
    db.session.commit()


def schedule_marathon_event(name: str, start_dt: datetime, end_dt: datetime, chunk_hours: int = 2):
    """
    Schedule a temporary marathon recording window in fixed-size chunks.
    Files are written under OUTPUT_FOLDER/Marathons/<name>/Name_day_start_end_RAWDATA.mp3
    """
    if flask_app is None:
        return
    if end_dt <= start_dt:
        return
    safe_name = name.replace(" ", "_")

    event = MarathonEvent(
        name=name,
        safe_name=safe_name,
        start_time=start_dt,
        end_time=end_dt,
        chunk_hours=chunk_hours,
        status="pending",
    )
    db.session.add(event)
    db.session.commit()

    _schedule_marathon_jobs(event)
    api_cache.invalidate("schedule")


def cancel_marathon_event(event_id: int):
    event = MarathonEvent.query.get(event_id)
    if not event:
        return False
    job_ids = (event.job_ids or "").split(',') if event.job_ids else []
    now = datetime.utcnow()
    for job_id in job_ids:
        job = scheduler.get_job(job_id.strip())
        if job and job.next_run_time and job.next_run_time > now:
            scheduler.remove_job(job_id)
    _update_marathon_status(event_id, "cancelled", canceled=True)
    api_cache.invalidate("schedule")
    return True


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


def schedule_icecast_analytics():
    if flask_app is None:
        return
    interval_minutes = flask_app.config.get("ICECAST_ANALYTICS_INTERVAL_MINUTES", 5)
    try:
        scheduler.add_job(
            run_icecast_analytics_job,
            "interval",
            minutes=interval_minutes,
            id="icecast_analytics_job",
            replace_existing=True,
        )
        logger.info("Icecast analytics job scheduled.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error scheduling icecast analytics job: {e}")


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


def schedule_settings_backup():
    if flask_app is None:
        return
    hours = flask_app.config.get("SETTINGS_BACKUP_INTERVAL_HOURS", 12)
    try:
        scheduler.add_job(
            run_settings_backup_job,
            "interval",
            hours=hours,
            id="settings_backup_job",
            replace_existing=True,
        )
        logger.info("Settings backup job scheduled.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error scheduling settings backup: {e}")


def schedule_radiodj_now_playing():
    """Poll RadioDJ now-playing and update cache/Icecast metadata."""
    if flask_app is None:
        return
    try:
        scheduler.add_job(
            run_radiodj_now_playing_job,
            "interval",
            seconds=8,
            id="radiodj_now_playing_job",
            replace_existing=True,
        )
        logger.info("RadioDJ now-playing job scheduled.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error scheduling RadioDJ now-playing job: {e}")


def schedule_library_index_job(run_now: bool = False):
    if flask_app is None:
        return
    hours = flask_app.config.get("LIBRARY_INDEX_INTERVAL_HOURS", 12)
    try:
        scheduler.add_job(
            run_library_index_job,
            "interval",
            hours=hours,
            id="library_index_job",
            replace_existing=True,
        )
        logger.info("Library index job scheduled.")
        if run_now:
            run_library_index_job()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error scheduling library index job: {e}")


def schedule_news_rotation():
    """Daily job to activate the news file for the current date if available."""
    if flask_app is None:
        return
    try:
        scheduler.add_job(
            run_news_rotation_job,
            "cron",
            hour=0,
            minute=5,
            id="news_rotation_job",
            replace_existing=True,
        )
        logger.info("News rotation job scheduled.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error scheduling news rotation job: {e}")


def run_news_rotation_job():
    if flask_app is None:
        return
    from app.routes.news import activate_news_for_date  # lazy import to avoid cycles
    with flask_app.app_context():
        activate_news_for_date(date_cls.today())


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
        try:
            probe_and_record()
        except Exception as exc:  # noqa: BLE001
            logger.error("Probe job crashed: %s", exc)
            record_failure("stream_probe", reason=str(exc), restarted=False)
            if flask_app.config.get("SELF_HEAL_ENABLED", True):
                logger.warning("Retrying probe after crash...")
                time.sleep(1)
                try:
                    probe_and_record()
                    record_failure("stream_probe", reason="job_retry_success", restarted=True)
                except Exception as exc2:  # noqa: BLE001
                    logger.error("Probe retry failed: %s", exc2)


def run_icecast_analytics_job():
    if flask_app is None:
        return
    with flask_app.app_context():
        try:
            record_icecast_stat()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Icecast analytics sample failed: %s", exc)


def run_settings_backup_job():
    if flask_app is None:
        return
    with flask_app.app_context():
        try:
            backup_settings()
            backup_data_snapshot()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Settings backup failed: %s", exc)


def run_radiodj_now_playing_job():
    if flask_app is None:
        return
    from app.routes.api import _get_cached_radiodj_nowplaying, _push_icecast_metadata
    from app.utils import get_current_show, show_display_title, show_host_names
    with flask_app.app_context():
        try:
            show = get_current_show()
            if show:
                show_name = show_display_title(show)
                host_label = show_host_names(show)
                _push_icecast_metadata({
                    "artist": host_label,
                    "title": show_name,
                    "song": f"{show_name} with {host_label}",
                })
            _get_cached_radiodj_nowplaying(push_icecast=not show)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RadioDJ now-playing update failed: %s", exc)


def run_library_index_job():
    if flask_app is None:
        return
    with flask_app.app_context():
        start_library_index_job()
