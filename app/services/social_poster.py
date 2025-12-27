from __future__ import annotations

import json
import mimetypes
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

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


def _post_bluesky(
    message: str,
    handle: str,
    app_password: str,
    image_bytes: Optional[bytes] = None,
    image_mime: Optional[str] = None,
) -> str:
    """Post to Bluesky using app password credentials (supports optional image)."""
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

        embed = None
        if image_bytes:
            upload_resp = requests.post(
                "https://bsky.social/xrpc/com.atproto.repo.uploadBlob",
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Content-Type": image_mime or "application/octet-stream",
                },
                data=image_bytes,
                timeout=15,
            )
            if upload_resp.ok:
                blob = upload_resp.json().get("blob")
                if blob:
                    embed = {
                        "$type": "app.bsky.embed.images",
                        "images": [
                            {
                                "image": blob,
                                "alt": (message or "RAMS upload")[:300],
                            }
                        ],
                    }
            else:
                # Continue with text-only post if upload fails.
                pass

        payload = {
            "repo": did,
            "collection": "app.bsky.feed.post",
            "record": {
                "text": message,
                "createdAt": datetime.utcnow().isoformat() + "Z",
            },
        }
        if embed:
            payload["record"]["embed"] = embed

        create_resp = requests.post(
            "https://bsky.social/xrpc/com.atproto.repo.createRecord",
            headers={"Authorization": f"Bearer {jwt}"},
            json=payload,
            timeout=15,
        )
        if create_resp.ok:
            return "sent"
        return f"error: post {create_resp.status_code}"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def _post_twitter(
    message: str,
    user_bearer: Optional[str] = None,
    consumer_key: Optional[str] = None,
    consumer_secret: Optional[str] = None,
    access_token: Optional[str] = None,
    access_secret: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    image_mime: Optional[str] = None,
) -> str:
    """Post to X (Twitter) using either OAuth2 user-context bearer or OAuth1a user tokens.

    Images are sent when OAuth1a user tokens are available; OAuth2 user-context posts
    fall back to text-only because Twitter's media upload endpoint is OAuth1a-only.
    """

    # Prefer OAuth1a if the full token set is available and requests-oauthlib is installed.
    if consumer_key and consumer_secret and access_token and access_secret:
        try:
            from requests_oauthlib import OAuth1  # type: ignore
        except Exception as exc:  # noqa: BLE001
            return "error: OAuth1 requires requests-oauthlib (install manually)"

        media_id = None
        if image_bytes:
            try:
                upload_resp = requests.post(
                    "https://upload.twitter.com/1.1/media/upload.json",
                    auth=OAuth1(consumer_key, consumer_secret, access_token, access_secret),
                    files={"media": ("image", image_bytes, image_mime or "application/octet-stream")},
                    timeout=20,
                )
                if upload_resp.ok:
                    media_id = upload_resp.json().get("media_id_string")
                else:
                    try:
                        err = upload_resp.json()
                    except Exception:  # noqa: BLE001
                        err = upload_resp.text
                    return f"error: media upload failed {err}"
            except Exception as exc:  # noqa: BLE001
                return f"error: media upload error {exc}"

        try:
            data = {"status": message}
            if media_id:
                data["media_ids"] = media_id
            resp = requests.post(
                "https://api.twitter.com/1.1/statuses/update.json",
                auth=OAuth1(consumer_key, consumer_secret, access_token, access_secret),
                data=data,
                timeout=15,
            )
            if resp.ok:
                return "sent"
            try:
                data = resp.json()
                err = data.get("errors") or data.get("error") or resp.text
            except Exception:  # noqa: BLE001
                err = resp.text
            return f"error: {err}"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    # Fall back to OAuth2 user-context bearer token (must be user-context, not app-only).
    if user_bearer:
        try:
            resp = requests.post(
                "https://api.twitter.com/2/tweets",
                headers={"Authorization": f"Bearer {user_bearer}"},
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

    return "error: no twitter credentials provided"


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
            has_user_bearer = bool(config.get("SOCIAL_TWITTER_BEARER_TOKEN"))
            has_oauth1 = bool(
                config.get("SOCIAL_TWITTER_CONSUMER_KEY")
                and config.get("SOCIAL_TWITTER_CONSUMER_SECRET")
                and config.get("SOCIAL_TWITTER_ACCESS_TOKEN")
                and config.get("SOCIAL_TWITTER_ACCESS_SECRET")
            )
            token_state = "present" if (has_user_bearer or has_oauth1) else "missing"
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

    image_bytes, image_mime, _ = _resolve_image(post, config)

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
                        user_bearer=config.get("SOCIAL_TWITTER_BEARER_TOKEN"),
                        consumer_key=config.get("SOCIAL_TWITTER_CONSUMER_KEY"),
                        consumer_secret=config.get("SOCIAL_TWITTER_CONSUMER_SECRET"),
                        access_token=config.get("SOCIAL_TWITTER_ACCESS_TOKEN"),
                        access_secret=config.get("SOCIAL_TWITTER_ACCESS_SECRET"),
                        image_bytes=image_bytes,
                        image_mime=image_mime,
                    )
                elif platform == "bluesky":
                    statuses[platform] = _post_bluesky(
                        post.content,
                        config.get("SOCIAL_BLUESKY_HANDLE"),
                        config.get("SOCIAL_BLUESKY_PASSWORD"),
                        image_bytes=image_bytes,
                        image_mime=image_mime,
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


def _resolve_image(post: SocialPost, config) -> Tuple[Optional[bytes], Optional[str], Optional[str]]:
    """Resolve an image from an uploaded path or external URL.

    Returns (bytes, mime_type, error_message). Errors are non-fatal; callers can
    still send text-only posts if bytes are None.
    """

    upload_dir = config.get("SOCIAL_UPLOAD_DIR") or os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)),
        "instance",
        "social_uploads",
    )

    if post.image_path:
        path = post.image_path
        if not os.path.isabs(path):
            path = os.path.join(upload_dir, path)
        if os.path.exists(path):
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
                mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                return data, mime, None
            except Exception as exc:  # noqa: BLE001
                return None, None, f"read error: {exc}"
        return None, None, "image path not found"

    if post.image_url:
        try:
            resp = requests.get(post.image_url, timeout=15)
            if resp.ok:
                mime = resp.headers.get("content-type", "application/octet-stream").split(";")[0]
                return resp.content, mime, None
            return None, None, f"fetch failed: {resp.status_code}"
        except Exception as exc:  # noqa: BLE001
            return None, None, f"fetch error: {exc}"

    return None, None, None
