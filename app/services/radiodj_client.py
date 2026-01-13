import os
import shutil
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

import requests
from flask import current_app

from app.logger import init_logger

logger = init_logger()


def _coerce_float(val: Optional[str]) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


class RadioDJClient:
    """
    Minimal helper for interacting with RadioDJ's REST API (if available)
    and staging files into the RadioDJ import folder.
    """

    def __init__(self):
        self.base_url = self._normalize_base_url(current_app.config.get("RADIODJ_API_BASE_URL"))
        self.api_password = current_app.config.get("RADIODJ_API_PASSWORD") or current_app.config.get("RADIODJ_API_KEY")
        self.import_folder = Path(current_app.config.get("RADIODJ_IMPORT_FOLDER"))
        self.import_folder.mkdir(parents=True, exist_ok=True)
        nas_root = Path(current_app.config.get("NAS_ROOT"))
        nas_root.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_password)

    def _normalize_base_url(self, base_url: Optional[str]) -> Optional[str]:
        if not base_url:
            return None
        trimmed = base_url.strip().rstrip("/")
        return trimmed or None

    def _endpoint(self, path: str) -> str:
        if not self.base_url:
            raise RuntimeError("RadioDJ API disabled")
        return f"{self.base_url}/{path.lstrip('/')}"

    def _command(self, command: str, arg: Optional[str] = None) -> str:
        if not self.enabled:
            raise RuntimeError("RadioDJ API disabled")
        params = {"auth": self.api_password, "command": command}
        if arg is not None:
            params["arg"] = arg
        resp = requests.get(self._endpoint("opt"), params=params, timeout=10)
        resp.raise_for_status()
        return resp.text

    def _xml_to_dict(self, payload: str) -> dict:
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError:
            return {}
        return self._element_to_dict(root)

    def _element_to_dict(self, root: ElementTree.Element) -> dict:
        data: dict[str, str] = {}
        for child in root:
            tag = child.tag.split("}")[-1] if child.tag else ""
            if not tag:
                continue
            data[tag.lower()] = (child.text or "").strip()
        return data

    def list_psas(self) -> list:
        logger.info("RadioDJ PSA listing not supported by the plugin API.")
        return []

    def update_psa_metadata(self, psa_id: str, metadata: dict) -> dict:
        raise RuntimeError("RadioDJ PSA metadata updates are not supported by the plugin API")

    def enable_psa(self, psa_id: str) -> dict:
        raise RuntimeError("RadioDJ PSA enable is not supported by the plugin API")

    def disable_psa(self, psa_id: str) -> dict:
        raise RuntimeError("RadioDJ PSA disable is not supported by the plugin API")

    def delete_psa(self, psa_id: str) -> dict:
        raise RuntimeError("RadioDJ PSA delete is not supported by the plugin API")

    def search_tracks(self, keyword: str) -> list:
        logger.info("RadioDJ search is not supported by the plugin API.")
        return []

    def insert_track_top(self, track_id: str) -> None:
        self._command("LoadTrackToTop", str(track_id))

    def set_autodj(self, enabled: bool) -> dict:
        try:
            response = self._command("EnableAutoDJ", "1" if enabled else "0")
            return {"status": "ok", "enabled": enabled, "raw": response}
        except Exception as exc:  # noqa: BLE001
            logger.error("RadioDJ AutoDJ toggle failed: %s", exc)
            raise

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
            resp = requests.get(self._endpoint("npjson"), params={"auth": self.api_password}, timeout=6)
            if resp.ok:
                return resp.json() or {}
            resp = requests.get(self._endpoint("np"), params={"auth": self.api_password}, timeout=6)
            resp.raise_for_status()
            payload = self._xml_to_dict(resp.text)
            if not payload:
                return None
            return {
                "artist": payload.get("artist"),
                "title": payload.get("title"),
                "album": payload.get("album"),
                "duration": _coerce_float(payload.get("duration")),
                "elapsed": _coerce_float(payload.get("elapsed")),
                "year": payload.get("year"),
                "path": payload.get("path"),
                "raw": payload,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error("RadioDJ now playing fetch failed: %s", exc)
            return None

    def playlist(self) -> list[dict]:
        if not self.enabled:
            return []
        try:
            resp = requests.get(self._endpoint("p"), params={"auth": self.api_password}, timeout=10)
            resp.raise_for_status()
            root = ElementTree.fromstring(resp.text)
        except Exception as exc:  # noqa: BLE001
            logger.error("RadioDJ playlist fetch failed: %s", exc)
            return []
        items = []
        for child in root:
            payload = self._element_to_dict(child)
            if payload:
                items.append(payload)
        return items

    def playlist_item(self, index: int) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            resp = requests.get(
                self._endpoint("pitem"),
                params={"auth": self.api_password, "arg": index},
                timeout=10,
            )
            resp.raise_for_status()
            payload = self._xml_to_dict(resp.text)
        except Exception as exc:  # noqa: BLE001
            logger.error("RadioDJ playlist item fetch failed: %s", exc)
            return None
        return payload or None

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


def search_track_by_term(term: str) -> list:
    client = RadioDJClient()
    return client.search_tracks(term)


def insert_track_top(track_id: str) -> None:
    client = RadioDJClient()
    client.insert_track_top(track_id)
