OPTIONAL_CONFIG_KEYS = {
    "TEMPEST_API_KEY",
    "OAUTH_CLIENT_ID",
    "OAUTH_CLIENT_SECRET",
    "OAUTH_ALLOWED_DOMAIN",
    "ADMIN_PASSWORD_HASH",
    "DISCORD_OAUTH_CLIENT_ID",
    "DISCORD_OAUTH_CLIENT_SECRET",
    "DISCORD_ALLOWED_GUILD_ID",
    "ALERTS_DISCORD_WEBHOOK",
    "ALERTS_EMAIL_TO",
    "ALERTS_EMAIL_FROM",
    "ALERTS_SMTP_SERVER",
    "ALERTS_SMTP_USERNAME",
    "ALERTS_SMTP_PASSWORD",
    "ICECAST_STATUS_URL",
    "ICECAST_USERNAME",
    "ICECAST_PASSWORD",
    "ICECAST_MOUNT",
    "SOCIAL_FACEBOOK_PAGE_TOKEN",
    "SOCIAL_INSTAGRAM_TOKEN",
    "SOCIAL_TWITTER_BEARER_TOKEN",
    "SOCIAL_BLUESKY_HANDLE",
    "SOCIAL_BLUESKY_PASSWORD",
    "ARCHIVIST_DB_PATH",
    "ARCHIVIST_UPLOAD_DIR",
    "DATA_ROOT",
    "NAS_MUSIC_ROOT",
    "RADIODJ_API_BASE_URL",
    "RADIODJ_API_PASSWORD",
    "MUSICBRAINZ_USER_AGENT",
}


def _normalize_optional(val):
    if val is None:
        return None
    if isinstance(val, str) and val.strip().lower() in {"", "none", "null"}:
        return None
    return val


def normalize_optional_config(config: dict) -> dict:
    for key in OPTIONAL_CONFIG_KEYS:
        if key in config:
            config[key] = _normalize_optional(config[key])
    return config
