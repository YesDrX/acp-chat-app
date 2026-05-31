"""ACP connection layer package.

Exports:
- AcpClient: Implements the ACP Client interface, queues session updates.
- AcpConnectionManager: Manages subprocess lifecycle for ACP agents.
- AcpBridge: Async generator bridging ACP update queues to WebSocket.
"""

from backend.acp_core.client import AcpClient
from backend.acp_core.manager import AcpConnectionManager
from backend.acp_core.bridge import AcpBridge

__all__ = ["AcpClient", "AcpConnectionManager", "AcpBridge"]
