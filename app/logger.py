import logging
import os
from logging.handlers import RotatingFileHandler


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

def init_logger(log_file_path=None):
    logger = logging.getLogger(name='ShowRecorder')

    if log_file_path is None:
        # Fallback to a local file so Windows paths don't break when None is passed.
        log_file_path = os.path.join(os.getcwd(), "ShowRecorder.log")

    _prune_log_file(log_file_path)

    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        handler = RotatingFileHandler(log_file_path, maxBytes=1024*1024*5, backupCount=5)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(logging.INFO)
    logger.propagate = False

    return logger
