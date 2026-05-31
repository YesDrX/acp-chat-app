"""Tests for FastAPI main application."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a TestClient for the FastAPI app."""
    from backend.main import app
    return TestClient(app)


def test_index_page(client):
    """Test that the home page renders."""
    response = client.get("/")
    assert response.status_code == 200
    assert "ACP Chat App" in response.text


def test_static_files_served(client):
    """Test that static files are served."""
    response = client.get("/static/css/style.css")
    assert response.status_code == 200


def test_api_docs_available(client):
    """Test that OpenAPI docs are available."""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "swagger" in response.text.lower() or "openapi" in response.text.lower()


def test_openapi_schema(client):
    """Test that the OpenAPI JSON schema is valid."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert data["info"]["title"] == "ACP Chat App"
