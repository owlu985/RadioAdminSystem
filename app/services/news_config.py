import json
import os
from typing import List, Dict
from flask import current_app
from app.models import NewsType
from app.db_utils import table_exists


def _apply_defaults(items: List[Dict]) -> List[Dict]:
    """Ensure each news type has consistent metadata templates."""
    for item in items:
        meta = item.setdefault("metadata", {})
        meta.setdefault("artist", "WLMC Radio")
        meta.setdefault("album", item.get("label") or "WLMC News")
        meta.setdefault("title_template", f"{item.get('label', 'WLMC News').upper()} {{date}}")
        meta.setdefault("date_format", "%m-%d-%Y")
    return items


def _load_news_types_from_json() -> List[Dict]:
    path = current_app.config["NEWS_TYPES_CONFIG"]
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        data = json.load(f)
    if isinstance(data, list):
        data = _apply_defaults(data)
    return data


def load_news_types() -> List[Dict]:
    try:
        if not table_exists("news_type"):
            return _load_news_types_from_json()
        types = NewsType.query.order_by(NewsType.label).all()
    except Exception:  # noqa: BLE001
        return _load_news_types_from_json()

    if types:
        return [t.as_dict() for t in types]
    return _load_news_types_from_json()

