from datetime import datetime

from sqlalchemy import select

from tracker.models import (
    Direction,
    Person,
    Snapshot,
    SnapshotEntry,
    SnapshotStatus,
)


def test_snapshot_entries_roundtrip(session):
    snapshot = Snapshot(
        taken_at=datetime(2026, 9, 11),
        follower_count=1,
        following_count=1,
        status=SnapshotStatus.ok,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        SnapshotEntry(
            snapshot_id=snapshot.id,
            ig_user_id=123456789012,
            username="alice",
            direction=Direction.follower,
        )
    )
    session.commit()

    entry = session.scalars(select(SnapshotEntry)).one()
    assert entry.ig_user_id == 123456789012
    assert entry.direction == Direction.follower
    assert snapshot.entries[0].username == "alice"


def test_person_roundtrip_with_large_id(session):
    person = Person(
        ig_user_id=987654321098,
        username="bob",
        is_follower=True,
        is_following=False,
        first_seen_at=datetime(2026, 9, 11),
    )
    session.add(person)
    session.commit()

    stored = session.get(Person, 987654321098)
    assert stored.username == "bob"
    assert stored.is_follower is True
