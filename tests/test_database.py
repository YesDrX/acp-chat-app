"""Tests for database schema initialization."""

import pytest

from backend.database import init_db, get_db


@pytest.mark.asyncio
async def test_schema_creation(test_db):
    """Test that all tables are created successfully."""
    import aiosqlite

    conn = await get_db()
    try:
        # Query for table existence
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]

        assert "sessions" in tables, f"Expected 'sessions' table, got: {tables}"
        assert "templates" in tables, f"Expected 'templates' table, got: {tables}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_sessions_table_columns(test_db):
    """Test that sessions table has all expected columns."""
    conn = await get_db()
    try:
        cursor = await conn.execute("PRAGMA table_info(sessions)")
        columns = {row[1]: row[2] for row in await cursor.fetchall()}

        expected = ["id", "agent_id", "name", "model", "cwd", "effort_level",
                     "permission_mode", "status", "env_vars", "created_at", "last_active_at"]
        for col in expected:
            assert col in columns, f"Missing column: {col}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_templates_table_columns(test_db):
    """Test that templates table has all expected columns."""
    conn = await get_db()
    try:
        cursor = await conn.execute("PRAGMA table_info(templates)")
        columns = {row[1]: row[2] for row in await cursor.fetchall()}

        expected = ["id", "name", "agent_id", "model", "cwd", "effort_level",
                     "permission_mode", "env_vars", "created_at"]
        for col in expected:
            assert col in columns, f"Missing column: {col}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_files_table_columns(test_db):
    """Files are now stored on disk only; verify no files table exists."""
    conn = await get_db()
    try:
        cursor = await conn.execute("PRAGMA table_info(files)")
        columns = {row[1] for row in await cursor.fetchall()}
        assert len(columns) == 0, f"Expected no files table, got columns: {columns}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_idempotent_init(test_db):
    """Test that init_db can be called multiple times without errors."""
    await init_db()
    await init_db()
    # Should not raise
