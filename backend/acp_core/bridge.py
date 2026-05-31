"""AcpBridge — async generator bridging ACP update queues to WebSocket consumers.

Reads from a session's asyncio.Queue (populated by AcpClient.session_update)
and yields updates one at a time for WebSocket delivery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

logger = logging.getLogger("acp.bridge")


class AcpBridge:
    """Bridges ACP session updates to a WebSocket consumer via async iteration.

    Reads from the per-session asyncio.Queue and yields updates.
    """

    def __init__(self, heartbeat_interval: float = 60.0) -> None:
        """Initialize the bridge.

        Args:
            heartbeat_interval: Seconds between heartbeat yields when queue is idle.
        """
        self.heartbeat_interval = heartbeat_interval
        logger.info("AcpBridge initialized: heartbeat_interval=%s", heartbeat_interval)

    async def stream_updates(
        self,
        session_id: str,
        acp_client: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream updates from the ACP client's queue.

        Yields update dicts (with type, session_id, timestamp, data fields).
        Yields heartbeats when no updates arrive within heartbeat_interval.
        Stops when a None sentinel is received.

        Args:
            session_id: The session to stream updates for.
            acp_client: The AcpClient instance managing the queue.

        Yields:
            dict: Update message or heartbeat.
        """
        queue = acp_client.get_queue(session_id)
        logger.debug("Streaming updates for session: %s", session_id)

        while True:
            try:
                # Wait for next update with timeout
                update = await asyncio.wait_for(
                    queue.get(),
                    timeout=self.heartbeat_interval,
                )
            except asyncio.TimeoutError:
                # No updates — send heartbeat
                heartbeat = {
                    "type": "heartbeat",
                    "session_id": session_id,
                    "timestamp": "",
                    "data": {},
                }
                logger.debug("Heartbeat: session=%s", session_id)
                yield heartbeat
                continue

            # Check for sentinel
            if update is None:
                logger.debug("Received sentinel, stopping stream: session=%s", session_id)
                break

            # logger.debug(
            #     "Streaming update: session=%s type=%s",
            #     session_id, update.get("type", "unknown"),
            # )
            yield update

        logger.debug("Stream ended for session: %s", session_id)

    async def stream_updates_sync(
        self,
        queue: asyncio.Queue[dict[str, Any]],
        session_id: str = "",
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream updates directly from a queue (for testing).

        Args:
            queue: The asyncio.Queue to read from.
            session_id: For logging.

        Yields:
            dict: Update message or heartbeat.
        """
        logger.debug("Streaming updates from queue: session=%s", session_id)

        while True:
            try:
                update = await asyncio.wait_for(
                    queue.get(),
                    timeout=self.heartbeat_interval,
                )
            except asyncio.TimeoutError:
                heartbeat = {
                    "type": "heartbeat",
                    "session_id": session_id,
                    "timestamp": "",
                    "data": {},
                }
                yield heartbeat
                continue

            if update is None:
                logger.debug("Received sentinel from queue: session=%s", session_id)
                break

            yield update

        logger.debug("Queue stream ended: session=%s", session_id)
