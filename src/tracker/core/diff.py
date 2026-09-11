from dataclasses import dataclass
from datetime import datetime

from tracker.models import EventType, Person


@dataclass(frozen=True)
class UserRecord:
    ig_user_id: int
    username: str
    full_name: str | None = None


@dataclass(frozen=True)
class DetectedEvent:
    ig_user_id: int
    username: str
    type: EventType


def diff_snapshots(
    prev_followers: dict[int, UserRecord],
    prev_following: dict[int, UserRecord],
    new_followers: dict[int, UserRecord],
    new_following: dict[int, UserRecord],
) -> list[DetectedEvent]:
    events: list[DetectedEvent] = []
    for user_id, record in sorted(new_followers.items()):
        if user_id not in prev_followers:
            events.append(DetectedEvent(user_id, record.username, EventType.new_follower))
    for user_id, record in sorted(prev_followers.items()):
        if user_id not in new_followers:
            events.append(DetectedEvent(user_id, record.username, EventType.unfollowed))
    for user_id, record in sorted(new_following.items()):
        if user_id not in prev_following:
            events.append(DetectedEvent(user_id, record.username, EventType.i_followed))
    for user_id, record in sorted(prev_following.items()):
        if user_id not in new_following:
            events.append(DetectedEvent(user_id, record.username, EventType.i_unfollowed))
    return events


def _set_state(person: Person, is_follower: bool, is_following: bool, now: datetime) -> None:
    if person.is_follower != is_follower or person.is_following != is_following:
        person.last_changed_at = now
    person.is_follower = is_follower
    person.is_following = is_following


def apply_people_state(
    people: dict[int, Person],
    followers: dict[int, UserRecord],
    following: dict[int, UserRecord],
    now: datetime,
) -> None:
    seen_ids = set(followers) | set(following)
    for user_id in sorted(seen_ids):
        record = followers.get(user_id) or following.get(user_id)
        person = people.get(user_id)
        if person is None:
            person = Person(
                ig_user_id=user_id,
                username=record.username,
                full_name=record.full_name,
                is_follower=False,
                is_following=False,
                whitelisted=False,
                first_seen_at=now,
            )
            people[user_id] = person
        person.username = record.username
        person.full_name = record.full_name
        _set_state(person, user_id in followers, user_id in following, now)
    for user_id, person in people.items():
        if user_id not in seen_ids:
            _set_state(person, False, False, now)
