"""AcpClient — implements the ACP Client interface.

Receives session_update notifications from the ACP agent and routes them
into per-session asyncio.Queues for consumption by WebSocket bridges.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from acp.exceptions import RequestError

logger = logging.getLogger("acp.client")


class AcpClient:
    """Implements the ACP Client interface.

    Route notifications and requests from the agent.
    session_update notifications are queued for WebSocket delivery.
    Each AcpClient instance is 1:1 with a session, so a single queue is used.
    """

    def __init__(self) -> None:
        self.conn: Any = None
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._buffer: str = ""
        self._thought_buffer: str = ""
        self._tool_calls: list[dict[str, Any]] = []
        self._pending_approvals: dict[str, asyncio.Future] = {}
        self._approval_cleanup_task = asyncio.create_task(self._cleanup_stale_approvals())
        logger.debug("AcpClient initialized")

    def on_connect(self, conn: Any) -> None:
        """Called when the connection is established."""
        logger.debug("AcpClient.on_connect: conn=%s", type(conn).__name__)
        self.conn = conn

    def get_buffer(self) -> str:
        """Get accumulated text from agent chunks."""
        return self._buffer

    def reset_buffer(self) -> None:
        """Reset all buffers for a new turn."""
        self._buffer = ""
        self._thought_buffer = ""
        self._tool_calls = []

    def get_thought_buffer(self) -> str:
        """Get accumulated thinking text."""
        return self._thought_buffer

    def get_tool_calls(self) -> list[dict[str, Any]]:
        """Get accumulated tool calls."""
        return self._tool_calls

    def get_queue(self, session_id: str = "") -> asyncio.Queue[dict[str, Any]]:
        """Get the update queue for this client. session_id is ignored — one queue per client."""
        return self._queue

    def remove_queue(self, session_id: str = "") -> None:
        """Signal the queue to stop (called on disconnect)."""
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        logger.debug("Queued sentinel for client shutdown")

    async def session_update(
        self,
        session_id: str,
        update: Any,
        **kwargs: Any,
    ) -> None:
        """Handle incoming session_update notification from the agent.

        The SDK router calls this with the update field from SessionNotification.
        Extracts the sessionUpdate discriminator and queues the update.

        Args:
            session_id: The session this update pertains to.
            update: The update object (e.g., AgentMessageChunk, ToolCallStart, etc.)
            **kwargs: Additional fields from the notification (e.g., _meta).
        """
        try:
            update_type = getattr(update, "session_update", type(update).__name__)
        except Exception:
            update_type = type(update).__name__

        # Serialize the update for transport
        try:
            update_data = update.model_dump()
        except AttributeError:
            update_data = str(update)

        # Add metadata
        message: dict[str, Any] = {
            "type": update_type,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "data": update_data,
        }

        if update_type != "agent_message_chunk" and update_type != "agent_thought_chunk":
            logger.debug(
                "session_update: session=%s type=%s",
                session_id, update_type,
            )

        # Accumulate text chunks for DB persistence
        if update_type == "agent_message_chunk":
            try:
                content = update_data.get("content", {})
                text = content.get("text", "")
                self._buffer += text
            except Exception:
                pass
        elif update_type == "agent_thought_chunk":
            try:
                content = update_data.get("content", {})
                text = content.get("text", "")
                self._thought_buffer += text
            except Exception:
                pass
        elif update_type == "tool_call":
            self._tool_calls.append(update_data)

        # Queue it for WebSocket delivery — use single queue (1 client = 1 session)
        await self._queue.put(message)

    async def request_permission(
        self,
        options: Any,
        session_id: str,
        tool_call: Any,
        **kwargs: Any,
    ) -> Any:
        logger.debug("request_permission called: session=%s", session_id)
        request_id = str(uuid.uuid4())

        def _serialize(obj):
            if isinstance(obj, (str, int, float, bool, type(None))):
                return obj
            if isinstance(obj, list):
                return [_serialize(item) for item in obj]
            if isinstance(obj, dict):
                return {k: _serialize(v) for k, v in obj.items()}
            
            if hasattr(obj, "model_dump"):
                return _serialize(obj.model_dump(by_alias=True))
            if hasattr(obj, "dict"):
                return _serialize(obj.dict(by_alias=True))
                
            return str(obj)
        
        tool_data = _serialize(tool_call)
        options_data = _serialize(options) if options else {}

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_approvals[request_id] = future

        # Send the approval request to the WebSocket queue
        approval_msg = {
            "type": "approval_request",
            "session_id": session_id,
            "request_id": request_id,
            "tool_call": tool_data,
            "options": options_data,
            "timestamp": datetime.now().isoformat(),
        }

        await self._queue.put(approval_msg)
        logger.info(
            "Waiting for approval: session=%s req_id=%s tool=%s",
            session_id, request_id, tool_data.get("title") or tool_data.get("name", "unknown")
        )

        try:
            # Wait for the frontend to respond (Timeout after 10 minutes)
            result = await asyncio.wait_for(future, timeout=600)
            logger.info(
                "✅ Approval resolved: session=%s req_id=%s result=%r",
                session_id, request_id, result
            )
            if isinstance(result, str):
                return {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": result
                    }
                }
            
            if result:
                option_id = "allow_once"
                if options_data and isinstance(options_data, list):
                    for opt in options_data:
                        if isinstance(opt, dict) and "allow" in str(opt.get("kind", "")):
                            option_id = opt.get("optionId", option_id)
                            break
                
                return {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": option_id
                    }
                }
            
            else:
                option_id = "reject_once"
                if options_data and isinstance(options_data, list):
                    for opt in options_data:
                        if isinstance(opt, dict) and "reject" in str(opt.get("kind", "")):
                            option_id = opt.get("optionId", option_id)
                            break
                
                return {
                    "outcome": {
                        "outcome": "selected",
                        "optionId": option_id
                    }
                }

        except asyncio.TimeoutError:
            logger.warning(
                "⏰ Approval timed out: session=%s req_id=%s — auto-denying",
                session_id, request_id
            )
            return {
                "outcome": {
                    "outcome": "cancelled"
                }
            }
        
        finally:
            # Clean up
            self._pending_approvals.pop(request_id, None)

    # acp_client.py — resolve_approval method (add safety)
    def resolve_approval(self, request_id: str, result: Any) -> bool:
        """Called by ws.py when the user responds to an approval request."""
        future = self._pending_approvals.get(request_id)
        if future and not future.done():
            future.set_result(result)
            logger.debug("Resolved approval %s with result: %s", request_id, result)
            return True
        logger.warning("Approval request %s not found or already resolved", request_id)
        return False

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> Any:
        logger.debug("write_text_file called (denied): session=%s path=%s", session_id, path)
        raise RequestError.method_not_found("fs/write_text_file")

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> Any:
        logger.debug("read_text_file called (denied): session=%s path=%s", session_id, path)
        raise RequestError.method_not_found("fs/read_text_file")

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: Any = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> Any:
        logger.debug("create_terminal called (denied): session=%s command=%s", session_id, command)
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> Any:
        logger.debug("terminal_output called (denied): session=%s", session_id)
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> Any:
        logger.debug("release_terminal called (denied): session=%s", session_id)
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> Any:
        logger.debug("wait_for_terminal_exit called (denied): session=%s", session_id)
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> Any:
        logger.debug("kill_terminal called (denied): session=%s", session_id)
        raise RequestError.method_not_found("terminal/kill")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        logger.debug("ext_method called (denied): %s", method)
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        logger.debug("ext_notification: method=%s params=%s", method, params)

    async def _cleanup_stale_approvals(self):
        """Periodically remove approvals older than 15 minutes."""
        while True:
            await asyncio.sleep(300)  # Check every 5 min
            cutoff = datetime.now().timestamp() - 900  # 15 min ago
            stale = [
                rid for rid, fut in self._pending_approvals.items()
                if fut.done() or (hasattr(fut, "_created_at") and fut._created_at < cutoff)
            ]
            for rid in stale:
                self._pending_approvals.pop(rid, None)
                logger.debug("Cleaned up stale approval: %s", rid)
