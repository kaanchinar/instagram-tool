from datetime import datetime

from tracker.core import queries
from tracker.models import Event, EventType, Person, Snapshot, SnapshotStatus

WHEN = datetime(2026, 9, 11, 12, 0)


def add_person(session, user_id, name, is_follower, is_following, whitelisted=False):
    person = Person(
        ig_user_id=user_id,
        username=name,
        is_follower=is_follower,
        is_following=is_following,
        whitelisted=whitelisted,
        first_seen_at=WHEN,
    )
    session.add(person)
    return person


def add_event(session, user_id, name, event_type, when=WHEN, snapshot_id=1):
    event = Event(
        detected_at=when,
        ig_user_id=user_id,
        username=name,
        type=event_type,
        snapshot_id=snapshot_id,
    )
    session.add(event)
    return event


def test_not_following_back(session):
    add_person(session, 1, "alice", True, True)
    add_person(session, 2, "bob", False, True)
    add_person(session, 3, "carol", True, False)
    session.commit()

    assert [person.username for person in queries.not_following_back(session)] == ["bob"]


def test_fans(session):
    add_person(session, 1, "alice", True, True)
    add_person(session, 2, "bob", False, True)
    add_person(session, 3, "carol", True, False)
    session.commit()

    assert [person.username for person in queries.fans(session)] == ["carol"]


def test_user_history_newest_first(session):
    add_event(session, 1, "alice", EventType.new_follower, datetime(2026, 9, 1))
    add_event(session, 1, "alice", EventType.unfollowed, datetime(2026, 9, 2))
    add_event(session, 2, "bob", EventType.unfollowed, datetime(2026, 9, 3))
    session.commit()

    history = queries.user_history(session, 1)
    assert [event.type for event in history] == [
        EventType.unfollowed,
        EventType.new_follower,
    ]


def test_counts_series_only_ok_ascending(session):
    session.add_all(
        [
            Snapshot(
                taken_at=datetime(2026, 9, 2),
                follower_count=10,
                following_count=20,
                status=SnapshotStatus.ok,
            ),
            Snapshot(
                taken_at=datetime(2026, 9, 3),
                follower_count=None,
                following_count=None,
                status=SnapshotStatus.failed,
                error="boom",
            ),
            Snapshot(
                taken_at=datetime(2026, 9, 1),
                follower_count=9,
                following_count=19,
                status=SnapshotStatus.ok,
            ),
        ]
    )
    session.commit()

    assert queries.counts_series(session) == [
        (datetime(2026, 9, 1), 9, 19),
        (datetime(2026, 9, 2), 10, 20),
    ]


def test_recent_events_filters_type_and_whitelist(session):
    add_person(session, 1, "alice", True, False, whitelisted=True)
    add_person(session, 2, "bob", False, True)
    add_event(session, 1, "alice", EventType.unfollowed)
    add_event(session, 2, "bob", EventType.unfollowed)
    add_event(session, 2, "bob", EventType.new_follower)
    session.commit()

    rows = queries.recent_events(session, event_type=EventType.unfollowed)
    assert {row.event.username for row in rows} == {"alice", "bob"}

    rows = queries.recent_events(session, hide_whitelisted=True)
    assert {row.event.username for row in rows} == {"bob"}


def test_latest_ok_snapshot_ignores_failed(session):
    session.add_all(
        [
            Snapshot(
                taken_at=datetime(2026, 9, 1),
                follower_count=9,
                following_count=19,
                status=SnapshotStatus.ok,
            ),
            Snapshot(
                taken_at=datetime(2026, 9, 2),
                follower_count=None,
                following_count=None,
                status=SnapshotStatus.failed,
                error="boom",
            ),
        ]
    )
    session.commit()

    latest = queries.latest_ok_snapshot(session)
    assert latest.taken_at == datetime(2026, 9, 1)
