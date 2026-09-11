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
    ig_sessionid: str = ""
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
