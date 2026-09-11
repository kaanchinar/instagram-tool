from datetime import datetime

import pytest
from instagrapi.exceptions import (
    BadPassword,
    ChallengeRequired as InstagramChallengeRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
)

from tracker.config import Settings
from tracker.core.diff import UserRecord
from tracker.snapshotter import client as client_module
from tracker.snapshotter.client import InstagramClient
from tracker.snapshotter.errors import ChallengeRequired, FetchFailed, LoginFailed, RateLimited


class FakeUser:
    def __init__(self, pk, username, full_name=""):
        self.pk = pk
        self.username = username
        self.full_name = full_name


class FakeInstagrapiClient:
    def __init__(self):
        self.delay_range = None
        self.settings_loaded = False
        self.logged_in_with = None
        self.dumped_to = None
        self.followers = {}
        self.following = {}
        self.me = FakeUser(999, "me", "Me")
        self.fail_followers = None
        self.fail_following = None

    def load_settings(self, path):
        self.settings_loaded = True

    def dump_settings(self, path):
        self.dumped_to = path

    def login(self, username, password):
        self.logged_in_with = (username, password)

    def account_info(self):
        return self.me

    def user_info_by_username(self, username):
        return self.me

    def user_followers(self, user_id, use_cache=False):
        if self.fail_followers is not None:
            raise self.fail_followers
        return self.followers

    def user_following(self, user_id, use_cache=False):
        if self.fail_following is not None:
            raise self.fail_following
        return self.following


@pytest.fixture()
def fake_client(monkeypatch):
    fake = FakeInstagrapiClient()
    monkeypatch.setattr(client_module, "Client", lambda: fake)
    return fake


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        ig_username="me",
        ig_password="secret",
        ig_session_path=tmp_path / "session.json",
        database_url="sqlite://",
        _env_file=None,
    )


def test_fresh_login_dumps_session(fake_client, settings):
    InstagramClient(settings).connect()

    assert fake_client.logged_in_with == ("me", "secret")
    assert fake_client.dumped_to == settings.ig_session_path
    assert fake_client.delay_range == [2, 6]


def test_existing_valid_session_skips_password_login(fake_client, settings):
    settings.ig_session_path.write_text("{}")
    InstagramClient(settings).connect()

    assert fake_client.settings_loaded is True
    assert fake_client.logged_in_with is None
    assert fake_client.dumped_to is None


def test_expired_session_falls_back_to_password_login(fake_client, settings):
    settings.ig_session_path.write_text("{}")
    calls = {"count": 0}

    def account_info():
        calls["count"] += 1
        if calls["count"] == 1:
            raise LoginRequired("expired")
        return fake_client.me

    fake_client.account_info = account_info
    InstagramClient(settings).connect()

    assert fake_client.logged_in_with == ("me", "secret")
    assert fake_client.dumped_to == settings.ig_session_path


def test_transient_session_error_does_not_trigger_password_login(fake_client, settings):
    settings.ig_session_path.write_text("{}")

    def account_info():
        raise PleaseWaitFewMinutes("slow down")

    fake_client.account_info = account_info

    with pytest.raises(RateLimited):
        InstagramClient(settings).connect()

    assert fake_client.logged_in_with is None


def test_session_dump_failure_wrapped(fake_client, settings):
    def boom(path):
        raise OSError("disk full")

    fake_client.dump_settings = boom

    with pytest.raises(FetchFailed):
        InstagramClient(settings).connect()

    assert fake_client.logged_in_with == ("me", "secret")


def test_own_id_error_not_rewrapped_as_fetch_failed(fake_client, settings):
    def boom(username):
        raise InstagramChallengeRequired()

    fake_client.user_info_by_username = boom

    with pytest.raises(ChallengeRequired):
        InstagramClient(settings).fetch_followers()


def test_invalid_credentials_wrapped(fake_client, settings, monkeypatch):
    def boom(username, password):
        raise BadPassword("bad password")

    monkeypatch.setattr(fake_client, "login", boom)

    with pytest.raises(LoginFailed):
        InstagramClient(settings).connect()


def test_rate_limit_wrapped(fake_client, settings):
    fake_client.fail_followers = PleaseWaitFewMinutes("slow down")

    with pytest.raises(RateLimited):
        InstagramClient(settings).fetch_followers()


def test_challenge_wrapped(fake_client, settings):
    fake_client.fail_followers = InstagramChallengeRequired()

    with pytest.raises(ChallengeRequired):
        InstagramClient(settings).fetch_followers()


def test_fetch_translates_users(fake_client, settings):
    fake_client.followers = {123: FakeUser(123, "alice", "Alice A")}
    fake_client.following = {"456": FakeUser(456, "bob", "Bob B")}

    client = InstagramClient(settings)
    followers = client.fetch_followers()
    following = client.fetch_following()

    assert followers == {123: UserRecord(123, "alice", "Alice A")}
    assert following == {456: UserRecord(456, "bob", "Bob B")}


def test_unexpected_fetch_error_wrapped(fake_client, settings):
    fake_client.fail_following = RuntimeError("network blew up")

    with pytest.raises(FetchFailed):
        InstagramClient(settings).fetch_following()


from tracker.models import Event, Person, Snapshot, SnapshotEntry, SnapshotStatus
from tracker.snapshotter.service import fetch_with_client, run_snapshot


class FakeFetcherClient:
    def __init__(self, followers=None, following=None, error=None):
        self.followers = followers or {}
        self.following = following or {}
        self.error = error

    def fetch_followers(self):
        return self.followers

    def fetch_following(self):
        if self.error is not None:
            raise self.error
        return self.following


def rec(user_id, name):
    return UserRecord(user_id, name)


def test_first_snapshot_is_baseline(session):
    now = datetime(2026, 9, 11, 12, 0)
    client = FakeFetcherClient({1: rec(1, "alice")}, {2: rec(2, "bob")})

    result = run_snapshot(session, lambda: fetch_with_client(client), now=now)

    assert result.status == SnapshotStatus.ok
    assert result.follower_count == 1
    assert result.following_count == 1
    assert session.query(Event).count() == 0
    assert session.get(Person, 1).is_follower is True
    assert session.get(Person, 1).is_following is False
    assert session.get(Person, 2).is_following is True


def test_second_snapshot_creates_events_and_notifies(session):
    t1 = datetime(2026, 9, 11, 12, 0)
    t2 = datetime(2026, 9, 12, 12, 0)
    run_snapshot(
        session,
        lambda: fetch_with_client(
            FakeFetcherClient(
                {1: rec(1, "alice"), 2: rec(2, "bob")},
                {3: rec(3, "carol")},
            )
        ),
        now=t1,
    )
    captured: list[Event] = []

    run_snapshot(
        session,
        lambda: fetch_with_client(
            FakeFetcherClient(
                {2: rec(2, "bob"), 4: rec(4, "dave")},
                {3: rec(3, "carol"), 5: rec(5, "erin")},
            )
        ),
        notify=captured.extend,
        now=t2,
    )

    assert sorted((event.type.value, event.username) for event in captured) == [
        ("i_followed", "erin"),
        ("new_follower", "dave"),
        ("unfollowed", "alice"),
    ]
    assert session.get(Person, 1).is_follower is False
    assert session.get(Person, 1).last_changed_at == t2
    assert session.get(Person, 4).is_follower is True


def test_failed_snapshot_is_persisted_and_reraised(session):
    client = FakeFetcherClient(error=FetchFailed("boom"))

    with pytest.raises(FetchFailed):
        run_snapshot(session, lambda: fetch_with_client(client))

    snapshot = session.query(Snapshot).one()
    assert snapshot.status == SnapshotStatus.failed
    assert snapshot.error == "boom"
    assert session.query(SnapshotEntry).count() == 0


def test_partial_fetch_discards_both_lists(session):
    client = FakeFetcherClient(
        followers={1: rec(1, "alice")},
        error=FetchFailed("following failed"),
    )

    with pytest.raises(FetchFailed):
        run_snapshot(session, lambda: fetch_with_client(client))

    assert session.query(SnapshotEntry).count() == 0
    snapshot = session.query(Snapshot).one()
    assert snapshot.status == SnapshotStatus.failed


def test_notifier_failure_does_not_fail_snapshot(session):
    t1 = datetime(2026, 9, 11, 12, 0)
    t2 = datetime(2026, 9, 12, 12, 0)
    run_snapshot(
        session,
        lambda: fetch_with_client(FakeFetcherClient({1: rec(1, "alice")}, {})),
        now=t1,
    )

    def broken_notifier(events):
        raise RuntimeError("telegram down")

    result = run_snapshot(
        session,
        lambda: fetch_with_client(FakeFetcherClient({}, {})),
        notify=broken_notifier,
        now=t2,
    )

    assert result.status == SnapshotStatus.ok
