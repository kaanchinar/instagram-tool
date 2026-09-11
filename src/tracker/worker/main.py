import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select

from tracker.config import get_settings
from tracker.db import init_db, session_scope
from tracker.models import Snapshot
from tracker.notifier.telegram import send_events, send_message
from tracker.snapshotter.client import InstagramClient
from tracker.snapshotter.errors import (
    ChallengeRequired,
    LoginFailed,
    RateLimited,
    SnapshotError,
)
from tracker.snapshotter.service import fetch_with_client, run_snapshot

logger = logging.getLogger(__name__)

MAX_BACKOFF_HOURS = 24.0


def backoff_seconds(interval_hours: float, failures: int) -> float:
    return min(interval_hours * (2 ** failures), MAX_BACKOFF_HOURS) * 3600.0


@dataclass
class WorkerState:
    running: bool = False
    paused: bool = False
    rate_limit_failures: int = 0
    scheduler: AsyncIOScheduler | None = None


state = WorkerState()


def _initial_next_run(has_snapshots: bool) -> datetime | None:
    if has_snapshots:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def run_migrations() -> None:
    command.upgrade(Config("alembic.ini"), "head")


def _snapshot_job() -> None:
    client = InstagramClient(get_settings())
    with session_scope() as session:
        run_snapshot(
            session,
            lambda: fetch_with_client(client),
            lambda events: send_events(session, events),
        )


async def _execute_snapshot() -> None:
    if state.running:
        logger.info("snapshot already running; skipping")
        return
    state.running = True
    try:
        await asyncio.to_thread(_snapshot_job)
    except RateLimited:
        state.rate_limit_failures += 1
        delay = backoff_seconds(
            get_settings().poll_interval_hours, state.rate_limit_failures
        )
        logger.warning("rate limited; rescheduling snapshot in %.0f seconds", delay)
        _reschedule(delay)
    except (ChallengeRequired, LoginFailed) as exc:
        state.paused = True
        if state.scheduler is not None:
            state.scheduler.pause()
        logger.error("snapshot paused: %s", exc)
        await asyncio.to_thread(
            send_message,
            f"Instagram tracker needs attention: {exc}. "
            "Re-run the login command and restart the worker.",
        )
    except SnapshotError:
        logger.exception("snapshot failed")
    except Exception:
        logger.exception("unexpected snapshot error")
    else:
        state.rate_limit_failures = 0
    finally:
        state.running = False


def _reschedule(delay_seconds: float) -> None:
    if state.scheduler is None:
        return
    job = state.scheduler.get_job("snapshot")
    if job is None:
        return
    jitter = min(
        int(get_settings().snapshot_jitter_minutes * 60),
        max(int(delay_seconds / 2), 0),
    )
    job.reschedule(
        trigger=IntervalTrigger(
            hours=delay_seconds / 3600.0,
            jitter=jitter,
        )
    )


def _start_scheduler() -> None:
    settings = get_settings()
    with session_scope() as session:
        has_snapshots = session.scalar(select(func.count()).select_from(Snapshot)) > 0
    scheduler = AsyncIOScheduler(timezone="UTC")
    kwargs = {}
    next_run = _initial_next_run(has_snapshots)
    if next_run is not None:
        kwargs["next_run_time"] = next_run
    scheduler.add_job(
        _execute_snapshot,
        "interval",
        hours=settings.poll_interval_hours,
        jitter=min(
            int(settings.snapshot_jitter_minutes * 60),
            int(settings.poll_interval_hours * 3600 / 2),
        ),
        id="snapshot",
        **kwargs,
    )
    scheduler.start()
    state.scheduler = scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run_migrations()
    init_db()
    _start_scheduler()
    try:
        yield
    finally:
        if state.scheduler is not None:
            state.scheduler.shutdown(wait=False)
        state.scheduler = None


app = FastAPI(title="Instagram Tracker Worker", lifespan=lifespan)


@app.post("/trigger", status_code=202)
async def trigger_snapshot() -> dict[str, str]:
    if state.running:
        raise HTTPException(status_code=409, detail="snapshot already running")
    asyncio.get_running_loop().create_task(_execute_snapshot())
    return {"status": "triggered"}


@app.get("/healthz")
def healthz() -> dict:
    last = None
    try:
        with session_scope() as session:
            snapshot = session.scalars(
                select(Snapshot).order_by(Snapshot.id.desc()).limit(1)
            ).first()
            if snapshot is not None:
                last = {
                    "id": snapshot.id,
                    "taken_at": snapshot.taken_at.isoformat(),
                    "status": snapshot.status.value,
                    "error": snapshot.error,
                    "follower_count": snapshot.follower_count,
                    "following_count": snapshot.following_count,
                }
    except Exception:
        logger.exception("healthz could not read the latest snapshot")
    return {
        "status": "paused" if state.paused else "ok",
        "running": state.running,
        "paused": state.paused,
        "rate_limit_failures": state.rate_limit_failures,
        "last_snapshot": last,
    }


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=9000)


if __name__ == "__main__":
    main()
