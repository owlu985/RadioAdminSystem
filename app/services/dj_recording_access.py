"""Access-code helpers for the public DJ recording archive."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta

from cachelib import SimpleCache
from flask import current_app, request, session
from sqlalchemy import or_

from app.models import DJRecordingAccessCode, db

PORTAL_SESSION_KEY = "dj_recording_access_id"
_attempts = SimpleCache(default_timeout=0)


def code_digest(code: str) -> str:
    secret = str(current_app.config["SECRET_KEY"]).encode("utf-8")
    return hmac.new(secret, code.encode("ascii"), hashlib.sha256).hexdigest()


def create_access_code(
    dj_id: int, expires_at: datetime | None = None, *, indefinite: bool = False
) -> tuple[DJRecordingAccessCode, str]:
    if expires_at is None and not indefinite:
        expires_at = datetime.utcnow() + timedelta(days=30)
    for _ in range(100):
        code = f"{secrets.randbelow(10000):04d}"
        digest = code_digest(code)
        existing = DJRecordingAccessCode.query.filter(
            DJRecordingAccessCode.code_digest == digest,
            DJRecordingAccessCode.revoked_at.is_(None),
            or_(DJRecordingAccessCode.expires_at.is_(None), DJRecordingAccessCode.expires_at > datetime.utcnow()),
        ).first()
        if not existing:
            record = DJRecordingAccessCode(dj_id=dj_id, code_digest=digest, expires_at=expires_at)
            db.session.add(record)
            db.session.commit()
            return record, code
    raise RuntimeError("Unable to allocate a unique four-digit access code")


def client_key() -> str:
    return request.remote_addr or "unknown"


def login_is_blocked() -> bool:
    bucket = _attempts.get(f"dj-portal:{client_key()}")
    return bool(bucket and bucket["count"] >= 5 and time.time() - bucket["started"] < 900)


def record_failed_login() -> None:
    key = f"dj-portal:{client_key()}"
    now = time.time()
    bucket = _attempts.get(key)
    if not bucket or now - bucket["started"] >= 900:
        bucket = {"started": now, "count": 0}
    bucket["count"] += 1
    _attempts.set(key, bucket, timeout=900)


def clear_failed_logins() -> None:
    _attempts.delete(f"dj-portal:{client_key()}")


def authenticate(code: str) -> DJRecordingAccessCode | None:
    if not code.isdigit() or len(code) != 4 or login_is_blocked():
        return None
    record = DJRecordingAccessCode.query.filter(
        DJRecordingAccessCode.code_digest == code_digest(code),
        DJRecordingAccessCode.revoked_at.is_(None),
        or_(DJRecordingAccessCode.expires_at.is_(None), DJRecordingAccessCode.expires_at > datetime.utcnow()),
    ).first()
    if not record or not record.dj or record.dj.is_archived:
        record_failed_login()
        return None
    clear_failed_logins()
    record.last_used_at = datetime.utcnow()
    db.session.commit()
    session[PORTAL_SESSION_KEY] = record.id
    session.permanent = False
    return record


def current_access() -> DJRecordingAccessCode | None:
    record_id = session.get(PORTAL_SESSION_KEY)
    if not record_id:
        return None
    record = DJRecordingAccessCode.query.filter(
        DJRecordingAccessCode.id == record_id,
        DJRecordingAccessCode.revoked_at.is_(None),
        or_(DJRecordingAccessCode.expires_at.is_(None), DJRecordingAccessCode.expires_at > datetime.utcnow()),
    ).first()
    if not record or not record.dj or record.dj.is_archived:
        session.pop(PORTAL_SESSION_KEY, None)
        return None
    return record


def logout() -> None:
    session.pop(PORTAL_SESSION_KEY, None)
