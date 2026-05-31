"""Tests for settings routes and API."""

import json

import pytest
from fastapi.testclient import TestClient


class TestSettingsAPI:
    """Tests for /api/settings endpoints."""

    def test_get_settings_defaults(self, client: TestClient):
        """GET /api/settings returns default settings."""
        response = client.get("/api/settings")
        assert response.status_code == 200
        data = response.json()
        assert "idle_timeout_seconds" in data
        assert "theme" in data
        assert "files_directory" in data
        assert "config_directory" in data
        assert data["theme"] == "dark"

    def test_update_idle_timeout(self, client: TestClient):
        """PUT /api/settings updates idle_timeout_seconds."""
        response = client.put("/api/settings", json={
            "idle_timeout_seconds": 600,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["idle_timeout_seconds"] == 600

        # Verify persistence
        response2 = client.get("/api/settings")
        assert response2.json()["idle_timeout_seconds"] == 600

    def test_update_theme(self, client: TestClient):
        """PUT /api/settings updates theme."""
        response = client.put("/api/settings", json={"theme": "light"})
        assert response.status_code == 200
        assert response.json()["theme"] == "light"

        # Switch back to dark
        response2 = client.put("/api/settings", json={"theme": "dark"})
        assert response2.json()["theme"] == "dark"

    def test_update_settings_persist(self, client: TestClient):
        """Settings persist across multiple reads."""
        # Update
        client.put("/api/settings", json={
            "idle_timeout_seconds": 120,
            "theme": "dark",
        })

        # Read three times
        for _ in range(3):
            response = client.get("/api/settings")
            assert response.status_code == 200
            data = response.json()
            assert data["idle_timeout_seconds"] == 120
            assert data["theme"] == "dark"

    def test_update_invalid_timeout(self, client: TestClient):
        """PUT with too-small timeout returns 400."""
        response = client.put("/api/settings", json={
            "idle_timeout_seconds": 5,
        })
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_update_invalid_theme(self, client: TestClient):
        """PUT with invalid theme returns 400."""
        response = client.put("/api/settings", json={"theme": "blue"})
        assert response.status_code == 400
        assert "detail" in response.json()

    def test_settings_page_renders(self, client: TestClient):
        """GET /settings returns HTML page."""
        response = client.get("/settings")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        html = response.text
        assert "Settings" in html
        assert "idle_timeout_seconds" in html or "Idle Timeout" in html
