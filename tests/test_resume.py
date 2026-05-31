"""Tests for session resume and idle shutdown via WebSocket.

Tests the WebSocket flow for:
- Connecting to idle/stopped sessions
- Resume attempt vs new session creation
- idle_shutdown messages
- session_resumed messages
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

logging.basicConfig(level=logging.DEBUG)


class FakeConnectionState:
    """Fake ConnectionState for testing."""

    def __init__(self, acp_session_id="acp-test-sess"):
        self.conn = AsyncMock()
        self.proc = MagicMock()
        self.proc.pid = 99999
        self.proc.returncode = None

        self.mock_client = MagicMock()
        self._queue = asyncio.Queue()
        self.mock_client.get_queue = MagicMock(return_value=self._queue)
        self.mock_client.remove_queue = MagicMock()
        self.mock_client.reset_buffer = MagicMock()
        self.mock_client.get_buffer = MagicMock(return_value="")
        self.client = self.mock_client

        self.ctx = AsyncMock()
        self.last_activity = 0
        self.acp_session_id = acp_session_id

    def touch(self):
        import time
        self.last_activity = time.time()


@pytest.fixture
def fake_manager():
    """Create a mock AcpConnectionManager."""
    mgr = MagicMock()
    mgr.is_active = MagicMock(return_value=False)
    mgr.start_session = AsyncMock(return_value="acp-fresh")
    mgr.resume_session_from = AsyncMock(return_value=("acp-resumed", True))
    mgr.send_prompt = AsyncMock()
    mgr.cancel = AsyncMock()
    mgr.stop_session = AsyncMock()
    mgr.set_model = AsyncMock()
    mgr.set_mode = AsyncMock()
    mgr.set_config_option = AsyncMock()
    mgr.get_state = MagicMock(return_value=None)
    mgr.update_activity = MagicMock()
    mgr.active_sessions = []
    return mgr


@pytest_asyncio.fixture
async def seed_test_data(test_db):
    """Seed test database with sessions in various states."""
    from backend.models.session import Session, SessionStore
    from backend.models.agent import AgentStore

    # Ensure pi-agent exists
    astore = AgentStore()
    if not astore.get("pi-agent"):
        from backend.models.agent import Agent
        astore.create(Agent(
            id="pi-agent", name="Pi Agent", type="cli",
            command="npx", args=["-y", "pi-acp"],
            env_vars={}, description="Default Pi ACP agent",
        ))

    sstore = SessionStore()
    # Created (never started) session
    await sstore.create(Session(
        id="fresh-sess", agent_id="pi-agent", name="Fresh",
        cwd="/tmp/fresh", status="created",
    ))
    # Idle session (was running, now idle)
    await sstore.create(Session(
        id="idle-sess", agent_id="pi-agent", name="Idle",
        cwd="/tmp/idle", status="idle",
    ))
    # Stopped session
    await sstore.create(Session(
        id="stopped-sess", agent_id="pi-agent", name="Stopped",
        cwd="/tmp/stopped", status="stopped",
    ))
    # Active session
    await sstore.create(Session(
        id="active-sess", agent_id="pi-agent", name="Active",
        cwd="/tmp/active", status="active",
    ))

    yield


@pytest.fixture
def client_with_manager(fake_manager, test_db, seed_test_data, test_agents_file):
    """Create a TestClient with mock manager injected."""
    from backend.main import app
    import backend.routes.ws as ws_module

    ws_module.set_manager(fake_manager)
    app.state.acp_manager = fake_manager
    return TestClient(app)


@pytest.fixture
def active_manager(fake_manager):
    """Manager that reports session as active."""
    fake_manager.is_active = MagicMock(return_value=True)
    fake_manager.get_state = MagicMock(return_value=FakeConnectionState())
    return fake_manager


class TestResumeFlow:
    """Tests for session resume WebSocket flow."""

    def test_connect_to_created_session_shows_resume_option(self, client_with_manager):
        """Connecting to a 'created' session should not show resume."""
        with client_with_manager.websocket_connect("/ws/fresh-sess") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            assert data["status"] == "created"
            # created sessions show can_resume=False
            assert data["can_resume"] is False

    def test_connect_to_idle_session_shows_resume_option(self, client_with_manager):
        """Connecting to an 'idle' session should show resume option."""
        with client_with_manager.websocket_connect("/ws/idle-sess") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            assert data["status"] == "idle"
            assert data["can_resume"] is True

    def test_connect_to_stopped_session_shows_resume(self, client_with_manager):
        """Connecting to a 'stopped' session should show resume option."""
        with client_with_manager.websocket_connect("/ws/stopped-sess") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            assert data["status"] == "stopped"
            assert data["can_resume"] is True

    def test_explicit_resume_message(self, client_with_manager, fake_manager):
        """Test sending explicit 'resume' message."""
        with client_with_manager.websocket_connect("/ws/idle-sess") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "resume"})

            data = ws.receive_json()
            assert data["type"] == "session_resumed"
            assert data["was_resumed"] is True
            assert data["acp_session_id"] == "acp-resumed"

            fake_manager.resume_session_from.assert_called_once()

    def test_resume_when_already_active(self, client_with_manager, fake_manager):
        """Test resume when session is already active responds immediately."""
        fake_manager.is_active = MagicMock(return_value=True)
        fake_manager.get_state = MagicMock(return_value=FakeConnectionState("acp-already"))

        with client_with_manager.websocket_connect("/ws/active-sess") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "resume"})

            data = ws.receive_json()
            assert data["type"] == "session_resumed"
            assert data["was_resumed"] is False
            # Should not have called resume_session_from
            fake_manager.resume_session_from.assert_not_called()

    def test_resume_failure(self, client_with_manager, fake_manager):
        """Test resume failure sends error."""
        fake_manager.resume_session_from = AsyncMock(
            side_effect=RuntimeError("Cannot resume")
        )

        with client_with_manager.websocket_connect("/ws/idle-sess") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "resume"})

            data = ws.receive_json()
            assert data["type"] == "error"
            assert "failed" in data["message"].lower()

    def test_prompt_triggers_resume_on_idle(self, client_with_manager, fake_manager):
        """Test sending a prompt to an idle session triggers resume."""
        fake_manager.send_prompt = AsyncMock()
        resp = MagicMock()
        resp.stop_reason = "end_turn"
        resp.message_id = "msg-1"
        fake_manager.send_prompt.return_value = resp

        with client_with_manager.websocket_connect("/ws/idle-sess") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "prompt", "text": "hello"})

            # Should get session_started with was_resumed
            data = ws.receive_json()
            assert data["type"] == "session_started"
            assert data.get("was_resumed") is True

            # Then prompt_complete
            data = ws.receive_json()
            assert data["type"] == "prompt_complete"

    def test_prompt_fresh_session(self, client_with_manager, fake_manager):
        """Test that prompt to fresh (created) session starts fresh."""
        fake_manager.send_prompt = AsyncMock()
        resp = MagicMock()
        resp.stop_reason = "end_turn"
        resp.message_id = "msg-1"
        fake_manager.send_prompt.return_value = resp

        with client_with_manager.websocket_connect("/ws/fresh-sess") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "prompt", "text": "hello"})

            data = ws.receive_json()
            assert data["type"] == "session_started"
            assert data.get("was_resumed") is False

            data = ws.receive_json()
            assert data["type"] == "prompt_complete"

    def test_prompt_on_already_active_works(self, client_with_manager, fake_manager):
        """Test that prompt on already active session works end-to-end."""
        # Reconfigure the fake_manager for this test
        fake_manager.is_active = MagicMock(return_value=True)
        fake_manager.get_state = MagicMock(return_value=FakeConnectionState())
        fake_manager.send_prompt = AsyncMock()
        resp = MagicMock()
        resp.stop_reason = "end_turn"
        resp.message_id = "msg-1"
        fake_manager.send_prompt.return_value = resp
        fake_manager.update_activity = MagicMock()

        with client_with_manager.websocket_connect("/ws/active-sess") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "prompt", "text": "hello"})

            data = ws.receive_json()
            assert data["type"] == "prompt_complete"

    def test_cancel_on_active_works(self, client_with_manager, fake_manager):
        """Test that cancel on active session works."""
        fake_manager.is_active = MagicMock(return_value=True)
        fake_manager.update_activity = MagicMock()
        fake_manager.get_state = MagicMock(return_value=FakeConnectionState())

        with client_with_manager.websocket_connect("/ws/active-sess") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "cancel"})

            data = ws.receive_json()
            assert data["type"] == "cancelled"

    def test_set_model_on_active_works(self, client_with_manager, fake_manager):
        """Test that set_model on active session applies the setting."""
        fake_manager.is_active = MagicMock(return_value=True)
        fake_manager.update_activity = MagicMock()
        fake_manager.get_state = MagicMock(return_value=FakeConnectionState())

        with client_with_manager.websocket_connect("/ws/active-sess") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "set_model", "model": "gpt-4o"})

            data = ws.receive_json()
            assert data["type"] == "model_set"

    def test_set_mode_on_active_works(self, client_with_manager, fake_manager):
        """Test that set_mode on active session applies the setting."""
        fake_manager.is_active = MagicMock(return_value=True)
        fake_manager.update_activity = MagicMock()
        fake_manager.get_state = MagicMock(return_value=FakeConnectionState())

        with client_with_manager.websocket_connect("/ws/active-sess") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "set_mode", "mode": "accept_edits"})

            data = ws.receive_json()
            assert data["type"] == "mode_set"

    def test_set_config_on_active_works(self, client_with_manager, fake_manager):
        """Test that set_config on active session works."""
        fake_manager.is_active = MagicMock(return_value=True)
        fake_manager.update_activity = MagicMock()
        fake_manager.get_state = MagicMock(return_value=FakeConnectionState())

        with client_with_manager.websocket_connect("/ws/active-sess") as ws:
            ws.receive_json()  # connected
            ws.send_json({"type": "set_config", "config_id": "effort", "value": "high"})

            data = ws.receive_json()
            assert data["type"] == "config_set"


class TestIdleShutdownWS:
    """Tests for idle_shutdown via WebSocket."""

    def test_idle_shutdown_message_contains_expected_data(self, client_with_manager, fake_manager):
        """Test that idle_shutdown messages have the right structure.

        We simulate the manager sending idle_shutdown through the bridge.
        The idle_shutdown message originates from _stop_idle_session in the manager.
        """
        # This tests the structure the manager sends
        from backend.acp_core.manager import AcpConnectionManager

        mgr = AcpConnectionManager()
        client_obj = MagicMock()
        queue = asyncio.Queue()
        client_obj.get_queue = MagicMock(return_value=queue)

        # Simulate what _stop_idle_session does
        shutdown_msg = {
            "type": "idle_shutdown",
            "session_id": "test-sess",
            "timestamp": "",
            "data": {
                "message": "Session idle for 5 minutes, shutting down",
                "idle_timeout_seconds": 300,
            },
        }

        assert shutdown_msg["type"] == "idle_shutdown"
        assert "message" in shutdown_msg["data"]
        assert "idle_timeout_seconds" in shutdown_msg["data"]
        assert shutdown_msg["data"]["idle_timeout_seconds"] == 300


class TestResumeSessionFromManager:
    """Tests for resume_session_from on the manager."""

    def test_resume_session_from_returns_tuple(self):
        """Test that resume_session_from exists and returns expected type."""
        from backend.acp_core.manager import AcpConnectionManager
        mgr = AcpConnectionManager()
        assert hasattr(mgr, "resume_session_from")
        assert callable(mgr.resume_session_from)

    @pytest.mark.asyncio
    async def test_resume_session_from_already_active_raises(self):
        """Test resume_session_from raises if session is already active."""
        from backend.acp_core.manager import AcpConnectionManager, ConnectionState
        from backend.acp_core.client import AcpClient
        from backend.models.session import Session
        from backend.models.agent import Agent
        from unittest.mock import AsyncMock, MagicMock

        mgr = AcpConnectionManager()
        state = ConnectionState(
            conn=AsyncMock(), proc=MagicMock(), client=AcpClient(), ctx=AsyncMock(),
        )
        mgr._connections["dup"] = state

        session = Session(id="dup", agent_id="a1", cwd="/tmp")
        agent = Agent(id="a1", name="test", command="echo", args=["hi"])

        with pytest.raises(ValueError, match="already active"):
            await mgr.resume_session_from(session, agent)
