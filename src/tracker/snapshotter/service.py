import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.core.diff import UserRecord, apply_people_state, diff_snapshots
from tracker.models import (
    Direction,
    Event,
    Person,
    Snapshot,
    SnapshotEntry,
    SnapshotStatus,
)
from tracker.snapshotter.client import InstagramClient
from tracker.snapshotter.errors import SnapshotError

logger = logging.getLogger(__name__)


@dataclass
class FetchedLists:
    followers: dict[int, UserRecord]
    following: dict[int, UserRecord]


def fetch_with_client(client: InstagramClient) -> FetchedLists:
    followers = client.fetch_followers()
    following = client.fetch_following()
    return FetchedLists(followers=followers, following=following)


def _previous_ok_snapshot(session: Session, before_id: int) -> Snapshot | None:
    stmt = (
        select(Snapshot)
        .where(Snapshot.status == SnapshotStatus.ok, Snapshot.id < before_id)
        .order_by(Snapshot.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def _entries_map(
    session: Session, snapshot_id: int, direction: Direction
) -> dict[int, UserRecord]:
    stmt = select(SnapshotEntry).where(
        SnapshotEntry.snapshot_id == snapshot_id,
        SnapshotEntry.direction == direction,
    )
    return {
        entry.ig_user_id: UserRecord(entry.ig_user_id, entry.username, None)
        for entry in session.scalars(stmt)
    }


def run_snapshot(
    session: Session,
    fetch_lists: Callable[[], FetchedLists],
    notify: Callable[[list[Event]], None] | None = None,
    now: datetime | None = None,
) -> Snapshot:
    now = now or datetime.now(timezone.utc)
    try:
        lists = fetch_lists()
    except SnapshotError as exc:
        failed = Snapshot(
            taken_at=now,
            follower_count=None,
            following_count=None,
            status=SnapshotStatus.failed,
            error=str(exc),
        )
        session.add(failed)
        session.commit()
        raise

    snapshot = Snapshot(
        taken_at=now,
        follower_count=len(lists.followers),
        following_count=len(lists.following),
        status=SnapshotStatus.ok,
    )
    session.add(snapshot)
    session.flush()

    for user_id, record in lists.followers.items():
        session.add(
            SnapshotEntry(
                snapshot_id=snapshot.id,
                ig_user_id=user_id,
                username=record.username,
                direction=Direction.follower,
            )
        )
    for user_id, record in lists.following.items():
        session.add(
            SnapshotEntry(
                snapshot_id=snapshot.id,
                ig_user_id=user_id,
                username=record.username,
                direction=Direction.following,
            )
        )

    previous = _previous_ok_snapshot(session, snapshot.id)
    events: list[Event] = []
    if previous is not None:
        detected = diff_snapshots(
            _entries_map(session, previous.id, Direction.follower),
            _entries_map(session, previous.id, Direction.following),
            lists.followers,
            lists.following,
        )
        for item in detected:
            event = Event(
                detected_at=now,
                ig_user_id=item.ig_user_id,
                username=item.username,
                type=item.type,
                snapshot_id=snapshot.id,
            )
            session.add(event)
            events.append(event)

    people = {person.ig_user_id: person for person in session.scalars(select(Person))}
    apply_people_state(people, lists.followers, lists.following, now)
    for person in people.values():
        session.add(person)

    session.commit()

    if events and notify is not None:
        try:
            notify(events)
        except Exception:
            logger.exception("notifier failed")

    return snapshot
