"""Tests for idle teardown and activity tracking."""

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.acp_core.manager import AcpConnectionManager, ConnectionState
from backend.acp_core.client import AcpClient

logging.basicConfig(level=logging.DEBUG)


@pytest.fixture
def manager():
    """Create a fresh AcpConnectionManager."""
    return AcpConnectionManager()


@pytest.fixture
def mock_state():
    """Create a mock ConnectionState for testing."""
    conn = AsyncMock()
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None
    client = AcpClient()
    ctx = AsyncMock()
    state = ConnectionState(conn=conn, proc=proc, client=client, ctx=ctx)
    state.acp_session_id = "acp-sess-1"
    return state


def test_update_activity_updates_timestamp(manager, mock_state):
    """Test that update_activity() resets the last_activity timestamp."""
    manager._connections["test-sess"] = mock_state

    old_time = mock_state.last_activity
    time.sleep(0.01)
    manager.update_activity("test-sess")

    assert mock_state.last_activity > old_time


def test_update_activity_nonexistent_session(manager):
    """Test that update_activity on unknown session doesn't raise."""
    manager.update_activity("nonexistent")  # Should not raise


@pytest.mark.asyncio
async def test_idle_checker_detects_idle_session():
    """Test that the idle checker detects idle sessions and stops them.

    Instead of running the full polling loop, we directly simulate the
    idle-detection logic: compare last_activity against now with a timeout.
    """
    manager = AcpConnectionManager()

    conn = AsyncMock()
    proc = MagicMock()
    proc.pid = 99999
    proc.returncode = None
    client = AcpClient()
    ctx = AsyncMock()
    state = ConnectionState(conn=conn, proc=proc, client=client, ctx=ctx)
    state.acp_session_id = "acp-idle"
    # Set last_activity to 100 seconds ago
    state.last_activity = time.time() - 100
    manager._connections["idle-sess"] = state

    # Create a second active session
    state2 = ConnectionState(
        conn=AsyncMock(), proc=MagicMock(), client=AcpClient(), ctx=AsyncMock(),
    )
    state2.last_activity = time.time()  # Just now
    state2.acp_session_id = "acp-active"
    manager._connections["active-sess"] = state2

    # Verify idle detection logic: only the old session is idle
    now = time.time()
    timeout = 5  # 5 seconds

    idle_sessions = []
    active_sessions = []
    for sid, s in manager._connections.items():
        if (now - s.last_activity) > timeout:
            idle_sessions.append(sid)
        else:
            active_sessions.append(sid)

    assert "idle-sess" in idle_sessions
    assert "active-sess" in active_sessions
    assert "active-sess" not in idle_sessions

    # Now stop the idle session
    await manager._stop_idle_session("idle-sess")
    assert "idle-sess" not in manager._connections
    assert "active-sess" in manager._connections


@pytest.mark.asyncio
async def test_idle_checker_does_not_kill_active():
    """Test that the idle checker does not stop recently active sessions."""
    manager = AcpConnectionManager()

    state = ConnectionState(
        conn=AsyncMock(), proc=MagicMock(), client=AcpClient(), ctx=AsyncMock(),
    )
    state.last_activity = time.time()  # Just now
    state.acp_session_id = "acp-active"
    manager._connections["active-sess"] = state

    original_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await original_sleep(0.001)

    with patch.object(asyncio, 'sleep', fast_sleep):
        # Start idle checker with moderate timeout (session was just touched)
        task = asyncio.create_task(manager._idle_checker(idle_timeout_seconds=10))
        await original_sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Active session should still be there
    assert "active-sess" in manager._connections


@pytest.mark.asyncio
async def test_idle_shutdown_message_sent():
    """Test that _stop_idle_session sends idle_shutdown to the client queue.

    We verify the shutdown message is put into the queue BEFORE remove_queue
    deletes it, by capturing the put operation on the original queue.
    """
    manager = AcpConnectionManager()

    client = AcpClient()
    queue = client.get_queue("shutdown-sess")

    # Track what's put on the queue
    put_items = []
    original_put = queue.put

    async def track_put(item):
        put_items.append(item)
        await original_put(item)

    queue.put = track_put

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None
    state = ConnectionState(
        conn=AsyncMock(), proc=proc, client=client, ctx=AsyncMock(),
    )
    state.acp_session_id = "acp-shutting-down"
    manager._connections["shutdown-sess"] = state

    await manager._stop_idle_session("shutdown-sess")

    # Session should be removed
    assert "shutdown-sess" not in manager._connections

    # Check that idle_shutdown was put on the original queue
    shutdown_msgs = [
        m for m in put_items
        if isinstance(m, dict) and m.get("type") == "idle_shutdown"
    ]
    assert len(shutdown_msgs) >= 1, f"Should have idle_shutdown in put items: {put_items}"


@pytest.mark.asyncio
async def test_idle_shutdown_stop_then_activity_cycle():
    """Test the full cycle: activity → idle → stop → resume attempt."""
    manager = AcpConnectionManager()

    client = AcpClient()
    proc = MagicMock()
    proc.pid = 11111
    proc.returncode = None
    state = ConnectionState(
        conn=AsyncMock(), proc=proc, client=client, ctx=AsyncMock(),
    )
    state.acp_session_id = "acp-cycle"
    state.last_activity = time.time()
    manager._connections["cycle-sess"] = state

    # Update activity
    manager.update_activity("cycle-sess")
    # Verify recent activity
    import time
    assert state.last_activity > time.time() - 2

    # Simulate idle by setting last_activity way back
    state.last_activity = time.time() - 1000

    # Manual stop simulation
    await manager._stop_idle_session("cycle-sess")
    assert not manager.is_active("cycle-sess")


@pytest.mark.asyncio
async def test_start_idle_checker_returns_task(manager):
    """Test that start_idle_checker returns an asyncio.Task."""
    task = manager.start_idle_checker()
    assert isinstance(task, asyncio.Task)
    assert not task.done()  # Should be running

    # Clean up
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_stop_idle_session_handles_nonexistent(manager):
    """Test that _stop_idle_session on a removed session doesn't raise."""
    await manager._stop_idle_session("already_gone")  # Should not raise


@pytest.mark.asyncio
async def test_connections_after_idle_timeout():
    """Test with multiple sessions, only idle ones get stopped.

    Directly tests the idle-detection logic rather than the async loop.
    """
    manager = AcpConnectionManager()

    # Create 3 sessions: 2 idle, 1 active
    now = time.time()
    for i in range(3):
        client = AcpClient()
        proc = MagicMock()
        proc.pid = 1000 + i
        proc.returncode = None
        state = ConnectionState(
            conn=AsyncMock(), proc=proc, client=client, ctx=AsyncMock(),
        )
        state.acp_session_id = f"acp-{i}"
        # Sessions 0 and 1 are old, session 2 is recent
        state.last_activity = now - 200 if i < 2 else now
        manager._connections[f"sess-{i}"] = state

    assert len(manager.active_sessions) == 3

    # Directly test idle detection
    timeout = 100  # 100 second timeout
    now_check = time.time()

    idle = [sid for sid, s in manager._connections.items()
            if (now_check - s.last_activity) > timeout]

    assert "sess-0" in idle
    assert "sess-1" in idle
    assert "sess-2" not in idle

    # Stop the idle sessions
    for sid in list(idle):
        await manager._stop_idle_session(sid)

    # sess-0 and sess-1 should be gone, sess-2 should remain
    assert "sess-0" not in manager._connections
    assert "sess-1" not in manager._connections
    assert "sess-2" in manager._connections
