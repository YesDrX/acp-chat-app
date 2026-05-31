"""End-to-end integration tests for ACP Chat App.

Tests the full CRUD lifecycle: agent → template → session → delete all.
Uses the FastAPI TestClient without spawning actual ACP subprocesses.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _patch_all_stores(test_db, test_agents_file):
    """Patch all stores in routes to use test backends."""
    from backend.models.session import SessionStore
    from backend.models.template import TemplateStore
    from backend.models.agent import AgentStore

    # Patch session routes
    import backend.routes.sessions as sessions_routes
    sessions_routes._session_store = SessionStore()
    sessions_routes._template_store = TemplateStore()
    sessions_routes._agent_store = AgentStore(file_path=test_agents_file)

    # Patch template routes
    import backend.routes.templates as templates_routes
    templates_routes._template_store = TemplateStore()
    templates_routes._agent_store = AgentStore(file_path=test_agents_file)

    # Patch agent routes
    import backend.routes.agents as agents_routes
    agents_routes._agent_store = AgentStore(file_path=test_agents_file)

    yield

    sessions_routes._session_store = None
    sessions_routes._template_store = None
    sessions_routes._agent_store = None
    templates_routes._template_store = None
    templates_routes._agent_store = None
    agents_routes._agent_store = None


class TestEndToEndLifecycle:
    """Full lifecycle test: create agent, template, session, verify, delete."""

    def test_full_crud_lifecycle(self, client: TestClient):
        """Create an agent, template, session, verify, then delete in reverse."""
        # ── 1. Create an agent ──
        agent_response = client.post("/api/agents", json={
            "name": "E2E Test Agent",
            "type": "cli",
            "command": "echo",
            "args": ["hello"],
            "env_vars": {"TEST_VAR": "test_value"},
            "description": "Agent for E2E testing",
        })
        assert agent_response.status_code == 201
        agent = agent_response.json()
        agent_id = agent["id"]
        assert agent["name"] == "E2E Test Agent"
        assert agent_id is not None

        # ── 2. Verify agent appears in list ──
        list_agents = client.get("/api/agents")
        assert list_agents.status_code == 200
        agent_ids = [a["id"] for a in list_agents.json()]
        assert agent_id in agent_ids

        # ── 3. Create a template ──
        template_response = client.post("/api/templates", json={
            "name": "E2E Test Template",
            "agent_id": agent_id,
            "model": "gpt-4",
            "cwd": "/tmp",
            "effort_level": "medium",
            "permission_mode": "default",
            "env_vars": {"OPENAI_API_KEY": "sk-test"},
        })
        assert template_response.status_code == 201
        template = template_response.json()
        template_id = template["id"]
        assert template["name"] == "E2E Test Template"
        assert template["agent_id"] == agent_id

        # ── 4. Verify template appears in list ──
        list_templates = client.get("/api/templates")
        assert list_templates.status_code == 200
        template_ids = [t["id"] for t in list_templates.json()]
        assert template_id in template_ids

        # ── 5. Create a session using the template ──
        session_response = client.post("/api/sessions", json={
            "agent_id": agent_id,
            "name": "E2E Test Session",
            "model": "gpt-4",
            "cwd": "/tmp/test",
            "effort_level": "high",
            "permission_mode": "accept_edits",
            "env_vars": {"CUSTOM_VAR": "custom"},
            "template_id": template_id,
        })
        assert session_response.status_code == 201
        session = session_response.json()
        session_id = session["id"]
        assert session_id is not None
        assert session["agent_id"] == agent_id
        # Template fields should be used since template was specified
        # (actual merge behavior depends on implementation)

        # ── 6. Get session details ──
        session_detail = client.get(f"/api/sessions/{session_id}")
        assert session_detail.status_code == 200
        detail = session_detail.json()
        assert detail["id"] == session_id
        assert detail["agent_id"] == agent_id

        # ── 7. Update session settings ──
        update_response = client.put(f"/api/sessions/{session_id}", json={
            "model": "gpt-4o",
            "effort_level": "low",
        })
        assert update_response.status_code == 200
        updated = update_response.json()
        assert updated["model"] == "gpt-4o"
        assert updated["effort_level"] == "low"

        # ── 8. Verify update persisted ──
        session_detail2 = client.get(f"/api/sessions/{session_id}")
        assert session_detail2.json()["model"] == "gpt-4o"

        # ── 9. Delete session ──
        delete_session = client.delete(f"/api/sessions/{session_id}")
        assert delete_session.status_code == 200

        # ── 10. Verify session deleted ──
        after_delete = client.get(f"/api/sessions/{session_id}")
        assert after_delete.status_code == 404

        # ── 11. Delete template ──
        delete_template = client.delete(f"/api/templates/{template_id}")
        assert delete_template.status_code == 200

        # ── 12. Delete agent ──
        delete_agent = client.delete(f"/api/agents/{agent_id}")
        assert delete_agent.status_code == 200


class TestEndToEndPages:
    """Tests that all main pages render correctly."""

    def test_index_page(self, client: TestClient):
        """Home page renders."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "ACP Chat" in response.text

    def test_agents_page(self, client: TestClient):
        """Agents page renders."""
        response = client.get("/agents")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Agents" in response.text

    def test_sessions_page(self, client: TestClient):
        """Sessions page renders."""
        response = client.get("/sessions")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Sessions" in response.text

    def test_templates_page(self, client: TestClient):
        """Templates page renders."""
        response = client.get("/templates")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Templates" in response.text

    def test_files_page(self, client: TestClient):
        """Files page renders."""
        response = client.get("/files")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "File" in response.text

    def test_settings_page(self, client: TestClient):
        """Settings page renders."""
        response = client.get("/settings")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Settings" in response.text


class TestEndToEndWithoutTemplate:
    """Test session creation without a template."""

    def test_session_without_template(self, client: TestClient):
        """Create session directly without template reference."""
        # Create agent first
        agent_resp = client.post("/api/agents", json={
            "name": "No-Template Agent",
            "type": "cli",
            "command": "echo",
            "args": ["hi"],
        })
        assert agent_resp.status_code == 201
        agent_id = agent_resp.json()["id"]

        # Create session directly
        session_resp = client.post("/api/sessions", json={
            "agent_id": agent_id,
            "name": "Direct Session",
            "model": "claude-3-opus",
            "cwd": "/home/test",
        })
        assert session_resp.status_code == 201
        session = session_resp.json()
        assert session["model"] == "claude-3-opus"
        assert session["cwd"] == "/home/test"
        assert session["name"] == "Direct Session"

        # Clean up
        client.delete(f"/api/sessions/{session['id']}")
        client.delete(f"/api/agents/{agent_id}")


class TestEndToEndMultipleSessions:
    """Test multiple sessions against same agent."""

    def test_multiple_sessions(self, client: TestClient):
        """Create multiple sessions for one agent."""
        # Create agent
        agent_resp = client.post("/api/agents", json={
            "name": "Multi-Session Agent",
            "type": "cli",
            "command": "node",
            "args": ["-e", "console.log(1)"],
        })
        assert agent_resp.status_code == 201
        agent_id = agent_resp.json()["id"]

        session_ids = []
        for i in range(3):
            resp = client.post("/api/sessions", json={
                "agent_id": agent_id,
                "name": f"Session {i + 1}",
                "model": "gpt-4",
            })
            assert resp.status_code == 201
            session_ids.append(resp.json()["id"])

        # List sessions — all should appear
        list_resp = client.get("/api/sessions")
        assert list_resp.status_code == 200
        sessions = list_resp.json()
        session_id_set = {s["id"] for s in sessions}
        for sid in session_ids:
            assert sid in session_id_set

        # Clean up
        for sid in session_ids:
            client.delete(f"/api/sessions/{sid}")
        client.delete(f"/api/agents/{agent_id}")

    def test_agent_deletion_cleanup(self, client: TestClient):
        """Deleting an agent should not break existing sessions (they persist)."""
        # Create agent
        agent_resp = client.post("/api/agents", json={
            "name": "Cleanup Test Agent",
            "type": "cli",
            "command": "echo",
            "args": [],
        })
        assert agent_resp.status_code == 201
        agent_id = agent_resp.json()["id"]

        # Create session
        session_resp = client.post("/api/sessions", json={
            "agent_id": agent_id,
            "name": "Orphan Test Session",
        })
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Delete agent
        client.delete(f"/api/agents/{agent_id}")

        # Session should still exist
        get_resp = client.get(f"/api/sessions/{session_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == session_id

        # Clean up
        client.delete(f"/api/sessions/{session_id}")
