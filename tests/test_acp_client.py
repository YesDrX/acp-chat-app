"""Tests for AcpClient.

Unit tests for the AcpClient class — tests queue routing, update parsing,
and error handling without needing an actual ACP agent subprocess.
"""

import asyncio
import pytest

from backend.acp_core.client import AcpClient


class FakeUpdate:
    """A minimal fake update that mimics ACP schema models."""

    def __init__(self, session_update: str, **kwargs):
        self.session_update = session_update
        for k, v in kwargs.items():
            setattr(self, k, v)

    def model_dump(self) -> dict:
        return {"sessionUpdate": self.session_update}


class FakeTextUpdate(FakeUpdate):
    """Fake agent_message_chunk with text content."""

    def __init__(self, text: str, **kwargs):
        super().__init__(session_update="agent_message_chunk", **kwargs)
        self.content = FakeContent(text=text, type="text")

    def model_dump(self) -> dict:
        return {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": self.content.text},
        }


class FakeContent:
    def __init__(self, text: str, type: str = "text"):
        self.text = text
        self.type = type


class FakeToolCallStart(FakeUpdate):
    """Fake tool_call update."""

    def __init__(self, tool_call_id: str, title: str):
        super().__init__(session_update="tool_call")
        self.tool_call_id = tool_call_id
        self.title = title

    def model_dump(self) -> dict:
        return {
            "sessionUpdate": "tool_call",
            "tool_call_id": self.tool_call_id,
            "title": self.title,
        }


class FakeToolCallProgress(FakeUpdate):
    """Fake tool_call_update."""

    def __init__(self, tool_call_id: str, status: str):
        super().__init__(session_update="tool_call_update")
        self.tool_call_id = tool_call_id
        self.status = status

    def model_dump(self) -> dict:
        return {
            "sessionUpdate": "tool_call_update",
            "tool_call_id": self.tool_call_id,
            "status": self.status,
        }


class FakeAvailableCommands(FakeUpdate):
    """Fake available_commands_update."""

    def __init__(self, commands: list[str]):
        super().__init__(session_update="available_commands_update")
        self.available_commands = commands

    def model_dump(self) -> dict:
        return {
            "sessionUpdate": "available_commands_update",
            "available_commands": self.available_commands,
        }


class FakePermissionRequest(FakeUpdate):
    """Fake request_permission (though handled differently)."""

    def __init__(self):
        super().__init__(session_update="request_permission")

    def model_dump(self) -> dict:
        return {"sessionUpdate": "request_permission"}


@pytest.mark.asyncio
async def test_acp_client_session_update_queues_item():
    """Test that session_update puts items in the queue."""
    client = AcpClient()
    update = FakeTextUpdate(text="Hello, world!")

    await client.session_update(session_id="sess-1", update=update)

    queue = client.get_queue("sess-1")
    assert not queue.empty()

    item = await queue.get()
    assert item["type"] == "agent_message_chunk"
    assert item["session_id"] == "sess-1"
    assert "timestamp" in item
    assert item["data"]["content"]["text"] == "Hello, world!"


@pytest.mark.asyncio
async def test_acp_client_queues_by_session_id():
    """Test that updates are correctly routed to per-session queues."""
    client = AcpClient()

    update1 = FakeTextUpdate(text="From sess-1")
    update2 = FakeTextUpdate(text="From sess-2")

    await client.session_update(session_id="sess-1", update=update1)
    await client.session_update(session_id="sess-2", update=update2)

    q1 = client.get_queue("sess-1")
    q2 = client.get_queue("sess-2")

    assert not q1.empty()
    assert not q2.empty()

    item1 = await q1.get()
    item2 = await q2.get()

    assert item1["data"]["content"]["text"] == "From sess-1"
    assert item2["data"]["content"]["text"] == "From sess-2"


@pytest.mark.asyncio
async def test_acp_client_multiple_updates_same_session():
    """Test that multiple updates for the same session are queued in order."""
    client = AcpClient()

    await client.session_update(session_id="sess-1", update=FakeTextUpdate(text="First"))
    await client.session_update(session_id="sess-1", update=FakeTextUpdate(text="Second"))
    await client.session_update(session_id="sess-1", update=FakeTextUpdate(text="Third"))

    queue = client.get_queue("sess-1")
    assert queue.qsize() == 3

    item1 = await queue.get()
    item2 = await queue.get()
    item3 = await queue.get()

    assert item1["data"]["content"]["text"] == "First"
    assert item2["data"]["content"]["text"] == "Second"
    assert item3["data"]["content"]["text"] == "Third"


@pytest.mark.asyncio
async def test_acp_client_agent_message_chunk_parsing():
    """Test parsing of agent_message_chunk updates."""
    client = AcpClient()
    update = FakeTextUpdate(text="The answer is 42.")

    await client.session_update(session_id="s1", update=update)

    queue = client.get_queue("s1")
    item = await queue.get()

    assert item["type"] == "agent_message_chunk"
    assert item["data"]["content"]["type"] == "text"
    assert item["data"]["content"]["text"] == "The answer is 42."


@pytest.mark.asyncio
async def test_acp_client_tool_call_parsing():
    """Test parsing of tool_call updates."""
    client = AcpClient()
    update = FakeToolCallStart(tool_call_id="tc-1", title="read_file")

    await client.session_update(session_id="s1", update=update)

    queue = client.get_queue("s1")
    item = await queue.get()

    assert item["type"] == "tool_call"
    assert item["data"]["tool_call_id"] == "tc-1"
    assert item["data"]["title"] == "read_file"


@pytest.mark.asyncio
async def test_acp_client_tool_call_update_parsing():
    """Test parsing of tool_call_update updates."""
    client = AcpClient()
    update = FakeToolCallProgress(tool_call_id="tc-1", status="in_progress")

    await client.session_update(session_id="s1", update=update)

    queue = client.get_queue("s1")
    item = await queue.get()

    assert item["type"] == "tool_call_update"
    assert item["data"]["tool_call_id"] == "tc-1"
    assert item["data"]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_acp_client_available_commands_parsing():
    """Test parsing of available_commands_update."""
    client = AcpClient()
    update = FakeAvailableCommands(commands=["/help", "/search", "/clear"])

    await client.session_update(session_id="s1", update=update)

    queue = client.get_queue("s1")
    item = await queue.get()

    assert item["type"] == "available_commands_update"
    assert item["data"]["available_commands"] == ["/help", "/search", "/clear"]


@pytest.mark.asyncio
async def test_acp_client_request_permission_parsing():
    """Test parsing of request_permission updates."""
    client = AcpClient()
    update = FakePermissionRequest()

    await client.session_update(session_id="s1", update=update)

    queue = client.get_queue("s1")
    item = await queue.get()

    assert item["type"] == "request_permission"


@pytest.mark.asyncio
async def test_acp_client_remove_queue_sends_sentinel():
    """Test that remove_queue puts a sentinel None into the queue."""
    client = AcpClient()

    # First, put some items
    await client.session_update(session_id="s1", update=FakeTextUpdate(text="test"))
    queue = client.get_queue()

    # Drain one item
    await queue.get()

    # Remove the queue
    client.remove_queue()

    # Sentinel should have been put
    assert not queue.empty()

    sentinel = await queue.get()
    assert sentinel is None


@pytest.mark.asyncio
async def test_acp_client_get_queue_creates_if_missing():
    """Test that get_queue creates a new queue if one doesn't exist."""
    client = AcpClient()
    queue = client.get_queue()
    assert queue is not None
    assert queue.empty()
    # Single queue, always returns same instance
    assert client.get_queue("any-id") is queue


@pytest.mark.asyncio
async def test_acp_client_on_connect_stores_connection():
    """Test that on_connect stores the connection reference."""
    client = AcpClient()
    assert client.conn is None

    mock_conn = object()
    client.on_connect(mock_conn)
    assert client.conn is mock_conn


@pytest.mark.asyncio
async def test_acp_client_update_without_model_dump():
    """Test handling of updates that don't have model_dump method."""
    client = AcpClient()

    class PlainUpdate:
        pass

    update = PlainUpdate()
    # This is a test for an object that doesn't have session_update or model_dump
    # We check the type inference fallback
    await client.session_update(session_id="s1", update=update)

    queue = client.get_queue("s1")
    item = await queue.get()

    # Should use type name as fallback
    assert item["type"] == "PlainUpdate"
    assert item["data"] == str(update)


@pytest.mark.asyncio
async def test_acp_client_concurrent_updates():
    """Test that the queue handles concurrent updates correctly."""
    client = AcpClient()

    async def send_update(text: str):
        await client.session_update(session_id="s1", update=FakeTextUpdate(text=text))

    await asyncio.gather(
        send_update("A"),
        send_update("B"),
        send_update("C"),
        send_update("D"),
        send_update("E"),
    )

    queue = client.get_queue("s1")
    assert queue.qsize() == 5


@pytest.mark.asyncio
async def test_acp_client_ext_notification():
    """Test ext_notification (should not raise)."""
    client = AcpClient()
    # Should just log, not raise
    await client.ext_notification("test_method", {"key": "value"})


@pytest.mark.asyncio
async def test_acp_client_sdk_interface_methods_raise():
    """Test that required SDK interface methods raise RequestError."""
    from acp.exceptions import RequestError

    client = AcpClient()

    # request_permission is implemented (queues approval and waits) — not tested here.

    with pytest.raises(RequestError):
        await client.write_text_file(content="", path="/tmp", session_id="s")

    with pytest.raises(RequestError):
        await client.read_text_file(path="/tmp", session_id="s")

    with pytest.raises(RequestError):
        await client.create_terminal(command="ls", session_id="s")

    with pytest.raises(RequestError):
        await client.terminal_output(session_id="s", terminal_id="t")

    with pytest.raises(RequestError):
        await client.release_terminal(session_id="s", terminal_id="t")

    with pytest.raises(RequestError):
        await client.wait_for_terminal_exit(session_id="s", terminal_id="t")

    with pytest.raises(RequestError):
        await client.kill_terminal(session_id="s", terminal_id="t")

    with pytest.raises(RequestError):
        await client.ext_method("test", {})
