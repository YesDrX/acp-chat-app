"""Tests for Agent model and AgentStore."""

import pytest

from backend.models.agent import Agent, AgentStore


def test_agent_creation():
    """Test creating an Agent dataclass."""
    agent = Agent(
        id="test-1",
        name="Test Agent",
        type="cli",
        command="echo",
        args=["hello"],
        env_vars={"KEY": "value"},
        description="A test agent",
    )
    assert agent.id == "test-1"
    assert agent.name == "Test Agent"
    assert agent.command == "echo"
    assert agent.args == ["hello"]
    assert agent.env_vars == {"KEY": "value"}


def test_agent_to_dict():
    """Test serializing Agent to dict."""
    agent = Agent(id="test-1", name="Test", command="echo", args=["hello"])
    d = agent.to_dict()
    assert d["id"] == "test-1"
    assert d["name"] == "Test"
    assert d["args"] == ["hello"]


def test_agent_from_dict():
    """Test deserializing Agent from dict."""
    data = {
        "id": "test-1",
        "name": "Test Agent",
        "type": "cli",
        "command": "echo",
        "args": ["hello"],
        "env_vars": {"K": "V"},
        "description": "desc",
    }
    agent = Agent.from_dict(data)
    assert agent.id == "test-1"
    assert agent.command == "echo"
    assert agent.env_vars == {"K": "V"}


def test_agent_from_dict_defaults():
    """Test from_dict provides defaults for missing fields."""
    data = {"id": "test-1", "name": "Test"}
    agent = Agent.from_dict(data)
    assert agent.args == []
    assert agent.env_vars == {}
    assert agent.description == ""


def test_store_list_all(test_agents_file):
    """Test listing agents from the store."""
    store = AgentStore(file_path=test_agents_file)
    agents = store.list_all()
    assert len(agents) >= 1
    assert any(a.id == "pi-agent" for a in agents)


def test_store_create(test_agents_file):
    """Test creating a new agent."""
    store = AgentStore(file_path=test_agents_file)
    agent = Agent(id="custom-1", name="Custom Agent", command="my-agent")
    created = store.create(agent)
    assert created.id == "custom-1"

    # Verify it was persisted
    found = store.get("custom-1")
    assert found is not None
    assert found.name == "Custom Agent"


def test_store_create_generates_id(test_agents_file):
    """Test that create generates a UUID when no ID is provided."""
    store = AgentStore(file_path=test_agents_file)
    agent = Agent(id="", name="Auto ID", command="test")
    created = store.create(agent)
    assert created.id != ""
    assert len(created.id) == 36  # UUID length


def test_store_get_not_found(test_agents_file):
    """Test getting a non-existent agent returns None."""
    store = AgentStore(file_path=test_agents_file)
    result = store.get("nonexistent")
    assert result is None


def test_store_update(test_agents_file):
    """Test updating an existing agent."""
    store = AgentStore(file_path=test_agents_file)
    agent = store.get("pi-agent")
    assert agent is not None
    agent.name = "Updated Pi Agent"
    agent.description = "Updated description"

    updated = store.update(agent)
    assert updated is not None
    assert updated.name == "Updated Pi Agent"

    # Verify persisted
    found = store.get("pi-agent")
    assert found.name == "Updated Pi Agent"


def test_store_update_not_found(test_agents_file):
    """Test updating a non-existent agent returns None."""
    store = AgentStore(file_path=test_agents_file)
    agent = Agent(id="nonexistent", name="Ghost")
    result = store.update(agent)
    assert result is None


def test_store_delete(test_agents_file):
    """Test deleting an agent."""
    store = AgentStore(file_path=test_agents_file)
    # Create an agent to delete
    store.create(Agent(id="delete-me", name="Delete Me", command="rm"))

    result = store.delete("delete-me")
    assert result is True
    assert store.get("delete-me") is None


def test_store_delete_not_found(test_agents_file):
    """Test deleting a non-existent agent returns False."""
    store = AgentStore(file_path=test_agents_file)
    result = store.delete("nonexistent")
    assert result is False


def test_default_agent_exists(test_agents_file):
    """Test that the default pi-agent is seeded on first access."""
    store = AgentStore(file_path=test_agents_file)
    agent = store.get("pi-agent")
    assert agent is not None
    assert agent.name == "Pi Agent"
    assert agent.command == "npx"
