import os
import shutil
from pathlib import Path
from typing import Optional

import requests
from flask import current_app

from app.logger import init_logger

logger = init_logger()


class RadioDJClient:
    """
    Minimal helper for interacting with RadioDJ's REST API (if available)
    and staging files into the RadioDJ import folder.
    """

    def __init__(self):
        self.base_url = current_app.config.get("RADIODJ_API_BASE_URL")
        self.api_key = current_app.config.get("RADIODJ_API_KEY")
        self.import_folder = Path(current_app.config.get("RADIODJ_IMPORT_FOLDER"))
        self.import_folder.mkdir(parents=True, exist_ok=True)
        nas_root = Path(current_app.config.get("NAS_ROOT"))
        nas_root.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def list_psas(self) -> list:
        if not self.enabled:
            logger.info("RadioDJ API disabled; returning empty PSA list.")
            return []
        resp = requests.get(f"{self.base_url}/psas", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def update_psa_metadata(self, psa_id: str, metadata: dict) -> dict:
        if not self.enabled:
            raise RuntimeError("RadioDJ API disabled")
        resp = requests.patch(
            f"{self.base_url}/psas/{psa_id}",
            headers=self._headers(),
            json=metadata,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def enable_psa(self, psa_id: str) -> dict:
        if not self.enabled:
            raise RuntimeError("RadioDJ API disabled")
        resp = requests.post(f"{self.base_url}/psas/{psa_id}/enable", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def disable_psa(self, psa_id: str) -> dict:
        if not self.enabled:
            raise RuntimeError("RadioDJ API disabled")
        resp = requests.post(f"{self.base_url}/psas/{psa_id}/disable", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def delete_psa(self, psa_id: str) -> dict:
        if not self.enabled:
            raise RuntimeError("RadioDJ API disabled")
        resp = requests.delete(f"{self.base_url}/psas/{psa_id}", headers=self._headers(), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def now_playing(self) -> Optional[dict]:
        """
        Fetch the current on-air metadata from RadioDJ (if enabled).
        Expected payload shape (best-effort):
        {
            "artist": str,
            "title": str,
            "album": str,
            "duration": float,
            "elapsed": float,
        }
        """
        if not self.enabled:
            return None
        try:
            resp = requests.get(f"{self.base_url}/nowplaying", headers=self._headers(), timeout=6)
            resp.raise_for_status()
            return resp.json() or {}
        except Exception as exc:  # noqa: BLE001
            logger.error("RadioDJ now playing fetch failed: %s", exc)
            return None

    def import_file(self, source_path: str, target_name: Optional[str] = None) -> Path:
        """
        Stage a file into the RadioDJ import folder (local handoff).
        """
        path = Path(source_path)
        if not path.exists():
            raise FileNotFoundError(source_path)

        target = self.import_folder / (target_name or path.name)
        shutil.copy(path, target)
        logger.info("Imported %s into RadioDJ import folder", target)
        return target


def import_news_or_calendar(kind: str) -> Path:
    """
    Copy the requested NAS file into the RadioDJ import folder.
    kind: 'news' | 'community_calendar'
    """
    client = RadioDJClient()
    if kind == "news":
        source = current_app.config["NAS_NEWS_FILE"]
        target_name = "wlmc_news.mp3"
    elif kind == "community_calendar":
        source = current_app.config["NAS_COMMUNITY_CALENDAR_FILE"]
        target_name = "wlmc_comm_calendar.mp3"
    else:
        raise ValueError(f"Unknown import kind: {kind}")

    return client.import_file(source, target_name=target_name)
