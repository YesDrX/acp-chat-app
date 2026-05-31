"""Session management routes — REST API + Jinja pages."""

from __future__ import annotations

import dataclasses
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.template_config import templates
from backend.models.agent import AgentStore
from backend.models.session import Session, SessionStore
from backend.models.template import TemplateStore

logger = logging.getLogger(__name__)

# Prefix /api/sessions — registered under /api in main.py
sessions_api_router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# Lazy singletons for test injection
_session_store: SessionStore | None = None
_template_store: TemplateStore | None = None
_agent_store: AgentStore | None = None


def _get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store


def _get_template_store() -> TemplateStore:
    global _template_store
    if _template_store is None:
        _template_store = TemplateStore()
    return _template_store


def _get_agent_store() -> AgentStore:
    global _agent_store
    if _agent_store is None:
        _agent_store = AgentStore()
    return _agent_store


# --- Pydantic models ---


class SessionCreate(BaseModel):
    agent_id: str
    name: str = ""
    model: str = ""
    cwd: str = ""
    effort_level: str = ""
    permission_mode: str = ""
    env_vars: dict[str, str] = {}
    template_id: str | None = None


class SessionUpdate(BaseModel):
    name: str | None = None
    model: str | None = None
    effort_level: str | None = None
    permission_mode: str | None = None
    env_vars: dict[str, str] | None = None


# --- REST API endpoints ---


@sessions_api_router.get("")
async def list_sessions(
    agent_id: str | None = None,
    status: str | None = None,
    cwd: str | None = None,
):
    """List all sessions with optional filters."""
    logger.debug("GET /api/sessions — listing: agent_id=%s status=%s cwd=%s",
                 agent_id, status, cwd)
    store = _get_session_store()
    sessions_list = await store.list_all(agent_id=agent_id, status=status)
    # Apply cwd filter in Python (not supported by store yet)
    if cwd:
        sessions_list = [s for s in sessions_list if s.cwd == cwd]
    result = [asdict_safe(s) for s in sessions_list]
    logger.debug("GET /api/sessions — returning %d sessions", len(result))
    return JSONResponse(content=result)


@sessions_api_router.post("")
async def create_session(body: SessionCreate):
    """Create a new session.

    If template_id is provided, load the template and use its settings
    as defaults. Explicit fields in the request override template values.
    """
    logger.debug("POST /api/sessions — creating: agent_id=%s name=%s template_id=%s",
                 body.agent_id, body.name, body.template_id)

    # Validate agent exists
    agent_store = _get_agent_store()
    agent = agent_store.get(body.agent_id)
    if agent is None:
        logger.debug("POST /api/sessions — agent not found: %s", body.agent_id)
        return JSONResponse(
            content={"detail": f"Agent not found: {body.agent_id}"},
            status_code=400,
        )

    # Build session defaults from template if provided
    defaults: dict[str, Any] = {
        "model": body.model,
        "cwd": body.cwd,
        "effort_level": body.effort_level,
        "permission_mode": body.permission_mode,
        "env_vars": body.env_vars,
    }

    if body.template_id:
        logger.debug("POST /api/sessions — loading template: %s", body.template_id)
        tstore = _get_template_store()
        tmpl = await tstore.get(body.template_id)
        if tmpl is None:
            logger.debug("POST /api/sessions — template not found: %s", body.template_id)
            return JSONResponse(
                content={"detail": f"Template not found: {body.template_id}"},
                status_code=404,
            )
        # Template defaults: only use if not explicitly provided
        if body.model == "" and tmpl.model:
            defaults["model"] = tmpl.model
        if body.cwd == "" and tmpl.cwd:
            defaults["cwd"] = tmpl.cwd
        if body.effort_level == "" and tmpl.effort_level:
            defaults["effort_level"] = tmpl.effort_level
        if body.permission_mode == "" and tmpl.permission_mode:
            defaults["permission_mode"] = tmpl.permission_mode
        if body.env_vars == {} and tmpl.env_vars:
            defaults["env_vars"] = dict(tmpl.env_vars)
        # Merge: template env_vars as base, explicit env_vars override
        if body.env_vars:
            merged_env = dict(tmpl.env_vars) if tmpl.env_vars else {}
            merged_env.update(body.env_vars)
            defaults["env_vars"] = merged_env

    # Fallback: use /tmp if cwd still empty after template processing
    if not defaults["cwd"]:
        defaults["cwd"] = "/tmp"

    session = Session(
        id=str(uuid.uuid4()),
        agent_id=body.agent_id,
        name=body.name,
        model=defaults["model"],
        cwd=defaults["cwd"],
        effort_level=defaults["effort_level"],
        permission_mode=defaults["permission_mode"],
        env_vars=defaults["env_vars"],
        status="created",
    )

    store = _get_session_store()
    created = await store.create(session)
    logger.debug("POST /api/sessions — created session: id=%s", created.id)
    return JSONResponse(content=asdict_safe(created), status_code=201)


@sessions_api_router.get("/{session_id}")
async def get_session(session_id: str):
    """Get a single session by ID."""
    logger.debug("GET /api/sessions/%s — getting session", session_id)
    store = _get_session_store()
    session = await store.get(session_id)
    if session is None:
        logger.debug("GET /api/sessions/%s — not found", session_id)
        return JSONResponse(content={"detail": "Session not found"}, status_code=404)
    logger.debug("GET /api/sessions/%s — found: name=%s", session_id, session.name)
    return JSONResponse(content=asdict_safe(session))


@sessions_api_router.put("/{session_id}")
async def update_session(session_id: str, body: SessionUpdate):
    """Update session settings."""
    logger.debug("PUT /api/sessions/%s — updating session", session_id)
    store = _get_session_store()
    existing = await store.get(session_id)
    if existing is None:
        logger.debug("PUT /api/sessions/%s — not found", session_id)
        return JSONResponse(content={"detail": "Session not found"}, status_code=404)

    if body.name is not None:
        existing.name = body.name
    if body.model is not None:
        existing.model = body.model
    if body.effort_level is not None:
        existing.effort_level = body.effort_level
    if body.permission_mode is not None:
        existing.permission_mode = body.permission_mode
    if body.env_vars is not None:
        existing.env_vars = body.env_vars

    updated = await store.update(existing)
    if updated is None:
        logger.debug("PUT /api/sessions/%s — update failed after fetch", session_id)
        return JSONResponse(content={"detail": "Session not found"}, status_code=404)

    logger.debug("PUT /api/sessions/%s — updated successfully", session_id)
    return JSONResponse(content=asdict_safe(updated))


@sessions_api_router.delete("/{session_id}")
async def delete_session(session_id: str):
    """Delete a session by ID."""
    logger.debug("DELETE /api/sessions/%s — deleting session", session_id)
    store = _get_session_store()
    deleted = await store.delete(session_id)
    if not deleted:
        logger.debug("DELETE /api/sessions/%s — not found", session_id)
        return JSONResponse(content={"detail": "Session not found"}, status_code=404)
    logger.debug("DELETE /api/sessions/%s — deleted successfully", session_id)
    return JSONResponse(content={"detail": "Session deleted"}, status_code=200)


@sessions_api_router.post("/{session_id}/resume")
async def resume_session(session_id: str):
    """Mark a session for resume (sets status to 'resuming')."""
    logger.debug("POST /api/sessions/%s/resume — resuming session", session_id)
    store = _get_session_store()
    existing = await store.get(session_id)
    if existing is None:
        logger.debug("POST /api/sessions/%s/resume — not found", session_id)
        return JSONResponse(content={"detail": "Session not found"}, status_code=404)

    existing.status = "resuming"
    updated = await store.update(existing)
    if updated is None:
        return JSONResponse(content={"detail": "Session not found"}, status_code=404)

    logger.debug("POST /api/sessions/%s/resume — status set to resuming", session_id)
    return JSONResponse(content=asdict_safe(updated))


# --- Jinja page routes ---

sessions_page_router = APIRouter(prefix="", tags=["pages"])


@sessions_page_router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(request: Request):
    """Render the sessions list page."""
    logger.debug("GET /sessions — rendering sessions page")
    sstore = _get_session_store()
    astore = _get_agent_store()
    tstore = _get_template_store()

    sessions_list = await sstore.list_all()
    agents = astore.list_all()
    templates_list = await tstore.list_all()

    session_dicts = [asdict_safe(s) for s in sessions_list]
    agent_dicts = [a.to_dict() for a in agents]
    template_dicts = [dataclasses.asdict(t) for t in templates_list]

    logger.debug("GET /sessions — loaded %d sessions, %d agents, %d templates",
                 len(session_dicts), len(agent_dicts), len(template_dicts))
    return templates.TemplateResponse(
        "pages/sessions.html",
        {
            "request": request,
            "sessions": session_dicts,
            "agents": agent_dicts,
            "templates": template_dicts,
        },
    )


@sessions_page_router.get("/sessions/new", response_class=HTMLResponse)
async def new_session_page(request: Request):
    """Render the new session form page (redirects to sessions page with modal open)."""
    logger.debug("GET /sessions/new — rendering new session form")
    astore = _get_agent_store()
    tstore = _get_template_store()

    agents = astore.list_all()
    templates_list = await tstore.list_all()

    agent_dicts = [a.to_dict() for a in agents]
    template_dicts = [dataclasses.asdict(t) for t in templates_list]

    return templates.TemplateResponse(
        "pages/sessions.html",
        {
            "request": request,
            "sessions": [],
            "agents": agent_dicts,
            "templates": template_dicts,
            "show_create_modal": True,
        },
    )


@sessions_page_router.get("/sessions/{session_id}", response_class=HTMLResponse)
async def session_chat_page(request: Request, session_id: str):
    """Render the session chat page."""
    logger.debug("GET /sessions/%s — rendering chat page", session_id)
    sstore = _get_session_store()
    session = await sstore.get(session_id)
    if session is None:
        logger.debug("GET /sessions/%s — not found", session_id)
        return HTMLResponse(content="<h1>Session not found</h1>", status_code=404)

    astore = _get_agent_store()
    agent = astore.get(session.agent_id)

    return templates.TemplateResponse(
        "pages/session_chat.html",
        {
            "request": request,
            "session": asdict_safe(session),
            "agent": agent.to_dict() if agent else {"name": "Unknown", "id": session.agent_id},
        },
    )


# --- Helpers ---


def asdict_safe(obj: Session) -> dict[str, Any]:
    """Convert a Session to a plain dict for JSON serialization."""
    return dataclasses.asdict(obj)
