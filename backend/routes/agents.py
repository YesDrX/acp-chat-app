"""Agent management routes — REST API + Jinja pages."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.template_config import templates
from backend.models.agent import Agent, AgentStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["agents"])

# Lazy singleton for test injection
_agent_store: AgentStore | None = None


def _get_store() -> AgentStore:
    """Get or create the agent store singleton."""
    global _agent_store
    if _agent_store is None:
        _agent_store = AgentStore()
    return _agent_store


# --- Pydantic models for request/response validation ---

class AgentCreate(BaseModel):
    name: str
    type: str = "cli"
    command: str = ""
    args: list[str] = []
    env_vars: dict[str, str] = {}
    description: str = ""


class AgentUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env_vars: dict[str, str] | None = None
    description: str | None = None


# --- REST API endpoints ---

@router.get("")
async def list_agents():
    """List all configured agents."""
    logger.debug("GET /api/agents — listing all agents")
    store = _get_store()
    agents = store.list_all()
    result = [a.to_dict() for a in agents]
    logger.debug("GET /api/agents — returning %d agents", len(result))
    return JSONResponse(content=result)


@router.post("")
async def create_agent(body: AgentCreate):
    """Create a new agent."""
    logger.debug("POST /api/agents — creating agent: name=%s", body.name)
    store = _get_store()
    agent = Agent(
        id=str(uuid.uuid4()),
        name=body.name,
        type=body.type,
        command=body.command,
        args=body.args,
        env_vars=body.env_vars,
        description=body.description,
    )
    created = store.create(agent)
    logger.debug("POST /api/agents — created agent: id=%s", created.id)
    return JSONResponse(content=created.to_dict(), status_code=201)


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """Get a single agent by ID."""
    logger.debug("GET /api/agents/%s — getting agent", agent_id)
    store = _get_store()
    agent = store.get(agent_id)
    if agent is None:
        logger.debug("GET /api/agents/%s — not found", agent_id)
        return JSONResponse(content={"detail": "Agent not found"}, status_code=404)
    logger.debug("GET /api/agents/%s — found: name=%s", agent_id, agent.name)
    return JSONResponse(content=agent.to_dict())


@router.put("/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate):
    """Update an existing agent."""
    logger.debug("PUT /api/agents/%s — updating agent", agent_id)
    store = _get_store()
    existing = store.get(agent_id)
    if existing is None:
        logger.debug("PUT /api/agents/%s — not found", agent_id)
        return JSONResponse(content={"detail": "Agent not found"}, status_code=404)

    # Apply updates only for provided fields
    if body.name is not None:
        existing.name = body.name
    if body.type is not None:
        existing.type = body.type
    if body.command is not None:
        existing.command = body.command
    if body.args is not None:
        existing.args = body.args
    if body.env_vars is not None:
        existing.env_vars = body.env_vars
    if body.description is not None:
        existing.description = body.description

    updated = store.update(existing)
    if updated is None:
        logger.debug("PUT /api/agents/%s — update failed after fetch", agent_id)
        return JSONResponse(content={"detail": "Agent not found"}, status_code=404)

    logger.debug("PUT /api/agents/%s — updated successfully", agent_id)
    return JSONResponse(content=updated.to_dict())


@router.delete("/{agent_id}")
async def delete_agent(agent_id: str):
    """Delete an agent."""
    logger.debug("DELETE /api/agents/%s — deleting agent", agent_id)
    store = _get_store()
    deleted = store.delete(agent_id)
    if not deleted:
        logger.debug("DELETE /api/agents/%s — not found", agent_id)
        return JSONResponse(content={"detail": "Agent not found"}, status_code=404)
    logger.debug("DELETE /api/agents/%s — deleted successfully", agent_id)
    return JSONResponse(content={"detail": "Agent deleted"}, status_code=200)


# --- Jinja page routes ---

page_router = APIRouter(prefix="", tags=["pages"])


@page_router.get("/agents", response_class=HTMLResponse)
async def agents_page(request: Request):
    """Render the agents management page."""
    logger.debug("GET /agents — rendering agents page")
    store = _get_store()
    agents = store.list_all()
    agent_dicts = [a.to_dict() for a in agents]
    logger.debug("GET /agents — loaded %d agents", len(agent_dicts))
    return templates.TemplateResponse(
        "pages/agents.html",
        {"request": request, "agents": agent_dicts},
    )
