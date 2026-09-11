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
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        return None


def _base_context(
    request: Request, session: Session | None = None, **extra
) -> dict:
    settings = get_settings()
    nav_counts = queries.list_counts(session) if session else {}
    context = {
        "request": request,
        "worker": _worker_health(),
        "event_types": list(EventType),
        "ig_username": settings.ig_username,
        "nav_counts": nav_counts,
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
            session=session,
            counts=queries.latest_ok_snapshot(session),
            rows=queries.recent_events(session, limit=10),
            active_tab="dashboard",
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
        session=session,
        rows=rows,
        active_type=event_type,
        hide_whitelisted=hide_whitelisted,
        active_tab="events",
    )
    template = (
        "_events_table.html"
        if request.headers.get("HX-Request")
        else "events.html"
    )
    return templates.TemplateResponse(request, template, context)


@router.get("/lists/followers", response_class=HTMLResponse)
@router.get("/followers", response_class=HTMLResponse)
def list_followers(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "list.html",
        _base_context(
            request,
            session=session,
            title="Followers",
            active_tab="followers",
            people=queries.followers(session),
        ),
    )


@router.get("/lists/following", response_class=HTMLResponse)
@router.get("/following", response_class=HTMLResponse)
def list_following(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "list.html",
        _base_context(
            request,
            session=session,
            title="Following",
            active_tab="following",
            people=queries.following(session),
        ),
    )


@router.get("/lists/not-following-back", response_class=HTMLResponse)
@router.get("/not-following-back", response_class=HTMLResponse)
def list_not_following_back(
    request: Request, session: Session = Depends(get_session)
):
    return templates.TemplateResponse(
        request,
        "list.html",
        _base_context(
            request,
            session=session,
            title="Not following back",
            active_tab="not_following_back",
            people=queries.not_following_back(session),
        ),
    )


@router.get("/lists/fans", response_class=HTMLResponse)
@router.get("/fans", response_class=HTMLResponse)
def list_fans(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "list.html",
        _base_context(
            request,
            session=session,
            title="Fans",
            active_tab="fans",
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
            session=session,
            person=person,
            history=queries.user_history(session, ig_user_id),
            active_tab="people",
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
