import logging
import os
from logging.handlers import RotatingFileHandler

def init_logger(log_file_path=None):
    logger = logging.getLogger(name='ShowRecorder')

    if log_file_path is None:
        # Fallback to a local file so Windows paths don't break when None is passed.
        log_file_path = os.path.join(os.getcwd(), "ShowRecorder.log")

    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        handler = RotatingFileHandler(log_file_path, maxBytes=1024*1024*5, backupCount=5)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(logging.INFO)
    logger.propagate = False

    return logger
