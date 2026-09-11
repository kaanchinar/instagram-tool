import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tracker.snapshotter.errors import ChallengeRequired, RateLimited
from tracker.worker import main as worker


class FakeJob:
    def __init__(self):
        self.trigger = None

    def reschedule(self, trigger):
        self.trigger = trigger


class FakeScheduler:
    def __init__(self):
        self.job = FakeJob()
        self.paused = False

    def get_job(self, job_id):
        return self.job

    def pause(self):
        self.paused = True


@pytest.fixture()
def worker_state(monkeypatch):
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(poll_interval_hours=6.0, snapshot_jitter_minutes=30),
    )
    worker.state.running = False
    worker.state.paused = False
    worker.state.rate_limit_failures = 0
    worker.state.scheduler = FakeScheduler()
    yield worker.state
    worker.state.running = False
    worker.state.paused = False
    worker.state.rate_limit_failures = 0
    worker.state.scheduler = None


def _job_interval(scheduler):
    return scheduler.get_job("snapshot").trigger.interval.total_seconds()


def test_backoff_seconds_doubles_and_caps():
    assert worker.backoff_seconds(6, 0) == 6 * 3600
    assert worker.backoff_seconds(6, 1) == 12 * 3600
    assert worker.backoff_seconds(6, 10) == 24 * 3600


def test_initial_next_run_is_soon_for_first_snapshot():
    result = worker._initial_next_run(has_snapshots=False)

    assert result is not None
    assert result > datetime.now(timezone.utc)


def test_initial_next_run_is_none_when_snapshots_exist():
    assert worker._initial_next_run(has_snapshots=True) is None


def test_trigger_returns_409_when_running():
    worker.state.running = True
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(worker.trigger_snapshot())
        assert exc_info.value.status_code == 409
    finally:
        worker.state.running = False


def test_rate_limited_increments_and_reschedules_with_backoff(worker_state, monkeypatch):
    def fail():
        raise RateLimited("slow down")

    monkeypatch.setattr(worker, "_snapshot_job", fail)

    asyncio.run(worker._execute_snapshot())

    assert worker.state.rate_limit_failures == 1
    assert _job_interval(worker_state.scheduler) == worker.backoff_seconds(6, 1)


def test_success_after_rate_limit_restores_base_interval(worker_state, monkeypatch):
    monkeypatch.setattr(worker, "_snapshot_job", lambda: None)
    worker.state.rate_limit_failures = 1

    asyncio.run(worker._execute_snapshot())

    assert worker.state.rate_limit_failures == 0
    assert _job_interval(worker_state.scheduler) == 6 * 3600


def test_challenge_required_pauses_and_alerts_once(worker_state, monkeypatch):
    def fail():
        raise ChallengeRequired("checkpoint")

    monkeypatch.setattr(worker, "_snapshot_job", fail)
    messages = []
    monkeypatch.setattr(worker, "send_message", messages.append)

    asyncio.run(worker._execute_snapshot())

    assert worker.state.paused is True
    assert worker_state.scheduler.paused is True
    assert len(messages) == 1
