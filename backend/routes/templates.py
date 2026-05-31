"""Template management routes — REST API + Jinja pages."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.template_config import templates
from backend.models.agent import AgentStore
from backend.models.template import Template, TemplateStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/templates", tags=["templates"])

# Lazy singletons for test injection
_template_store: TemplateStore | None = None
_agent_store: AgentStore | None = None


def _get_template_store() -> TemplateStore:
    """Get or create the template store singleton."""
    global _template_store
    if _template_store is None:
        _template_store = TemplateStore()
    return _template_store


def _get_agent_store() -> AgentStore:
    """Get or create the agent store singleton."""
    global _agent_store
    if _agent_store is None:
        _agent_store = AgentStore()
    return _agent_store


# --- Pydantic models for request/response validation ---

class TemplateCreate(BaseModel):
    name: str
    agent_id: str
    model: str = ""
    cwd: str = ""
    effort_level: str = ""
    permission_mode: str = ""
    env_vars: dict[str, str] = {}


class TemplateUpdate(BaseModel):
    name: str | None = None
    agent_id: str | None = None
    model: str | None = None
    cwd: str | None = None
    effort_level: str | None = None
    permission_mode: str | None = None
    env_vars: dict[str, str] | None = None


# --- REST API endpoints ---

@router.get("")
async def list_templates():
    """List all templates."""
    logger.debug("GET /api/templates — listing all templates")
    store = _get_template_store()
    templates_list = await store.list_all()
    result = [asdict_safe(t) for t in templates_list]
    logger.debug("GET /api/templates — returning %d templates", len(result))
    return JSONResponse(content=result)


@router.post("")
async def create_template(body: TemplateCreate):
    """Create a new template."""
    logger.debug("POST /api/templates — creating template: name=%s agent_id=%s",
                 body.name, body.agent_id)
    store = _get_template_store()
    template = Template(
        id=str(uuid.uuid4()),
        name=body.name,
        agent_id=body.agent_id,
        model=body.model,
        cwd=body.cwd,
        effort_level=body.effort_level,
        permission_mode=body.permission_mode,
        env_vars=body.env_vars,
    )
    created = await store.create(template)
    logger.debug("POST /api/templates — created template: id=%s", created.id)
    return JSONResponse(content=asdict_safe(created), status_code=201)


@router.get("/{template_id}")
async def get_template(template_id: str):
    """Get a single template by ID."""
    logger.debug("GET /api/templates/%s — getting template", template_id)
    store = _get_template_store()
    template = await store.get(template_id)
    if template is None:
        logger.debug("GET /api/templates/%s — not found", template_id)
        return JSONResponse(content={"detail": "Template not found"}, status_code=404)
    logger.debug("GET /api/templates/%s — found: name=%s", template_id, template.name)
    return JSONResponse(content=asdict_safe(template))


@router.put("/{template_id}")
async def update_template(template_id: str, body: TemplateUpdate):
    """Update an existing template."""
    logger.debug("PUT /api/templates/%s — updating template", template_id)
    store = _get_template_store()
    existing = await store.get(template_id)
    if existing is None:
        logger.debug("PUT /api/templates/%s — not found", template_id)
        return JSONResponse(content={"detail": "Template not found"}, status_code=404)

    # Apply updates only for provided fields
    if body.name is not None:
        existing.name = body.name
    if body.agent_id is not None:
        existing.agent_id = body.agent_id
    if body.model is not None:
        existing.model = body.model
    if body.cwd is not None:
        existing.cwd = body.cwd
    if body.effort_level is not None:
        existing.effort_level = body.effort_level
    if body.permission_mode is not None:
        existing.permission_mode = body.permission_mode
    if body.env_vars is not None:
        existing.env_vars = body.env_vars

    updated = await store.update(existing)
    if updated is None:
        logger.debug("PUT /api/templates/%s — update failed after fetch", template_id)
        return JSONResponse(content={"detail": "Template not found"}, status_code=404)

    logger.debug("PUT /api/templates/%s — updated successfully", template_id)
    return JSONResponse(content=asdict_safe(updated))


@router.delete("/{template_id}")
async def delete_template(template_id: str):
    """Delete a template."""
    logger.debug("DELETE /api/templates/%s — deleting template", template_id)
    store = _get_template_store()
    deleted = await store.delete(template_id)
    if not deleted:
        logger.debug("DELETE /api/templates/%s — not found", template_id)
        return JSONResponse(content={"detail": "Template not found"}, status_code=404)
    logger.debug("DELETE /api/templates/%s — deleted successfully", template_id)
    return JSONResponse(content={"detail": "Template deleted"}, status_code=200)


# --- Jinja page routes ---

page_router = APIRouter(prefix="", tags=["pages"])


@page_router.get("/templates", response_class=HTMLResponse)
async def templates_page(request: Request):
    """Render the templates management page."""
    logger.debug("GET /templates — rendering templates page")
    tstore = _get_template_store()
    astore = _get_agent_store()
    templates_list = await tstore.list_all()
    agents = astore.list_all()
    template_dicts = [asdict_safe(t) for t in templates_list]
    agent_dicts = [a.to_dict() for a in agents]
    logger.debug("GET /templates — loaded %d templates, %d agents",
                 len(template_dicts), len(agent_dicts))
    return templates.TemplateResponse(
        "pages/templates.html",
        {
            "request": request,
            "templates": template_dicts,
            "agents": agent_dicts,
        },
    )


# --- Helpers ---

def asdict_safe(obj: Template) -> dict:
    """Convert a Template to a plain dict for JSON serialization."""
    import dataclasses
    return dataclasses.asdict(obj)
