"""Tests for Template REST API routes and page routes."""

import pytest


@pytest.fixture(autouse=True)
def _patch_template_store(test_db, test_agents_file):
    """Patch the template store in routes to use the test database."""
    import backend.routes.templates as routes_module
    from backend.models.template import TemplateStore
    from backend.models.agent import AgentStore

    routes_module._template_store = TemplateStore()
    routes_module._agent_store = AgentStore(file_path=test_agents_file)
    yield
    routes_module._template_store = None
    routes_module._agent_store = None


class TestTemplateAPI:
    """REST API tests for /api/templates endpoints."""

    def test_list_templates_empty(self, client):
        """GET /api/templates returns empty list when no templates exist."""
        response = client.get("/api/templates")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert data == []

    def test_create_template(self, client):
        """POST /api/templates creates a new template."""
        response = client.post("/api/templates", json={
            "name": "Default Template",
            "agent_id": "pi-agent",
            "model": "gpt-4",
            "cwd": "/home/user/project",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Default Template"
        assert data["agent_id"] == "pi-agent"
        assert data["model"] == "gpt-4"
        assert data["id"] != ""
        assert len(data["id"]) == 36

    def test_create_template_defaults(self, client):
        """POST /api/templates with minimal fields uses defaults."""
        response = client.post("/api/templates", json={
            "name": "Minimal",
            "agent_id": "pi-agent",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["model"] == ""
        assert data["cwd"] == ""
        assert data["env_vars"] == {}

    def test_get_template(self, client):
        """GET /api/templates/{id} returns a specific template."""
        # First create one
        create = client.post("/api/templates", json={
            "name": "To Get", "agent_id": "pi-agent",
        })
        tid = create.json()["id"]

        response = client.get(f"/api/templates/{tid}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "To Get"

    def test_get_template_not_found(self, client):
        """GET /api/templates/{id} returns 404 for non-existent template."""
        response = client.get("/api/templates/nonexistent")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_template(self, client):
        """PUT /api/templates/{id} updates a template."""
        create = client.post("/api/templates", json={
            "name": "Original", "agent_id": "pi-agent", "model": "gpt-3",
        })
        tid = create.json()["id"]

        response = client.put(f"/api/templates/{tid}", json={
            "name": "Updated", "model": "gpt-4",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated"
        assert data["model"] == "gpt-4"

        # Verify persistence
        get_response = client.get(f"/api/templates/{tid}")
        assert get_response.json()["name"] == "Updated"

    def test_update_template_partial(self, client):
        """PUT /api/templates/{id} with partial data only updates provided fields."""
        create = client.post("/api/templates", json={
            "name": "Partial", "agent_id": "pi-agent", "cwd": "/original",
        })
        tid = create.json()["id"]

        # Update only name
        response = client.put(f"/api/templates/{tid}", json={"name": "Renamed"})
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Renamed"
        assert data["cwd"] == "/original"  # unchanged

    def test_update_template_not_found(self, client):
        """PUT /api/templates/{id} returns 404 for non-existent template."""
        response = client.put("/api/templates/nonexistent", json={"name": "Ghost"})
        assert response.status_code == 404

    def test_delete_template(self, client):
        """DELETE /api/templates/{id} deletes a template."""
        create = client.post("/api/templates", json={
            "name": "To Delete", "agent_id": "pi-agent",
        })
        tid = create.json()["id"]

        response = client.delete(f"/api/templates/{tid}")
        assert response.status_code == 200
        assert response.json()["detail"] == "Template deleted"

        # Verify gone
        get_response = client.get(f"/api/templates/{tid}")
        assert get_response.status_code == 404

    def test_delete_template_not_found(self, client):
        """DELETE /api/templates/{id} returns 404 for non-existent template."""
        response = client.delete("/api/templates/nonexistent")
        assert response.status_code == 404

    def test_create_template_invalid_body(self, client):
        """POST /api/templates with missing required field returns 422."""
        response = client.post("/api/templates", json={})
        assert response.status_code == 422

    def test_list_templates_has_data(self, client):
        """GET /api/templates returns populated list after creating templates."""
        client.post("/api/templates", json={"name": "T1", "agent_id": "pi-agent"})
        client.post("/api/templates", json={"name": "T2", "agent_id": "pi-agent"})

        response = client.get("/api/templates")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        names = [t["name"] for t in data]
        assert "T1" in names
        assert "T2" in names


class TestTemplatePages:
    """Jinja page route tests."""

    def test_templates_page_renders(self, client):
        """GET /templates returns HTML page."""
        response = client.get("/templates")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Templates" in response.text

    def test_templates_page_has_create_button(self, client):
        """The templates page has an Add Template button."""
        response = client.get("/templates")
        assert "Add Template" in response.text
