"""Template model — async CRUD backed by SQLite."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

import aiosqlite

from backend.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class Template:
    """A session template with pre-configured settings."""
    id: str = ""
    name: str = ""
    agent_id: str = ""
    model: str = ""
    cwd: str = ""
    effort_level: str = ""
    permission_mode: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)
    created_at: str = ""

    def to_row(self) -> dict[str, Any]:
        """Convert to a dict suitable for SQLite INSERT."""
        d = asdict(self)
        d["env_vars"] = json.dumps(d.get("env_vars", {}))
        return d

    @classmethod
    def from_row(cls, row: aiosqlite.Row | dict[str, Any]) -> Template:
        """Create a Template from a database row."""
        data = dict(row)
        env_raw = data.get("env_vars", "{}")
        try:
            env_vars = json.loads(env_raw) if isinstance(env_raw, str) else env_raw
        except (json.JSONDecodeError, TypeError):
            env_vars = {}
        logger.debug("Deserializing template: id=%s name=%s", data.get("id"), data.get("name"))
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            agent_id=data.get("agent_id", ""),
            model=data.get("model", ""),
            cwd=data.get("cwd", ""),
            effort_level=data.get("effort_level", ""),
            permission_mode=data.get("permission_mode", ""),
            env_vars=env_vars,
            created_at=data.get("created_at", ""),
        )


class TemplateStore:
    """Async store for template CRUD."""

    def __init__(self) -> None:
        logger.debug("TemplateStore initialized")

    async def list_all(self) -> list[Template]:
        """List all templates."""
        logger.debug("Listing all templates")
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM templates ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            templates = [Template.from_row(r) for r in rows]
            logger.debug("Found %d templates", len(templates))
            return templates
        finally:
            await db.close()

    async def get(self, template_id: str) -> Template | None:
        """Get a template by ID."""
        logger.debug("Getting template: %s", template_id)
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
            row = await cursor.fetchone()
            if row:
                logger.debug("Found template: %s", template_id)
                return Template.from_row(row)
            logger.debug("Template not found: %s", template_id)
            return None
        finally:
            await db.close()

    async def create(self, template: Template) -> Template:
        """Create a new template. Generates an ID if not provided."""
        logger.debug("Creating template: name=%s", template.name)
        if not template.id:
            template.id = str(uuid.uuid4())
        db = await get_db()
        try:
            row = template.to_row()
            await db.execute(
                """INSERT INTO templates
                   (id, name, agent_id, model, cwd, effort_level,
                    permission_mode, env_vars, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    row["id"], row["name"], row["agent_id"], row["model"],
                    row["cwd"], row["effort_level"], row["permission_mode"],
                    row["env_vars"],
                ),
            )
            await db.commit()
            logger.debug("Created template: id=%s", template.id)
            return await self.get(template.id) or template
        finally:
            await db.close()

    async def update(self, template: Template) -> Template | None:
        """Update an existing template. Returns None if not found."""
        logger.debug("Updating template: id=%s", template.id)
        db = await get_db()
        try:
            row = template.to_row()
            await db.execute(
                """UPDATE templates SET
                   name = ?, agent_id = ?, model = ?, cwd = ?,
                   effort_level = ?, permission_mode = ?, env_vars = ?
                   WHERE id = ?""",
                (
                    row["name"], row["agent_id"], row["model"], row["cwd"],
                    row["effort_level"], row["permission_mode"], row["env_vars"],
                    row["id"],
                ),
            )
            await db.commit()
            if db.total_changes > 0:
                logger.debug("Updated template: id=%s", template.id)
                return await self.get(template.id)
            logger.debug("Template not found for update: %s", template.id)
            return None
        finally:
            await db.close()

    async def delete(self, template_id: str) -> bool:
        """Delete a template by ID."""
        logger.debug("Deleting template: %s", template_id)
        db = await get_db()
        try:
            await db.execute("DELETE FROM templates WHERE id = ?", (template_id,))
            await db.commit()
            if db.total_changes > 0:
                logger.debug("Deleted template: %s", template_id)
                return True
            logger.debug("Template not found for delete: %s", template_id)
            return False
        finally:
            await db.close()
