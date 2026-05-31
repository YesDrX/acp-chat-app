"""Tests for AcpConnectionManager.

Structural tests for the manager's bookkeeping. These do NOT spawn actual
ACP agent subprocesses — they test the connection state management, session
tracking, and cleanup logic.
"""

import pytest

from backend.acp_core.manager import AcpConnectionManager, ConnectionState
from backend.acp_core.client import AcpClient


@pytest.fixture
def manager():
    """Create a fresh AcpConnectionManager."""
    return AcpConnectionManager()


@pytest.fixture
def mock_state():
    """Create a mock ConnectionState for testing."""
    from unittest.mock import AsyncMock, MagicMock

    conn = AsyncMock()
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None
    client = AcpClient()
    ctx = AsyncMock()
    state = ConnectionState(conn=conn, proc=proc, client=client, ctx=ctx)
    state.acp_session_id = "acp-sess-1"
    return state


def test_manager_initial_state(manager):
    """Test that a new manager has no active sessions."""
    assert manager.active_sessions == []
    assert manager.is_active("any-session") is False


def test_manager_get_state_none(manager):
    """Test get_state returns None for unknown session."""
    assert manager.get_state("nonexistent") is None


def test_connection_state_touch():
    """Test that touch() updates the last_activity timestamp."""
    import time
    from unittest.mock import AsyncMock, MagicMock

    state = ConnectionState(
        conn=AsyncMock(),
        proc=MagicMock(),
        client=AcpClient(),
        ctx=AsyncMock(),
    )

    old_time = state.last_activity
    time.sleep(0.01)
    state.touch()
    assert state.last_activity > old_time


@pytest.mark.asyncio
async def test_stop_session_removes_entry(manager, mock_state):
    """Test that stop_session removes the entry from connections."""
    manager._connections["test-session"] = mock_state

    await manager.stop_session("test-session")

    assert "test-session" not in manager._connections
    assert manager.is_active("test-session") is False


@pytest.mark.asyncio
async def test_stop_session_idempotent(manager):
    """Test that stopping a non-existent session doesn't raise."""
    await manager.stop_session("nonexistent")  # Should not raise


@pytest.mark.asyncio
async def test_multiple_sessions_coexist(manager):
    """Test that multiple sessions can coexist independently."""
    from unittest.mock import AsyncMock, MagicMock

    state1 = ConnectionState(
        conn=AsyncMock(),
        proc=MagicMock(),
        client=AcpClient(),
        ctx=AsyncMock(),
    )
    state1.proc.returncode = None  # Simulate running subprocess
    state2 = ConnectionState(
        conn=AsyncMock(),
        proc=MagicMock(),
        client=AcpClient(),
        ctx=AsyncMock(),
    )
    state2.proc.returncode = None  # Simulate running subprocess

    manager._connections["sess-1"] = state1
    manager._connections["sess-2"] = state2

    assert len(manager.active_sessions) == 2
    assert manager.is_active("sess-1") is True
    assert manager.is_active("sess-2") is True

    await manager.stop_session("sess-1")

    assert manager.is_active("sess-1") is False
    assert manager.is_active("sess-2") is True
    assert len(manager.active_sessions) == 1


@pytest.mark.asyncio
async def test_stop_all_clears_everything(manager):
    """Test that stop_all stops all sessions."""
    from unittest.mock import AsyncMock, MagicMock

    for i in range(5):
        state = ConnectionState(
            conn=AsyncMock(),
            proc=MagicMock(),
            client=AcpClient(),
            ctx=AsyncMock(),
        )
        manager._connections[f"sess-{i}"] = state

    assert len(manager.active_sessions) == 5

    await manager.stop_all()

    assert len(manager.active_sessions) == 0


@pytest.mark.asyncio
async def test_start_session_already_active_raises():
    """Test that starting an already active session raises ValueError."""
    from backend.models.agent import Agent
    from backend.models.session import Session
    from unittest.mock import AsyncMock, MagicMock

    manager = AcpConnectionManager()

    # Manually add a session
    state = ConnectionState(
        conn=AsyncMock(),
        proc=MagicMock(),
        client=AcpClient(),
        ctx=AsyncMock(),
    )
    manager._connections["already-active"] = state

    session = Session(id="already-active", agent_id="a1", cwd="/tmp")
    agent = Agent(id="a1", name="test", command="echo", args=["hello"])

    with pytest.raises(ValueError, match="already active"):
        await manager.start_session(session=session, agent=agent)


@pytest.mark.asyncio
async def test_send_prompt_inactive_raises():
    """Test that sending prompt to inactive session raises ValueError."""
    manager = AcpConnectionManager()

    with pytest.raises(ValueError, match="not active"):
        await manager.send_prompt(session_id="nonexistent", text="hello")


@pytest.mark.asyncio
async def test_cancel_inactive_raises():
    """Test that cancelling inactive session raises ValueError."""
    manager = AcpConnectionManager()

    with pytest.raises(ValueError, match="not active"):
        await manager.cancel(session_id="nonexistent")


@pytest.mark.asyncio
async def test_set_model_inactive_raises():
    """Test that setting model on inactive session raises ValueError."""
    manager = AcpConnectionManager()

    with pytest.raises(ValueError, match="not active"):
        await manager.set_model(session_id="nonexistent", model_id="gpt-4")


@pytest.mark.asyncio
async def test_set_mode_inactive_raises():
    """Test that setting mode on inactive session raises ValueError."""
    manager = AcpConnectionManager()

    with pytest.raises(ValueError, match="not active"):
        await manager.set_mode(session_id="nonexistent", mode_id="default")


@pytest.mark.asyncio
async def test_set_config_option_inactive_raises():
    """Test that setting config option on inactive session raises ValueError."""
    manager = AcpConnectionManager()

    with pytest.raises(ValueError, match="not active"):
        await manager.set_config_option(
            session_id="nonexistent",
            config_id="effort",
            value="high",
        )


@pytest.mark.asyncio
async def test_cleanup_on_stop(manager, mock_state):
    """Test that stop_session properly cleans up: closes conn, terminates proc, closes ctx."""
    manager._connections["test-session"] = mock_state

    await manager.stop_session("test-session")

    # Connection should be closed
    mock_state.conn.close.assert_called_once()

    # Process should be terminated
    mock_state.proc.terminate.assert_called_once()

    # Context manager should be closed
    mock_state.ctx.__aexit__.assert_called_once()
