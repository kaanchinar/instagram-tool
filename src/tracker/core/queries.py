from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tracker.models import Event, EventType, Person, Snapshot, SnapshotStatus


@dataclass(frozen=True)
class EventWithWhitelist:
    event: Event
    whitelisted: bool


def followers(session: Session) -> list[Person]:
    stmt = (
        select(Person)
        .where(Person.is_follower.is_(True))
        .order_by(Person.username)
    )
    return list(session.scalars(stmt))


def following(session: Session) -> list[Person]:
    stmt = (
        select(Person)
        .where(Person.is_following.is_(True))
        .order_by(Person.username)
    )
    return list(session.scalars(stmt))


def not_following_back(session: Session) -> list[Person]:
    stmt = (
        select(Person)
        .where(Person.is_following.is_(True), Person.is_follower.is_(False))
        .order_by(Person.username)
    )
    return list(session.scalars(stmt))


def fans(session: Session) -> list[Person]:
    stmt = (
        select(Person)
        .where(Person.is_follower.is_(True), Person.is_following.is_(False))
        .order_by(Person.username)
    )
    return list(session.scalars(stmt))


def list_counts(session: Session) -> dict[str, int]:
    followers_c = (
        session.scalar(
            select(func.count(Person.ig_user_id)).where(
                Person.is_follower.is_(True)
            )
        )
        or 0
    )
    following_c = (
        session.scalar(
            select(func.count(Person.ig_user_id)).where(
                Person.is_following.is_(True)
            )
        )
        or 0
    )
    not_back_c = (
        session.scalar(
            select(func.count(Person.ig_user_id)).where(
                Person.is_following.is_(True), Person.is_follower.is_(False)
            )
        )
        or 0
    )
    fans_c = (
        session.scalar(
            select(func.count(Person.ig_user_id)).where(
                Person.is_follower.is_(True), Person.is_following.is_(False)
            )
        )
        or 0
    )
    return {
        "followers": followers_c,
        "following": following_c,
        "not_following_back": not_back_c,
        "fans": fans_c,
    }


def user_history(session: Session, ig_user_id: int) -> list[Event]:
    stmt = (
        select(Event)
        .where(Event.ig_user_id == ig_user_id)
        .order_by(Event.detected_at.desc(), Event.id.desc())
    )
    return list(session.scalars(stmt))


def counts_series(session: Session) -> list[tuple[datetime, int, int]]:
    stmt = (
        select(Snapshot.taken_at, Snapshot.follower_count, Snapshot.following_count)
        .where(Snapshot.status == SnapshotStatus.ok)
        .order_by(Snapshot.taken_at, Snapshot.id)
    )
    return [(row[0], row[1], row[2]) for row in session.execute(stmt)]


def recent_events(
    session: Session,
    limit: int = 10,
    event_type: EventType | None = None,
    hide_whitelisted: bool = False,
) -> list[EventWithWhitelist]:
    stmt = (
        select(Event, Person.whitelisted)
        .join(Person, Person.ig_user_id == Event.ig_user_id, isouter=True)
        .order_by(Event.detected_at.desc(), Event.id.desc())
        .limit(limit)
    )
    if event_type is not None:
        stmt = stmt.where(Event.type == event_type)
    if hide_whitelisted:
        stmt = stmt.where(Person.whitelisted.is_not(True))
    rows = session.execute(stmt).all()
    return [
        EventWithWhitelist(event=row[0], whitelisted=bool(row[1])) for row in rows
    ]


def latest_ok_snapshot(session: Session) -> Snapshot | None:
    stmt = (
        select(Snapshot)
        .where(Snapshot.status == SnapshotStatus.ok)
        .order_by(Snapshot.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def get_person(session: Session, ig_user_id: int) -> Person | None:
    return session.get(Person, ig_user_id)
