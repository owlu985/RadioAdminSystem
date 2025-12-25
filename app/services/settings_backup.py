from __future__ import annotations

import os
import shutil
from datetime import datetime
from typing import Optional

from flask import current_app

from app.logger import init_logger

logger = init_logger()


def backup_settings() -> Optional[str]:
    """Create a timestamped backup of user_config.json into a backup folder."""
    inst = current_app.instance_path
    user_config_path = os.path.join(inst, "user_config.json")
    if not os.path.exists(user_config_path):
        logger.warning("user_config.json not found; skipping settings backup")
        return None

    dirname = current_app.config.get("SETTINGS_BACKUP_DIRNAME", "settings_backups")
    backup_dir = os.path.join(inst, dirname)
    os.makedirs(backup_dir, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"user_config_{ts}.json")
    shutil.copyfile(user_config_path, dest)
    logger.info("Settings backed up to %s", dest)

    retention = int(current_app.config.get("SETTINGS_BACKUP_RETENTION", 10) or 0)
    if retention > 0:
        _prune_backups(backup_dir, retention)
    return dest


def _prune_backups(backup_dir: str, retention: int) -> None:
    files = sorted(
        [f for f in os.listdir(backup_dir) if f.startswith("user_config_")],
        reverse=True,
    )
    for stale in files[retention:]:
        try:
            os.remove(os.path.join(backup_dir, stale))
        except Exception:  # noqa: BLE001
            logger.warning("Failed to remove old backup %s", stale)
