from __future__ import annotations

from datetime import date
from typing import Iterable

from sqlalchemy import or_

from app.models import LiveReadCard, db


def upsert_cards(titles: Iterable[str], expiries: Iterable[str], copies: Iterable[str]) -> int:
    """Create multiple live-read cards from parallel lists of data."""

    created = 0
    for title, exp, copy in zip(titles, expiries, copies):
        title = (title or "").strip()
        copy = (copy or "").strip()
        if not title and not copy:
            continue
        expires_on = None
        if exp:
            try:
                expires_on = date.fromisoformat(exp)
            except ValueError:
                expires_on = None

        card = LiveReadCard(title=title or "Untitled", expires_on=expires_on, copy=copy or "")
        db.session.add(card)
        created += 1

    if created:
        db.session.commit()
    return created


def card_query(include_expired: bool = False):
    today = date.today()
    query = LiveReadCard.query
    if not include_expired:
        query = query.filter(or_(LiveReadCard.expires_on.is_(None), LiveReadCard.expires_on >= today))
    return query.order_by(
        LiveReadCard.expires_on.is_(None).desc(),
        LiveReadCard.expires_on.asc(),
        LiveReadCard.created_at.desc(),
    )


def chunk_cards(cards, per_row: int = 2):
    row = []
    for card in cards:
        row.append(card)
        if len(row) == per_row:
            yield row
            row = []
    if row:
        yield row
