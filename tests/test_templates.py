"""Tests for Template model and TemplateStore."""

import pytest

from backend.models.template import Template, TemplateStore


@pytest.mark.asyncio
async def test_template_creation():
    """Test creating a Template dataclass."""
    tmpl = Template(
        id="t1",
        name="Default",
        agent_id="pi-agent",
        model="gpt-4",
        cwd="/tmp",
        permission_mode="default",
        env_vars={"KEY": "val"},
    )
    assert tmpl.id == "t1"
    assert tmpl.agent_id == "pi-agent"
    assert tmpl.env_vars == {"KEY": "val"}


@pytest.mark.asyncio
async def test_template_to_row():
    """Test env_vars serialization in to_row."""
    tmpl = Template(id="t1", name="T", agent_id="a1", env_vars={"K": "V"})
    row = tmpl.to_row()
    assert row["env_vars"] == '{"K": "V"}'


@pytest.mark.asyncio
async def test_template_from_row():
    """Test deserializing template from row."""
    row = {
        "id": "t1", "name": "Test", "agent_id": "a1", "model": "m1",
        "cwd": "/tmp", "effort_level": "", "permission_mode": "",
        "env_vars": '{"K": "V"}', "created_at": "2026-01-01",
    }
    tmpl = Template.from_row(row)
    assert tmpl.env_vars == {"K": "V"}


@pytest.mark.asyncio
async def test_store_create_and_get(test_db):
    """Test creating and retrieving a template."""
    store = TemplateStore()
    tmpl = Template(name="My Template", agent_id="pi-agent")
    created = await store.create(tmpl)

    assert created.id != ""
    assert created.name == "My Template"

    found = await store.get(created.id)
    assert found is not None
    assert found.name == "My Template"


@pytest.mark.asyncio
async def test_store_list_all(test_db):
    """Test listing all templates."""
    store = TemplateStore()
    await store.create(Template(name="T1", agent_id="a1"))
    await store.create(Template(name="T2", agent_id="a2"))

    templates = await store.list_all()
    assert len(templates) >= 2


@pytest.mark.asyncio
async def test_store_update(test_db):
    """Test updating a template."""
    store = TemplateStore()
    tmpl = await store.create(Template(name="Original", agent_id="a1"))

    tmpl.name = "Updated"
    tmpl.model = "claude-3"
    updated = await store.update(tmpl)
    assert updated is not None
    assert updated.name == "Updated"
    assert updated.model == "claude-3"


@pytest.mark.asyncio
async def test_store_delete(test_db):
    """Test deleting a template."""
    store = TemplateStore()
    tmpl = await store.create(Template(name="To Delete", agent_id="a1"))

    result = await store.delete(tmpl.id)
    assert result is True
    assert await store.get(tmpl.id) is None


@pytest.mark.asyncio
async def test_store_get_not_found(test_db):
    """Test getting non-existent template."""
    store = TemplateStore()
    assert await store.get("nonexistent") is None


@pytest.mark.asyncio
async def test_store_update_not_found(test_db):
    """Test updating non-existent template."""
    store = TemplateStore()
    result = await store.update(Template(id="nonexistent", name="Ghost", agent_id="a1"))
    assert result is None


@pytest.mark.asyncio
async def test_store_delete_not_found(test_db):
    """Test deleting non-existent template."""
    store = TemplateStore()
    result = await store.delete("nonexistent")
    assert result is False
