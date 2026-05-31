"""Tests for Agent REST API routes and page routes."""

import pytest

from backend.models.agent import Agent, AgentStore


@pytest.fixture(autouse=True)
def _patch_agent_store(test_agents_file):
    """Patch the agent store in routes to use a test file."""
    import backend.routes.agents as routes_module
    routes_module._agent_store = AgentStore(file_path=test_agents_file)
    yield
    routes_module._agent_store = None


class TestAgentAPI:
    """REST API tests for /api/agents endpoints."""

    def test_list_agents(self, client):
        """GET /api/agents returns list of agents."""
        response = client.get("/api/agents")
        assert response.status_code == 200
        agents = response.json()
        assert isinstance(agents, list)
        assert len(agents) >= 1
        assert any(a["id"] == "pi-agent" for a in agents)

    def test_create_agent(self, client):
        """POST /api/agents creates a new agent."""
        response = client.post("/api/agents", json={
            "name": "New Agent",
            "type": "cli",
            "command": "echo",
            "args": ["hello"],
            "description": "A test agent",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Agent"
        assert data["id"] != ""
        assert len(data["id"]) == 36  # UUID

    def test_create_agent_defaults(self, client):
        """POST /api/agents with minimal fields uses defaults."""
        response = client.post("/api/agents", json={
            "name": "Minimal Agent",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "cli"
        assert data["command"] == ""
        assert data["args"] == []
        assert data["env_vars"] == {}
        assert data["description"] == ""

    def test_get_agent(self, client):
        """GET /api/agents/{id} returns a specific agent."""
        response = client.get("/api/agents/pi-agent")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "pi-agent"
        assert data["name"] == "Pi Agent"

    def test_get_agent_not_found(self, client):
        """GET /api/agents/{id} returns 404 for non-existent agent."""
        response = client.get("/api/agents/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_agent(self, client):
        """PUT /api/agents/{id} updates an agent."""
        response = client.put("/api/agents/pi-agent", json={
            "name": "Updated Pi",
            "description": "Updated description",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Pi"
        assert data["description"] == "Updated description"

        # Verify persistence
        get_response = client.get("/api/agents/pi-agent")
        assert get_response.json()["name"] == "Updated Pi"

    def test_update_agent_partial(self, client):
        """PUT /api/agents/{id} with partial data only updates provided fields."""
        # First create an agent to update
        create = client.post("/api/agents", json={
            "name": "Partial Test", "command": "original", "args": ["a", "b"]
        })
        aid = create.json()["id"]

        # Update only the name
        response = client.put(f"/api/agents/{aid}", json={"name": "Renamed"})
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Renamed"
        assert data["command"] == "original"  # unchanged
        assert data["args"] == ["a", "b"]  # unchanged

    def test_update_agent_not_found(self, client):
        """PUT /api/agents/{id} returns 404 for non-existent agent."""
        response = client.put("/api/agents/nonexistent", json={"name": "Ghost"})
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_delete_agent(self, client):
        """DELETE /api/agents/{id} deletes an agent."""
        # Create an agent to delete
        create = client.post("/api/agents", json={"name": "To Delete"})
        aid = create.json()["id"]

        # Delete it
        response = client.delete(f"/api/agents/{aid}")
        assert response.status_code == 200
        assert response.json()["detail"] == "Agent deleted"

        # Verify it's gone
        get_response = client.get(f"/api/agents/{aid}")
        assert get_response.status_code == 404

    def test_delete_agent_not_found(self, client):
        """DELETE /api/agents/{id} returns 404 for non-existent agent."""
        response = client.delete("/api/agents/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_create_agent_invalid_body(self, client):
        """POST /api/agents with missing required field returns 422."""
        response = client.post("/api/agents", json={})
        assert response.status_code == 422


class TestAgentPages:
    """Jinja page route tests."""

    def test_agents_page_renders(self, client):
        """GET /agents returns HTML page."""
        response = client.get("/agents")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Agents" in response.text

    def test_agents_page_has_create_button(self, client):
        """The agents page has an Add Agent button."""
        response = client.get("/agents")
        assert "Add Agent" in response.text
