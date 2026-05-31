"""Tests for AcpBridge.

Tests the async generator that bridges ACP update queues to consumers.
"""

import asyncio
import pytest

from backend.acp_core.bridge import AcpBridge


@pytest.mark.asyncio
async def test_bridge_yields_items_from_queue():
    """Test that the bridge yields items from the queue."""
    bridge = AcpBridge(heartbeat_interval=0.1)
    queue: asyncio.Queue[dict] = asyncio.Queue()

    # Put some items
    await queue.put({"type": "agent_message_chunk", "session_id": "s1", "data": {"text": "Hello"}})
    await queue.put({"type": "agent_message_chunk", "session_id": "s1", "data": {"text": "World"}})
    await queue.put(None)  # Sentinel

    items = []
    async for item in bridge.stream_updates_sync(queue, session_id="s1"):
        items.append(item)

    assert len(items) == 2
    assert items[0]["data"]["text"] == "Hello"
    assert items[1]["data"]["text"] == "World"


@pytest.mark.asyncio
async def test_bridge_stops_on_sentinel():
    """Test that the bridge stops when it receives a None sentinel."""
    bridge = AcpBridge(heartbeat_interval=0.1)
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    # Put a sentinel immediately
    await queue.put(None)

    items = []
    async for item in bridge.stream_updates_sync(queue, session_id="s1"):
        items.append(item)

    assert len(items) == 0


@pytest.mark.asyncio
async def test_bridge_handles_empty_queue():
    """Test that the bridge yields heartbeats when queue is empty for a while."""
    bridge = AcpBridge(heartbeat_interval=0.05)
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    # Don't put anything — just wait and check heartbeats, then stop
    async def put_sentinel_later():
        await asyncio.sleep(0.12)
        await queue.put(None)

    task = asyncio.create_task(put_sentinel_later())

    items = []
    async for item in bridge.stream_updates_sync(queue, session_id="s1"):
        items.append(item)
        if len(items) >= 5:
            break

    await task

    # Should have at least one heartbeat
    heartbeats = [i for i in items if i["type"] == "heartbeat"]
    assert len(heartbeats) > 0


@pytest.mark.asyncio
async def test_bridge_multiple_items_in_order():
    """Test that items are yielded in the order they were queued."""
    bridge = AcpBridge(heartbeat_interval=0.1)
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    expected = []
    for i in range(10):
        msg = {"type": "chunk", "session_id": "s1", "data": {"seq": i}}
        expected.append(msg)
        await queue.put(msg)
    await queue.put(None)

    items = []
    async for item in bridge.stream_updates_sync(queue, session_id="s1"):
        items.append(item)

    assert len(items) == 10
    for i in range(10):
        assert items[i]["data"]["seq"] == i


@pytest.mark.asyncio
async def test_bridge_mixed_items_and_heartbeats():
    """Test bridge with items interspersed with idle periods (heartbeats)."""
    bridge = AcpBridge(heartbeat_interval=0.05)
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def feed():
        await queue.put({"type": "chunk", "session_id": "s1", "data": {"seq": 1}})
        await asyncio.sleep(0.12)  # Long enough for heartbeat
        await queue.put({"type": "chunk", "session_id": "s1", "data": {"seq": 2}})
        await queue.put(None)

    task = asyncio.create_task(feed())

    items = []
    async for item in bridge.stream_updates_sync(queue, session_id="s1"):
        items.append(item)

    await task

    chunks = [i for i in items if i["type"] == "chunk"]
    heartbeats = [i for i in items if i["type"] == "heartbeat"]

    assert len(chunks) == 2
    assert chunks[0]["data"]["seq"] == 1
    assert chunks[1]["data"]["seq"] == 2
    assert len(heartbeats) >= 1
