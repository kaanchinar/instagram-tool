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
