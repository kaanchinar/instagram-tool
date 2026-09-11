# Instagram Unfollower Tracker — Design Spec

- **Date:** 2026-09-11
- **Status:** Approved design, pending implementation plan
- **Scope:** Personal, single-user, self-hosted tool

## 1. Overview

A self-hosted app that periodically snapshots the owner's Instagram follower and
following lists, stores them locally, diffs consecutive snapshots, and presents
the results in a local web dashboard with optional Telegram alerts.

### Goals

- Detect **who unfollowed** (and followed) since the last snapshot
- Show **not-following-back** and **fans** lists
- Show **follower-count growth chart** and a **per-user event history**
- Send **Telegram alerts** on detected changes
- Run unattended on a schedule via Docker Compose
- No third-party tracking services; all data stays on the owner's machine

### Non-goals (v1)

- Multi-user / hosted deployment
- Ghost-follower engagement analysis (likes/comments scraping)
- Story viewers, profile-picture tracking, or any endpoint beyond followers/following
- Real-time (sub-hourly) detection

## 2. Key decisions

### Data source: Instagram private API via `instagrapi`

The official Instagram Graph API exposes only `followers_count` for
Business/Creator accounts — never the list of usernames. That makes it useless
for identifying *who* unfollowed. The official "Download Your Information"
export does include full lists, but exports take minutes–hours to generate and
the flow is manual, so it can't power scheduled checks.

Decision: use the private API through the Python
[`instagrapi`](https://github.com/subzeroid/instagrapi) library.

**Accepted risk:** private-API use violates Instagram's ToS; the account could
be checkpointed or (in the worst case) banned. Mitigations, all built in:

- Default poll interval **every 6 hours**, configurable, with a documented
  recommendation to never go below 1 hour
- Random jitter added to the schedule
- **Session reuse**: login once, persist the session file, reuse it across
  runs (avoids repeated logins, the most bot-like signal)
- instagrapi `delay_range` enabled so requests are paced like human browsing
- Only followers/following endpoints are called — nothing else

### App shape: Docker Compose, 3 services

`db` (Postgres) + `worker` (scheduler/snapshotter/notifier) + `web` (FastAPI
dashboard). One repo, one Dockerfile, different commands per service. Web and
worker share models and core logic as a single Python package.

### Stack

Python 3.12 · FastAPI + Jinja2 + HTMX (server-rendered, no JS build step) ·
Chart.js (CDN) for charts · SQLAlchemy 2 + Alembic · APScheduler ·
httpx (Telegram) · pydantic-settings · pytest.

## 3. Architecture

```
┌────────────┐   reads    ┌──────────────┐   writes   ┌─────────┐
│ web        │───────────▶│  Postgres    │◀───────────│ worker  │
│ FastAPI+   │            │  snapshots   │            │ APSched │
│ HTMX       │            │  entries     │            │ snapshot│
│            │──POST /trigger────────────▶│  diff      │ diff    │
└────────────┘            │  people      │            │ notify  │
                          │  events      │            └────┬────┘
                          └──────────────┘                 │
                                            Telegram API ◀─┘ (httpx)
```

- **`web`** binds to `127.0.0.1:8000` only. No auth in v1 (local-only tool).
- **`worker`** exposes an internal HTTP endpoint `POST /trigger` (compose
  network only, never published) so the dashboard's "Refresh now" button can
  request an immediate snapshot. Also `GET /healthz`.
- **Migrations** run from the worker at startup (`alembic upgrade head`)
  before the scheduler starts. Web assumes migrations have run.
- **Volumes:** `pgdata` (Postgres), `igsession` (instagrapi session file,
  mounted on the worker only).

## 4. Project layout

```
.
├── compose.yaml
├── Dockerfile                    # python:3.12-slim, one image for web+worker
├── .env.example
├── .gitignore                    # .env, *.db, __pycache__, .venv
├── pyproject.toml
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
├── src/tracker/
│   ├── __init__.py
│   ├── config.py                 # pydantic-settings, reads env
│   ├── db.py                     # engine + session factory
│   ├── models.py                 # SQLAlchemy models (see §6)
│   ├── snapshotter/
│   │   ├── __init__.py
│   │   ├── client.py             # instagrapi wrapper: login, session reuse
│   │   ├── service.py            # run_snapshot(): fetch → persist → diff → notify
│   │   └── errors.py             # RateLimited, ChallengeRequired, LoginFailed, FetchFailed
│   ├── core/
│   │   ├── __init__.py
│   │   ├── diff.py               # diff_snapshots(prev, new) -> list[Event]
│   │   └── queries.py            # not_following_back(), fans(), user_history(), counts_series()
│   ├── notifier/
│   │   ├── __init__.py
│   │   └── telegram.py           # send_events(events); disabled if unconfigured
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI micro-app: /trigger, /healthz + APScheduler
│   │   └── cli.py                # `login` (interactive session bootstrap), `snapshot` (one-shot)
│   └── web/
│       ├── __init__.py
│       ├── main.py               # FastAPI app factory
│       ├── routes.py             # pages + partials + /api/stats/counts
│       ├── templates/            # Jinja2: base, dashboard, events, lists, person
│       └── static/               # htmx.min.js (vendored), app.css
└── tests/
    ├── conftest.py               # SQLite test DB session
    ├── test_diff.py
    ├── test_queries.py
    ├── test_snapshotter.py       # mocked instagrapi client
    └── test_web.py               # FastAPI TestClient
```

## 5. Configuration

`.env` (gitignored); `.env.example` committed with dummy values.

| Variable | Default | Purpose |
|---|---|---|
| `IG_USERNAME` | — | Instagram username |
| `IG_PASSWORD` | — | Instagram password (used only when no valid session file exists) |
| `IG_SESSION_PATH` | `/data/session.json` | instagrapi session file location (on `igsession` volume) |
| `DATABASE_URL` | `postgresql+psycopg://tracker:tracker@db:5432/tracker` | SQLAlchemy URL |
| `POLL_INTERVAL_HOURS` | `6` | Snapshot cadence |
| `SNAPSHOT_JITTER_MINUTES` | `30` | Random ± jitter on the schedule |
| `TELEGRAM_BOT_TOKEN` | unset | If unset (with chat id), notifier is disabled |
| `TELEGRAM_CHAT_ID` | unset | Target chat for alerts |
| `WORKER_INTERNAL_URL` | `http://worker:9000` | How `web` reaches `POST /trigger` |
| `LOG_LEVEL` | `INFO` | — |

Tests override `DATABASE_URL` with SQLite (`sqlite://`), so models must stay
dialect-agnostic (see §6 note).

## 6. Data model

All timestamps stored in UTC (`DateTime(timezone=True)`). Instagram user IDs
are large: use `BigInteger`. For SQLite test compatibility, PK columns use
`BigInteger().with_variant(Integer, "sqlite")`.

### `snapshots`

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | |
| `taken_at` | timestamptz | indexed |
| `follower_count` | int | as reported by the fetched list |
| `following_count` | int | |
| `status` | enum `ok` / `failed` | only `ok` snapshots are diff baselines |
| `error` | text, nullable | failure reason when `failed` |

### `snapshot_entries`

Raw audit trail; one row per user per snapshot.

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | |
| `snapshot_id` | FK → snapshots.id | indexed |
| `ig_user_id` | bigint | |
| `username` | text | username at snapshot time |
| `direction` | enum `follower` / `following` | |
| — | | unique `(snapshot_id, ig_user_id, direction)` |

### `people`

Current known state, one row per Instagram user ever seen.

| Column | Type | Notes |
|---|---|---|
| `ig_user_id` | bigint PK | |
| `username` | text | updated when Instagram username changes (matched by `ig_user_id`) |
| `full_name` | text, nullable | |
| `is_follower` | bool | current state after latest ok snapshot |
| `is_following` | bool | |
| `whitelisted` | bool, default false | excluded from Telegram alerts |
| `first_seen_at` | timestamptz | |
| `last_changed_at` | timestamptz | last time is_follower/is_following changed |

### `events`

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | |
| `detected_at` | timestamptz | indexed |
| `ig_user_id` | bigint | indexed |
| `username` | text | denormalized: survives later username changes |
| `type` | enum `new_follower`, `unfollowed`, `i_followed`, `i_unfollowed` | |
| `snapshot_id` | FK → snapshots.id | the snapshot that detected it |

## 7. Core logic

### Diff semantics (`core/diff.py`)

Input: previous `ok` snapshot's entry sets `P_followers`, `P_following`
and the new sets `N_followers`, `N_following` — all four are maps of
`ig_user_id → UserRecord` (P from `snapshot_entries`, N from the fetch
result; `snapshot_entries` stores only the username, which is all the diff
needs beyond the id). Matching is always by `ig_user_id`, never username.

- `N_followers − P_followers` → event `new_follower`
- `P_followers − N_followers` → event `unfollowed`
- `N_following − P_following` → event `i_followed`
- `P_following − N_following` → event `i_unfollowed`

Edge cases:

- **First-ever snapshot:** baseline only — persist entries, update `people`,
  create **no** events.
- **Username change:** same `ig_user_id` with a new username → update
  `people.username`/`full_name`; no event.
- **Deactivated accounts:** they vanish from lists → appear as `unfollowed`;
  if they return, a `new_follower` event appears. Accepted behavior.
- **Failed snapshots** are persisted with status `failed` + error, and are
  never used as a diff baseline.

After a successful diff, `people` is updated to reflect the new sets and
`last_changed_at` is bumped for users whose state changed.

### Queries (`core/queries.py`)

- `not_following_back()` — `people` where `is_following and not is_follower`
- `fans()` — `is_follower and not is_following`
- `user_history(ig_user_id)` — all events for a user, newest first
- `counts_series()` — `(taken_at, follower_count, following_count)` from `ok`
  snapshots, ascending

## 8. Snapshotter (`snapshotter/`)

### Client (`client.py`)

- `get_client()`:
  1. If `IG_SESSION_PATH` exists → `Client.load_settings()` and validate with
     a lightweight call (`account_info()`).
  2. Else login with `IG_USERNAME`/`IG_PASSWORD`; on success
     `Client.dump_settings(IG_SESSION_PATH)`.
  3. `Client.delay_range = [2, 6]` (seconds between requests).
- Exceptions from instagrapi are wrapped into domain errors in `errors.py`:
  `RateLimited`, `ChallengeRequired`, `LoginFailed`, `FetchFailed`.

### Fetching

- Own `user_id` resolved once at login via `user_info_by_username(IG_USERNAME)`.
- `fetch_followers() -> dict[int, UserRecord]` via `user_followers(user_id)`
- `fetch_following() -> dict[int, UserRecord]` via `user_following(user_id)`
- `UserRecord` dataclass: `ig_user_id`, `username`, `full_name`

### Service (`service.py`)

`run_snapshot()`:

1. Fetch both lists. On `RateLimited` → persist `failed` snapshot, back off
   (see §10). On `ChallengeRequired`/`LoginFailed` → persist `failed`,
   pause polling, alert. On partial failure (one list fetched, other not) →
   discard both, persist `failed`.
2. In one transaction: insert `ok` snapshot + all `snapshot_entries`.
3. Load previous `ok` snapshot; if none, baseline (no events).
4. Run diff → insert `events` → update `people` (same transaction).
5. Call notifier with the new events (non-fatal: log on failure).

## 9. Worker (`worker/`)

### `main.py`

A small FastAPI app (internal port 9000) hosting APScheduler
(`AsyncIOScheduler`) in the same process:

- On startup: `alembic upgrade head`, then start scheduler.
- Scheduled job: `run_snapshot()` every `POLL_INTERVAL_HOURS` with
  `SNAPSHOT_JITTER_MINUTES` jitter.
- `POST /trigger`: run the job immediately. If a snapshot is already running,
  return `409` and do nothing.
- `GET /healthz`: liveness + last snapshot status (read from DB) — consumed
  by the dashboard status banner.

### Backoff / pause behavior

- Consecutive `RateLimited` failures: exponential backoff of the next run —
  interval × 2^n, capped at 24h. Counter resets on success.
- `ChallengeRequired` / `LoginFailed`: scheduler pauses (no further attempts),
  one Telegram alert is sent ("needs attention — re-run login"), and the
  dashboard banner shows the paused state. Recovery: `docker compose run
  worker python -m tracker.worker.cli login`, then restart the worker.

### `cli.py`

- `login` — interactive session bootstrap: prompts for credentials and 2FA /
  challenge code if Instagram asks, writes a fresh session file to
  `IG_SESSION_PATH`.
- `snapshot` — run one snapshot immediately (debugging/manual use).

## 10. Notifier (`notifier/telegram.py`)

- If `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are unset → disabled, log only.
- `send_events(events)` posts **one batched message per snapshot** via Bot API
  `sendMessage` (httpx). Example:

  ```
  Instagram update (2026-09-11 18:03 UTC)
  Unfollowed (2): @alice, @bob
  New followers (1): @carol
  You followed (1): @dave
  You unfollowed (1): @erin
  ```

- Whitelisted users' events are stored and shown in the dashboard but excluded
  from Telegram messages. Empty non-whitelisted diff → no message.
- Delivery failure is logged and swallowed (never fails the snapshot job).

## 11. Web dashboard (`web/`)

Server-rendered Jinja2 + HTMX partial refreshes; Chart.js from CDN; HTMX
vendored into `static/`. All pages share `base.html` with a status banner
(last snapshot time/status, worker paused?) sourced from `GET /healthz` of the
worker via a server-side fetch, degraded gracefully if the worker is down.

| Route | Page |
|---|---|
| `GET /` | Dashboard: current follower/following counts, Chart.js growth chart (data from `GET /api/stats/counts`), 10 most recent events, "Refresh now" button |
| `GET /events?type=…` | Event feed, filterable by the 4 event types (HTMX partial reload). Whitelisted users shown by default with a marker; `?hide_whitelisted=1` hides them |
| `GET /lists/not-following-back` | Accounts you follow who don't follow back |
| `GET /lists/fans` | Accounts who follow you but you don't follow back |
| `GET /people/{ig_user_id}` | Per-user page: current state + full event history |
| `POST /people/{ig_user_id}/whitelist` | Toggle whitelist; returns updated row fragment |
| `POST /refresh` | Proxies `POST {WORKER_INTERNAL_URL}/trigger`; on `409` shows "snapshot already running" |
| `GET /api/stats/counts` | JSON time series for the chart |

## 12. Docker Compose

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
    # no published ports — internal network only

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

## 13. Error-handling matrix

| Failure | Behavior |
|---|---|
| Rate limit (429 / instagrapi `PleaseWaitFewMinutes`) | Snapshot `failed`; exponential backoff, cap 24h |
| Checkpoint / 2FA challenge | Snapshot `failed`; scheduler pauses; one Telegram alert; dashboard banner; manual `cli login` to recover |
| Session expired | Automatic re-login from `IG_USERNAME`/`IG_PASSWORD`; alert only if that also fails |
| Partial fetch (one list failed) | Discard both; snapshot `failed`; never diffed |
| Telegram delivery fails | Logged; snapshot still succeeds |
| Worker down | Dashboard loads, banner shows "worker unreachable", lists still work from DB |
| DB unavailable at worker start | Crash-loop with compose restart policy (`restart: unless-stopped` on worker and web) |

## 14. Testing

pytest, tests run against SQLite via `DATABASE_URL` override
(`tests/conftest.py` builds a fresh in-memory DB per test session using
`Base.metadata.create_all` — Alembic migrations stay a worker-startup
concern and are not exercised in unit tests).

- `test_diff.py` — fixture follower/following sets → expected event lists;
  covers: first-snapshot baseline (no events), each of the 4 event types,
  username change (no event), deactivated-then-returning account, `people`
  state updates.
- `test_queries.py` — not-following-back / fans / user_history / counts_series.
- `test_snapshotter.py` — instagrapi `Client` mocked: session-file reuse path,
  fresh-login path, error wrapping (429 → `RateLimited`, challenge →
  `ChallengeRequired`), partial fetch discards both lists.
- `test_web.py` — FastAPI `TestClient`: dashboard renders, events filter,
  whitelist toggle flips the flag, `POST /refresh` handles worker `409`.
- No live Instagram calls in tests, ever.

## 15. Security & privacy

- `.env` gitignored; `.env.example` committed. Session file lives only on the
  `igsession` volume, mounted solely into `worker`.
- `web` publishes only `127.0.0.1:8000`. No authentication in v1 — acceptable
  because the port is loopback-only. If the user later binds it to a LAN
  interface, HTTP basic auth must be added first (documented in README).
- The Instagram password is used only when no usable session file exists;
  after the first login the session file is the credential.
- All data stays local (Postgres volume). The only outbound calls are to
  Instagram and (optionally) the Telegram Bot API.

## 16. Build milestones

1. **Skeleton** — repo, `pyproject.toml`, Dockerfile, compose, `.env.example`,
   config module, empty FastAPI apps, healthz, CI-less `pytest` green on a
   smoke test.
2. **Data layer** — models + Alembic initial migration, run automatically on
   worker start.
3. **Snapshotter** — client (session reuse), fetch, service with persist;
   `cli.py login` + `snapshot`; `test_snapshotter.py` green.
4. **Diff engine** — `core/diff.py` + `people` updates + `test_diff.py` /
   `test_queries.py` green.
5. **Dashboard** — all routes in §11, growth chart, status banner, refresh
   button wired to `/trigger`.
6. **Notifier** — Telegram batching, whitelist exclusion, `test_web.py`
   extended for whitelist.
7. **Polish** — README (setup: env, `cli login`, compose up, Telegram bot
   creation), error-path verification (kill worker, bad session file).

Each milestone ends green: `pytest` passes and the affected services start
under `docker compose up`.

## 17. Operational notes

- First run: `cp .env.example .env`, fill in credentials,
  `docker compose run --rm worker python -m tracker.worker.cli login`
  (handles 2FA interactively), then `docker compose up -d`.
- First snapshot is a baseline — no events until the second snapshot, by
  design.
- Recommended floor for `POLL_INTERVAL_HOURS` is 1; default 6. Lower values
  increase checkpoint risk.
- Accounts with 10k+ followers: full fetches take minutes and produce many
  requests; consider 12–24h intervals in that case.
