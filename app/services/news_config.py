import json
import os
import json
from typing import List, Dict
from flask import current_app


def load_news_types() -> List[Dict]:
    path = current_app.config["NEWS_TYPES_CONFIG"]
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


def get_news_type(key: str) -> Dict | None:
    for item in load_news_types():
        if item.get("key") == key:
            return item
    return None
