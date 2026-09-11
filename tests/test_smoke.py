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
