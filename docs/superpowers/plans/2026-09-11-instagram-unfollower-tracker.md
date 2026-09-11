# Instagram Unfollower Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted Docker Compose app that snapshots the owner's Instagram followers/following via `instagrapi`, diffs consecutive snapshots, stores results in Postgres, serves an HTMX dashboard, and sends batched Telegram alerts.

**Architecture:** One Python package (`src/tracker`) shared by two runtime processes: a `worker` (FastAPI micro-app on internal port 9000 hosting an APScheduler snapshot loop) and a `web` (FastAPI + Jinja2 + HTMX dashboard bound to `127.0.0.1:8000`). Both talk to Postgres via SQLAlchemy 2; Alembic migrations run from the worker at startup. Diff logic and queries are pure, unit-testable modules.

**Tech Stack:** Python 3.12 (Docker `python:3.12-slim`) · FastAPI + Jinja2 + HTMX (vendored, no JS build) · Chart.js (CDN) · SQLAlchemy 2 + Alembic · APScheduler 3 · httpx · pydantic-settings · pytest (SQLite in tests)

**Spec:** `docs/superpowers/specs/2026-09-11-instagram-unfollower-tracker-design.md`

## Global Constraints

- Python 3.12 in Docker (`python:3.12-slim`); `requires-python = ">=3.12"`.
- All timestamps are UTC and use `DateTime(timezone=True)`.
- Instagram user IDs are `BigInteger`; auto-increment PKs use `BigInteger().with_variant(Integer, "sqlite")`.
- Tests run against SQLite only (`Base.metadata.create_all`); no live Instagram calls in tests, ever. Tests do not exercise Alembic.
- `web` publishes only `127.0.0.1:8000`; worker port 9000 is never published. No auth in v1.
- Default poll interval 6h (`POLL_INTERVAL_HOURS`); documented recommended floor is 1h.
- Session file lives only on the `igsession` volume, mounted only into `worker`.
- The first-ever snapshot is a baseline: persist entries + update `people`, create **no** events.
- Failed snapshots are persisted (`status=failed`, `error`) and are never diff baselines.
- Whitelisted users' events are stored/displayed but excluded from Telegram.
- Telegram delivery failures are logged and swallowed; they never fail a snapshot.
- All `people` matching is by `ig_user_id`, never username.
- No code comments (lint directives like `# noqa` are allowed).

## Milestone Mapping (spec §16)

| Spec milestone | Plan tasks |
|---|---|
| 1. Skeleton (repo, pyproject, Dockerfile, compose, config, pytest green) | Task 1 |
| 2. Data layer (models + Alembic, run on worker start) | Task 2 (models/migration), Task 7 (worker startup) |
| 3. Snapshotter (client session reuse, fetch, service, CLI) | Task 4, Task 6 |
| 4. Diff engine (`core/diff.py`, `people` updates, tests) | Task 3, Task 6 |
| 5. Dashboard (routes, chart, banner, refresh) | Task 8 |
| 6. Notifier (Telegram batching, whitelist exclusion) | Task 5, Task 8 |
| 7. Polish (README, error-path verification) | Task 9 |

---

### Task 1: Project Skeleton, Config, and DB Layer

**Files:**
- Create: `pyproject.toml`, `.env.example`, `Dockerfile`, `.dockerignore`, `compose.yaml`, `README.md` (stub)
- Create: `src/tracker/__init__.py`, `src/tracker/config.py`, `src/tracker/db.py`
- Create: `tests/conftest.py`, `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing (first task)
- Produces:
  - `tracker.config.Settings` with fields `ig_username`, `ig_password`, `ig_session_path: Path`, `database_url: str`, `poll_interval_hours: float`, `snapshot_jitter_minutes: int`, `telegram_bot_token: str`, `telegram_chat_id: str`, `worker_internal_url: str`, `log_level: str`
  - `tracker.config.get_settings() -> Settings` (lru-cached)
  - `tracker.db.Base`, `tracker.db.make_engine(url) -> Engine`, `tracker.db.init_db(url: str | None = None) -> Engine`, `tracker.db.get_session_factory()`, `tracker.db.session_scope()` context manager yielding `Session`
  - pytest fixtures `engine` (SQLite + StaticPool, tables created) and `session` (`tests/conftest.py`)

- [ ] **Step 1: Commit the existing design spec**

```bash
git add .gitignore docs
git commit -m "docs: add design spec and implementation plan"
```

- [ ] **Step 2: Write the failing smoke tests**

`tests/test_smoke.py`:

```python
from sqlalchemy import text

from tracker.config import Settings
from tracker.db import make_engine


def test_settings_defaults():
    settings = Settings(_env_file=None)
    assert settings.poll_interval_hours == 6.0
    assert settings.database_url.startswith("postgresql+psycopg")
    assert settings.worker_internal_url == "http://worker:9000"


def test_make_engine_supports_sqlite():
    engine = make_engine("sqlite://")
    with engine.connect() as connection:
        assert connection.execute(text("select 1")).scalar() == 1
    engine.dispose()
```

- [ ] **Step 3: Create the package manifest, README stub, and virtualenv**

`pyproject.toml`:

```toml
[project]
name = "instagram-tracker"
version = "0.1.0"
description = "Self-hosted Instagram unfollower tracker"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "alembic>=1.14",
    "apscheduler>=3.10,<4",
    "fastapi>=0.115",
    "httpx>=0.27",
    "instagrapi>=2.0",
    "jinja2>=3.1",
    "psycopg[binary]>=3.2",
    "pydantic-settings>=2.6",
    "sqlalchemy>=2.0",
    "uvicorn[standard]>=0.32",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/tracker"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`README.md` (stub; fully rewritten in Task 9):

```markdown
# Instagram Unfollower Tracker

Self-hosted tool that snapshots your Instagram followers/following, detects
changes, and shows them in a local dashboard with optional Telegram alerts.

See `docs/superpowers/specs/2026-09-11-instagram-unfollower-tracker-design.md`.
```

Create `src/tracker/__init__.py` as an empty file so the editable install has a package, then:

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
```

- [ ] **Step 4: Run the smoke tests to verify they fail**

Run: `.venv/bin/pytest tests/test_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.config'` (`config.py`/`db.py` do not exist yet).

- [ ] **Step 5: Create the remaining project files**

`.env.example`:

```
IG_USERNAME=your_username
IG_PASSWORD=your_password
IG_SESSION_PATH=/data/session.json
DATABASE_URL=postgresql+psycopg://tracker:tracker@db:5432/tracker
POLL_INTERVAL_HOURS=6
SNAPSHOT_JITTER_MINUTES=30
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
WORKER_INTERNAL_URL=http://worker:9000
LOG_LEVEL=INFO
```

`Dockerfile`:

```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install .

EXPOSE 8000 9000

CMD ["python", "-m", "tracker.worker.main"]
```

`.dockerignore`:

```
.git
.gitignore
.venv
__pycache__
*.pyc
.pytest_cache
*.egg-info
docs
.env
*.db
```

`compose.yaml`:

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: tracker
      POSTGRES_PASSWORD: tracker
      POSTGRES_DB: tracker
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U tracker"]
      interval: 5s
      timeout: 3s
      retries: 10

  worker:
    build: .
    command: python -m tracker.worker.main
    env_file: .env
    restart: unless-stopped
    volumes: [igsession:/data]
    depends_on:
      db: { condition: service_healthy }

  web:
    build: .
    command: uvicorn tracker.web.main:app --host 0.0.0.0 --port 8000
    env_file: .env
    restart: unless-stopped
    ports: ["127.0.0.1:8000:8000"]
    depends_on:
      db: { condition: service_healthy }

volumes:
  pgdata:
  igsession:
```

`src/tracker/__init__.py` (empty file), `src/tracker/config.py`:

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ig_username: str = ""
    ig_password: str = ""
    ig_session_path: Path = Path("/data/session.json")
    database_url: str = "postgresql+psycopg://tracker:tracker@db:5432/tracker"
    poll_interval_hours: float = 6.0
    snapshot_jitter_minutes: int = 30
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    worker_internal_url: str = "http://worker:9000"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`src/tracker/db.py`:

```python
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from tracker.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return create_engine(database_url, pool_pre_ping=True)


def init_db(database_url: str | None = None) -> Engine:
    global _engine, _session_factory
    _engine = make_engine(database_url or get_settings().database_url)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        init_db()
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

`tests/conftest.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tracker import models  # noqa: F401
from tracker.db import Base


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
```

Note: `tests/conftest.py` imports `tracker.models`, which does not exist until Task 2. Create an empty `src/tracker/models.py` in this task so conftest imports; Task 2 replaces it.

- [ ] **Step 6: Run the smoke tests to verify they pass**

Run: `.venv/bin/pytest tests/test_smoke.py -v`
Expected: `2 passed`

- [ ] **Step 7: Verify the compose file parses**

```bash
cp -n .env.example .env
docker compose config --quiet
```

Expected: exit 0. (The Docker image build is deferred to Task 2, which creates `alembic.ini` and `alembic/` that the Dockerfile copies.)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .env.example Dockerfile .dockerignore compose.yaml README.md src tests
git commit -m "chore: add project skeleton, config, and db layer"
```

---

### Task 2: SQLAlchemy Models and Initial Alembic Migration

**Files:**
- Create: `src/tracker/models.py` (replace the empty placeholder)
- Create: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_initial.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `tracker.db.Base` (Task 1)
- Produces:
  - Enums: `SnapshotStatus` (`ok`, `failed`), `Direction` (`follower`, `following`), `EventType` (`new_follower`, `unfollowed`, `i_followed`, `i_unfollowed`) — all `str, enum.Enum`
  - `BigIntPK = BigInteger().with_variant(Integer, "sqlite")` (module-level SQLAlchemy type)
  - ORM classes: `Snapshot` (`id`, `taken_at`, `follower_count: int | None`, `following_count: int | None`, `status`, `error: str | None`, `entries` relationship), `SnapshotEntry` (`id`, `snapshot_id`, `ig_user_id`, `username`, `direction`), `Person` (`ig_user_id` PK, `username`, `full_name`, `is_follower`, `is_following`, `whitelisted`, `first_seen_at`, `last_changed_at`), `Event` (`id`, `detected_at`, `ig_user_id`, `username`, `type`, `snapshot_id`)
  - Constraint name `uq_snapshot_entry` on `(snapshot_id, ig_user_id, direction)`

- [ ] **Step 1: Write the failing model tests**

`tests/test_models.py`:

```python
from datetime import datetime

from sqlalchemy import select

from tracker.models import (
    Direction,
    Person,
    Snapshot,
    SnapshotEntry,
    SnapshotStatus,
)


def test_snapshot_entries_roundtrip(session):
    snapshot = Snapshot(
        taken_at=datetime(2026, 9, 11),
        follower_count=1,
        following_count=1,
        status=SnapshotStatus.ok,
    )
    session.add(snapshot)
    session.flush()
    session.add(
        SnapshotEntry(
            snapshot_id=snapshot.id,
            ig_user_id=123456789012,
            username="alice",
            direction=Direction.follower,
        )
    )
    session.commit()

    entry = session.scalars(select(SnapshotEntry)).one()
    assert entry.ig_user_id == 123456789012
    assert entry.direction == Direction.follower
    assert snapshot.entries[0].username == "alice"


def test_person_roundtrip_with_large_id(session):
    person = Person(
        ig_user_id=987654321098,
        username="bob",
        is_follower=True,
        is_following=False,
        first_seen_at=datetime(2026, 9, 11),
    )
    session.add(person)
    session.commit()

    stored = session.get(Person, 987654321098)
    assert stored.username == "bob"
    assert stored.is_follower is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'Direction' from 'tracker.models'`

- [ ] **Step 3: Implement the models**

`src/tracker/models.py`:

```python
import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tracker.db import Base

BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class SnapshotStatus(str, enum.Enum):
    ok = "ok"
    failed = "failed"


class Direction(str, enum.Enum):
    follower = "follower"
    following = "following"


class EventType(str, enum.Enum):
    new_follower = "new_follower"
    unfollowed = "unfollowed"
    i_followed = "i_followed"
    i_unfollowed = "i_unfollowed"


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    follower_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    following_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[SnapshotStatus] = mapped_column(
        Enum(SnapshotStatus, name="snapshot_status")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    entries: Mapped[list["SnapshotEntry"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class SnapshotEntry(Base):
    __tablename__ = "snapshot_entries"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "ig_user_id", "direction", name="uq_snapshot_entry"
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id"), index=True
    )
    ig_user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(Text)
    direction: Mapped[Direction] = mapped_column(Enum(Direction, name="direction"))

    snapshot: Mapped[Snapshot] = relationship(back_populates="entries")


class Person(Base):
    __tablename__ = "people"

    ig_user_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    username: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_follower: Mapped[bool] = mapped_column(Boolean)
    is_following: Mapped[bool] = mapped_column(Boolean)
    whitelisted: Mapped[bool] = mapped_column(Boolean)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ig_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str] = mapped_column(Text)
    type: Mapped[EventType] = mapped_column(Enum(EventType, name="event_type"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"))
```

- [ ] **Step 4: Run the model tests to verify they pass**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: `2 passed`

- [ ] **Step 5: Create the Alembic setup**

`alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

`alembic/env.py`:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from tracker import models  # noqa: F401
from tracker.config import get_settings
from tracker.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = get_settings().database_url
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

`alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

`alembic/versions/0001_initial.py`:

```python
"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-11

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "snapshots",
        sa.Column("id", BIGINT_PK, primary_key=True),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("following_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ok", "failed", name="snapshot_status"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_snapshots_taken_at", "snapshots", ["taken_at"])

    op.create_table(
        "snapshot_entries",
        sa.Column("id", BIGINT_PK, primary_key=True),
        sa.Column(
            "snapshot_id",
            BIGINT_PK,
            sa.ForeignKey("snapshots.id"),
            nullable=False,
        ),
        sa.Column("ig_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("follower", "following", name="direction"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "snapshot_id", "ig_user_id", "direction", name="uq_snapshot_entry"
        ),
    )
    op.create_index(
        "ix_snapshot_entries_snapshot_id", "snapshot_entries", ["snapshot_id"]
    )

    op.create_table(
        "people",
        sa.Column("ig_user_id", BIGINT_PK, primary_key=True),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column(
            "is_follower", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "is_following", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "whitelisted", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "events",
        sa.Column("id", BIGINT_PK, primary_key=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ig_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "new_follower",
                "unfollowed",
                "i_followed",
                "i_unfollowed",
                name="event_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            BIGINT_PK,
            sa.ForeignKey("snapshots.id"),
            nullable=False,
        ),
    )
    op.create_index("ix_events_detected_at", "events", ["detected_at"])
    op.create_index("ix_events_ig_user_id", "events", ["ig_user_id"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("people")
    op.drop_table("snapshot_entries")
    op.drop_table("snapshots")
    sa.Enum(name="event_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="direction").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="snapshot_status").drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 6: Verify the migration runs up and down against SQLite**

```bash
rm -f /tmp/opencode/igtracker-migration.db
DATABASE_URL=sqlite:////tmp/opencode/igtracker-migration.db .venv/bin/alembic upgrade head
DATABASE_URL=sqlite:////tmp/opencode/igtracker-migration.db .venv/bin/alembic downgrade base
DATABASE_URL=sqlite:////tmp/opencode/igtracker-migration.db .venv/bin/alembic upgrade head
```

Expected: each command exits 0; the final upgrade logs "Running upgrade -> 0001, initial schema".

- [ ] **Step 7: Run the full test suite and the Docker build**

Run: `.venv/bin/pytest -q && docker compose build worker`
Expected: all tests pass; image builds (now that `alembic.ini`/`alembic/` exist).

- [ ] **Step 8: Commit**

```bash
git add alembic.ini alembic src/tracker/models.py tests/test_models.py
git commit -m "feat: add SQLAlchemy models and initial Alembic migration"
```

---

### Task 3: Diff Engine and Dashboard Queries

**Files:**
- Create: `src/tracker/core/__init__.py`, `src/tracker/core/diff.py`, `src/tracker/core/queries.py`
- Test: `tests/test_diff.py`, `tests/test_queries.py`

**Interfaces:**
- Consumes: `tracker.models` enums and ORM classes (Task 2)
- Produces:
  - `tracker.core.diff.UserRecord` dataclass: `ig_user_id: int`, `username: str`, `full_name: str | None = None`
  - `tracker.core.diff.DetectedEvent` dataclass: `ig_user_id: int`, `username: str`, `type: EventType`
  - `tracker.core.diff.diff_snapshots(prev_followers, prev_following, new_followers, new_following) -> list[DetectedEvent]`
  - `tracker.core.diff.apply_people_state(people: dict[int, Person], followers: dict[int, UserRecord], following: dict[int, UserRecord], now: datetime) -> None`
  - `tracker.core.queries.EventWithWhitelist` dataclass: `event: Event`, `whitelisted: bool`
  - `tracker.core.queries.not_following_back(session) -> list[Person]`
  - `tracker.core.queries.fans(session) -> list[Person]`
  - `tracker.core.queries.user_history(session, ig_user_id) -> list[Event]`
  - `tracker.core.queries.counts_series(session) -> list[tuple[datetime, int, int]]`
  - `tracker.core.queries.recent_events(session, limit=10, event_type=None, hide_whitelisted=False) -> list[EventWithWhitelist]`
  - `tracker.core.queries.latest_ok_snapshot(session) -> Snapshot | None`
  - `tracker.core.queries.get_person(session, ig_user_id) -> Person | None`

- [ ] **Step 1: Write the failing diff tests**

`tests/test_diff.py`:

```python
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
```

- [ ] **Step 2: Run the diff tests to verify they fail**

Run: `.venv/bin/pytest tests/test_diff.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.core'`

- [ ] **Step 3: Implement the diff engine**

`src/tracker/core/__init__.py` (empty file), `src/tracker/core/diff.py`:

```python
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
```

- [ ] **Step 4: Run the diff tests to verify they pass**

Run: `.venv/bin/pytest tests/test_diff.py -v`
Expected: `10 passed`

- [ ] **Step 5: Write the failing query tests**

`tests/test_queries.py`:

```python
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
```

- [ ] **Step 6: Run the query tests to verify they fail**

Run: `.venv/bin/pytest tests/test_queries.py -v`
Expected: FAIL with `AttributeError: module 'tracker.core.queries' has no attribute ...` (module missing)

- [ ] **Step 7: Implement the queries**

`src/tracker/core/queries.py`:

```python
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.models import Event, EventType, Person, Snapshot, SnapshotStatus


@dataclass(frozen=True)
class EventWithWhitelist:
    event: Event
    whitelisted: bool


def not_following_back(session: Session) -> list[Person]:
    stmt = (
        select(Person)
        .where(Person.is_following.is_(True), Person.is_follower.is_(False))
        .order_by(Person.username)
    )
    return list(session.scalars(stmt))


def fans(session: Session) -> list[Person]:
    stmt = (
        select(Person)
        .where(Person.is_follower.is_(True), Person.is_following.is_(False))
        .order_by(Person.username)
    )
    return list(session.scalars(stmt))


def user_history(session: Session, ig_user_id: int) -> list[Event]:
    stmt = (
        select(Event)
        .where(Event.ig_user_id == ig_user_id)
        .order_by(Event.detected_at.desc(), Event.id.desc())
    )
    return list(session.scalars(stmt))


def counts_series(session: Session) -> list[tuple[datetime, int, int]]:
    stmt = (
        select(Snapshot.taken_at, Snapshot.follower_count, Snapshot.following_count)
        .where(Snapshot.status == SnapshotStatus.ok)
        .order_by(Snapshot.taken_at, Snapshot.id)
    )
    return [(row[0], row[1], row[2]) for row in session.execute(stmt)]


def recent_events(
    session: Session,
    limit: int = 10,
    event_type: EventType | None = None,
    hide_whitelisted: bool = False,
) -> list[EventWithWhitelist]:
    stmt = (
        select(Event, Person.whitelisted)
        .join(Person, Person.ig_user_id == Event.ig_user_id, isouter=True)
        .order_by(Event.detected_at.desc(), Event.id.desc())
        .limit(limit)
    )
    if event_type is not None:
        stmt = stmt.where(Event.type == event_type)
    if hide_whitelisted:
        stmt = stmt.where(Person.whitelisted.is_not(True))
    rows = session.execute(stmt).all()
    return [
        EventWithWhitelist(event=row[0], whitelisted=bool(row[1])) for row in rows
    ]


def latest_ok_snapshot(session: Session) -> Snapshot | None:
    stmt = (
        select(Snapshot)
        .where(Snapshot.status == SnapshotStatus.ok)
        .order_by(Snapshot.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def get_person(session: Session, ig_user_id: int) -> Person | None:
    return session.get(Person, ig_user_id)
```

- [ ] **Step 8: Run all tests to verify they pass**

Run: `.venv/bin/pytest -q`
Expected: all tests pass (`6` new query tests included).

- [ ] **Step 9: Commit**

```bash
git add src/tracker/core tests/test_diff.py tests/test_queries.py
git commit -m "feat: add diff engine and dashboard queries"
```

---

### Task 4: Instagram Client (Session Reuse, Fetch, Error Mapping)

**Files:**
- Create: `src/tracker/snapshotter/__init__.py`, `src/tracker/snapshotter/errors.py`, `src/tracker/snapshotter/client.py`
- Test: `tests/test_snapshotter.py`

**Interfaces:**
- Consumes: `tracker.config.Settings` (Task 1), `tracker.core.diff.UserRecord` (Task 3)
- Produces:
  - Errors: `SnapshotError`, `RateLimited`, `ChallengeRequired`, `LoginFailed`, `FetchFailed` (all except base subclass `SnapshotError`)
  - `tracker.snapshotter.client.InstagramClient(settings: Settings | None = None)` with methods `connect() -> None`, `fetch_followers() -> dict[int, UserRecord]`, `fetch_following() -> dict[int, UserRecord]`
  - Behavior contract: if the session file exists and `load_settings` + `account_info()` succeeds, no password login happens; otherwise a fresh `Client()` logs in with credentials and dumps the session file; `delay_range = [2, 6]`.
  - Error mapping: `PleaseWaitFewMinutes`/`RateLimitError`/`ClientThrottledError` → `RateLimited`; `ChallengeRequired`/`TwoFactorRequired` → `ChallengeRequired`; `UserPasswordInvalid`/`LoginRequired` → `LoginFailed`; anything else → `FetchFailed`.

- [ ] **Step 1: Write the failing client tests**

`tests/test_snapshotter.py`:

```python
from datetime import datetime

import pytest
from instagrapi.exceptions import (
    ChallengeRequired as InstagramChallengeRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    UserPasswordInvalid,
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
        raise UserPasswordInvalid("bad password")

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_snapshotter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.snapshotter'`

- [ ] **Step 3: Implement errors and client**

`src/tracker/snapshotter/__init__.py` (empty file), `src/tracker/snapshotter/errors.py`:

```python
class SnapshotError(Exception):
    pass


class RateLimited(SnapshotError):
    pass


class ChallengeRequired(SnapshotError):
    pass


class LoginFailed(SnapshotError):
    pass


class FetchFailed(SnapshotError):
    pass
```

`src/tracker/snapshotter/client.py`:

```python
from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired as InstagramChallengeRequired,
    ClientThrottledError,
    LoginRequired,
    PleaseWaitFewMinutes,
    RateLimitError,
    TwoFactorRequired,
    UserPasswordInvalid,
)

from tracker.config import Settings, get_settings
from tracker.core.diff import UserRecord
from tracker.snapshotter.errors import (
    ChallengeRequired,
    FetchFailed,
    LoginFailed,
    RateLimited,
)

RATE_LIMIT_ERRORS = (PleaseWaitFewMinutes, RateLimitError, ClientThrottledError)


def _wrap_error(exc: Exception) -> Exception:
    if isinstance(exc, RATE_LIMIT_ERRORS):
        return RateLimited(str(exc))
    if isinstance(exc, (InstagramChallengeRequired, TwoFactorRequired)):
        return ChallengeRequired(str(exc))
    if isinstance(exc, (UserPasswordInvalid, LoginRequired)):
        return LoginFailed(str(exc))
    return FetchFailed(str(exc))


def _to_records(users: dict) -> dict[int, UserRecord]:
    records: dict[int, UserRecord] = {}
    for key, user in users.items():
        user_id = int(getattr(user, "pk", key))
        records[user_id] = UserRecord(
            ig_user_id=user_id,
            username=getattr(user, "username", ""),
            full_name=getattr(user, "full_name", None),
        )
    return records


class InstagramClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client: Client | None = None
        self._own_user_id: int | None = None

    def connect(self) -> None:
        if self._client is not None:
            return
        client = Client()
        client.delay_range = [2, 6]
        session_path = self.settings.ig_session_path
        if session_path.exists():
            try:
                client.load_settings(session_path)
                client.account_info()
                self._client = client
                return
            except Exception:
                client = Client()
                client.delay_range = [2, 6]
        try:
            client.login(self.settings.ig_username, self.settings.ig_password)
            client.account_info()
        except Exception as exc:
            raise _wrap_error(exc) from exc
        session_path.parent.mkdir(parents=True, exist_ok=True)
        client.dump_settings(session_path)
        self._client = client

    def _own_id(self) -> int:
        self.connect()
        if self._own_user_id is None:
            try:
                info = self._client.user_info_by_username(self.settings.ig_username)
            except Exception as exc:
                raise _wrap_error(exc) from exc
            self._own_user_id = int(info.pk)
        return self._own_user_id

    def _fetch(self, method: str) -> dict[int, UserRecord]:
        self.connect()
        try:
            users = getattr(self._client, method)(self._own_id(), use_cache=False)
        except Exception as exc:
            raise _wrap_error(exc) from exc
        return _to_records(users)

    def fetch_followers(self) -> dict[int, UserRecord]:
        return self._fetch("user_followers")

    def fetch_following(self) -> dict[int, UserRecord]:
        return self._fetch("user_following")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_snapshotter.py -v`
Expected: `8 passed`

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/tracker/snapshotter tests/test_snapshotter.py
git commit -m "feat: add instagrapi client with session reuse and error mapping"
```

---

### Task 5: Telegram Notifier

**Files:**
- Create: `src/tracker/notifier/__init__.py`, `src/tracker/notifier/telegram.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: `tracker.config.get_settings` (Task 1), `tracker.models.Event/EventType/Person` (Task 2)
- Produces:
  - `tracker.notifier.telegram.format_events(events: list[Event], moment: datetime | None = None) -> str`
  - `tracker.notifier.telegram.send_message(text: str) -> bool` (returns False when unconfigured or delivery fails)
  - `tracker.notifier.telegram.send_events(session: Session, events: list[Event]) -> bool` (filters whitelisted IDs, then `send_message`)
  - Message format contract:

    ```
    Instagram update (2026-09-11 18:03 UTC)
    Unfollowed (2): @alice, @bob
    New followers (1): @carol
    You followed (1): @dave
    You unfollowed (1): @erin
    ```

- [ ] **Step 1: Write the failing notifier tests**

`tests/test_notifier.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.notifier'`

- [ ] **Step 3: Implement the notifier**

`src/tracker/notifier/__init__.py` (empty file), `src/tracker/notifier/telegram.py`:

```python
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.config import get_settings
from tracker.models import Event, EventType, Person

logger = logging.getLogger(__name__)

TITLES = {
    EventType.unfollowed: "Unfollowed",
    EventType.new_follower: "New followers",
    EventType.i_followed: "You followed",
    EventType.i_unfollowed: "You unfollowed",
}
ORDER = [
    EventType.unfollowed,
    EventType.new_follower,
    EventType.i_followed,
    EventType.i_unfollowed,
]


def format_events(events: list[Event], moment: datetime | None = None) -> str:
    moment = moment or max(
        (event.detected_at for event in events), default=datetime.now(timezone.utc)
    )
    lines = [f"Instagram update ({moment.strftime('%Y-%m-%d %H:%M')} UTC)"]
    for event_type in ORDER:
        names = sorted(event.username for event in events if event.type == event_type)
        if names:
            joined = ", ".join(f"@{name}" for name in names)
            lines.append(f"{TITLES[event_type]} ({len(names)}): {joined}")
    return "\n".join(lines)


def send_message(text: str) -> bool:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("telegram notifier disabled; message not sent: %s", text)
        return False
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        response = httpx.post(
            url,
            json={"chat_id": settings.telegram_chat_id, "text": text},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("telegram delivery failed")
        return False
    return True


def send_events(session: Session, events: list[Event]) -> bool:
    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("telegram notifier disabled; skipping %d events", len(events))
        return False
    whitelisted = set(
        session.scalars(
            select(Person.ig_user_id).where(Person.whitelisted.is_(True))
        )
    )
    sendable = [event for event in events if event.ig_user_id not in whitelisted]
    if not sendable:
        logger.info("all events belong to whitelisted users; skipping telegram")
        return False
    return send_message(format_events(sendable))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_notifier.py -v`
Expected: `7 passed`

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/tracker/notifier tests/test_notifier.py
git commit -m "feat: add Telegram notifier with whitelist filtering"
```

---

### Task 6: Snapshot Service and CLI

**Files:**
- Create: `src/tracker/snapshotter/service.py`, `src/tracker/worker/__init__.py`, `src/tracker/worker/cli.py`
- Test: `tests/test_snapshotter.py` (extend with service tests)

**Interfaces:**
- Consumes: `tracker.core.diff` (Task 3), `tracker.models` (Task 2), `tracker.snapshotter.client/errors` (Task 4), `tracker.notifier.telegram` (Task 5), `tracker.db` (Task 1)
- Produces:
  - `tracker.snapshotter.service.FetchedLists` dataclass: `followers: dict[int, UserRecord]`, `following: dict[int, UserRecord]`
  - `tracker.snapshotter.service.fetch_with_client(client) -> FetchedLists` (fetches followers, then following; any error propagates before anything is persisted — partial fetch discards both)
  - `tracker.snapshotter.service.run_snapshot(session, fetch_lists, notify=None, now=None) -> Snapshot`
    - on `SnapshotError` from `fetch_lists`: persists `failed` snapshot with `error`, commits, re-raises
    - on success: inserts `ok` snapshot + entries, diffs vs previous `ok` snapshot (first snapshot → baseline, no events), inserts events, updates `people`, commits, then calls `notify(events)` if non-empty (exceptions swallowed + logged)
  - `tracker.worker.cli.main()` with subcommands `login` (interactive session bootstrap handling 2FA/challenge) and `snapshot` (one-shot, notifies)

- [ ] **Step 1: Write the failing service tests (append to `tests/test_snapshotter.py`)**

```python
from tracker.models import Event, Person, Snapshot, SnapshotEntry, SnapshotStatus
from tracker.snapshotter.errors import FetchFailed
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
```

- [ ] **Step 2: Run the service tests to verify they fail**

Run: `.venv/bin/pytest tests/test_snapshotter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.snapshotter.service'`

- [ ] **Step 3: Implement the service**

`src/tracker/snapshotter/service.py`:

```python
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from tracker.core.diff import UserRecord, apply_people_state, diff_snapshots
from tracker.models import (
    Direction,
    Event,
    Person,
    Snapshot,
    SnapshotEntry,
    SnapshotStatus,
)
from tracker.snapshotter.client import InstagramClient
from tracker.snapshotter.errors import SnapshotError

logger = logging.getLogger(__name__)


@dataclass
class FetchedLists:
    followers: dict[int, UserRecord]
    following: dict[int, UserRecord]


def fetch_with_client(client: InstagramClient) -> FetchedLists:
    followers = client.fetch_followers()
    following = client.fetch_following()
    return FetchedLists(followers=followers, following=following)


def _previous_ok_snapshot(session: Session, before_id: int) -> Snapshot | None:
    stmt = (
        select(Snapshot)
        .where(Snapshot.status == SnapshotStatus.ok, Snapshot.id < before_id)
        .order_by(Snapshot.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def _entries_map(
    session: Session, snapshot_id: int, direction: Direction
) -> dict[int, UserRecord]:
    stmt = select(SnapshotEntry).where(
        SnapshotEntry.snapshot_id == snapshot_id,
        SnapshotEntry.direction == direction,
    )
    return {
        entry.ig_user_id: UserRecord(entry.ig_user_id, entry.username, None)
        for entry in session.scalars(stmt)
    }


def run_snapshot(
    session: Session,
    fetch_lists: Callable[[], FetchedLists],
    notify: Callable[[list[Event]], None] | None = None,
    now: datetime | None = None,
) -> Snapshot:
    now = now or datetime.now(timezone.utc)
    try:
        lists = fetch_lists()
    except SnapshotError as exc:
        failed = Snapshot(
            taken_at=now,
            follower_count=None,
            following_count=None,
            status=SnapshotStatus.failed,
            error=str(exc),
        )
        session.add(failed)
        session.commit()
        raise

    snapshot = Snapshot(
        taken_at=now,
        follower_count=len(lists.followers),
        following_count=len(lists.following),
        status=SnapshotStatus.ok,
    )
    session.add(snapshot)
    session.flush()

    for user_id, record in lists.followers.items():
        session.add(
            SnapshotEntry(
                snapshot_id=snapshot.id,
                ig_user_id=user_id,
                username=record.username,
                direction=Direction.follower,
            )
        )
    for user_id, record in lists.following.items():
        session.add(
            SnapshotEntry(
                snapshot_id=snapshot.id,
                ig_user_id=user_id,
                username=record.username,
                direction=Direction.following,
            )
        )

    previous = _previous_ok_snapshot(session, snapshot.id)
    events: list[Event] = []
    if previous is not None:
        detected = diff_snapshots(
            _entries_map(session, previous.id, Direction.follower),
            _entries_map(session, previous.id, Direction.following),
            lists.followers,
            lists.following,
        )
        for item in detected:
            event = Event(
                detected_at=now,
                ig_user_id=item.ig_user_id,
                username=item.username,
                type=item.type,
                snapshot_id=snapshot.id,
            )
            session.add(event)
            events.append(event)

    people = {person.ig_user_id: person for person in session.scalars(select(Person))}
    apply_people_state(people, lists.followers, lists.following, now)
    for person in people.values():
        session.add(person)

    session.commit()

    if events and notify is not None:
        try:
            notify(events)
        except Exception:
            logger.exception("notifier failed")

    return snapshot
```

- [ ] **Step 4: Run the service tests to verify they pass**

Run: `.venv/bin/pytest tests/test_snapshotter.py -v`
Expected: `13 passed`

- [ ] **Step 5: Implement the CLI**

`src/tracker/worker/__init__.py` (empty file), `src/tracker/worker/cli.py`:

```python
import argparse
import getpass
import logging

from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired

from tracker.config import get_settings
from tracker.db import init_db, session_scope
from tracker.notifier.telegram import send_events
from tracker.snapshotter.client import InstagramClient
from tracker.snapshotter.service import fetch_with_client, run_snapshot


def login() -> None:
    settings = get_settings()
    username = settings.ig_username or input("Instagram username: ").strip()
    password = settings.ig_password or getpass.getpass("Instagram password: ")
    client = Client()
    client.delay_range = [2, 6]
    try:
        client.login(username, password)
    except TwoFactorRequired:
        code = input("Two-factor code: ").strip()
        client.two_factor_login(code)
    except ChallengeRequired:
        code = input("Challenge code sent by Instagram: ").strip()
        client.challenge_code(code)
    session_path = settings.ig_session_path
    session_path.parent.mkdir(parents=True, exist_ok=True)
    client.dump_settings(session_path)
    print(f"Session saved to {session_path}")


def snapshot() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    init_db()
    client = InstagramClient(settings)
    with session_scope() as session:
        result = run_snapshot(
            session,
            lambda: fetch_with_client(client),
            lambda events: send_events(session, events),
        )
        print(
            f"Snapshot #{result.id} {result.status.value}: "
            f"followers={result.follower_count} following={result.following_count}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("login", help="Interactively create an Instagram session file")
    subparsers.add_parser("snapshot", help="Run a single snapshot now")
    args = parser.parse_args()
    if args.command == "login":
        login()
    else:
        snapshot()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Verify the CLI imports and dispatches**

Run: `.venv/bin/python -m tracker.worker.cli --help`
Expected: exits 0 and prints `{login,snapshot}` subcommands.

- [ ] **Step 7: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/tracker/snapshotter/service.py src/tracker/worker tests/test_snapshotter.py
git commit -m "feat: add snapshot service and worker CLI"
```

---

### Task 7: Worker Process (Scheduler, Trigger API, Health, Backoff/Pause)

**Files:**
- Create: `src/tracker/worker/main.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6
- Produces:
  - `tracker.worker.main.app` (FastAPI, port 9000)
  - `tracker.worker.main.state` (dataclass with `running: bool`, `paused: bool`, `rate_limit_failures: int`, `scheduler: AsyncIOScheduler | None`)
  - `tracker.worker.main.backoff_seconds(interval_hours: float, failures: int) -> float` = `min(interval * 2**failures, 24) * 3600`
  - `tracker.worker.main._initial_next_run(has_snapshots: bool) -> datetime | None` (`None` means "use the interval trigger's own first fire time"; only pass `next_run_time` to `add_job` when non-None, because `next_run_time=None` pauses an APScheduler job)
  - `POST /trigger` → `202 {"status": "triggered"}`, or `409` when `state.running`
  - `GET /healthz` → `{"status": "ok"|"paused", "running", "paused", "rate_limit_failures", "last_snapshot": {id, taken_at, status, error, follower_count, following_count} | None}`
  - Startup: `alembic upgrade head`, `init_db()`, scheduler start; first-ever run (no snapshots) fires 30 s after startup, otherwise at `POLL_INTERVAL_HOURS` with `SNAPSHOT_JITTER_MINUTES` jitter
  - On `RateLimited`: exponential backoff `interval * 2^n` capped at 24 h, counter resets on success
  - On `ChallengeRequired`/`LoginFailed`: pause scheduler, send one Telegram alert (`send_message`)

- [ ] **Step 1: Write the failing worker tests**

`tests/test_worker.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.worker.main'`

- [ ] **Step 3: Implement the worker**

`src/tracker/worker/main.py`:

```python
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
```

- [ ] **Step 4: Run the worker tests to verify they pass**

Run: `.venv/bin/pytest tests/test_worker.py -v`
Expected: `4 passed`

- [ ] **Step 5: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Verify the worker starts and serves healthz against a local SQLite DB**

```bash
rm -f /tmp/opencode/worker-smoke.db
DATABASE_URL=sqlite:////tmp/opencode/worker-smoke.db .venv/bin/python -m tracker.worker.main &
WORKER_PID=$!
sleep 3
curl -sS http://127.0.0.1:9000/healthz
kill $WORKER_PID
```

Expected: JSON with `"status": "ok"`, `"running": false`, `"paused": false` (or a `last_snapshot` of `null`). Note: the 30-second first-run timer may fire during this smoke test and produce a `failed` snapshot with an Instagram login error — that is expected without credentials; kill the process before or after, either is fine.

- [ ] **Step 7: Commit**

```bash
git add src/tracker/worker/main.py tests/test_worker.py
git commit -m "feat: add worker scheduler, trigger API, and backoff/pause handling"
```

---

### Task 8: Web Dashboard

**Files:**
- Create: `src/tracker/web/__init__.py`, `src/tracker/web/main.py`, `src/tracker/web/routes.py`
- Create: `src/tracker/web/templates/base.html`, `_banner.html`, `dashboard.html`, `events.html`, `_events_table.html`, `list.html`, `person.html`, `_person_row.html`, `_refresh_result.html`
- Create: `src/tracker/web/static/app.css`, `src/tracker/web/static/htmx.min.js` (vendored download)
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `tracker.config`, `tracker.db`, `tracker.core.queries`, `tracker.models`, worker `GET /healthz` (Task 7)
- Produces (routes):
  - `GET /` dashboard: counts from `latest_ok_snapshot`, 10 most recent events, chart canvas + `GET /api/stats/counts`, refresh button, status banner
  - `GET /events?type=<EventType value>&hide_whitelisted=<0|1>`: full page, or `_events_table.html` fragment when `HX-Request` header is present
  - `GET /lists/not-following-back`, `GET /lists/fans`: tables using `_person_row.html`
  - `GET /people/{ig_user_id}`: state + history, 404 when unknown
  - `POST /people/{ig_user_id}/whitelist`: toggles flag, returns `_person_row.html`, 404 when unknown
  - `POST /refresh`: proxies worker `/trigger`; 409 → "Snapshot already running"; connection error → "Worker unreachable"; 202 → "Snapshot triggered"; returns `_refresh_result.html`
  - `GET /api/stats/counts`: JSON `[{"taken_at": iso, "followers": int, "following": int}]` (ok snapshots ascending)
  - `tracker.web.routes.get_session` FastAPI dependency (overridable in tests)
  - `tracker.web.main.create_app() -> FastAPI` and module-level `app`; lifespan calls `init_db()` + `SELECT 1` so the web service crash-loops when Postgres is down (compose restart policy)

- [ ] **Step 1: Write the failing web tests**

`tests/test_web.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_web.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tracker.web'`

- [ ] **Step 3: Implement the app factory and routes**

`src/tracker/web/__init__.py` (empty file), `src/tracker/web/main.py`:

```python
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from tracker.config import get_settings
from tracker.db import init_db, session_scope
from tracker.web.routes import router

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    init_db()
    with session_scope() as session:
        session.execute(text("select 1"))
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Instagram Tracker", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    return app


app = create_app()
```

`src/tracker/web/routes.py`:

```python
from collections.abc import Iterator
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from tracker.config import get_settings
from tracker.core import queries
from tracker.db import session_scope
from tracker.models import EventType

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def _worker_health() -> dict | None:
    settings = get_settings()
    try:
        response = httpx.get(
            f"{settings.worker_internal_url}/healthz", timeout=2.0
        )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None


def _base_context(request: Request, **extra) -> dict:
    context = {
        "request": request,
        "worker": _worker_health(),
        "event_types": list(EventType),
    }
    context.update(extra)
    return context


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _base_context(
            request,
            counts=queries.latest_ok_snapshot(session),
            rows=queries.recent_events(session, limit=10),
        ),
    )


@router.get("/events", response_class=HTMLResponse)
def events(
    request: Request,
    type: str | None = None,
    hide_whitelisted: bool = False,
    session: Session = Depends(get_session),
):
    valid_types = {event_type.value for event_type in EventType}
    event_type = EventType(type) if type in valid_types else None
    rows = queries.recent_events(
        session,
        limit=200,
        event_type=event_type,
        hide_whitelisted=hide_whitelisted,
    )
    context = _base_context(
        request,
        rows=rows,
        active_type=event_type,
        hide_whitelisted=hide_whitelisted,
    )
    template = (
        "_events_table.html"
        if request.headers.get("HX-Request")
        else "events.html"
    )
    return templates.TemplateResponse(request, template, context)


@router.get("/lists/not-following-back", response_class=HTMLResponse)
def list_not_following_back(
    request: Request, session: Session = Depends(get_session)
):
    return templates.TemplateResponse(
        request,
        "list.html",
        _base_context(
            request,
            title="Not following back",
            people=queries.not_following_back(session),
        ),
    )


@router.get("/lists/fans", response_class=HTMLResponse)
def list_fans(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "list.html",
        _base_context(
            request,
            title="Fans",
            people=queries.fans(session),
        ),
    )


@router.get("/people/{ig_user_id}", response_class=HTMLResponse)
def person_page(
    ig_user_id: int, request: Request, session: Session = Depends(get_session)
):
    person = queries.get_person(session, ig_user_id)
    if person is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "person.html",
        _base_context(
            request,
            person=person,
            history=queries.user_history(session, ig_user_id),
        ),
    )


@router.post("/people/{ig_user_id}/whitelist", response_class=HTMLResponse)
def toggle_whitelist(
    ig_user_id: int, request: Request, session: Session = Depends(get_session)
):
    person = queries.get_person(session, ig_user_id)
    if person is None:
        raise HTTPException(status_code=404)
    person.whitelisted = not person.whitelisted
    session.commit()
    return templates.TemplateResponse(
        request,
        "_person_row.html",
        {"request": request, "person": person},
    )


@router.post("/refresh", response_class=HTMLResponse)
def refresh(request: Request):
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.worker_internal_url}/trigger", timeout=5.0
        )
    except httpx.HTTPError:
        message = "Worker unreachable"
    else:
        if response.status_code == 409:
            message = "Snapshot already running"
        elif response.status_code == 202:
            message = "Snapshot triggered"
        else:
            message = f"Unexpected worker response: {response.status_code}"
    return templates.TemplateResponse(
        request,
        "_refresh_result.html",
        {"request": request, "message": message},
    )


@router.get("/api/stats/counts")
def stats_counts(session: Session = Depends(get_session)):
    return [
        {
            "taken_at": taken_at.isoformat(),
            "followers": followers,
            "following": following,
        }
        for taken_at, followers, following in queries.counts_series(session)
    ]
```

- [ ] **Step 4: Create the templates**

`src/tracker/web/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Instagram Tracker{% endblock %}</title>
  <link rel="stylesheet" href="/static/app.css">
  <script src="/static/htmx.min.js" defer></script>
  {% block head %}{% endblock %}
</head>
<body>
  <header>
    <h1><a href="/">Instagram Tracker</a></h1>
    <nav>
      <a href="/">Dashboard</a>
      <a href="/events">Events</a>
      <a href="/lists/not-following-back">Not following back</a>
      <a href="/lists/fans">Fans</a>
    </nav>
    {% include "_banner.html" %}
  </header>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

`src/tracker/web/templates/_banner.html`:

```html
<div id="status-banner" class="banner {% if not worker %}banner-error{% elif worker.paused %}banner-warn{% elif worker.running %}banner-info{% else %}banner-ok{% endif %}">
  {% if not worker %}
    Worker unreachable — dashboard data comes from the database.
  {% elif worker.paused %}
    Worker paused — re-run the login command and restart the worker.
  {% elif worker.running %}
    Snapshot in progress…
  {% elif worker.last_snapshot %}
    Last snapshot: {{ worker.last_snapshot.taken_at }} — {{ worker.last_snapshot.status }}
  {% else %}
    No snapshots yet.
  {% endif %}
</div>
```

`src/tracker/web/templates/dashboard.html`:

```html
{% extends "base.html" %}
{% block title %}Dashboard — Instagram Tracker{% endblock %}
{% block head %}
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
{% endblock %}
{% block content %}
<section class="cards">
  <div class="card"><h2>Followers</h2><p>{{ counts.follower_count if counts else "—" }}</p></div>
  <div class="card"><h2>Following</h2><p>{{ counts.following_count if counts else "—" }}</p></div>
</section>
<section>
  <h2>Growth</h2>
  <canvas id="growth-chart" height="120"></canvas>
</section>
<section>
  <h2>Recent events
    <form class="inline" hx-post="/refresh" hx-target="#refresh-result" hx-swap="innerHTML">
      <button type="submit">Refresh now</button>
    </form>
  </h2>
  <div id="refresh-result"></div>
  {% include "_events_table.html" %}
</section>
<script>
fetch("/api/stats/counts")
  .then((response) => response.json())
  .then((series) => {
    new Chart(document.getElementById("growth-chart"), {
      type: "line",
      data: {
        labels: series.map((point) => point.taken_at),
        datasets: [
          { label: "Followers", data: series.map((point) => point.followers), borderColor: "#2563eb", tension: 0.2 },
          { label: "Following", data: series.map((point) => point.following), borderColor: "#d97706", tension: 0.2 },
        ],
      },
      options: { responsive: true, scales: { y: { beginAtZero: false } } },
    });
  });
</script>
{% endblock %}
```

`src/tracker/web/templates/_events_table.html`:

```html
<table class="events">
  <thead><tr><th>When</th><th>User</th><th>Event</th></tr></thead>
  <tbody>
  {% for row in rows %}
    <tr class="{% if row.whitelisted %}whitelisted{% endif %}">
      <td>{{ row.event.detected_at }}</td>
      <td>
        <a href="/people/{{ row.event.ig_user_id }}">@{{ row.event.username }}</a>
        {% if row.whitelisted %}<span class="tag">whitelisted</span>{% endif %}
      </td>
      <td>{{ row.event.type.value }}</td>
    </tr>
  {% else %}
    <tr><td colspan="3">No events.</td></tr>
  {% endfor %}
  </tbody>
</table>
```

`src/tracker/web/templates/events.html`:

```html
{% extends "base.html" %}
{% block title %}Events — Instagram Tracker{% endblock %}
{% block content %}
<h2>Events</h2>
<nav class="filters">
  <a hx-get="/events" hx-target="#events-table" hx-push-url="true" class="{% if not active_type %}active{% endif %}">All</a>
  {% for event_type in event_types %}
    <a hx-get="/events?type={{ event_type.value }}" hx-target="#events-table" hx-push-url="true" class="{% if active_type == event_type %}active{% endif %}">{{ event_type.value }}</a>
  {% endfor %}
  {% if hide_whitelisted %}
    <a hx-get="/events" hx-target="#events-table" hx-push-url="true">Show whitelisted</a>
  {% else %}
    <a hx-get="/events?hide_whitelisted=1" hx-target="#events-table" hx-push-url="true">Hide whitelisted</a>
  {% endif %}
</nav>
<div id="events-table">
  {% include "_events_table.html" %}
</div>
{% endblock %}
```

`src/tracker/web/templates/list.html`:

```html
{% extends "base.html" %}
{% block title %}{{ title }} — Instagram Tracker{% endblock %}
{% block content %}
<h2>{{ title }}</h2>
<table class="people">
  <thead><tr><th>Username</th><th>Name</th><th>Whitelisted</th><th></th></tr></thead>
  <tbody>
  {% for person in people %}
    {% include "_person_row.html" %}
  {% else %}
    <tr><td colspan="4">Nobody here.</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

`src/tracker/web/templates/_person_row.html`:

```html
<tr id="person-{{ person.ig_user_id }}">
  <td><a href="/people/{{ person.ig_user_id }}">@{{ person.username }}</a></td>
  <td>{{ person.full_name or "" }}</td>
  <td>{{ "yes" if person.whitelisted else "no" }}</td>
  <td>
    <form hx-post="/people/{{ person.ig_user_id }}/whitelist" hx-target="closest tr" hx-swap="outerHTML">
      <button type="submit">{{ "Un-whitelist" if person.whitelisted else "Whitelist" }}</button>
    </form>
  </td>
</tr>
```

`src/tracker/web/templates/person.html`:

```html
{% extends "base.html" %}
{% block title %}@{{ person.username }} — Instagram Tracker{% endblock %}
{% block content %}
<h2>@{{ person.username }}</h2>
<p>{{ person.full_name or "" }}</p>
<table class="people">
  <thead><tr><th>Username</th><th>Name</th><th>Whitelisted</th><th></th></tr></thead>
  <tbody id="person-row">
    {% include "_person_row.html" %}
  </tbody>
</table>
<p>
  Follower: {{ "yes" if person.is_follower else "no" }} ·
  Following: {{ "yes" if person.is_following else "no" }} ·
  First seen: {{ person.first_seen_at }}
</p>
<h3>History</h3>
<table class="events">
  <thead><tr><th>When</th><th>Event</th></tr></thead>
  <tbody>
  {% for event in history %}
    <tr><td>{{ event.detected_at }}</td><td>{{ event.type.value }}</td></tr>
  {% else %}
    <tr><td colspan="2">No events.</td></tr>
  {% endfor %}
  </tbody>
</table>
{% endblock %}
```

`src/tracker/web/templates/_refresh_result.html`:

```html
<p class="flash">{{ message }}</p>
```

- [ ] **Step 5: Add the stylesheet and vendor HTMX**

`src/tracker/web/static/app.css`:

```css
:root {
  color-scheme: light;
  --accent: #2563eb;
  --muted: #6b7280;
  --border: #e5e7eb;
  --ok: #ecfdf5;
  --warn: #fffbeb;
  --error: #fef2f2;
}
* { box-sizing: border-box; }
body {
  margin: 0 auto;
  max-width: 68rem;
  padding: 1rem 1.5rem 3rem;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #111827;
}
header { border-bottom: 1px solid var(--border); padding-bottom: .75rem; margin-bottom: 1.5rem; }
header h1 { margin: 0 0 .5rem; font-size: 1.4rem; }
header h1 a { color: inherit; text-decoration: none; }
nav a { margin-right: 1rem; color: var(--accent); text-decoration: none; }
nav a.active { font-weight: 600; text-decoration: underline; }
.banner { margin-top: .75rem; padding: .6rem .9rem; border-radius: .5rem; border: 1px solid var(--border); }
.banner-ok { background: var(--ok); }
.banner-warn { background: var(--warn); }
.banner-error { background: var(--error); }
.banner-info { background: #eff6ff; }
.cards { display: flex; gap: 1rem; }
.card { flex: 1; border: 1px solid var(--border); border-radius: .5rem; padding: .75rem 1rem; }
.card h2 { margin: 0; font-size: .8rem; text-transform: uppercase; color: var(--muted); }
.card p { margin: .25rem 0 0; font-size: 1.6rem; }
table { border-collapse: collapse; width: 100%; margin-top: .5rem; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--border); }
th { font-size: .75rem; text-transform: uppercase; color: var(--muted); }
tr.whitelisted { opacity: .65; }
.tag { font-size: .7rem; background: var(--border); border-radius: .75rem; padding: .1rem .45rem; margin-left: .35rem; }
.filters a { margin-right: .75rem; color: var(--accent); text-decoration: none; }
.filters a.active { font-weight: 600; text-decoration: underline; }
button { cursor: pointer; }
.flash { background: var(--ok); border-radius: .4rem; padding: .5rem .75rem; }
```

Vendor HTMX:

```bash
curl -fsSL https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js -o src/tracker/web/static/htmx.min.js
test -s src/tracker/web/static/htmx.min.js && echo "htmx vendored"
```

Expected: prints `htmx vendored`.

- [ ] **Step 6: Run the web tests to verify they pass**

Run: `.venv/bin/pytest tests/test_web.py -v`
Expected: `13 passed`

- [ ] **Step 7: Run all tests**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/tracker/web tests/test_web.py
git commit -m "feat: add web dashboard with HTMX partials, chart, and banner"
```

---

### Task 9: README, .env Safety, and End-to-End Verification

**Files:**
- Modify: `README.md` (replace stub with full documentation)
- Create: `.env` (local only, gitignored — copy of `.env.example`)
- Verify: full test suite, Docker builds, Postgres migrations, both services up, dashboard reachable

**Interfaces:**
- Consumes: everything
- Produces: documented setup/ops flow; no new code interfaces

- [ ] **Step 1: Replace `README.md` with full documentation**

```markdown
# Instagram Unfollower Tracker

Self-hosted tool that periodically snapshots your Instagram followers and
following lists, stores them locally in Postgres, diffs consecutive
snapshots, and shows who unfollowed/followed in a local web dashboard with
optional Telegram alerts.

## ⚠️ Read this first

This tool talks to Instagram's **private API** through
[`instagrapi`](https://github.com/subzeroid/instagrapi). That violates
Instagram's Terms of Service and your account could be checkpointed or, in
the worst case, banned. Mitigations are built in (session reuse, request
pacing, 6-hour default poll interval, jitter), but the risk is yours. Do not
lower `POLL_INTERVAL_HOURS` below 1.

## Features

- Detects unfollows, new followers, who you followed, and who you unfollowed
- "Not following back" and "fans" lists
- Follower/following growth chart and per-user event history
- Batched Telegram alerts (whitelisted users are excluded from alerts)
- Runs unattended via Docker Compose; all data stays on your machine

## Requirements

- Docker and Docker Compose
- An Instagram account (ideally not your primary one)
- Optional: a Telegram bot token + chat id for alerts

## Quick start

```bash
cp .env.example .env
# edit .env: set IG_USERNAME and IG_PASSWORD
docker compose run --rm worker python -m tracker.worker.cli login
docker compose up -d
```

Open <http://127.0.0.1:8000>. The first snapshot is a baseline — no events
appear until the second snapshot. The worker takes the first snapshot about
30 seconds after the first startup (or immediately via the dashboard's
"Refresh now" button, or `docker compose run --rm worker python -m tracker.worker.cli snapshot`).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `IG_USERNAME` | — | Instagram username |
| `IG_PASSWORD` | — | Password, used only when no valid session file exists |
| `IG_SESSION_PATH` | `/data/session.json` | Session file location (on the `igsession` volume) |
| `DATABASE_URL` | `postgresql+psycopg://tracker:tracker@db:5432/tracker` | SQLAlchemy URL |
| `POLL_INTERVAL_HOURS` | `6` | Snapshot cadence (recommended floor: 1) |
| `SNAPSHOT_JITTER_MINUTES` | `30` | Random ± jitter on the schedule |
| `TELEGRAM_BOT_TOKEN` | unset | Leave unset to disable alerts |
| `TELEGRAM_CHAT_ID` | unset | Target chat for alerts |
| `WORKER_INTERNAL_URL` | `http://worker:9000` | How `web` reaches the worker |
| `LOG_LEVEL` | `INFO` | Log verbosity |

## Telegram alerts

1. Message [@BotFather](https://t.me/BotFather) and create a bot; copy the token.
2. Send your bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`, restart the
   worker: `docker compose up -d --force-recreate worker`.

Alerts are one batched message per snapshot. Empty diffs send nothing.
Whitelisted users never appear in alerts.

## Dashboard

| Page | What it shows |
|---|---|
| `/` | Current counts, growth chart, 10 most recent events, "Refresh now" |
| `/events` | Full event feed, filter by type, hide whitelisted |
| `/lists/not-following-back` | Accounts you follow that don't follow back |
| `/lists/fans` | Accounts that follow you but you don't follow back |
| `/people/{id}` | Per-user state and full event history (whitelist toggle) |

## When something goes wrong

- **Banner says "Worker paused"** (checkpoint / 2FA / bad password):
  re-run `docker compose run --rm worker python -m tracker.worker.cli login`,
  then `docker compose restart worker`.
- **Rate limited**: the worker backs off automatically (doubling up to 24 h).
- **Session expired**: the worker logs back in with `IG_USERNAME`/`IG_PASSWORD`
  automatically; you only get an alert if that also fails.
- **Worker down**: the dashboard still loads; lists and history come from the
  database.
- **Telegraph/delivery failures**: logged and ignored; snapshots still succeed.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q

# local run against SQLite
export DATABASE_URL=sqlite:///local.db
.venv/bin/alembic upgrade head
.venv/bin/uvicorn tracker.web.main:app --reload
.venv/bin/python -m tracker.worker.main
```

Tests never call Instagram: the instagrapi client is mocked and tests use an
in-memory SQLite database.

## Security / privacy

- `.env` is gitignored; the Instagram password is only used when no usable
  session file exists.
- The session file lives on the `igsession` volume, mounted only into the
  worker.
- `web` publishes only `127.0.0.1:8000`. There is no authentication — do not
  bind it to a LAN/public interface as-is.
- All data lives in the local `pgdata` volume. Outbound calls go only to
  Instagram and (optionally) the Telegram Bot API.
```

- [ ] **Step 2: Run the full test suite and Docker build**

```bash
.venv/bin/pytest -q
docker compose config --quiet
docker compose build
```

Expected: all tests pass; compose config valid; both images build.

- [ ] **Step 3: End-to-end smoke test with Postgres**

```bash
cp -n .env.example .env
docker compose up -d db
docker compose run --rm worker alembic upgrade head
docker compose up -d web worker
sleep 5
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/
curl -sS http://127.0.0.1:8000/ | grep -c "Instagram Tracker"
docker compose exec -T db psql -U tracker -d tracker -c "\dt"
docker compose down
```

Expected: HTTP `200`; dashboard HTML contains "Instagram Tracker"; `\dt`
lists `alembic_version`, `events`, `people`, `snapshot_entries`, `snapshots`.
(A failed snapshot attempt will be logged ~30 s after the worker starts
because `.env.example` contains placeholder credentials — that is expected
and exercises the failure path; the worker pauses until real credentials and
a session file exist.)

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add full README and document operational flows"
```

---

## Self-Review Notes

- Spec §6 note "PK columns use `BigInteger().with_variant(Integer, "sqlite")`" is honored in both `models.py` and `0001_initial.py`.
- Spec test list §14 is covered: `test_diff.py`, `test_queries.py`, `test_snapshotter.py`, `test_web.py`; `test_models.py`, `test_notifier.py`, `test_worker.py`, `test_smoke.py` are additive (no live Instagram calls anywhere).
- `next_run_time=None` in APScheduler pauses a job, so `_initial_next_run` returning `None` is only used to decide whether to pass the kwarg — see Task 7 Step 3.
- The worker's 30-second first-run only happens when the `snapshots` table is empty, so container restarts never trigger extra snapshots (spec §2 bot-signal mitigation).
