"""Session model — async CRUD backed by SQLite."""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

import aiosqlite

from backend.database import get_db

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Message:
    """Represents a chat message in a session."""
    id: str = ""
    session_id: str = ""
    role: str = "user"
    content: str = ""
    created_at: str = ""

    def to_row(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_row(cls, row: aiosqlite.Row | dict[str, Any]) -> Message:
        data = dict(row)
        return cls(
            id=data.get("id", ""),
            session_id=data.get("session_id", ""),
            role=data.get("role", "user"),
            content=data.get("content", ""),
            created_at=data.get("created_at", ""),
        )


class MessageStore:
    """Async store for message CRUD."""

    def __init__(self) -> None:
        logger.debug("MessageStore initialized")

    async def create(self, message: Message) -> Message:
        """Create a new message."""
        if not message.id:
            message.id = str(uuid.uuid4())
        logger.debug("Creating message: id=%s session=%s role=%s", message.id, message.session_id, message.role)
        db = await get_db()
        try:
            row = message.to_row()
            await db.execute(
                """INSERT INTO messages (id, session_id, role, content, created_at)
                   VALUES (?, ?, ?, ?, datetime('now'))""",
                (row["id"], row["session_id"], row["role"], row["content"]),
            )
            await db.commit()
            return await self.get(message.id) or message
        finally:
            await db.close()

    async def get(self, message_id: str) -> Message | None:
        """Get a message by ID."""
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
            row = await cursor.fetchone()
            return Message.from_row(row) if row else None
        finally:
            await db.close()

    async def get_by_session(self, session_id: str) -> list[Message]:
        """Get all messages for a session, ordered by creation time."""
        logger.debug("Getting messages for session: %s", session_id)
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
            messages = [Message.from_row(r) for r in rows]
            logger.debug("Found %d messages for session: %s", len(messages), session_id)
            return messages
        finally:
            await db.close()

    async def delete_by_session(self, session_id: str) -> int:
        """Delete all messages for a session. Returns count deleted."""
        logger.debug("Deleting messages for session: %s", session_id)
        db = await get_db()
        try:
            cursor = await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            await db.commit()
            logger.debug("Deleted %d messages for session: %s", cursor.rowcount, session_id)
            return cursor.rowcount
        finally:
            await db.close()


@dataclass
class Session:
    """Represents a chat session with an ACP agent."""
    id: str = ""
    agent_id: str = ""
    name: str = ""
    model: str = ""
    cwd: str = ""
    effort_level: str = ""
    permission_mode: str = ""
    status: str = "active"
    env_vars: dict[str, str] = field(default_factory=dict)
    created_at: str = ""
    last_active_at: str = ""
    acp_session_id: str = ""

    def to_row(self) -> dict[str, Any]:
        """Convert to a dict suitable for SQLite INSERT, with env_vars as JSON string."""
        d = asdict(self)
        d["env_vars"] = json.dumps(d.get("env_vars", {}))
        return d

    def to_row_insert(self) -> dict[str, Any]:
        """Convert to a dict for INSERT, excluding auto-computed fields."""
        d = asdict(self)
        d["env_vars"] = json.dumps(d.get("env_vars", {}))
        # Strip fields that are set by SQLite defaults
        d.pop("created_at", None)
        d.pop("last_active_at", None)
        return d

    @classmethod
    def from_row(cls, row: aiosqlite.Row | dict[str, Any]) -> Session:
        """Create a Session from a database row."""
        data = dict(row)
        env_raw = data.get("env_vars", "{}")
        try:
            env_vars = json.loads(env_raw) if isinstance(env_raw, str) else env_raw
        except (json.JSONDecodeError, TypeError):
            env_vars = {}
        logger.debug("Deserializing session: id=%s", data.get("id", "unknown"))
        return cls(
            id=data.get("id", ""),
            agent_id=data.get("agent_id", ""),
            name=data.get("name", ""),
            model=data.get("model", ""),
            cwd=data.get("cwd", ""),
            effort_level=data.get("effort_level", ""),
            permission_mode=data.get("permission_mode", ""),
            status=data.get("status", "active"),
            env_vars=env_vars,
            created_at=data.get("created_at", ""),
            last_active_at=data.get("last_active_at", ""),
            acp_session_id=data.get("acp_session_id", ""),
        )


class SessionStore:
    """Async store for session CRUD."""

    def __init__(self) -> None:
        logger.debug("SessionStore initialized")

    async def list_all(
        self,
        agent_id: str | None = None,
        status: str | None = None,
    ) -> list[Session]:
        """List all sessions, optionally filtered by agent_id and/or status."""
        logger.debug("Listing sessions: agent_id=%s status=%s", agent_id, status)
        db = await get_db()
        try:
            query = "SELECT * FROM sessions WHERE 1=1"
            params: list[Any] = []
            if agent_id:
                query += " AND agent_id = ?"
                params.append(agent_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY last_active_at DESC"
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            sessions = [Session.from_row(r) for r in rows]
            logger.debug("Found %d sessions", len(sessions))
            return sessions
        finally:
            await db.close()

    async def get(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        logger.debug("Getting session: %s", session_id)
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
            if row:
                logger.debug("Found session: %s", session_id)
                return Session.from_row(row)
            logger.debug("Session not found: %s", session_id)
            return None
        finally:
            await db.close()

    async def create(self, session: Session) -> Session:
        """Create a new session. Generates an ID if not provided."""
        logger.debug("Creating session: agent_id=%s name=%s", session.agent_id, session.name)
        if not session.id:
            session.id = str(uuid.uuid4())
        db = await get_db()
        try:
            row = session.to_row_insert()
            await db.execute(
                """INSERT INTO sessions
                   (id, agent_id, name, model, cwd, effort_level,
                    permission_mode, status, env_vars, acp_session_id,
                    created_at, last_active_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           datetime('now'), datetime('now'))""",
                (
                    row["id"], row["agent_id"], row["name"], row["model"],
                    row["cwd"], row["effort_level"], row["permission_mode"],
                    row["status"], row["env_vars"], row["acp_session_id"],
                ),
            )
            await db.commit()
            logger.debug("Created session: id=%s", session.id)
            return await self.get(session.id) or session
        finally:
            await db.close()

    async def update(self, session: Session) -> Session | None:
        """Update an existing session. Returns None if not found."""
        logger.debug("Updating session: id=%s", session.id)
        db = await get_db()
        try:
            row = session.to_row()
            await db.execute(
                """UPDATE sessions SET
                   agent_id = ?, name = ?, model = ?, cwd = ?,
                   effort_level = ?, permission_mode = ?, status = ?,
                   env_vars = ?, acp_session_id = ?,
                   last_active_at = datetime('now')
                   WHERE id = ?""",
                (
                    row["agent_id"], row["name"], row["model"], row["cwd"],
                    row["effort_level"], row["permission_mode"], row["status"],
                    row["env_vars"], row["acp_session_id"], row["id"],
                ),
            )
            await db.commit()
            if db.total_changes > 0:
                logger.debug("Updated session: id=%s", session.id)
                return await self.get(session.id)
            logger.debug("Session not found for update: %s", session.id)
            return None
        finally:
            await db.close()

    async def delete(self, session_id: str) -> bool:
        """Delete a session by ID."""
        logger.debug("Deleting session: %s", session_id)
        db = await get_db()
        try:
            await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await db.commit()
            if db.total_changes > 0:
                logger.debug("Deleted session: %s", session_id)
                return True
            logger.debug("Session not found for delete: %s", session_id)
            return False
        finally:
            await db.close()

    async def touch(self, session_id: str) -> None:
        """Update last_active_at timestamp."""
        logger.debug("Touching session: %s", session_id)
        db = await get_db()
        try:
            await db.execute(
                "UPDATE sessions SET last_active_at = datetime('now') WHERE id = ?",
                (session_id,),
            )
            await db.commit()
        finally:
            await db.close()
