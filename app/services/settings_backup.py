from __future__ import annotations

import os
import shutil
import json
from datetime import datetime, timedelta
from typing import Optional

from flask import current_app

from app.logger import init_logger
from app.models import DJ, DJDisciplinary, Show

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


def backup_data_snapshot() -> Optional[str]:
    """Backup DJs, shows (schedule), and disciplinary records into a JSON snapshot."""
    inst = current_app.instance_path
    dirname = current_app.config.get("DATA_BACKUP_DIRNAME", "data_backups")
    backup_dir = os.path.join(inst, dirname)
    os.makedirs(backup_dir, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"rams_data_{ts}.json")

    try:
        payload = {
            "generated_at": datetime.utcnow().isoformat(),
            "djs": [
                {
                    "id": dj.id,
                    "first_name": dj.first_name,
                    "last_name": dj.last_name,
                    "bio": dj.bio,
                    "photo_url": dj.photo_url,
                }
                for dj in DJ.query.order_by(DJ.id).all()
            ],
            "shows": [
                {
                    "id": show.id,
                    "show_name": show.show_name,
                    "host_first_name": show.host_first_name,
                    "host_last_name": show.host_last_name,
                    "genre": show.genre,
                    "description": show.description,
                    "is_regular_host": show.is_regular_host,
                    "start_date": show.start_date.isoformat(),
                    "end_date": show.end_date.isoformat(),
                    "start_time": show.start_time.isoformat(),
                    "end_time": show.end_time.isoformat(),
                    "days_of_week": show.days_of_week,
                    "dj_ids": [dj.id for dj in show.djs],
                }
                for show in Show.query.order_by(Show.id).all()
            ],
            "disciplinary": [
                {
                    "id": rec.id,
                    "dj_id": rec.dj_id,
                    "dj_name": f"{rec.dj.first_name} {rec.dj.last_name}" if rec.dj else None,
                    "issued_at": rec.issued_at.isoformat(),
                    "severity": rec.severity,
                    "notes": rec.notes,
                    "action_taken": rec.action_taken,
                    "resolved": rec.resolved,
                    "created_by": rec.created_by,
                }
                for rec in DJDisciplinary.query.order_by(DJDisciplinary.issued_at.desc()).all()
            ],
        }

        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        logger.info("Data snapshot backed up to %s", dest)
    except Exception as exc:  # noqa: BLE001
        logger.error("Data backup failed: %s", exc)
        return None

    retention_days = int(current_app.config.get("DATA_BACKUP_RETENTION_DAYS", 60) or 0)
    if retention_days > 0:
        _prune_backups_by_age(backup_dir, retention_days)

    return dest


def _prune_backups_by_age(backup_dir: str, retention_days: int) -> None:
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    for fname in os.listdir(backup_dir):
        if not fname.endswith(".json"):
            continue
        full = os.path.join(backup_dir, fname)
        try:
            mtime = datetime.utcfromtimestamp(os.path.getmtime(full))
            if mtime < cutoff:
                os.remove(full)
        except Exception:  # noqa: BLE001
            logger.warning("Failed to evaluate backup file %s for pruning", fname)
