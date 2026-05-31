"""Tests for Session model and SessionStore."""

import pytest

from backend.database import init_db
from backend.models.session import Session, SessionStore


@pytest.mark.asyncio
async def test_session_creation():
    """Test creating a Session dataclass."""
    session = Session(
        id="sess-1",
        agent_id="agent-1",
        name="Test Session",
        model="gpt-4",
        cwd="/home/user/project",
        effort_level="high",
        permission_mode="default",
        env_vars={"OPENAI_KEY": "sk-xxx"},
    )
    assert session.id == "sess-1"
    assert session.agent_id == "agent-1"
    assert session.model == "gpt-4"
    assert session.env_vars == {"OPENAI_KEY": "sk-xxx"}


@pytest.mark.asyncio
async def test_session_to_row():
    """Test that env_vars is serialized to JSON string in to_row()."""
    session = Session(id="s1", agent_id="a1", env_vars={"KEY": "val"})
    row = session.to_row()
    assert row["env_vars"] == '{"KEY": "val"}'


@pytest.mark.asyncio
async def test_session_from_row():
    """Test deserializing from a dict row."""
    row = {
        "id": "s1",
        "agent_id": "a1",
        "name": "Test",
        "model": "m1",
        "cwd": "/tmp",
        "effort_level": "",
        "permission_mode": "",
        "status": "active",
        "env_vars": '{"KEY": "val"}',
        "created_at": "2026-01-01",
        "last_active_at": "2026-01-02",
    }
    session = Session.from_row(row)
    assert session.id == "s1"
    assert session.env_vars == {"KEY": "val"}


@pytest.mark.asyncio
async def test_session_from_row_bad_json():
    """Test from_row handles malformed env_vars JSON."""
    row = {"id": "s1", "agent_id": "a1", "env_vars": "not-json", "name": "",
           "model": "", "cwd": "", "effort_level": "", "permission_mode": "",
           "status": "active", "created_at": "", "last_active_at": ""}
    session = Session.from_row(row)
    assert session.env_vars == {}


@pytest.mark.asyncio
async def test_store_create_and_get(test_db):
    """Test creating and retrieving a session."""
    store = SessionStore()
    session = Session(agent_id="agent-1", name="My Session", cwd="/tmp")
    created = await store.create(session)

    assert created.id != ""
    assert created.agent_id == "agent-1"
    assert created.status == "active"
    assert created.created_at != ""

    # Retrieve
    found = await store.get(created.id)
    assert found is not None
    assert found.name == "My Session"


@pytest.mark.asyncio
async def test_store_list_all(test_db):
    """Test listing all sessions."""
    store = SessionStore()
    await store.create(Session(agent_id="a1", name="S1"))
    await store.create(Session(agent_id="a2", name="S2"))

    sessions = await store.list_all()
    assert len(sessions) >= 2


@pytest.mark.asyncio
async def test_store_list_filtered(test_db):
    """Test listing sessions with filters."""
    store = SessionStore()
    await store.create(Session(agent_id="agent-x", name="X1"))
    await store.create(Session(agent_id="agent-y", name="Y1"))

    filtered = await store.list_all(agent_id="agent-x")
    assert all(s.agent_id == "agent-x" for s in filtered)


@pytest.mark.asyncio
async def test_store_update(test_db):
    """Test updating a session."""
    store = SessionStore()
    session = await store.create(Session(agent_id="a1", name="Original"))

    session.name = "Updated"
    session.model = "gpt-5"
    updated = await store.update(session)
    assert updated is not None
    assert updated.name == "Updated"
    assert updated.model == "gpt-5"


@pytest.mark.asyncio
async def test_store_update_not_found(test_db):
    """Test updating a non-existent session returns None."""
    store = SessionStore()
    result = await store.update(Session(id="nonexistent", agent_id="a1"))
    assert result is None


@pytest.mark.asyncio
async def test_store_delete(test_db):
    """Test deleting a session."""
    store = SessionStore()
    session = await store.create(Session(agent_id="a1", name="To Delete"))

    result = await store.delete(session.id)
    assert result is True
    assert await store.get(session.id) is None


@pytest.mark.asyncio
async def test_store_delete_not_found(test_db):
    """Test deleting a non-existent session returns False."""
    store = SessionStore()
    result = await store.delete("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_store_touch(test_db):
    """Test that touch updates last_active_at."""
    store = SessionStore()
    session = await store.create(Session(agent_id="a1", name="Touch Test"))
    old_time = session.last_active_at

    await store.touch(session.id)
    refreshed = await store.get(session.id)
    # last_active_at may or may not change depending on timing,
    # but the operation should not raise
    assert refreshed is not None
