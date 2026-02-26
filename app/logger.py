import logging
import os
from logging.handlers import RotatingFileHandler

from flask import current_app, has_app_context

from config import Config


def _prune_log_file(path: str, max_lines: int = 5000) -> None:
    """Trim the log file to the last ``max_lines`` to keep size predictable."""
    try:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
        if len(lines) <= max_lines:
            return
        tail = lines[-max_lines:]
        with open(path, "w", encoding="utf-8", errors="ignore") as fh:
            fh.writelines(tail)
    except Exception:
        # Best-effort pruning; do not block app startup on log maintenance.
        return

def _resolve_log_file_path(log_file_path: str | None = None) -> str:
    """Resolve the ShowRecorder log path from app config whenever possible."""
    if has_app_context():
        logs_dir = current_app.config.get("LOGS_DIR")
        if logs_dir:
            return os.path.join(logs_dir, "ShowRecorder.log")

    # App-context independent fallback: use configured default logs directory
    # instead of the process CWD to avoid accidental writes to "/ShowRecorder.log".
    if log_file_path:
        return log_file_path

    return os.path.join(Config.LOGS_DIR, "ShowRecorder.log")


def init_logger(log_file_path=None):
    logger = logging.getLogger(name='ShowRecorder')

    log_file_path = _resolve_log_file_path(log_file_path)

    log_dir = os.path.dirname(log_file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    _prune_log_file(log_file_path)

    existing_file_handler = next(
        (handler for handler in logger.handlers if isinstance(handler, RotatingFileHandler)),
        None,
    )

    if existing_file_handler and getattr(existing_file_handler, "baseFilename", None) != os.path.abspath(log_file_path):
        logger.removeHandler(existing_file_handler)
        try:
            existing_file_handler.close()
        except Exception:
            pass
        existing_file_handler = None

    if existing_file_handler is None:
        handler = RotatingFileHandler(log_file_path, maxBytes=1024*1024*5, backupCount=5)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(logging.INFO)
    logger.propagate = False

    return logger
