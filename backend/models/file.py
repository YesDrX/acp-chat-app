"""File metadata model — async CRUD backed by SQLite."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, asdict
from typing import Any

import aiosqlite

from backend.database import get_db

logger = logging.getLogger(__name__)


@dataclass
class FileMetadata:
    """Represents metadata for an uploaded file."""
    id: str = ""
    session_id: str = ""
    name: str = ""
    path: str = ""
    size: int = 0
    created_at: str = ""

    @classmethod
    def from_row(cls, row: aiosqlite.Row | dict[str, Any]) -> FileMetadata:
        """Create FileMetadata from a database row."""
        data = dict(row)
        logger.debug("Deserializing file: id=%s name=%s", data.get("id"), data.get("name"))
        return cls(
            id=data.get("id", ""),
            session_id=data.get("session_id", ""),
            name=data.get("name", ""),
            path=data.get("path", ""),
            size=data.get("size", 0),
            created_at=data.get("created_at", ""),
        )


class FileStore:
    """Async store for file metadata CRUD."""

    def __init__(self) -> None:
        logger.debug("FileStore initialized")

    async def list_all(self, session_id: str | None = None) -> list[FileMetadata]:
        """List all files, optionally filtered by session_id."""
        logger.debug("Listing files: session_id=%s", session_id)
        db = await get_db()
        try:
            if session_id:
                cursor = await db.execute(
                    "SELECT * FROM files WHERE session_id = ? ORDER BY created_at DESC",
                    (session_id,),
                )
            else:
                cursor = await db.execute("SELECT * FROM files ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            files = [FileMetadata.from_row(r) for r in rows]
            logger.debug("Found %d files", len(files))
            return files
        finally:
            await db.close()

    async def get(self, file_id: str) -> FileMetadata | None:
        """Get a file by ID."""
        logger.debug("Getting file: %s", file_id)
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM files WHERE id = ?", (file_id,))
            row = await cursor.fetchone()
            if row:
                logger.debug("Found file: %s", file_id)
                return FileMetadata.from_row(row)
            logger.debug("File not found: %s", file_id)
            return None
        finally:
            await db.close()

    async def create(self, file_meta: FileMetadata) -> FileMetadata:
        """Create a new file record. Generates an ID if not provided."""
        logger.debug("Creating file record: name=%s", file_meta.name)
        if not file_meta.id:
            file_meta.id = str(uuid.uuid4())
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO files (id, session_id, name, path, size, created_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))""",
                (file_meta.id, file_meta.session_id, file_meta.name,
                 file_meta.path, file_meta.size),
            )
            await db.commit()
            logger.debug("Created file record: id=%s", file_meta.id)
            return await self.get(file_meta.id) or file_meta
        finally:
            await db.close()

    async def delete(self, file_id: str) -> bool:
        """Delete a file record by ID."""
        logger.debug("Deleting file: %s", file_id)
        db = await get_db()
        try:
            await db.execute("DELETE FROM files WHERE id = ?", (file_id,))
            await db.commit()
            if db.total_changes > 0:
                logger.debug("Deleted file record: %s", file_id)
                return True
            logger.debug("File not found for delete: %s", file_id)
            return False
        finally:
            await db.close()

    async def update(self, file_meta: FileMetadata) -> FileMetadata | None:
        """Update file metadata (name, path, size). Returns None if not found."""
        logger.debug("Updating file record: id=%s name=%s", file_meta.id, file_meta.name)
        db = await get_db()
        try:
            await db.execute(
                """UPDATE files SET
                   name = ?, path = ?, size = ?, session_id = ?
                   WHERE id = ?""",
                (file_meta.name, file_meta.path, file_meta.size,
                 file_meta.session_id, file_meta.id),
            )
            await db.commit()
            if db.total_changes > 0:
                logger.debug("Updated file record: id=%s", file_meta.id)
                return await self.get(file_meta.id)
            logger.debug("File not found for update: %s", file_meta.id)
            return None
        finally:
            await db.close()
