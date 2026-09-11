from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from tracker.config import get_settings
from tracker.models import Event, EventType, Person, Snapshot, SnapshotStatus
from tracker.web import routes
from tracker.web.main import create_app
from tracker.web.routes import get_session

WHEN = datetime(2026, 9, 11, 12, 0)


@pytest.fixture(autouse=True)
def sqlite_settings(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def stub_worker_health(monkeypatch):
    monkeypatch.setattr(routes, "_worker_health", lambda: None)


@pytest.fixture()
def client(engine, sqlite_settings):
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    with TestClient(app) as test_client:
        yield test_client


def seed_person(session, user_id, name, is_follower=True, is_following=True, whitelisted=False):
    person = Person(
        ig_user_id=user_id,
        username=name,
        is_follower=is_follower,
        is_following=is_following,
        whitelisted=whitelisted,
        first_seen_at=WHEN,
    )
    session.add(person)
    session.commit()
    return person


def seed_snapshot(session, followers, following, status=SnapshotStatus.ok):
    snapshot = Snapshot(
        taken_at=WHEN,
        follower_count=followers,
        following_count=following,
        status=status,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def seed_event(session, user_id, name, event_type, when=WHEN, snapshot_id=1):
    event = Event(
        detected_at=when,
        ig_user_id=user_id,
        username=name,
        type=event_type,
        snapshot_id=snapshot_id,
    )
    session.add(event)
    session.commit()
    return event


def test_dashboard_renders_counts_chart_and_banner(client, session):
    seed_snapshot(session, 120, 300)

    response = client.get("/")

    assert response.status_code == 200
    assert "growth-chart" in response.text
    assert "120" in response.text
    assert "Worker unreachable" in response.text


def test_dashboard_shows_paused_banner(client, session, monkeypatch):
    monkeypatch.setattr(
        routes, "_worker_health", lambda: {"paused": True, "running": False}
    )

    response = client.get("/")

    assert "Worker paused" in response.text


def test_dashboard_failed_snapshot_shows_error_banner(client, session, monkeypatch):
    monkeypatch.setattr(
        routes,
        "_worker_health",
        lambda: {
            "paused": False,
            "running": False,
            "last_snapshot": {"taken_at": WHEN.isoformat(), "status": "failed"},
        },
    )

    response = client.get("/")

    assert "banner-error" in response.text


class TestWorkerHealth:
    @pytest.fixture()
    def stub_worker_health(self):
        yield

    def test_returns_none_on_invalid_json(self, monkeypatch):
        def fake_get(url, timeout=None):
            return httpx.Response(
                200, content=b"not json", request=httpx.Request("GET", url)
            )

        monkeypatch.setattr(routes.httpx, "get", fake_get)

        assert routes._worker_health() is None

    def test_returns_none_on_invalid_url(self, monkeypatch):
        monkeypatch.setenv("WORKER_INTERNAL_URL", "http://foo:bar")
        get_settings.cache_clear()

        assert routes._worker_health() is None


def test_events_filter_by_type(client, session):
    seed_event(session, 1, "alice", EventType.unfollowed)
    seed_event(session, 2, "bob", EventType.new_follower)

    response = client.get("/events?type=unfollowed")

    assert "@alice" in response.text
    assert "@bob" not in response.text


def test_events_htmx_returns_partial(client, session):
    seed_event(session, 1, "alice", EventType.unfollowed)

    response = client.get("/events", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert "<html" not in response.text


def test_hide_whitelisted_events(client, session):
    seed_person(session, 1, "alice", whitelisted=True)
    seed_event(session, 1, "alice", EventType.unfollowed)
    seed_event(session, 2, "bob", EventType.unfollowed)

    response = client.get("/events?hide_whitelisted=1")

    assert "@bob" in response.text
    assert "@alice" not in response.text


def test_lists_pages(client, session):
    seed_person(session, 1, "alice", is_follower=True, is_following=False)
    seed_person(session, 2, "bob", is_follower=False, is_following=True)

    fans = client.get("/lists/fans")
    assert "@alice" in fans.text

    not_back = client.get("/lists/not-following-back")
    assert "@bob" in not_back.text
    assert "@alice" not in not_back.text


def test_whitelist_toggle(client, session):
    seed_person(session, 1, "alice")

    response = client.post("/people/1/whitelist")
    assert response.status_code == 200
    assert "Un-whitelist" in response.text
    session.expire_all()
    assert session.get(Person, 1).whitelisted is True

    response = client.post("/people/1/whitelist")
    assert ">Whitelist<" in response.text
    session.expire_all()
    assert session.get(Person, 1).whitelisted is False


def test_whitelist_toggle_missing_person(client):
    assert client.post("/people/999/whitelist").status_code == 404


def test_person_page_shows_state_and_history(client, session):
    seed_person(session, 1, "alice", is_follower=False, is_following=True)
    seed_event(session, 1, "alice", EventType.unfollowed)

    response = client.get("/people/1")

    assert response.status_code == 200
    assert "@alice" in response.text
    assert "unfollowed" in response.text


def test_person_page_404(client):
    assert client.get("/people/123").status_code == 404


def test_refresh_handles_409(client, monkeypatch):
    def fake_post(url, json=None, timeout=None, **kwargs):
        return httpx.Response(409, request=httpx.Request("POST", url))

    monkeypatch.setattr(routes.httpx, "post", fake_post)

    response = client.post("/refresh")

    assert response.status_code == 200
    assert "already running" in response.text


def test_refresh_handles_worker_unreachable(client, monkeypatch):
    def fake_post(url, json=None, timeout=None, **kwargs):
        raise httpx.ConnectError("boom", request=httpx.Request("POST", url))

    monkeypatch.setattr(routes.httpx, "post", fake_post)

    response = client.post("/refresh")

    assert "unreachable" in response.text


def test_stats_counts_json(client, session):
    seed_snapshot(session, 120, 300)
    seed_snapshot(session, None, None, status=SnapshotStatus.failed)

    response = client.get("/api/stats/counts")

    assert response.status_code == 200
    assert response.json() == [
        {"taken_at": WHEN.isoformat(), "followers": 120, "following": 300}
    ]
