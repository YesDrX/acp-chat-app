"""Tests for WebSocket chat endpoint.

Test WebSocket connection, message handling, ping/pong, error handling,
and disconnect behavior. Uses FastAPI TestClient websocket support
and mocks the AcpConnectionManager to avoid actual subprocess spawning.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from fastapi import WebSocket

logging.basicConfig(level=logging.DEBUG)


class FakeConnectionState:
    """Fake ConnectionState for testing — no actual subprocess needed."""

    def __init__(self):
        self.conn = AsyncMock()
        self.proc = MagicMock()
        self.proc.pid = 99999
        self.proc.returncode = None

        # Create a proper mock client with a real asyncio.Queue
        self.mock_client = MagicMock()
        self._queue = asyncio.Queue()
        self.mock_client.get_queue = MagicMock(return_value=self._queue)
        self.mock_client.remove_queue = MagicMock()
        self.mock_client.reset_buffer = MagicMock()
        self.mock_client.get_buffer = MagicMock(return_value="")
        self.mock_client.get_thought_buffer = MagicMock(return_value="")
        self.mock_client.get_tool_calls = MagicMock(return_value=[])
        self.client = self.mock_client

        self.ctx = AsyncMock()
        self.last_activity = 0
        self.acp_session_id = "acp-test-sess"

    def touch(self):
        import time
        self.last_activity = time.time()


@pytest.fixture
def fake_manager():
    """Create a mock AcpConnectionManager."""
    manager = MagicMock()
    manager.is_active.side_effect = lambda sid: sid == "test-sess-active"
    manager.start_session = AsyncMock(return_value="acp-test-sess")
    manager.resume_session_from = AsyncMock(return_value=("acp-test-sess", False))
    manager.send_prompt = AsyncMock()
    manager.cancel = AsyncMock()
    manager.stop_session = AsyncMock()
    manager.set_model = AsyncMock()
    manager.set_mode = AsyncMock()
    manager.set_config_option = AsyncMock()
    manager.get_state = MagicMock(return_value=None)
    manager.active_sessions = []
    return manager


@pytest_asyncio.fixture
async def seed_test_data(test_db):
    """Seed the test database with a session and ensure agent exists.

    Creates session 'test-sess-1' tied to agent 'pi-agent'.
    Also ensures the pi-agent exists in the agents config file.
    """
    from backend.models.session import Session, SessionStore
    from backend.models.agent import AgentStore
    import backend.routes.ws as ws_module

    # Ensure pi-agent exists in agent store
    astore = AgentStore()
    existing_agent = astore.get("pi-agent")
    if not existing_agent:
        from backend.models.agent import Agent
        astore.create(Agent(
            id="pi-agent",
            name="Pi Agent",
            type="cli",
            command="npx",
            args=["-y", "pi-acp"],
            env_vars={},
            description="Default Pi ACP agent",
        ))

    # Create test session
    sstore = SessionStore()
    session = Session(
        id="test-sess-1",
        agent_id="pi-agent",
        name="Test Session 1",
        model="gpt-4o",
        cwd="/tmp/test",
        effort_level="medium",
        permission_mode="default",
        status="created",
    )
    await sstore.create(session)

    # Also create an active session for active-session tests
    session2 = Session(
        id="test-sess-active",
        agent_id="pi-agent",
        name="Active Session",
        model="gpt-4o",
        cwd="/tmp/test",
        status="active",
    )
    await sstore.create(session2)

    yield


@pytest.fixture
def client_with_manager(fake_manager, test_db, seed_test_data, test_agents_file):
    """Create a TestClient with a mock manager injected.

    This fixture integrates with conftest.py's test_db fixture for the database,
    seeds test data (session + agent), and patches the ws module to use
    the fake_manager.
    """
    from backend.main import app
    import backend.routes.ws as ws_module

    # Inject mock manager
    ws_module.set_manager(fake_manager)

    # Also patch the manager in app.state for any direct access
    app.state.acp_manager = fake_manager

    return TestClient(app)


@pytest.fixture
def active_manager(fake_manager):
    """A manager that reports the session as active."""
    fake_manager.is_active.side_effect = lambda sid: True
    fake_manager.get_state = MagicMock(return_value=FakeConnectionState())
    return fake_manager


@pytest.fixture
def client_with_active_manager(active_manager, test_db, seed_test_data, test_agents_file):
    """Create a TestClient with a manager that reports sessions as active."""
    from backend.main import app
    import backend.routes.ws as ws_module

    ws_module.set_manager(active_manager)
    app.state.acp_manager = active_manager

    return TestClient(app)


def _drain_replay(ws):
    """Consume the prompt_complete replay message sent for active sessions."""
    msg = ws.receive_json()
    assert msg["type"] == "prompt_complete"
    assert msg["response"]["stop_reason"] == "replay_complete"


class TestWebSocketConnection:
    """Tests for WebSocket connection lifecycle."""

    def test_connect_and_receive_connected(self, client_with_manager):
        """Test that connecting to a valid session sends 'connected' message."""
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            assert data["session_id"] == "test-sess-1"
            assert data["session_name"] == "Test Session 1"
            assert data["agent_name"] == "Pi Agent"
            assert data["is_active"] is False

    def test_invalid_session_id(self, client_with_manager):
        """Test that connecting to non-existent session sends error and closes."""
        with client_with_manager.websocket_connect("/ws/nonexistent") as ws:
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "not found" in data["message"].lower()

    def test_ping_pong(self, client_with_manager):
        """Test that ping receives pong."""
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            # Consume connected message
            ws.receive_json()

            # Send ping
            ws.send_json({"type": "ping"})

            # Receive pong
            data = ws.receive_json()
            assert data["type"] == "pong"

    def test_unknown_message_type(self, client_with_manager):
        """Test that unknown message types get an error response."""
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "unknown_command", "data": "test"})

            data = ws.receive_json()
            assert data["type"] == "error"
            assert "unknown" in data["message"].lower()

    def test_invalid_json(self, client_with_manager):
        """Test that the WebSocket handles invalid JSON gracefully.

        Note: FastAPI's WebSocket.receive_json() will close the connection
        on invalid JSON (protocol error). We verify that sending bad data
        doesn't crash the server by checking the connection was accepted first.
        """
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            connected_msg = ws.receive_json()
            assert connected_msg["type"] == "connected"

            # Send non-JSON text — this will cause the server to
            # receive a WebSocketDisconnect exception.
            ws.send_text("this is not json")

            # After sending invalid data, the connection should eventually close.
            # We don't expect a valid response.
            # This test just verifies the server doesn't crash.


class TestPromptHandling:
    """Tests for the prompt message flow."""

    @staticmethod
    def _make_prompt_response(stop_reason="end_turn", message_id="msg-1"):
        """Create a mock prompt response."""
        resp = MagicMock()
        resp.stop_reason = stop_reason
        resp.message_id = message_id
        return resp

    def test_send_prompt_starts_session(self, client_with_manager, fake_manager):
        """Test that sending a prompt starts the session if not active."""
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected

            fake_manager.send_prompt = AsyncMock(
                return_value=self._make_prompt_response()
            )

            # Send prompt
            ws.send_json({"type": "prompt", "text": "Hello, agent!"})

            # Should receive session_started
            data = ws.receive_json()
            assert data["type"] == "session_started"
            assert data["acp_session_id"] == "acp-test-sess"

            # Then prompt_complete
            data = ws.receive_json()
            assert data["type"] == "prompt_complete"
            assert data["response"]["stop_reason"] == "end_turn"

    def test_send_empty_prompt(self, client_with_manager):
        """Test that empty prompts are rejected."""
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "prompt", "text": ""})

            data = ws.receive_json()
            assert data["type"] == "error"
            assert "empty" in data["message"].lower()

    def test_send_prompt_already_active(self, client_with_active_manager, active_manager):
        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)
            ws.receive_json()  # prompt_complete replay (active session)

            active_manager.send_prompt = AsyncMock(
                return_value=self._make_prompt_response("success", "msg-2")
            )

            ws.send_json({"type": "prompt", "text": "Hello!"})

            # Should get prompt_complete (since send_prompt is mocked)
            data = ws.receive_json()
            assert data["type"] == "prompt_complete"
            assert data["response"]["stop_reason"] == "success"

    def test_prompt_start_session_failure(self, client_with_manager, fake_manager):
        """Test error handling when start_session fails."""
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected

            fake_manager.resume_session_from = AsyncMock(
                side_effect=RuntimeError("Subprocess failed to start")
            )

            ws.send_json({"type": "prompt", "text": "Hello"})

            data = ws.receive_json()
            assert data["type"] == "error"
            assert "failed" in data["message"].lower()

    def test_prompt_send_failure(self, client_with_active_manager, active_manager):
        """Test error handling when send_prompt fails."""
        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)

            active_manager.send_prompt = AsyncMock(
                side_effect=RuntimeError("Prompt failed")
            )

            ws.send_json({"type": "prompt", "text": "Hello"})

            data = ws.receive_json()
            assert data["type"] == "error"
            assert "agent error" in data["message"].lower()


class TestCancelStop:
    """Tests for cancel and stop operations."""

    def test_cancel_active_session(self, client_with_active_manager, active_manager):
        """Test that cancel is forwarded to the manager."""
        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)

            ws.send_json({"type": "cancel"})

            data = ws.receive_json()
            assert data["type"] == "cancelled"
            active_manager.cancel.assert_called_once_with("test-sess-1")

    def test_cancel_inactive_session(self, client_with_manager, fake_manager):
        """Test that cancelling an inactive session gives an error."""
        fake_manager.is_active = MagicMock(return_value=False)

        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "cancel"})

            data = ws.receive_json()
            assert data["type"] == "error"

    def test_stop_active_session(self, client_with_active_manager, active_manager):
        """Test that stop is forwarded to the manager."""
        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)

            ws.send_json({"type": "stop"})

            data = ws.receive_json()
            assert data["type"] == "stopped"
            active_manager.stop_session.assert_called_once_with("test-sess-1")

    def test_stop_inactive_session(self, client_with_manager, fake_manager):
        """Test that stopping an inactive session succeeds silently."""
        fake_manager.is_active = MagicMock(return_value=False)

        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "stop"})

            data = ws.receive_json()
            assert data["type"] == "stopped"

    def test_stop_failure(self, client_with_active_manager, active_manager):
        """Test error handling when stop fails."""
        active_manager.stop_session = AsyncMock(
            side_effect=RuntimeError("Stop failed")
        )

        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)

            ws.send_json({"type": "stop"})

            data = ws.receive_json()
            assert data["type"] == "error"
            assert "stop failed" in data["message"].lower()

    def test_cancel_failure(self, client_with_active_manager, active_manager):
        """Test error handling when cancel fails."""
        active_manager.cancel = AsyncMock(
            side_effect=RuntimeError("Cancel failed")
        )

        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)

            ws.send_json({"type": "cancel"})

            data = ws.receive_json()
            assert data["type"] == "error"
            assert "cancel failed" in data["message"].lower()


class TestSettingsUpdates:
    """Tests for model/mode/config update messages."""

    def test_set_model(self, client_with_active_manager, active_manager):
        """Test setting the model."""
        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)

            ws.send_json({"type": "set_model", "model": "gpt-4o"})

            data = ws.receive_json()
            assert data["type"] == "model_set"
            assert data["model"] == "gpt-4o"
            active_manager.set_model.assert_called_once_with("test-sess-1", "gpt-4o")

    def test_set_mode(self, client_with_active_manager, active_manager):
        """Test setting the mode."""
        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)

            ws.send_json({"type": "set_mode", "mode": "accept_edits"})

            data = ws.receive_json()
            assert data["type"] == "mode_set"
            assert data["mode"] == "accept_edits"
            active_manager.set_mode.assert_called_once_with("test-sess-1", "accept_edits")

    def test_set_config(self, client_with_active_manager, active_manager):
        """Test setting a config option."""
        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)

            ws.send_json({
                "type": "set_config",
                "config_id": "effort_level",
                "value": "high",
            })

            data = ws.receive_json()
            assert data["type"] == "config_set"
            assert data["config_id"] == "effort_level"
            assert data["value"] == "high"
            active_manager.set_config_option.assert_called_once_with(
                "test-sess-1", "effort_level", "high"
            )

    def test_set_model_inactive(self, client_with_manager, fake_manager):
        """Test that setting model on inactive session gives error."""
        fake_manager.is_active = MagicMock(return_value=False)

        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected

            ws.send_json({"type": "set_model", "model": "gpt-4o"})

            data = ws.receive_json()
            assert data["type"] == "error"

    def test_set_mode_failure(self, client_with_active_manager, active_manager):
        """Test error handling when set_mode fails."""
        active_manager.set_mode = AsyncMock(
            side_effect=RuntimeError("Set mode failed")
        )

        with client_with_active_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            _drain_replay(ws)

            ws.send_json({"type": "set_mode", "mode": "invalid"})

            data = ws.receive_json()
            assert data["type"] == "error"


class TestDisconnectHandling:
    """Tests for WebSocket disconnect behavior."""

    def test_disconnect_after_connected(self, client_with_manager):
        """Test that disconnecting after receiving connected doesn't crash."""
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            data = ws.receive_json()
            assert data["type"] == "connected"
            # Connection automatically closes when context exits

    def test_disconnect_during_message_loop(self, client_with_manager):
        """Test that the handler gracefully handles disconnect during message loop."""
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws:
            ws.receive_json()  # connected
            # Close without sending any message — should be handled gracefully

    def test_multiple_connections_same_session(self, client_with_manager):
        """Test that multiple clients can connect to the same session."""
        with client_with_manager.websocket_connect("/ws/test-sess-1") as ws1:
            data1 = ws1.receive_json()
            assert data1["type"] == "connected"

            with client_with_manager.websocket_connect("/ws/test-sess-1") as ws2:
                data2 = ws2.receive_json()
                assert data2["type"] == "connected"
                assert data2["session_id"] == "test-sess-1"


class TestManagerNotInitialized:
    """Tests for the error case when manager is not set up."""

    def test_manager_not_set_error(self, test_db):
        """Test that connecting without a manager raises an error.

        Since we can't easily test this through the TestClient
        (the manager is set during app startup), we unit-test
        the ws module directly.
        """
        import backend.routes.ws as ws_module

        # Save and clear
        original = ws_module._manager
        ws_module._manager = None

        try:
            with pytest.raises(RuntimeError, match="not initialized"):
                ws_module.get_manager()
        finally:
            ws_module._manager = original
