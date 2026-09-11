import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from tracker.worker import main as worker


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
