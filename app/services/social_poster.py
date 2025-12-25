from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List

from app.models import SocialPost, db


def _platform_status(platforms: List[str], config) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    dry_run = (not config.get("SOCIAL_SEND_ENABLED", False)) or config.get("SOCIAL_DRY_RUN", True)

    for platform in platforms:
        platform = platform.lower()
        has_token = False
        if platform == "facebook":
            has_token = bool(config.get("SOCIAL_FACEBOOK_PAGE_TOKEN"))
        elif platform == "instagram":
            has_token = bool(config.get("SOCIAL_INSTAGRAM_TOKEN"))
        elif platform in {"twitter", "x"}:
            has_token = bool(config.get("SOCIAL_TWITTER_BEARER_TOKEN"))
        elif platform == "bluesky":
            has_token = bool(config.get("SOCIAL_BLUESKY_HANDLE") and config.get("SOCIAL_BLUESKY_PASSWORD"))

        if dry_run or not has_token:
            statuses[platform] = "simulated"
        else:
            statuses[platform] = "queued"

    return statuses


def send_social_post(post: SocialPost, config) -> Dict[str, str]:
    platforms = []
    if post.platforms:
        try:
            platforms = json.loads(post.platforms)
        except json.JSONDecodeError:
            platforms = [p.strip() for p in (post.platforms or "").split(",") if p.strip()]

    statuses = _platform_status(platforms, config)
    post.status = "simulated" if any(v == "simulated" for v in statuses.values()) else "queued"
    post.result_log = json.dumps(statuses)
    post.sent_at = datetime.utcnow()
    db.session.commit()
    return statuses
