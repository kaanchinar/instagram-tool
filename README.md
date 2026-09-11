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
- **Telegram/delivery failures**: logged and ignored; snapshots still succeed.

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
- All data lives in the local `pgdata` volume. Server-side outbound calls go
  only to Instagram and (optionally) the Telegram Bot API. Dashboard pages
  load Chart.js from the jsDelivr CDN, so your browser also contacts
  `cdn.jsdelivr.net`; no user data is sent there.
