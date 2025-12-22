from flask import current_app as app
from .logger import init_logger
import threading
import json
import os

config_lock = threading.Lock()
logger = None

def init_utils():
    global logger
    logger = init_logger()
    logger.info("Utils logger initialized.")

def update_user_config(updates):
    """Update the user configuration file and Flask's configuration."""

    config_path = os.path.join(app.instance_path, 'user_config.json')

    with config_lock:
        current_config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    current_config = json.load(f)
            except Exception as e:
                raise f"Error reading user configuration: {e}"

        current_config.update(updates)

        try:
            with open(config_path, 'w') as f:
                json.dump(current_config, f, indent=4)
        except Exception as e:
            logger.error(f"Error writing user configuration: {e}")

        app.config.update(current_config)
        logger.info(f"User configuration updated successfully with {updates}.")