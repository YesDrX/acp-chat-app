"""Tests for Phase 6: Smart Bubble Grouping & Tool Calls (Backend).

Verifies that the WebSocket handler correctly routes different ACP update
types (agent_message_chunk, tool_call, tool_call_update, available_commands_update,
request_permission) and that the bridge streams updates properly.
"""

import asyncio
import logging

import pytest

logging.basicConfig(level=logging.DEBUG)


class TestUpdateTypeRouting:
    """Tests that the AcpClient correctly parses each update type."""

    @pytest.mark.asyncio
    async def test_agent_message_chunk_routed_correctly(self):
        """Test agent_message_chunk is parsed with text content."""
        from backend.acp_core.client import AcpClient
        from tests.test_acp_client import FakeTextUpdate

        client = AcpClient()
        update = FakeTextUpdate(text="Hello streaming world")

        await client.session_update(session_id="s1", update=update)

        queue = client.get_queue("s1")
        item = await queue.get()

        assert item["type"] == "agent_message_chunk"
        assert item["data"]["content"]["text"] == "Hello streaming world"
        assert item["session_id"] == "s1"
        assert "timestamp" in item

    @pytest.mark.asyncio
    async def test_multiple_text_chunks_same_session(self):
        """Test multiple text chunks preserve ordering."""
        from backend.acp_core.client import AcpClient
        from tests.test_acp_client import FakeTextUpdate

        client = AcpClient()
        texts = ["Hello", " ", "World", "!", " How", " are", " you?"]

        for text in texts:
            await client.session_update(session_id="s1", update=FakeTextUpdate(text=text))

        queue = client.get_queue("s1")
        items = []
        for _ in range(len(texts)):
            items.append(await queue.get())

        reconstructed = "".join(
            item["data"]["content"]["text"] for item in items
        )
        assert reconstructed == "Hello World! How are you?"

    @pytest.mark.asyncio
    async def test_tool_call_routed_correctly(self):
        """Test tool_call is parsed with tool_call_id and title."""
        from backend.acp_core.client import AcpClient
        from tests.test_acp_client import FakeToolCallStart

        client = AcpClient()
        update = FakeToolCallStart(tool_call_id="tc-read", title="read_file")

        await client.session_update(session_id="s1", update=update)

        queue = client.get_queue("s1")
        item = await queue.get()

        assert item["type"] == "tool_call"
        assert item["data"]["tool_call_id"] == "tc-read"
        assert item["data"]["title"] == "read_file"

    @pytest.mark.asyncio
    async def test_tool_call_update_routed_correctly(self):
        """Test tool_call_update is parsed with status."""
        from backend.acp_core.client import AcpClient
        from tests.test_acp_client import FakeToolCallProgress

        client = AcpClient()
        update = FakeToolCallProgress(tool_call_id="tc-1", status="in_progress")

        await client.session_update(session_id="s1", update=update)

        queue = client.get_queue("s1")
        item = await queue.get()

        assert item["type"] == "tool_call_update"
        assert item["data"]["tool_call_id"] == "tc-1"
        assert item["data"]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_available_commands_routed_correctly(self):
        """Test available_commands_update is parsed with commands list."""
        from backend.acp_core.client import AcpClient
        from tests.test_acp_client import FakeAvailableCommands

        client = AcpClient()
        commands = ["/help", "/search", "/files", "/settings"]
        update = FakeAvailableCommands(commands=commands)

        await client.session_update(session_id="s1", update=update)

        queue = client.get_queue("s1")
        item = await queue.get()

        assert item["type"] == "available_commands_update"
        assert item["data"]["available_commands"] == commands

    @pytest.mark.asyncio
    async def test_request_permission_routed_correctly(self):
        """Test request_permission is parsed."""
        from backend.acp_core.client import AcpClient
        from tests.test_acp_client import FakePermissionRequest

        client = AcpClient()
        update = FakePermissionRequest()

        await client.session_update(session_id="s1", update=update)

        queue = client.get_queue("s1")
        item = await queue.get()

        assert item["type"] == "request_permission"


class TestInterleavedUpdateStream:
    """Tests for interleaved updates simulating real ACP agent output."""

    @pytest.mark.asyncio
    async def test_interleaved_text_and_tool_calls(self):
        """Simulate: text1 text2 tool1 tool1_update text3 text4."""
        from backend.acp_core.client import AcpClient
        from tests.test_acp_client import (
            FakeTextUpdate,
            FakeToolCallStart,
            FakeToolCallProgress,
        )

        client = AcpClient()

        # Simulate real agent output
        await client.session_update(session_id="s1", update=FakeTextUpdate(text="Let me read the file."))
        await client.session_update(session_id="s1", update=FakeTextUpdate(text="\n"))
        await client.session_update(session_id="s1", update=FakeToolCallStart(tool_call_id="tc1", title="read_file"))
        await client.session_update(session_id="s1", update=FakeToolCallProgress(tool_call_id="tc1", status="in_progress"))
        await client.session_update(session_id="s1", update=FakeToolCallProgress(tool_call_id="tc1", status="completed"))
        await client.session_update(session_id="s1", update=FakeTextUpdate(text="\nHere's the content:\n"))
        await client.session_update(session_id="s1", update=FakeTextUpdate(text="hello world"))

        queue = client.get_queue("s1")

        expected_sequence = [
            ("agent_message_chunk", "Let me read the file."),
            ("agent_message_chunk", "\n"),
            ("tool_call", "tc1"),
            ("tool_call_update", "in_progress"),
            ("tool_call_update", "completed"),
            ("agent_message_chunk", "\nHere's the content:\n"),
            ("agent_message_chunk", "hello world"),
        ]

        for expected_type, expected_check in expected_sequence:
            item = await queue.get()
            assert item["type"] == expected_type

            if expected_type == "agent_message_chunk":
                assert item["data"]["content"]["text"] == expected_check
            elif expected_type == "tool_call":
                assert item["data"]["tool_call_id"] == expected_check
            elif expected_type == "tool_call_update":
                assert item["data"]["status"] == expected_check

        assert queue.empty()

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_interleaved(self):
        """Simulate: text1 tool1 tool2 text2."""
        from backend.acp_core.client import AcpClient
        from tests.test_acp_client import (
            FakeTextUpdate,
            FakeToolCallStart,
        )

        client = AcpClient()

        await client.session_update(session_id="s1", update=FakeTextUpdate(text="I'll search and read."))
        await client.session_update(session_id="s1", update=FakeToolCallStart(tool_call_id="tc-a", title="search"))
        await client.session_update(session_id="s1", update=FakeToolCallStart(tool_call_id="tc-b", title="read_file"))
        await client.session_update(session_id="s1", update=FakeTextUpdate(text="Done!"))

        queue = client.get_queue("s1")

        types = []
        for _ in range(4):
            item = await queue.get()
            types.append(item["type"])

        assert types == ["agent_message_chunk", "tool_call", "tool_call", "agent_message_chunk"]


class TestBridgeUpdateStreaming:
    """Tests that AcpBridge streams updates correctly."""

    @pytest.mark.asyncio
    async def test_bridge_yields_all_update_types(self):
        """Test that the bridge yields each update type."""
        from backend.acp_core.bridge import AcpBridge

        bridge = AcpBridge(heartbeat_interval=999.0)  # Long timeout to avoid heartbeats
        queue: asyncio.Queue = asyncio.Queue()

        # Put various update types
        updates_to_send = [
            {"type": "agent_message_chunk", "session_id": "s1", "data": {"content": {"text": "Hi"}}},
            {"type": "tool_call", "session_id": "s1", "data": {"tool_call_id": "t1", "title": "search"}},
            {"type": "tool_call_update", "session_id": "s1", "data": {"tool_call_id": "t1", "status": "running"}},
            {"type": "available_commands_update", "session_id": "s1", "data": {"available_commands": ["/help"]}},
            {"type": "request_permission", "session_id": "s1", "data": {}},
        ]

        for u in updates_to_send:
            await queue.put(u)
        await queue.put(None)  # Sentinel

        yielded = []
        async for update in bridge.stream_updates_sync(queue):
            yielded.append(update)

        assert len(yielded) == 5
        assert [u["type"] for u in yielded] == [
            "agent_message_chunk",
            "tool_call",
            "tool_call_update",
            "available_commands_update",
            "request_permission",
        ]

    @pytest.mark.asyncio
    async def test_bridge_stops_on_sentinel(self):
        """Test that bridge stops streaming when receiving None sentinel."""
        from backend.acp_core.bridge import AcpBridge

        bridge = AcpBridge(heartbeat_interval=999.0)
        queue: asyncio.Queue = asyncio.Queue()

        await queue.put({"type": "agent_message_chunk", "data": {}})
        await queue.put({"type": "agent_message_chunk", "data": {}})
        await queue.put(None)
        await queue.put({"type": "agent_message_chunk", "data": {}})  # Should not be consumed

        yielded = []
        async for update in bridge.stream_updates_sync(queue):
            yielded.append(update)

        assert len(yielded) == 2

    @pytest.mark.asyncio
    async def test_bridge_preserves_update_data(self):
        """Test that bridge preserves all update fields."""
        from backend.acp_core.bridge import AcpBridge

        bridge = AcpBridge(heartbeat_interval=999.0)
        queue: asyncio.Queue = asyncio.Queue()

        complex_update = {
            "type": "agent_message_chunk",
            "session_id": "complex-session",
            "timestamp": "2026-01-01T00:00:00",
            "data": {
                "content": {"type": "text", "text": "Hello with metadata"},
                "message_id": "msg-123",
            },
        }
        await queue.put(complex_update)
        await queue.put(None)

        async for update in bridge.stream_updates_sync(queue):
            assert update == complex_update
            break

