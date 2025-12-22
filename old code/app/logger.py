import logging
from logging.handlers import RotatingFileHandler

def init_logger(log_file_path=None):
    logger = logging.getLogger(name='ShowRecorder')

    if not any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        handler = RotatingFileHandler(log_file_path, maxBytes=1024*1024*5, backupCount=5)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    logger.setLevel(logging.INFO)
    logger.propagate = False

    return logger

