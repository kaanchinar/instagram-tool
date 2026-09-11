import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.config import get_settings
from tracker.models import Event, EventType, Person

logger = logging.getLogger(__name__)

TITLES = {
    EventType.unfollowed: "Unfollowed",
    EventType.new_follower: "New followers",
    EventType.i_followed: "You followed",
    EventType.i_unfollowed: "You unfollowed",
}
ORDER = [
    EventType.unfollowed,
    EventType.new_follower,
    EventType.i_followed,
    EventType.i_unfollowed,
]


def format_events(events: list[Event], moment: datetime | None = None) -> str:
    moment = moment or max(
        (event.detected_at for event in events), default=datetime.now(timezone.utc)
    )
    lines = [f"Instagram update ({moment.strftime('%Y-%m-%d %H:%M')} UTC)"]
    for event_type in ORDER:
        names = sorted(event.username for event in events if event.type == event_type)
        if names:
            joined = ", ".join(f"@{name}" for name in names)
            lines.append(f"{TITLES[event_type]} ({len(names)}): {joined}")
    return "\n".join(lines)


def send_message(text: str) -> bool:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("telegram notifier disabled; message not sent: %s", text)
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        response = httpx.post(
            url,
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("telegram delivery failed")
        return False
    return True


def send_events(session: Session, events: list[Event]) -> bool:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("telegram notifier disabled; skipping %d events", len(events))
        return False
    whitelisted = set(
        session.scalars(
            select(Person.ig_user_id).where(Person.whitelisted.is_(True))
        )
    )
    sendable = [event for event in events if event.ig_user_id not in whitelisted]
    if not sendable:
        logger.info("all events belong to whitelisted users; skipping telegram")
        return False
    return send_message(format_events(sendable))
