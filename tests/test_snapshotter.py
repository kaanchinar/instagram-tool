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
