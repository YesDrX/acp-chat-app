"""Tests for Session REST API routes and page routes."""

import pytest


@pytest.fixture(autouse=True)
def _patch_session_stores(test_db, test_agents_file):
    """Patch the session, template, and agent stores in routes to use test backends."""
    import backend.routes.sessions as routes_module
    from backend.models.session import SessionStore
    from backend.models.template import TemplateStore
    from backend.models.agent import AgentStore

    routes_module._session_store = SessionStore()
    routes_module._template_store = TemplateStore()
    routes_module._agent_store = AgentStore(file_path=test_agents_file)
    yield
    routes_module._session_store = None
    routes_module._template_store = None
    routes_module._agent_store = None


class TestSessionAPI:
    """REST API tests for /api/sessions endpoints."""

    def test_list_sessions_empty(self, client):
        """GET /api/sessions returns empty list when no sessions exist."""
        response = client.get("/api/sessions")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data == []

    def test_create_session(self, client):
        """POST /api/sessions creates a new session."""
        response = client.post("/api/sessions", json={
            "agent_id": "pi-agent",
            "name": "Test Session",
            "model": "gpt-4",
            "cwd": "/tmp/test",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["agent_id"] == "pi-agent"
        assert data["name"] == "Test Session"
        assert data["model"] == "gpt-4"
        assert data["status"] == "created"
        assert data["id"] != ""
        assert len(data["id"]) == 36  # UUID

    def test_create_session_defaults(self, client):
        """POST /api/sessions with minimal fields uses defaults."""
        response = client.post("/api/sessions", json={
            "agent_id": "pi-agent",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["agent_id"] == "pi-agent"
        assert data["model"] == ""
        assert data["cwd"] == "/tmp"
        assert data["env_vars"] == {}
        assert data["status"] == "created"

    def test_create_session_invalid_agent(self, client):
        """POST /api/sessions with invalid agent_id returns 400."""
        response = client.post("/api/sessions", json={
            "agent_id": "nonexistent-agent",
        })
        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    def test_create_session_missing_agent_id(self, client):
        """POST /api/sessions without agent_id returns 422."""
        response = client.post("/api/sessions", json={})
        assert response.status_code == 422

    def test_get_session(self, client):
        """GET /api/sessions/{id} returns a specific session."""
        # Create first
        create = client.post("/api/sessions", json={
            "agent_id": "pi-agent", "name": "To Get",
        })
        sid = create.json()["id"]

        response = client.get(f"/api/sessions/{sid}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "To Get"
        assert data["agent_id"] == "pi-agent"

    def test_get_session_not_found(self, client):
        """GET /api/sessions/{id} returns 404 for non-existent session."""
        response = client.get("/api/sessions/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_session(self, client):
        """PUT /api/sessions/{id} updates session settings."""
        create = client.post("/api/sessions", json={
            "agent_id": "pi-agent", "name": "Original", "model": "gpt-3",
        })
        sid = create.json()["id"]

        response = client.put(f"/api/sessions/{sid}", json={
            "name": "Updated", "model": "gpt-4",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated"
        assert data["model"] == "gpt-4"

        # Verify persistence
        get_response = client.get(f"/api/sessions/{sid}")
        assert get_response.json()["name"] == "Updated"

    def test_update_session_partial(self, client):
        """PUT /api/sessions/{id} with partial data only updates provided fields."""
        create = client.post("/api/sessions", json={
            "agent_id": "pi-agent", "name": "Partial", "model": "gpt-3",
        })
        sid = create.json()["id"]

        # Update only name
        response = client.put(f"/api/sessions/{sid}", json={"name": "Renamed"})
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Renamed"
        assert data["model"] == "gpt-3"  # unchanged

    def test_update_session_not_found(self, client):
        """PUT /api/sessions/{id} returns 404 for non-existent session."""
        response = client.put("/api/sessions/nonexistent", json={"name": "Ghost"})
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_delete_session(self, client):
        """DELETE /api/sessions/{id} deletes a session."""
        create = client.post("/api/sessions", json={
            "agent_id": "pi-agent", "name": "To Delete",
        })
        sid = create.json()["id"]

        response = client.delete(f"/api/sessions/{sid}")
        assert response.status_code == 200
        assert response.json()["detail"] == "Session deleted"

        # Verify gone
        get_response = client.get(f"/api/sessions/{sid}")
        assert get_response.status_code == 404

    def test_delete_session_not_found(self, client):
        """DELETE /api/sessions/{id} returns 404 for non-existent session."""
        response = client.delete("/api/sessions/nonexistent")
        assert response.status_code == 404

    def test_list_sessions_with_agent_filter(self, client):
        """GET /api/sessions?agent_id=X filters by agent."""
        client.post("/api/sessions", json={"agent_id": "pi-agent", "name": "S1"})
        # Create a second agent
        from backend.models.agent import AgentStore
        import os
        # Create another agent in the test store
        agent_data = {
            "id": "other-agent", "name": "Other", "type": "cli",
            "command": "echo", "args": [], "env_vars": {}, "description": "",
        }
        import json
        agents_file = os.environ.get("ACP_CHAT_TEST_AGENTS_FILE")
        # Since we're using test_agents_file fixture, agents.json has pi-agent
        # We need to add another agent first
        from backend.models.agent import Agent
        from backend.routes.sessions import _get_agent_store
        agent_store = _get_agent_store()
        agent_store.create(Agent(**agent_data))

        client.post("/api/sessions", json={"agent_id": "other-agent", "name": "S2"})

        # Filter by pi-agent
        response = client.get("/api/sessions?agent_id=pi-agent")
        assert response.status_code == 200
        data = response.json()
        assert all(s["agent_id"] == "pi-agent" for s in data)

    def test_list_sessions_with_status_filter(self, client):
        """GET /api/sessions?status=X filters by status."""
        client.post("/api/sessions", json={"agent_id": "pi-agent", "name": "S1"})
        client.post("/api/sessions", json={"agent_id": "pi-agent", "name": "S2"})

        response = client.get("/api/sessions?status=created")
        assert response.status_code == 200
        data = response.json()
        assert all(s["status"] == "created" for s in data)

    def test_list_sessions_populated(self, client):
        """GET /api/sessions returns populated list after creating sessions."""
        client.post("/api/sessions", json={"agent_id": "pi-agent", "name": "S1"})
        client.post("/api/sessions", json={"agent_id": "pi-agent", "name": "S2"})

        response = client.get("/api/sessions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        names = [s["name"] for s in data]
        assert "S1" in names
        assert "S2" in names


class TestSessionTemplateIntegration:
    """Tests for template-based session creation."""

    def test_create_session_with_template(self, client):
        """POST /api/sessions with template_id loads defaults from template."""
        # Create a template first
        tmpl_resp = client.post("/api/templates", json={
            "name": "My Template",
            "agent_id": "pi-agent",
            "model": "gpt-4",
            "cwd": "/home/user/project",
            "effort_level": "high",
            "permission_mode": "accept_edits",
            "env_vars": {"OPENAI_KEY": "sk-123"},
        })
        assert tmpl_resp.status_code == 201
        tmpl_id = tmpl_resp.json()["id"]

        # Create session with template_id
        session_resp = client.post("/api/sessions", json={
            "agent_id": "pi-agent",
            "template_id": tmpl_id,
        })
        assert session_resp.status_code == 201
        data = session_resp.json()
        assert data["model"] == "gpt-4"
        assert data["cwd"] == "/home/user/project"
        assert data["effort_level"] == "high"
        assert data["permission_mode"] == "accept_edits"
        assert data["env_vars"] == {"OPENAI_KEY": "sk-123"}

    def test_create_session_with_template_overrides(self, client):
        """Explicit fields override template defaults."""
        # Create a template
        tmpl_resp = client.post("/api/templates", json={
            "name": "Override Template",
            "agent_id": "pi-agent",
            "model": "gpt-3",
            "cwd": "/template/cwd",
            "effort_level": "low",
            "env_vars": {"BASE": "template"},
        })
        tmpl_id = tmpl_resp.json()["id"]

        # Create session with template + overrides
        session_resp = client.post("/api/sessions", json={
            "agent_id": "pi-agent",
            "template_id": tmpl_id,
            "model": "gpt-4",  # override
            "cwd": "/override/cwd",  # override
        })
        assert session_resp.status_code == 201
        data = session_resp.json()
        assert data["model"] == "gpt-4"  # overridden
        assert data["cwd"] == "/override/cwd"  # overridden
        assert data["effort_level"] == "low"  # from template (not overridden)
        assert data["env_vars"] == {"BASE": "template"}  # from template

    def test_create_session_with_template_env_merge(self, client):
        """Session env_vars merge with template env_vars (explicit overrides)."""
        tmpl_resp = client.post("/api/templates", json={
            "name": "Env Template",
            "agent_id": "pi-agent",
            "env_vars": {"A": "from_template", "B": "also_template"},
        })
        tmpl_id = tmpl_resp.json()["id"]

        # Session overrides B, adds C
        session_resp = client.post("/api/sessions", json={
            "agent_id": "pi-agent",
            "template_id": tmpl_id,
            "env_vars": {"B": "overridden", "C": "session_only"},
        })
        assert session_resp.status_code == 201
        data = session_resp.json()
        assert data["env_vars"] == {
            "A": "from_template",
            "B": "overridden",
            "C": "session_only",
        }

    def test_create_session_with_nonexistent_template(self, client):
        """POST /api/sessions with invalid template_id returns 404."""
        response = client.post("/api/sessions", json={
            "agent_id": "pi-agent",
            "template_id": "nonexistent-template",
        })
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestSessionResume:
    """Tests for the session resume endpoint."""

    def test_resume_session(self, client):
        """POST /api/sessions/{id}/resume sets status to resuming."""
        create = client.post("/api/sessions", json={
            "agent_id": "pi-agent", "name": "Resume Me",
        })
        sid = create.json()["id"]
        assert create.json()["status"] == "created"

        response = client.post(f"/api/sessions/{sid}/resume")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "resuming"

        # Verify persisted
        get_response = client.get(f"/api/sessions/{sid}")
        assert get_response.json()["status"] == "resuming"

    def test_resume_session_not_found(self, client):
        """POST /api/sessions/{id}/resume returns 404 for non-existent session."""
        response = client.post("/api/sessions/nonexistent/resume")
        assert response.status_code == 404


class TestSessionPages:
    """Jinja page route tests."""

    def test_sessions_page_renders(self, client):
        """GET /sessions returns HTML page."""
        response = client.get("/sessions")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Sessions" in response.text

    def test_sessions_page_has_new_button(self, client):
        """The sessions page has a New Session button."""
        response = client.get("/sessions")
        assert "New Session" in response.text

    def test_sessions_new_page_renders(self, client):
        """GET /sessions/new returns HTML page."""
        response = client.get("/sessions/new")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Sessions" in response.text

    def test_session_chat_page_renders(self, client):
        """GET /sessions/{id} returns HTML chat page."""
        # Create a session first
        create = client.post("/api/sessions", json={
            "agent_id": "pi-agent", "name": "Chat Test",
        })
        sid = create.json()["id"]

        response = client.get(f"/sessions/{sid}")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Chat Test" in response.text
        assert "Type a message" in response.text

    def test_session_chat_page_not_found(self, client):
        """GET /sessions/{id} returns 404 for non-existent session."""
        response = client.get("/sessions/nonexistent")
        assert response.status_code == 404
