"""Database layer — async SQLite connection and schema initialization."""

import logging
import aiosqlite

from backend.config import DATABASE_PATH

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    name TEXT DEFAULT '',
    model TEXT DEFAULT '',
    cwd TEXT DEFAULT '',
    effort_level TEXT DEFAULT '',
    permission_mode TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    env_vars TEXT DEFAULT '{}',
    acp_session_id TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    last_active_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    model TEXT DEFAULT '',
    cwd TEXT DEFAULT '',
    effort_level TEXT DEFAULT '',
    permission_mode TEXT DEFAULT '',
    env_vars TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_agent_id ON sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
"""


async def get_db() -> aiosqlite.Connection:
    """Get a new async SQLite connection."""
    logger.debug("Opening database connection: %s", DATABASE_PATH)
    conn = await aiosqlite.connect(DATABASE_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    logger.debug("Database connection opened with WAL mode and foreign keys")
    return conn


async def init_db(conn: aiosqlite.Connection | None = None) -> None:
    """Initialize the database schema.

    Creates all tables if they don't exist.
    If conn is not provided, creates a temporary connection.
    """
    logger.debug("Initializing database schema")
    own_conn = conn is None
    if own_conn:
        conn = await get_db()

    try:
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()
        logger.debug("Database schema initialized successfully")
    finally:
        if own_conn and conn:
            await conn.close()
            logger.debug("Temporary database connection closed")


async def close_db(conn: aiosqlite.Connection) -> None:
    """Close a database connection."""
    logger.debug("Closing database connection")
    await conn.close()
