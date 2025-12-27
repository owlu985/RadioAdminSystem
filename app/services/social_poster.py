from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List

import requests
from requests import Response

from app.models import SocialPost, db


def _post_facebook(message: str, image_url: str | None, token: str) -> str:
    """Post to a Facebook page feed using a page access token."""
    url = "https://graph.facebook.com/v18.0/me/feed"
    payload = {"message": message, "access_token": token}
    if image_url:
        # The Graph API accepts link/picture; link is simplest for page feed posts.
        payload["link"] = image_url
    resp: Response = requests.post(url, data=payload, timeout=15)
    if resp.ok:
        return "sent"
    try:
        err = resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        err = resp.text
    return f"error: {err}"


def _post_bluesky(message: str, handle: str, app_password: str) -> str:
    """Post to Bluesky using app password credentials."""
    try:
        auth_resp = requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": handle, "password": app_password},
            timeout=15,
        )
        if not auth_resp.ok:
            return f"error: login {auth_resp.status_code}"
        session = auth_resp.json()
        jwt = session.get("accessJwt")
        did = session.get("did")
        if not jwt or not did:
            return "error: missing session token"

        create_resp = requests.post(
            "https://bsky.social/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "repo": did,
                "collection": "app.bsky.feed.post",
                "record": {
                    "text": message,
                    "createdAt": datetime.utcnow().isoformat() + "Z",
                },
            },
            timeout=15,
        )
        if create_resp.ok:
            return "sent"
        return f"error: post {create_resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def _post_twitter(message: str, bearer_token: str) -> str:
    """Post to X (Twitter) using an OAuth2 bearer token with tweet.write scope."""
    try:
        resp = requests.post(
            "https://api.twitter.com/2/tweets",
            headers={"Authorization": f"Bearer {bearer_token}"},
            json={"text": message},
            timeout=15,
        )
        if resp.ok:
            return "sent"
        try:
            data = resp.json()
            err = data.get("errors") or data.get("detail") or resp.text
        except Exception:  # noqa: BLE001
            err = resp.text
        return f"error: {err}"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def _platform_status(platforms: List[str], config) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    dry_run = (not config.get("SOCIAL_SEND_ENABLED", False)) or config.get("SOCIAL_DRY_RUN", True)

    for platform in platforms:
        platform = platform.lower()
        token_state = "missing"
        if platform == "facebook":
            token_state = "present" if config.get("SOCIAL_FACEBOOK_PAGE_TOKEN") else "missing"
        elif platform == "instagram":
            # Posting to Instagram requires additional account IDs not captured in current settings; keep simulated.
            token_state = "unsupported"
        elif platform in {"twitter", "x"}:
            token_state = "present" if config.get("SOCIAL_TWITTER_BEARER_TOKEN") else "missing"
        elif platform == "bluesky":
            token_state = "present" if (config.get("SOCIAL_BLUESKY_HANDLE") and config.get("SOCIAL_BLUESKY_PASSWORD")) else "missing"

        if dry_run or token_state != "present":
            if token_state == "unsupported":
                statuses[platform] = "unsupported"
            elif token_state == "missing":
                statuses[platform] = "skipped (no token)"
            else:
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

    if not platforms:
        platforms = ["facebook", "instagram", "twitter", "bluesky"]

    dry_run = (not config.get("SOCIAL_SEND_ENABLED", False)) or config.get("SOCIAL_DRY_RUN", True)
    statuses = _platform_status(platforms, config)

    # Perform live sends where possible
    if not dry_run:
        for platform, state in statuses.items():
            if state != "queued":
                continue
            try:
                if platform == "facebook":
                    statuses[platform] = _post_facebook(
                        post.content,
                        post.image_url,
                        config.get("SOCIAL_FACEBOOK_PAGE_TOKEN"),
                    )
                elif platform in {"twitter", "x"}:
                    statuses[platform] = _post_twitter(
                        post.content,
                        config.get("SOCIAL_TWITTER_BEARER_TOKEN"),
                    )
                elif platform == "bluesky":
                    statuses[platform] = _post_bluesky(
                        post.content,
                        config.get("SOCIAL_BLUESKY_HANDLE"),
                        config.get("SOCIAL_BLUESKY_PASSWORD"),
                    )
                else:
                    statuses[platform] = "unsupported"
            except Exception as exc:  # noqa: BLE001
                statuses[platform] = f"error: {exc}"

    if statuses:
        if all(val == "sent" for val in statuses.values()):
            post.status = "sent"
        elif any(val.startswith("error") for val in statuses.values()):
            post.status = "error"
        elif any(val.startswith("simulated") for val in statuses.values()):
            post.status = "simulated"
        else:
            post.status = "queued"
    else:
        post.status = "simulated"

    post.result_log = json.dumps(statuses)
    post.sent_at = datetime.utcnow()
    db.session.commit()
    return statuses
