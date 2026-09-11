from datetime import datetime

from tracker.core.diff import (
    DetectedEvent,
    UserRecord,
    apply_people_state,
    diff_snapshots,
)
from tracker.models import EventType, Person


def rec(user_id, name):
    return UserRecord(ig_user_id=user_id, username=name)


def test_no_change_produces_no_events():
    followers = {1: rec(1, "alice")}
    following = {2: rec(2, "bob")}
    assert diff_snapshots(followers, following, followers, following) == []


def test_new_follower_detected():
    prev = {1: rec(1, "alice")}
    new = {1: rec(1, "alice"), 2: rec(2, "bob")}
    assert diff_snapshots(prev, {}, new, {}) == [
        DetectedEvent(2, "bob", EventType.new_follower)
    ]


def test_unfollowed_detected():
    prev = {1: rec(1, "alice"), 2: rec(2, "bob")}
    new = {1: rec(1, "alice")}
    assert diff_snapshots(prev, {}, new, {}) == [
        DetectedEvent(2, "bob", EventType.unfollowed)
    ]


def test_i_followed_detected():
    assert diff_snapshots({}, {}, {}, {3: rec(3, "carol")}) == [
        DetectedEvent(3, "carol", EventType.i_followed)
    ]


def test_i_unfollowed_detected():
    assert diff_snapshots({}, {3: rec(3, "carol")}, {}, {}) == [
        DetectedEvent(3, "carol", EventType.i_unfollowed)
    ]


def test_username_change_is_not_an_event():
    prev = {1: rec(1, "old_name")}
    new = {1: rec(1, "new_name")}
    assert diff_snapshots(prev, {}, new, {}) == []


def test_deactivated_then_returning_account():
    baseline = {1: rec(1, "alice")}
    assert diff_snapshots(baseline, {}, {}, {}) == [
        DetectedEvent(1, "alice", EventType.unfollowed)
    ]
    assert diff_snapshots({}, {}, baseline, {}) == [
        DetectedEvent(1, "alice", EventType.new_follower)
    ]


def test_apply_people_state_creates_and_flags():
    now = datetime(2026, 9, 11, 12, 0)
    people: dict[int, Person] = {}
    apply_people_state(people, {1: rec(1, "alice")}, {2: rec(2, "bob")}, now)

    alice = people[1]
    assert (alice.is_follower, alice.is_following) == (True, False)
    assert alice.first_seen_at == now
    assert alice.last_changed_at == now

    bob = people[2]
    assert (bob.is_follower, bob.is_following) == (False, True)


def test_apply_people_state_username_change_keeps_last_changed():
    t1 = datetime(2026, 9, 11, 12, 0)
    t2 = datetime(2026, 9, 12, 12, 0)
    people: dict[int, Person] = {}
    apply_people_state(people, {1: rec(1, "old")}, {}, t1)
    apply_people_state(people, {1: rec(1, "new")}, {}, t2)

    assert people[1].username == "new"
    assert people[1].last_changed_at == t1


def test_apply_people_state_turns_flags_off_when_disappeared():
    t1 = datetime(2026, 9, 11, 12, 0)
    t2 = datetime(2026, 9, 12, 12, 0)
    people: dict[int, Person] = {}
    apply_people_state(people, {1: rec(1, "alice")}, {1: rec(1, "alice")}, t1)
    apply_people_state(people, {}, {}, t2)

    assert people[1].is_follower is False
    assert people[1].is_following is False
    assert people[1].last_changed_at == t2
