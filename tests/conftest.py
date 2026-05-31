"""Test fixtures for ACP Chat App."""

import logging
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

# Set TEST_MODE before importing backend modules that read config
os.environ["ACP_CHAT_TEST_MODE"] = "1"

logging.basicConfig(level=logging.DEBUG)


@pytest.fixture
def temp_config_dir():
    """Create a temporary config directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        yield tmpdir_path


@pytest_asyncio.fixture
async def test_db():
    """Create a temporary SQLite database for tests and initialize schema."""
    import backend.database as db_module

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    # Override the config path temporarily
    original_path = db_module.DATABASE_PATH
    db_module.DATABASE_PATH = db_path

    try:
        await db_module.init_db()
        yield db_path
    finally:
        db_module.DATABASE_PATH = original_path
        os.unlink(db_path)


@pytest.fixture
def test_agents_file(tmp_path):
    """Create a temporary agents.json for testing AgentStore."""
    import backend.models.agent as agent_module

    agents_file = tmp_path / "agents.json"
    original = agent_module.AGENTS_FILE
    agent_module.AGENTS_FILE = agents_file

    # Clear any cached state
    yield agents_file

    agent_module.AGENTS_FILE = original


@pytest.fixture
def client():
    """Create a FastAPI TestClient."""
    from backend.main import app
    return TestClient(app)
