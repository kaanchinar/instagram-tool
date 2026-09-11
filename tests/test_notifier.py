from datetime import datetime
from types import SimpleNamespace

import httpx

from tracker.models import Event, EventType, Person
from tracker.notifier import telegram

WHEN = datetime(2026, 9, 11, 18, 3)


def make_event(user_id, name, event_type, when=WHEN):
    return Event(
        detected_at=when,
        ig_user_id=user_id,
        username=name,
        type=event_type,
        snapshot_id=1,
    )


def enable_telegram(monkeypatch):
    settings = SimpleNamespace(telegram_bot_token="token", telegram_chat_id="chat")
    monkeypatch.setattr(telegram, "get_settings", lambda: settings)


def test_format_events_groups_and_orders():
    events = [
        make_event(1, "alice", EventType.unfollowed),
        make_event(2, "bob", EventType.unfollowed),
        make_event(3, "carol", EventType.new_follower),
        make_event(4, "dave", EventType.i_followed),
        make_event(5, "erin", EventType.i_unfollowed),
    ]

    assert telegram.format_events(events, WHEN) == (
        "Instagram update (2026-09-11 18:03 UTC)\n"
        "Unfollowed (2): @alice, @bob\n"
        "New followers (1): @carol\n"
        "You followed (1): @dave\n"
        "You unfollowed (1): @erin"
    )


def test_format_events_omits_empty_sections():
    events = [make_event(3, "carol", EventType.new_follower)]

    assert telegram.format_events(events, WHEN) == (
        "Instagram update (2026-09-11 18:03 UTC)\n"
        "New followers (1): @carol"
    )


def test_send_events_disabled_without_config(monkeypatch, session):
    monkeypatch.setattr(
        telegram,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="", telegram_chat_id=""),
    )
    assert telegram.send_events(session, [make_event(1, "alice", EventType.unfollowed)]) is False


def test_send_events_filters_whitelisted(monkeypatch, session):
    enable_telegram(monkeypatch)
    session.add(
        Person(
            ig_user_id=1,
            username="alice",
            is_follower=False,
            is_following=True,
            whitelisted=True,
            first_seen_at=WHEN,
        )
    )
    session.commit()
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(telegram.httpx, "post", fake_post)

    result = telegram.send_events(
        session,
        [
            make_event(1, "alice", EventType.unfollowed),
            make_event(2, "bob", EventType.unfollowed),
        ],
    )

    assert result is True
    assert captured["url"] == "https://api.telegram.org/bottoken/sendMessage"
    assert captured["json"]["chat_id"] == "chat"
    assert "@bob" in captured["json"]["text"]
    assert "@alice" not in captured["json"]["text"]


def test_send_events_all_whitelisted_is_noop(monkeypatch, session):
    enable_telegram(monkeypatch)
    session.add(
        Person(
            ig_user_id=1,
            username="alice",
            is_follower=False,
            is_following=True,
            whitelisted=True,
            first_seen_at=WHEN,
        )
    )
    session.commit()

    called = {"post": False}
    monkeypatch.setattr(
        telegram.httpx,
        "post",
        lambda *args, **kwargs: called.__setitem__("post", True),
    )

    assert telegram.send_events(session, [make_event(1, "alice", EventType.unfollowed)]) is False
    assert called["post"] is False


def test_delivery_failure_is_swallowed(monkeypatch, session):
    enable_telegram(monkeypatch)

    def fake_post(url, json, timeout):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(telegram.httpx, "post", fake_post)

    assert telegram.send_events(session, [make_event(2, "bob", EventType.unfollowed)]) is False


def test_send_message_returns_false_when_unconfigured(monkeypatch):
    monkeypatch.setattr(
        telegram,
        "get_settings",
        lambda: SimpleNamespace(telegram_bot_token="", telegram_chat_id=""),
    )
    assert telegram.send_message("hello") is False
