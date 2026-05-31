"""WebSocket endpoint for real-time chat with ACP agents.

Handles bidirectional communication between the browser and ACP agent subprocess:
- User messages → sent to agent via AcpConnectionManager
- Agent streaming updates → broadcast to all connected WebSocket clients
- Multiple clients per session (browser + phone, etc.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

from backend.template_config import templates
from backend.models.agent import AgentStore
from backend.models.session import SessionStore, Message, MessageStore
from backend.acp_core.bridge import AcpBridge

logger = logging.getLogger("routes.ws")

ws_router = APIRouter()

# Module-level singletons — set by main.py after app startup
_agent_store: AgentStore | None = None
_session_store: SessionStore | None = None
_manager: Any = None  # AcpConnectionManager
_message_store: MessageStore | None = None

# Multi-client support: track all WebSocket connections per session
_session_conns: dict[str, set[WebSocket]] = {}
_session_lock: asyncio.Lock = asyncio.Lock()
# Per-session broadcast task (one reader → many senders)
_broadcast_tasks: dict[str, asyncio.Task[None]] = {}
# Prevent concurrent prompts on the same session
_prompt_active: dict[str, bool] = {}


def get_agent_store() -> AgentStore:
    global _agent_store
    if _agent_store is None:
        _agent_store = AgentStore()
    return _agent_store


def get_session_store() -> SessionStore:
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store


def get_manager() -> Any:
    global _manager
    if _manager is None:
        raise RuntimeError("AcpConnectionManager not initialized")
    return _manager


def get_message_store() -> MessageStore:
    global _message_store
    if _message_store is None:
        _message_store = MessageStore()
    return _message_store


def set_manager(manager: Any) -> None:
    """Called by main.py lifespan to inject the manager."""
    global _manager
    _manager = manager
    logger.debug("AcpConnectionManager injected into ws routes")


# --- Safe send / broadcast helpers ---


async def _safe_send_json(websocket: WebSocket, data: dict) -> None:
    """Send JSON to WebSocket, silently ignoring disconnect errors."""
    try:
        await websocket.send_json(data)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        logger.debug("Failed to send message to WebSocket (client likely disconnected)")


async def _register_connection(session_id: str, ws: WebSocket) -> bool:
    """Register a WebSocket connection for a session. Returns True if first."""
    async with _session_lock:
        if session_id not in _session_conns:
            _session_conns[session_id] = set()
        was_empty = len(_session_conns[session_id]) == 0
        _session_conns[session_id].add(ws)
        logger.debug(
            "Session %s: connection registered (%d total)",
            session_id, len(_session_conns[session_id]),
        )
        return was_empty


async def _unregister_connection(session_id: str, ws: WebSocket) -> bool:
    """Unregister a WebSocket connection. Returns True if last one gone."""
    async with _session_lock:
        if session_id in _session_conns:
            _session_conns[session_id].discard(ws)
            remaining = len(_session_conns[session_id])
            logger.debug(
                "Session %s: connection unregistered (%d remaining)",
                session_id, remaining,
            )
            if remaining == 0:
                del _session_conns[session_id]
                return True
    return False


async def _broadcast(session_id: str, data: dict) -> None:
    """Send data to ALL connected WebSocket clients for a session."""
    conns: set[WebSocket] = set()
    async with _session_lock:
        if session_id in _session_conns:
            conns = set(_session_conns[session_id])
    dead = []
    for ws in conns:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        await _unregister_connection(session_id, ws)


async def _broadcast_stream(
    session_id: str,
    manager: Any,
    bridge: AcpBridge,
    stream_state: dict,
) -> None:
    """Background task: read agent updates and broadcast to all connections."""
    state = manager.get_state(session_id)
    if state is None:
        logger.debug("No connection state for session %s, cannot broadcast", session_id)
        return

    logger.debug("Starting broadcast stream for session: %s", session_id)
    try:
        async for update in bridge.stream_updates(session_id, state.client):
            manager.update_activity(session_id)
            if stream_state.get("suppress_text") and update.get("type") in (
                "agent_message_chunk", "agent_thought_chunk",
            ):
                continue
            if update.get("type") == "tool_call_update" and update.get("data", {}).get("status") == "pending":
                continue
            await _broadcast(session_id, update)
    except asyncio.CancelledError:
        logger.debug("Broadcast stream cancelled for session: %s", session_id)
    except Exception as e:
        logger.debug("Broadcast stream error for session %s: %s", session_id, e)


async def _ensure_broadcast(
    session_id: str,
    manager: Any,
    bridge: AcpBridge,
    stream_state: dict,
) -> None:
    """Ensure the broadcast task is running; starts it if not."""
    task = _broadcast_tasks.get(session_id)
    if task is None or task.done():
        task = asyncio.create_task(
            _broadcast_stream(session_id, manager, bridge, stream_state)
        )
        _broadcast_tasks[session_id] = task
        logger.debug("Broadcast task (re)started for session: %s", session_id)


async def _stop_broadcast(session_id: str) -> None:
    """Stop the broadcast task for a session if it's running."""
    task = _broadcast_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# --- Background prompt handler (shared by all connections) ---


async def _do_prompt(
    session_id: str,
    text: str,
    manager: Any,
    session: Any,
    agent: Any,
    sstore: Any,
    mstore: Any,
    stream_state: dict,
) -> None:
    """Background task: send prompt to agent and broadcast response to all."""
    try:
        await _do_prompt_inner(session_id, text, manager, session, agent, sstore, mstore, stream_state)
    except Exception as e:
        err_msg = str(e)
        hint = err_msg if "Authentication" in err_msg or "API key" in err_msg else f"Agent error: {e}"
        logger.error("Prompt failed: session=%s error=%s", session_id, e, exc_info=True)
        await _broadcast(session_id, {
            "type": "error",
            "message": hint,
            "hint": "pi-acp needs an API key.",
        })
    finally:
        _prompt_active.pop(session_id, None)


async def _do_prompt_inner(
    session_id: str,
    text: str,
    manager: Any,
    session: Any,
    agent: Any,
    sstore: Any,
    mstore: Any,
    stream_state: dict,
) -> None:
    """Inner prompt logic with retry and broadcast response."""
    prompt_retried = False
    response = None
    while True:
        try:
            state = manager.get_state(session_id)
            if state:
                state.client.reset_buffer()
            response = await manager.send_prompt(session_id, text)
            break
        except (ValueError, ConnectionError) as e:
            err_msg = str(e)
            if not prompt_retried and ("connection" in err_msg.lower() or "lost" in err_msg.lower()):
                logger.info("Connection lost during prompt for session %s, retrying", session_id)
                prompt_retried = True
                await _broadcast(session_id, {
                    "type": "system",
                    "message": "ACP connection was lost. Restarting session and retrying...",
                })
                try:
                    acp_sid, was_resumed = await manager.resume_session_from(session, agent)
                    logger.info("Session restarted: session=%s acp_sid=%s was_resumed=%s", session_id, acp_sid, was_resumed)
                    session.acp_session_id = acp_sid
                    await _ensure_broadcast(session_id, manager, AcpBridge(), stream_state)
                    session.status = "active"
                    await sstore.update(session)
                    continue
                except Exception as restart_e:
                    logger.error("Session restart failed: %s", restart_e, exc_info=True)
                    await _broadcast(session_id, {
                        "type": "error",
                        "message": f"Session restart failed: {restart_e}",
                    })
                    return
            else:
                raise

    if response is not None:
        response_data = {}
        if hasattr(response, "stop_reason"):
            response_data["stop_reason"] = response.stop_reason
        if hasattr(response, "message_id"):
            response_data["message_id"] = response.message_id

        state = manager.get_state(session_id)
        if state:
            thought_text = state.client.get_thought_buffer()
            if thought_text and thought_text.strip():
                thought_msg = Message(session_id=session_id, role="thinking", content=thought_text.strip())
                await mstore.create(thought_msg)
            for tc in state.client.get_tool_calls():
                tc_msg = Message(session_id=session_id, role="tool_call", content=json.dumps(tc))
                await mstore.create(tc_msg)
            agent_text = state.client.get_buffer()
            if agent_text:
                agent_msg = Message(session_id=session_id, role="agent", content=agent_text)
                await mstore.create(agent_msg)

        logger.info("Prompt complete: session=%s stop_reason=%s", session_id, response_data.get("stop_reason", "unknown"))
        await _broadcast(session_id, {
            "type": "prompt_complete",
            "session_id": session_id,
            "response": response_data,
        })


# --- WebSocket endpoint ---
@ws_router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint for session chat. Multiple clients can connect."""
    await websocket.accept()
    logger.info("WebSocket connected: session=%s client=%s", session_id, websocket.client)

    sstore = get_session_store()
    astore = get_agent_store()
    manager = get_manager()
    bridge = AcpBridge()

    session = await sstore.get(session_id)
    if session is None:
        await websocket.send_json({"type": "error", "message": f"Session not found: {session_id}"})
        await websocket.close(code=4000)
        return

    agent = astore.get(session.agent_id)
    if agent is None:
        await websocket.send_json({"type": "error", "message": f"Agent not found: {session.agent_id}"})
        await websocket.close(code=4001)
        return

    mstore = get_message_store()
    history = await mstore.get_by_session(session_id)
    history_list = [{"id": m.id, "role": m.role, "content": m.content, "created_at": m.created_at} for m in history]

    # Register this connection
    is_first = await _register_connection(session_id, websocket)

    # Shared stream_state for this session
    stream_state = {"suppress_text": False}

    # Send connected message (per-client)
    connected_msg = {
        "type": "connected",
        "session_id": session.id,
        "session_name": session.name,
        "agent_name": agent.name,
        "agent_id": agent.id,
        "model": session.model or "default",
        "cwd": session.cwd,
        "status": session.status,
        "is_active": manager.is_active(session_id),
        "can_resume": session.status in ("idle", "stopped", "active"),
        "history": history_list,
    }
    await _safe_send_json(websocket, connected_msg)

    # If session is active, replay current turn's in-progress stream to new client
    if manager.is_active(session_id):
        state = manager.get_state(session_id)
        if state:
            thought_text = state.client.get_thought_buffer()
            tool_calls = state.client.get_tool_calls()
            agent_text = state.client.get_buffer()
            logger.info(
                "Replaying in-progress stream for new client: session=%s "
                "thought_len=%d tool_calls=%d agent_text_len=%d",
                session_id,
                len(thought_text) if thought_text else 0,
                len(tool_calls),
                len(agent_text) if agent_text else 0,
            )
            if thought_text and thought_text.strip():
                await _safe_send_json(websocket, {
                    "type": "agent_thought_chunk",
                    "session_id": session_id,
                    "timestamp": "",
                    "data": {"content": {"type": "text", "text": thought_text.strip()}},
                })
                logger.debug("Replayed thought to session %s", session_id)
            for tc in tool_calls:
                await _safe_send_json(websocket, {
                    "type": "tool_call",
                    "session_id": session_id,
                    "timestamp": "",
                    "data": tc,
                })
                logger.debug("Replayed tool call %s to session %s", tc.get("tool_call_id", "?"), session_id)
            if agent_text:
                await _safe_send_json(websocket, {
                    "type": "agent_message_chunk",
                    "session_id": session_id,
                    "timestamp": "",
                    "data": {"content": {"type": "text", "text": agent_text}},
                })
                logger.debug("Replayed agent text to session %s", session_id)
            
            await _safe_send_json(websocket, {
                "type": "prompt_complete",
                "session_id": session_id,
                "response": {"stop_reason": "replay_complete"}
            })
            
        else:
            logger.debug("Session %s is active but no connection state", session_id)

    # Ensure broadcast task is running (first connection starts it)
    if is_first:
        await _ensure_broadcast(session_id, manager, bridge, stream_state)

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")
            logger.debug("WebSocket received: session=%s type=%s", session_id, msg_type)

            if msg_type == "ping":
                await _safe_send_json(websocket, {"type": "pong"})

            elif msg_type == "resume":
                logger.info("Resume request: session=%s", session_id)
                if manager.is_active(session_id):
                    state = manager.get_state(session_id)
                    if state:
                        await _safe_send_json(websocket, {
                            "type": "session_resumed",
                            "session_id": session_id,
                            "acp_session_id": state.acp_session_id,
                            "was_resumed": False,
                        })
                        continue

                try:
                    stream_state["suppress_text"] = True
                    acp_sid, was_resumed = await manager.resume_session_from(session, agent)
                    logger.info("Session resume: session=%s acp_sid=%s was_resumed=%s", session_id, acp_sid, was_resumed)
                    session.acp_session_id = acp_sid
                    session.status = "active"
                    await sstore.update(session)
                    await _ensure_broadcast(session_id, manager, bridge, stream_state)
                    await _broadcast(session_id, {
                        "type": "session_resumed",
                        "session_id": session_id,
                        "acp_session_id": acp_sid,
                        "was_resumed": was_resumed,
                    })
                    await asyncio.sleep(0.5)
                    state = manager.get_state(session_id)
                    if state:
                        agent_text = state.client.get_buffer()
                        if agent_text and agent_text.strip():
                            state.client.reset_buffer()
                    stream_state["suppress_text"] = False
                    await _broadcast(session_id, {
                        "type": "prompt_complete",
                        "session_id": session_id,
                        "response": {"stop_reason": "startup_complete"}
                    })
                except Exception as e:
                    logger.error("Failed to resume session: %s", e, exc_info=True)
                    await _safe_send_json(websocket, {
                        "type": "error",
                        "message": f"Failed to resume session: {e}",
                    })

            elif msg_type == "prompt":
                text = data.get("text", "")
                if not text.strip():
                    await _safe_send_json(websocket, {"type": "error", "message": "Empty prompt"})
                    continue

                # Prevent concurrent prompts
                if _prompt_active.get(session_id):
                    await _safe_send_json(websocket, {
                        "type": "error",
                        "message": "Another prompt is already in progress",
                    })
                    continue

                logger.info("Prompt: session=%s text=%s...", session_id, text[:80])

                # Append uploaded file paths
                files = data.get("files", [])
                if files:
                    from backend.config import FILES_DIR
                    from backend.routes.settings import _load_settings
                    settings = _load_settings()
                    utc_now = datetime.now(timezone.utc)
                    files_dir = os.path.join(
                        settings.get("files_directory", str(FILES_DIR)),
                        "chat_uploads",
                        utc_now.strftime("%Y%m%d")
                    )
                    fnames = [f.get("filename", f.get("name", "unknown")) for f in files]
                    text = text + f"\n\nFiles are uploaded to {files_dir}. Attached files: {', '.join(fnames)}"

                # Save user message
                user_msg = Message(session_id=session_id, role="user", content=text)
                await mstore.create(user_msg)

                # Start session if needed
                if not manager.is_active(session_id):
                    logger.info("Starting ACP session: %s", session_id)
                    try:
                        stream_state["suppress_text"] = True
                        acp_sid, was_resumed = await manager.resume_session_from(session, agent)
                        if was_resumed:
                            logger.info("Session resumed: session=%s acp_sid=%s", session_id, acp_sid)
                        else:
                            logger.info("Session started fresh: session=%s acp_sid=%s", session_id, acp_sid)
                        session.acp_session_id = acp_sid
                        await _ensure_broadcast(session_id, manager, bridge, stream_state)
                        await _broadcast(session_id, {
                            "type": "session_started",
                            "session_id": session_id,
                            "acp_session_id": acp_sid,
                            "was_resumed": was_resumed,
                        })
                        await asyncio.sleep(0.5)
                        state = manager.get_state(session_id)
                        if state:
                            state.client.reset_buffer()
                        stream_state["suppress_text"] = False
                    except Exception as e:
                        logger.error("Failed to start ACP session: %s", e, exc_info=True)
                        await _safe_send_json(websocket, {"type": "error", "message": f"Failed to start agent: {e}"})
                        continue
                else:
                    manager.update_activity(session_id)

                try:
                    session.status = "active"
                    await sstore.update(session)
                except Exception as e:
                    logger.warning("Failed to update session status: %s", e)

                manager.update_activity(session_id)

                # Launch prompt as background task
                _prompt_active[session_id] = True
                asyncio.create_task(_do_prompt(
                    session_id, text, manager, session, agent, sstore, mstore, stream_state,
                ))

            elif msg_type == "cancel":
                logger.info("Cancel: session=%s", session_id)
                if manager.is_active(session_id):
                    manager.update_activity(session_id)
                    try:
                        await manager.cancel(session_id)
                        await _broadcast(session_id, {"type": "cancelled", "session_id": session_id})
                    except Exception as e:
                        logger.error("Cancel failed: %s", e, exc_info=True)
                        await _safe_send_json(websocket, {"type": "error", "message": f"Cancel failed: {e}"})
                else:
                    await _safe_send_json(websocket, {"type": "error", "message": "Session is not active"})

            elif msg_type == "stop":
                logger.info("Stop: session=%s", session_id)
                if manager.is_active(session_id):
                    try:
                        await manager.stop_session(session_id)
                        await _broadcast(session_id, {"type": "stopped", "session_id": session_id})
                    except Exception as e:
                        logger.error("Stop failed: %s", e, exc_info=True)
                        await _safe_send_json(websocket, {"type": "error", "message": f"Stop failed: {e}"})
                else:
                    await _safe_send_json(websocket, {"type": "stopped", "session_id": session_id})

            elif msg_type == "restart":
                logger.info("Restart: session=%s", session_id)
                try:
                    await manager.stop_session(session_id)
                except Exception as e:
                    logger.warning("Restart stop failed: %s", e)
                try:
                    acp_sid, was_resumed = await manager.resume_session_from(session, agent)
                    session.acp_session_id = acp_sid
                    session.status = "active"
                    await sstore.update(session)
                    await _ensure_broadcast(session_id, manager, bridge, stream_state)
                    await _broadcast(session_id, {
                        "type": "restarted",
                        "session_id": session_id,
                        "acp_session_id": session.acp_session_id,
                    })
                    logger.info("Session restarted: %s", session_id)
                except Exception as e:
                    logger.error("Restart failed: %s", e, exc_info=True)
                    await _safe_send_json(websocket, {"type": "error", "message": f"Restart failed: {e}"})

            elif msg_type == "set_model":
                model = data.get("model", "")
                logger.info("Set model: session=%s model=%s", session_id, model)
                
                # update database session record
                try:    
                    session.model = model
                    await sstore.update(session)
                except Exception as e:
                    logger.warning("Failed to update session model in database: %s", e)

                # update model in acp session if active
                if manager.is_active(session_id):
                    manager.update_activity(session_id)
                    try:
                        await manager.set_model(session_id, model)
                        await _broadcast(session_id, {
                            "type": "model_set", "session_id": session_id, "model": model,
                        })
                    except Exception as e:
                        logger.error("Set model failed: %s", e, exc_info=True)
                        await _safe_send_json(websocket, {"type": "error", "message": f"Failed to set model: {e}"})
                else:
                    await _safe_send_json(websocket, {"type": "error", "message": "Session is not active"})

            elif msg_type == "set_mode":
                mode = data.get("mode", "")
                logger.info("Set mode: session=%s mode=%s", session_id, mode)
                if manager.is_active(session_id):
                    manager.update_activity(session_id)
                    try:
                        await manager.set_mode(session_id, mode)
                        await _broadcast(session_id, {
                            "type": "mode_set", "session_id": session_id, "mode": mode,
                        })
                    except Exception as e:
                        logger.error("Set mode failed: %s", e, exc_info=True)
                        await _safe_send_json(websocket, {"type": "error", "message": f"Failed to set mode: {e}"})
                else:
                    await _safe_send_json(websocket, {"type": "error", "message": "Session is not active"})

            elif msg_type == "set_config":
                config_id = data.get("config_id", "")
                value = data.get("value")
                logger.info("Set config: session=%s config=%s value=%s", session_id, config_id, value)
                if manager.is_active(session_id):
                    manager.update_activity(session_id)
                    try:
                        await manager.set_config_option(session_id, config_id, value)
                        await _broadcast(session_id, {
                            "type": "config_set", "session_id": session_id,
                            "config_id": config_id, "value": value,
                        })
                    except Exception as e:
                        logger.error("Set config failed: %s", e, exc_info=True)
                        await _safe_send_json(websocket, {"type": "error", "message": f"Failed to set config: {e}"})
                else:
                    await _safe_send_json(websocket, {"type": "error", "message": "Session is not active"})

            elif msg_type == "approval_response":
                request_id = data.get("request_id")
                result = data.get("result", False)

                logger.info(
                    "Approval response received: request_id=%s result=%r (type: %s)",
                    request_id, result, type(result).__name__
                )                
                
                if not request_id:
                    await _safe_send_json(websocket, {
                        "type": "error", 
                        "message": "Missing request_id in approval_response"
                    })
                    continue

                # Get the AcpClient instance for this session
                state = manager.get_state(session_id)
                if state and hasattr(state.client, "resolve_approval"):
                    resolved = state.client.resolve_approval(request_id, result)
                    logger.info(
                        "Approval resolved: request_id=%s success=%s",
                        request_id, resolved
                    )
                    if not resolved:
                        logger.warning("Approval request %s not found or already resolved", request_id)
                        await _safe_send_json(websocket, {
                            "type": "error", 
                            "message": "Approval request expired or already answered"
                        })
                else:
                    logger.error("Cannot resolve approval: no active client state for session %s", session_id)

            else:
                logger.debug("Unknown message type: %s", msg_type)
                await _safe_send_json(websocket, {
                    "type": "error", "message": f"Unknown message type: {msg_type}",
                })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session=%s", session_id)
    except Exception as e:
        logger.error("WebSocket error: session=%s error=%s", session_id, e, exc_info=True)
    finally:
        is_last = await _unregister_connection(session_id, websocket)
        if is_last:
            await _stop_broadcast(session_id)
            _prompt_active.pop(session_id, None)
            logger.info("Session %s: last connection gone, broadcast stopped", session_id)
        logger.info("WebSocket handler exiting: session=%s", session_id)
