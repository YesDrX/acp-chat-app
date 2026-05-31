"""AcpConnectionManager — manages subprocess lifecycle for ACP agents.

Spawns ACP agent subprocesses, initializes connections, creates/loads sessions,
and handles prompt/cancel/stop operations.

One subprocess per session for isolation.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import time
from typing import Any

from acp import PROTOCOL_VERSION
from acp.client.connection import ClientSideConnection
from acp.helpers import text_block
from acp.schema import ClientCapabilities, Implementation

from backend.acp_core.client import AcpClient
from backend.models.agent import Agent
from backend.models.session import Session

logger = logging.getLogger("acp.manager")


def _resolve_env_vars(env_vars: dict[str, str]) -> dict[str, str]:
    """Resolve \"\$VAR\" references in env var values using the current environment."""
    resolved = {}
    for k, v in env_vars.items():
        resolved[k] = re.sub(
            r"\$(\w+)",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            v,
        )
    return resolved


def _log_spawn_info(session: Session, agent: Agent, env: dict[str, str]) -> None:
    """Log the full spawn configuration for debugging."""
    logger.info(
        "Spawning agent process: command=%s args=%s cwd=%s",
        agent.command,
        agent.args if agent.args else [],
        session.cwd or os.getcwd(),
    )
    logger.info(
        "Session settings: model=%s effort=%s permission=%s status=%s",
        session.model or "(default)",
        session.effort_level or "(default)",
        session.permission_mode or "(default)",
        session.status,
    )
    # Log env var keys only (values may contain secrets)
    agent_env_keys = list(agent.env_vars.keys()) if agent.env_vars else []
    session_env_keys = list(session.env_vars.keys()) if session.env_vars else []
    inherited_keys = sorted(
        k for k in env if k not in agent_env_keys and k not in session_env_keys
    )
    if agent_env_keys:
        logger.debug("Agent env vars: %s", agent_env_keys)
    if session_env_keys:
        logger.debug("Session env vars: %s", session_env_keys)
    logger.debug("Inherited parent env keys: %s", inherited_keys[:20])


class ConnectionState:
    """Holds the state for an active ACP connection."""

    def __init__(
        self,
        conn: ClientSideConnection,
        proc: Any,
        client: AcpClient,
        ctx: Any,
    ) -> None:
        self.conn = conn
        self.proc = proc
        self.client = client
        self.ctx = ctx  # The async context manager from spawn_agent_process
        self.last_activity: float = time.time()
        self.acp_session_id: str = ""

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.time()


class AcpConnectionManager:
    """Manages ACP agent subprocess lifecycle.

    Stores active connections keyed by our internal session_id.
    Each session gets its own subprocess and ACP connection.
    """

    def __init__(self) -> None:
        self._connections: dict[str, ConnectionState] = {}
        logger.debug("AcpConnectionManager initialized")

    @property
    def active_sessions(self) -> list[str]:
        """Return list of active session IDs."""
        return list(self._connections.keys())

    def get_state(self, session_id: str) -> ConnectionState | None:
        """Get the connection state for a session."""
        return self._connections.get(session_id)

    def is_active(self, session_id: str) -> bool:
        """Check if a session has an active connection with a living process."""
        state = self._connections.get(session_id)
        if not state:
            return False

        # Check ACP connection health first (faster than subprocess checks)
        # Real Connection._raise_if_unavailable() is sync. Mocks (AsyncMock)
        # return a coroutine, which we detect and close to avoid warnings.
        try:
            result = state.conn._conn._raise_if_unavailable()
            if inspect.iscoroutine(result):
                result.close()  # Close coroutine to avoid RuntimeWarning
        except ConnectionError:
            logger.warning(
                "Session %s ACP connection closed/disconnected, removing stale state",
                session_id,
            )
            state.client.remove_queue(session_id)
            self._connections.pop(session_id, None)
            return False
        except Exception:
            # Mock or unexpected error — proceed to subprocess check
            logger.debug(
                "Connection health check skipped for session %s (mock or unexpected)",
                session_id, exc_info=True,
            )

        # Verify the subprocess is still running
        if state.proc and hasattr(state.proc, "returncode") and state.proc.returncode is not None:
            # Process exited — remove stale state synchronously
            logger.warning("Session %s process exited (rc=%s), removing stale state",
                          session_id, state.proc.returncode)
            state.client.remove_queue(session_id)
            self._connections.pop(session_id, None)
            return False
        return True

    async def start_session(
        self,
        session: Session,
        agent: Agent,
    ) -> str:
        """Start an ACP agent subprocess for a session.

        Args:
            session: The session record (with cwd, model, etc.)
            agent: The agent config (with command, args, etc.)

        Returns:
            The ACP session_id from the agent.

        Raises:
            ValueError: If session is already active.
            RuntimeError: If the agent process fails to start.
        """
        if session.id in self._connections:
            raise ValueError(f"Session {session.id} is already active")

        logger.info(
            "Starting ACP session: session_id=%s agent=%s cwd=%s",
            session.id, agent.name, session.cwd or os.getcwd(),
        )

        from acp.stdio import spawn_agent_process

        # Build command + args
        command = agent.command
        args = list(agent.args) if agent.args else []

        # Create AcpClient for this session
        client = AcpClient()

        # Spawn the agent subprocess — inherit parent env for API keys,
        # but isolate HOME to prevent pi-acp from seeing main pi sessions
        resolved_agent_env = _resolve_env_vars(dict(agent.env_vars))
        env = {**os.environ, **resolved_agent_env, **dict(session.env_vars)} if session.env_vars else {**os.environ, **resolved_agent_env}
        _log_spawn_info(session, agent, env)
        ctx = spawn_agent_process(
            client,
            command,
            *args,
            cwd=session.cwd or os.getcwd(),
            env=env,
            # 10MB StreamReader limit — pi-acp session replay can emit large JSON lines
            transport_kwargs={"limit": 10 * 1024 * 1024},
        )

        conn, proc = await ctx.__aenter__()
        logger.debug(
            "Agent subprocess started: pid=%s command=%s %s",
            proc.pid if hasattr(proc, "pid") else "unknown",
            command, " ".join(args),
        )

        # Create state
        state = ConnectionState(conn=conn, proc=proc, client=client, ctx=ctx)
        self._connections[session.id] = state

        try:
            # Initialize ACP connection
            logger.debug("Initializing ACP connection...")
            init_resp = await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(fs={}, terminal=False),
                client_info=Implementation(
                    name="acp-chat-app",
                    title="ACP Chat App",
                    version="0.1.0",
                ),
            )
            logger.info(
                "ACP initialized: protocol=%s agent=%s",
                init_resp.protocol_version,
                init_resp.agent_info.name if init_resp.agent_info else "unknown",
            )

            # Create new session on the agent
            logger.debug("Creating ACP session: cwd=%s", session.cwd or os.getcwd())
            new_sess = await conn.new_session(
                cwd=session.cwd or os.getcwd(),
                mcp_servers=[],
            )

            acp_session_id = new_sess.session_id
            state.acp_session_id = acp_session_id
            logger.info("ACP session created: acp_session_id=%s", acp_session_id)

            # Optionally set model if specified — let the agent validate it.
            if session.model:
                try:
                    resp = await conn.set_session_model(
                        model_id=session.model,
                        session_id=acp_session_id,
                    )
                    logger.info(
                        "Model set: requested=%s response=%s",
                        session.model,
                        resp.model_dump(exclude_none=True) if resp else "None",
                    )
                except Exception as e:
                    logger.error("Failed to set model '%s': %s", session.model, e, exc_info=True)

            # Optionally set effort_level as config option if specified
            if session.effort_level:
                try:
                    await conn.set_config_option(
                        config_id="effort",
                        session_id=acp_session_id,
                        value=session.effort_level,
                    )
                    logger.debug("Effort level set to: %s", session.effort_level)
                except Exception as e:
                    logger.warning("Failed to set effort '%s': %s", session.effort_level, e)

            # Optionally set permission mode if specified
            if session.permission_mode:
                try:
                    await conn.set_session_mode(
                        mode_id=session.permission_mode,
                        session_id=acp_session_id,
                    )
                    logger.debug("Permission mode set to: %s", session.permission_mode)
                except Exception as e:
                    logger.warning("Failed to set permission_mode '%s': %s", session.permission_mode, e)

            state.touch()
            return acp_session_id

        except Exception as e:
            # Clean up on failure
            logger.error(
                "Failed to initialize ACP session for session=%s: %s",
                session.id, e, exc_info=True,
            )
            await self._cleanup_state(state, session.id)
            raise

    async def send_prompt(
        self,
        session_id: str,
        text: str,
        message_id: str | None = None,
    ) -> Any:
        """Send a prompt to the agent.

        Args:
            session_id: Our internal session ID.
            text: The text prompt to send.
            message_id: Optional message ID.

        Returns:
            The PromptResponse from the agent.

        Raises:
            ValueError: If session is not active.
        """
        state = self._connections.get(session_id)
        if state is None:
            raise ValueError(f"Session {session_id} is not active")

        # Defensive check: ACP connection may have been disconnected after is_active()
        try:
            result = state.conn._conn._raise_if_unavailable()
            if inspect.iscoroutine(result):
                result.close()  # Close coroutine to avoid RuntimeWarning
        except ConnectionError:
            logger.warning(
                "ACP connection lost before send_prompt for session %s, cleaning up",
                session_id,
            )
            await self._cleanup_state(state, session_id)
            raise ValueError(
                f"ACP connection for session {session_id} was lost. "
                "Please try again — the session will be restarted automatically."
            )
        except Exception:
            # Mock or unexpected error — proceed
            logger.debug(
                "send_prompt preflight health check skipped for session %s (mock or unexpected)",
                session_id, exc_info=True,
            )

        logger.debug("Sending prompt: session=%s text=%s...", session_id, text[:50])
        state.touch()

        try:
            response = await state.conn.prompt(
                session_id=state.acp_session_id,
                prompt=[text_block(text)],
                message_id=message_id,
            )
        except ConnectionError:
            logger.warning(
                "ACP connection failed during send_prompt for session %s, cleaning up",
                session_id,
            )
            await self._cleanup_state(state, session_id)
            raise ValueError(
                f"ACP connection for session {session_id} was lost during prompt. "
                "Please try again — the session will be restarted automatically."
            )

        logger.debug(
            "Prompt response: stop_reason=%s",
            response.stop_reason if hasattr(response, "stop_reason") else "unknown",
        )
        state.touch()
        return response

    async def cancel(self, session_id: str) -> None:
        """Send a cancel notification to the agent.

        Args:
            session_id: Our internal session ID.

        Raises:
            ValueError: If session is not active.
        """
        state = self._connections.get(session_id)
        if state is None:
            raise ValueError(f"Session {session_id} is not active")

        logger.info("Cancelling session: %s", session_id)
        state.touch()

        await state.conn.cancel(session_id=state.acp_session_id)
        logger.debug("Cancel sent for session: %s", session_id)

    async def stop_session(self, session_id: str) -> None:
        """Gracefully stop a session's agent subprocess.

        Args:
            session_id: Our internal session ID.
        """
        state = self._connections.get(session_id)
        if state is None:
            logger.debug("Session %s already stopped", session_id)
            return

        logger.info("Stopping session: %s", session_id)
        await self._cleanup_state(state, session_id)

    async def _cleanup_state(self, state: ConnectionState, session_id: str) -> None:
        """Internal: clean up a connection state."""
        try:
            # Close the ACP connection
            await state.conn.close()
        except Exception as e:
            logger.warning("Error closing ACP connection: %s", e)

        # Terminate the subprocess
        try:
            if state.proc and hasattr(state.proc, "returncode") and state.proc.returncode is None:
                logger.debug("Terminating subprocess (pid=%s)", getattr(state.proc, "pid", "unknown"))
                state.proc.terminate()
                try:
                    await asyncio.wait_for(state.proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("Process did not exit, killing...")
                    if hasattr(state.proc, "kill"):
                        state.proc.kill()
                    await state.proc.wait()
        except ProcessLookupError:
            logger.debug("Process already exited")
        except Exception as e:
            logger.warning("Error terminating subprocess: %s", e)

        # Close the context manager
        try:
            await state.ctx.__aexit__(None, None, None)
        except Exception as e:
            logger.warning("Error closing context manager: %s", e)

        # Remove the client queue
        state.client.remove_queue(session_id)

        # Remove from connections
        self._connections.pop(session_id, None)
        logger.info("Session stopped: %s", session_id)

    async def set_model(self, session_id: str, model_id: str) -> None:
        """Update the model for an active session.

        Args:
            session_id: Our internal session ID.
            model_id: The model ID to set.
        """
        state = self._connections.get(session_id)
        if state is None:
            raise ValueError(f"Session {session_id} is not active")

        logger.info("Setting model: session=%s model=%s", session_id, model_id)
        state.touch()

        await state.conn.set_session_model(
            model_id=model_id,
            session_id=state.acp_session_id,
        )
        logger.debug("Model set to: %s", model_id)

    async def set_mode(self, session_id: str, mode_id: str) -> None:
        """Update the mode for an active session.

        Args:
            session_id: Our internal session ID.
            mode_id: The mode ID to set.
        """
        state = self._connections.get(session_id)
        if state is None:
            raise ValueError(f"Session {session_id} is not active")

        logger.info("Setting mode: session=%s mode=%s", session_id, mode_id)
        state.touch()

        await state.conn.set_session_mode(
            mode_id=mode_id,
            session_id=state.acp_session_id,
        )
        logger.debug("Mode set to: %s", mode_id)

    async def set_config_option(
        self,
        session_id: str,
        config_id: str,
        value: str | bool,
    ) -> None:
        """Update a config option for an active session.

        Args:
            session_id: Our internal session ID.
            config_id: The config option ID.
            value: The value (string or boolean).
        """
        state = self._connections.get(session_id)
        if state is None:
            raise ValueError(f"Session {session_id} is not active")

        logger.info(
            "Setting config option: session=%s config=%s value=%s",
            session_id, config_id, value,
        )
        state.touch()

        await state.conn.set_config_option(
            config_id=config_id,
            session_id=state.acp_session_id,
            value=value,
        )
        logger.debug("Config option set: %s=%s", config_id, value)

    def update_activity(self, session_id: str) -> None:
        """Reset the idle timer for a session.

        Called on every interaction (prompt, cancel, settings).

        Args:
            session_id: Our internal session ID.
        """
        state = self._connections.get(session_id)
        if state is not None:
            state.touch()
            # logger.debug("Activity updated for session: %s", session_id)

    async def _idle_checker(self) -> None:
        """Background task: check for idle sessions and stop them.

        Runs every 30 seconds. If a session has been idle for longer than
        the configured IDLE_TIMEOUT_SECONDS, it sends an idle_shutdown
        notification and stops the session.
        """
        from backend.config import IDLE_TIMEOUT_SECONDS

        timeout = IDLE_TIMEOUT_SECONDS
        logger.debug(
            "Idle checker started: interval=30s timeout=%ds",
            timeout,
        )

        while True:
            await asyncio.sleep(30)
            now = time.time()
            sessions_to_stop: list[str] = []

            for sid, state in list(self._connections.items()):
                idle_seconds = now - state.last_activity
                if idle_seconds > timeout:
                    logger.info(
                        "Idle session detected: session=%s idle_seconds=%.0f timeout=%d",
                        sid, idle_seconds, timeout,
                    )
                    sessions_to_stop.append(sid)

            for sid in sessions_to_stop:
                await self._stop_idle_session(sid)

    async def _stop_idle_session(self, session_id: str) -> None:
        """Stop an idle session with a WebSocket notification.

        Puts an idle_shutdown message into the client queue before stopping,
        so any connected WebSocket client will be notified.

        Args:
            session_id: Our internal session ID.
        """
        state = self._connections.get(session_id)
        if state is None:
            logger.debug("Idle session %s already gone, nothing to stop", session_id)
            return

        logger.info("Stopping idle session: %s", session_id)

        # Send idle_shutdown notification via the client queue
        try:
            queue = state.client.get_queue(session_id)
            shutdown_msg = {
                "type": "idle_shutdown",
                "session_id": session_id,
                "timestamp": "",
                "data": {
                    "message": "Session idle for 5 minutes, shutting down",
                    "idle_timeout_seconds": 300,
                },
            }
            await queue.put(shutdown_msg)
            logger.debug("Sent idle_shutdown message to queue: session=%s", session_id)
        except Exception as e:
            logger.warning("Failed to queue idle_shutdown message: %s", e)

        # Stop the session
        await self.stop_session(session_id)

    def start_idle_checker(self) -> asyncio.Task[None]:
        """Start the idle checker background task.

        Returns:
            The asyncio.Task for the checker (for cancellation on shutdown).
        """
        logger.info("Starting idle checker")
        task = asyncio.create_task(self._idle_checker())
        return task

    async def stop_all(self) -> None:
        """Stop all active sessions. Called during app shutdown."""
        logger.info("Stopping all active sessions (%d)", len(self._connections))
        session_ids = list(self._connections.keys())
        for sid in session_ids:
            await self.stop_session(sid)
        logger.info("All sessions stopped")

    async def resume_session_from(
        self,
        session: Session,
        agent: Agent,
    ) -> str:
        """Start an ACP agent subprocess and attempt to load an existing session.

        Uses ACP session/load to restore the session state if supported.
        Falls back to new_session if load fails or is not supported.

        Args:
            session: The session record.
            agent: The agent config.

        Returns:
            A tuple of (acp_session_id, was_resumed: bool).

        Raises:
            ValueError: If session is already active.
            RuntimeError: If the agent process fails to start.
        """
        if session.id in self._connections:
            raise ValueError(f"Session {session.id} is already active")

        logger.info(
            "Resuming ACP session: session_id=%s agent=%s cwd=%s",
            session.id, agent.name, session.cwd or os.getcwd(),
        )

        from acp.stdio import spawn_agent_process

        command = agent.command
        args = list(agent.args) if agent.args else []

        client = AcpClient()

        resolved_agent_env = _resolve_env_vars(dict(agent.env_vars))
        env = {**os.environ, **resolved_agent_env, **dict(session.env_vars)} if session.env_vars else {**os.environ, **resolved_agent_env}
        _log_spawn_info(session, agent, env)
        ctx = spawn_agent_process(
            client,
            command,
            *args,
            cwd=session.cwd or os.getcwd(),
            env=env,
            transport_kwargs={"limit": 10 * 1024 * 1024},
        )

        conn, proc = await ctx.__aenter__()
        logger.debug(
            "Agent subprocess started for resume: pid=%s",
            proc.pid if hasattr(proc, "pid") else "unknown",
        )

        state = ConnectionState(conn=conn, proc=proc, client=client, ctx=ctx)
        self._connections[session.id] = state

        was_resumed = False

        try:
            # Initialize
            await conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(fs={}, terminal=False),
                client_info=Implementation(
                    name="acp-chat-app",
                    title="ACP Chat App",
                    version="0.1.0",
                ),
            )

            # Try to load the existing session using the persisted ACP session ID.
            # SKIP if empty — no previous session to resume, and sending an
            # unknown UUID as session_id may crash some agents.
            if session.acp_session_id:
                try:
                    load_resp = await conn.load_session(
                        cwd=session.cwd or os.getcwd(),
                        session_id=session.acp_session_id,
                    )

                    if load_resp is not None:
                        # loadSession response may return sessionId (camelCase) or omit it.
                        # The sessionId is the one we requested — reuse it.
                        state.acp_session_id = getattr(load_resp, "sessionId", session.acp_session_id)
                        was_resumed = True
                        logger.info(
                            "Session resumed successfully: acp_session_id=%s",
                            state.acp_session_id,
                        )
                    else:
                        logger.warning(
                            "Session load returned None for acp_session=%s, creating new",
                            session.acp_session_id,
                        )
                except ConnectionError:
                    # load_session caused the agent subprocess to crash/close.
                    # Don't try new_session on the dead connection — let outer handler
                    # handle cleanup and propagate.
                    logger.error(
                        "ACP connection lost during load_session for acp_session=%s. "
                        "Agent subprocess may have crashed.",
                        session.acp_session_id, exc_info=True,
                    )
                    raise RuntimeError(
                        f"ACP connection lost during session load for "
                        f"{session.acp_session_id}. Agent subprocess may have crashed."
                    )
                except Exception as e:
                    logger.warning(
                        "Session load failed for acp_session=%s: %s. Creating new session.",
                        session.acp_session_id, e, exc_info=True,
                    )
            else:
                logger.debug(
                    "No stored acp_session_id for session=%s, skipping load",
                    session.id,
                )

            # If load failed or was skipped, create a new session
            if not was_resumed:
                logger.debug("Creating new session for: %s", session.id)
                new_sess = await conn.new_session(
                    cwd=session.cwd or os.getcwd(),
                    mcp_servers=[],
                )
                state.acp_session_id = new_sess.session_id
                logger.info(
                    "New session created: acp_session_id=%s",
                    state.acp_session_id,
                )

            # Optionally set model if specified — let the agent validate it.
            if session.model:
                try:
                    resp = await conn.set_session_model(
                        model_id=session.model,
                        session_id=state.acp_session_id,
                    )
                    logger.info(
                        "Model set: requested=%s response=%s",
                        session.model,
                        resp.model_dump(exclude_none=True) if resp else "None",
                    )
                except Exception as e:
                    logger.error("Failed to set model '%s': %s", session.model, e, exc_info=True)

            # Optionally set effort_level as config option if specified
            if session.effort_level:
                try:
                    await conn.set_config_option(
                        config_id="effort",
                        session_id=state.acp_session_id,
                        value=session.effort_level,
                    )
                    logger.info("Effort level set to: %s", session.effort_level)
                except Exception as e:
                    logger.error("Failed to set effort '%s': %s", session.effort_level, e, exc_info=True)

            # Optionally set permission mode if specified
            if session.permission_mode:
                try:
                    await conn.set_session_mode(
                        mode_id=session.permission_mode,
                        session_id=state.acp_session_id,
                    )
                    logger.info("Permission mode set to: %s", session.permission_mode)
                except Exception as e:
                    logger.error("Failed to set permission_mode '%s': %s", session.permission_mode, e, exc_info=True)

            state.touch()
            return state.acp_session_id, was_resumed

        except Exception as e:
            logger.error(
                "Failed to resume ACP session for session=%s: %s",
                session.id, e, exc_info=True,
            )
            await self._cleanup_state(state, session.id)
            raise
