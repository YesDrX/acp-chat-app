"""Message model and store for chat history persistence."""

from __future__ import annotations

import dataclasses
import logging
import uuid
from datetime import datetime
from typing import Any

from backend.database import get_db

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class Message:
    id: str
    session_id: str
    role: str  # "user" | "agent" | "system"
    content: str
    created_at: str = ""



class MessageStore:
    """CRUD operations for chat messages in SQLite."""

    async def create(self, message: Message) -> Message:
        db = await get_db()
        try:
            if not message.id:
                message.id = str(uuid.uuid4())
            if not message.created_at:
                message.created_at = datetime.now().isoformat()
            await db.execute(
                """INSERT INTO messages (id, session_id, role, content, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (message.id, message.session_id, message.role, message.content, message.created_at),
            )
            await db.commit()
            logger.debug("Created message: id=%s role=%s session=%s", message.id, message.role, message.session_id)
            return message
        finally:
            await db.close()

    async def get_by_session(self, session_id: str) -> list[Message]:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            )
            rows = await cursor.fetchall()
            return [
                Message(
                    id=row["id"],
                    session_id=row["session_id"],
                    role=row["role"],
                    content=row["content"],
                    created_at=row["created_at"] or "",
                )
                for row in rows
            ]
        finally:
            await db.close()

    async def delete_by_session(self, session_id: str) -> int:
        db = await get_db()
        try:
            cursor = await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            await db.commit()
            count = cursor.rowcount
            logger.debug("Deleted %d messages for session: %s", count, session_id)
            return count
        finally:
            await db.close()
