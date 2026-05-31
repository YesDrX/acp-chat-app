/**
 * ACP Chat App — WebSocket chat client
 *
 * Manages WebSocket connection to /ws/{session_id}, handles streaming
 * message display, user input, interrupt/stop controls, and settings.
 *
 * Integrates BubbleManager for smart grouping and FileUploadManager for file uploads.
 *
 * All events are logged with console.log prefixed with "[ACP Chat]" for
 * easy debugging in the browser console.
 */

(function () {
  "use strict";

  const PREFIX = "[ACP Chat]";
  const RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000, 30000];

  // DOM elements
  const chatContainer = document.getElementById("chat-messages");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("send-btn");
  const interruptBtn = document.getElementById("interrupt-btn");

  const statusDot = document.getElementById("connection-status");
  const statusText = document.getElementById("connection-text");
  const settingsPanel = document.getElementById("settings-panel");
  const settingsToggleBtn = document.getElementById("settings-toggle");
  const fileUploadBtn = document.getElementById("file-upload-btn");
  const fileInput = document.getElementById("file-input");
  const filePreviewContainer = document.getElementById("file-preview-container");
  const slashMenu = document.getElementById("slash-menu");

  // Read session_id from page data attribute
  const sessionId = document.body.getAttribute("data-session-id");
  if (!sessionId) {
    console.error(PREFIX, "No session_id found on page");
    alert("Error: No session ID configured.");
    return;
  }

  console.log(PREFIX, "Initializing chat for session:", sessionId);

  // State
  let ws = null;
  let reconnectAttempt = 0;
  let reconnectTimer = null;
  let isConnected = false;
  let isAgentActive = false;
  let bubbleManager = null;
  let fileUploadManager = null;
  let slashCommands = [];
  let slashActive = false;

  // --- Connection management ---

  function getWsUrl() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return protocol + "//" + window.location.host + "/ws/" + sessionId;
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      console.log(PREFIX, "WebSocket already connecting/connected, skipping");
      return;
    }

    const url = getWsUrl();
    console.log(PREFIX, "Connecting WebSocket:", url);
    setStatus("connecting");

    ws = new WebSocket(url);

    ws.onopen = function () {
      console.log(PREFIX, "WebSocket connected");
      isConnected = true;
      reconnectAttempt = 0;
      setStatus("connected");
    };

    ws.onmessage = function (event) {
      try {
        const msg = JSON.parse(event.data);
        // Suppress noise: tool_call_update with pending status are streaming args
        const isPendingToolUpdate = msg.type === 'tool_call_update' && msg.data && msg.data.status === 'pending';
        if (!isPendingToolUpdate) {
          // Only log the type for streaming chunks, full msg for others
          if (msg.type === 'agent_message_chunk' || msg.type === 'agent_thought_chunk') {
            // console.log(PREFIX, 'WS:', msg.type);
          } else {
            // console.log(PREFIX, 'WS:', msg.type, Object.keys(msg).join(','));
          }
        }
        handleMessage(msg);
      } catch (e) {
        console.error(PREFIX, "Failed to parse WebSocket message:", e, event.data);
      }
    };

    ws.onerror = function (error) {
      console.error(PREFIX, "WebSocket error:", error);
    };

    ws.onclose = function (event) {
      console.log(PREFIX, "WebSocket closed:", event.code, event.reason);
      isConnected = false;
      setStatus("disconnected");
      ws = null;

      // Attempt reconnection with exponential backoff
      if (reconnectAttempt < RECONNECT_DELAYS.length) {
        const delay = RECONNECT_DELAYS[reconnectAttempt];
        console.log(PREFIX, "Reconnecting in", delay, "ms (attempt", reconnectAttempt + 1, ")");
        reconnectTimer = setTimeout(function () {
          reconnectAttempt++;
          connect();
        }, delay);
      } else {
        console.log(PREFIX, "Max reconnection attempts reached");
        setStatus("failed");
      }
    };
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    reconnectAttempt = RECONNECT_DELAYS.length; // Prevent auto-reconnect
    if (ws) {
      ws.close();
      ws = null;
    }
  }

  function setStatus(status) {
    if (statusDot) {
      statusDot.className = "status-dot";
      if (status === "connected") {
        statusDot.classList.add("connected");
      } else if (status === "connecting") {
        statusDot.classList.add("connecting");
      } else if (status === "disconnected") {
        statusDot.classList.add("disconnected");
      } else if (status === "failed") {
        statusDot.classList.add("failed");
      }
    }
    if (statusText) {
      const labels = {
        connected: "Connected",
        connecting: "Connecting...",
        disconnected: "Disconnected",
        failed: "Connection failed",
      };
      statusText.textContent = labels[status] || status;
    }
  }

  // --- Bubble Manager Integration ---

  function initBubbleManager() {
    bubbleManager = new BubbleManager(chatContainer);
    console.log(PREFIX, "BubbleManager initialized");
  }

  // --- File Upload Manager Integration ---

  function initFileUploadManager() {
    if (fileInput && fileUploadBtn && filePreviewContainer) {
      fileUploadManager = new FileUploadManager({
        dropZone: chatContainer,
        fileInput: fileInput,
        triggerBtn: fileUploadBtn,
        previewContainer: filePreviewContainer,
        sessionId: sessionId,
      });
      console.log(PREFIX, "FileUploadManager initialized");
    }
  }

  // --- Message handling ---

  function handleMessage(msg) {
    switch (msg.type) {
      case "connected":
        handleConnected(msg);
        break;
      case "session_started":
        handleSessionStarted(msg);
        break;
      case "session_resumed":
        handleSessionResumed(msg);
        break;
      case "idle_shutdown":
        handleIdleShutdown(msg);
        break;
      case "agent_message_chunk":
        handleAgentChunk(msg);
        break;
      case "agent_thought_chunk":
        handleThoughtChunk(msg);
        break;
      case "tool_call":
        handleToolCall(msg);
        break;
      case "tool_call_update":
        handleToolCallUpdate(msg);
        break;
      case "available_commands_update":
        handleAvailableCommands(msg);
        break;
      case "approval_request": // <--- ADD THIS
        handleApprovalRequest(msg);
        break;        
      case "prompt_complete":
        handlePromptComplete(msg);
        break;
      case "cancelled":
        handleCancelled(msg);
        break;
      case "restarted":
        handleRestarted(msg);
        break;
      case "stopped":
        handleStopped(msg);
        break;
      case "model_set":
      case "mode_set":
      case "config_set":
        handleSettingChanged(msg);
        break;
      case "error":
        handleError(msg);
        break;
      case "heartbeat":
        // Silently ignore heartbeats
        break;
      case "pong":
        console.log(PREFIX, "Pong received");
        break;
      case "usage_update":
        handleUsageUpdate(msg);
        break
      default:
        console.log(PREFIX, "Unknown message type:", msg.type, msg);
    }
  }

  function handleConnected(msg) {
    console.log(PREFIX, "Connected to session:", msg.session_name, "agent:", msg.agent_name);
    addSystemMessage(chatContainer, "Connected to " + (msg.agent_name || "agent"));

    // Render message history
    if (msg.history && msg.history.length > 0) {
      console.log(PREFIX, "Rendering", msg.history.length, "history messages");
      for (const m of msg.history) {
        if (m.role === "user") {
          addUserBubble(chatContainer, m.content);
        } else if (m.role === "thinking") {
          // Render thinking as collapsible container
          if (bubbleManager && m.content) {
            bubbleManager.addThinkingChunk(msg.session_id, m.content);
            bubbleManager._finalizeThinking();
          }
        } else if (m.role === "tool_call") {
          // Render tool call card
          if (bubbleManager) {
            try {
              var tcData = JSON.parse(m.content);
              bubbleManager.addToolCall(msg.session_id, tcData);
            } catch(e) {}
          }
        } else {
          addAgentBubble(chatContainer, m.content);
        }
      }
    }

    if (msg.is_active) {
      addSystemMessage(chatContainer, "Session is already active");
      isAgentActive = true;
    }
    
    // Show resume button if session was idle/stopped
    if (msg.can_resume && !msg.is_active && msg.status === "idle") {
      showResumeBanner();
    }
    
    // Update status indicator
    if (msg.status) {
      updateSessionStatus(msg.status);
    }

    // Auto-start agent if not already active
    if (!msg.is_active) {
      console.log(PREFIX, "Auto-starting agent...");
      setTimeout(function() {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "resume" }));
          addSystemMessage(chatContainer, "Starting agent...");
        }
      }, 500);
    }
  }

  function handleSessionStarted(msg) {
    console.log(PREFIX, "Session started:", msg.acp_session_id);
    var startMsg = "Agent started (ACP session: " + msg.acp_session_id + ")";
    if (msg.was_resumed) {
      startMsg += " [resumed]";
    }
    addSystemMessage(chatContainer, startMsg);
    isAgentActive = true;
    hideResumeBanner();
    updateSessionStatus("active");
    if (bubbleManager) {
      bubbleManager.showTypingIndicator();
    }
    updateButtons();
  }

  function handleSessionResumed(msg) {
    console.log(PREFIX, "Session resumed:", msg.acp_session_id, "was_resumed:", msg.was_resumed);
    if (msg.was_resumed) {
      addSystemMessage(chatContainer, "Session resumed successfully (ACP: " + msg.acp_session_id + ")");
    } else {
      addSystemMessage(chatContainer, "Session started fresh (resume not available: " + msg.acp_session_id + ")");
    }
    hideResumeBanner();
    isAgentActive = true;
    updateSessionStatus("active");
    updateButtons();
  }

  function handleIdleShutdown(msg) {
    console.log(PREFIX, "Idle shutdown:", msg);
    var data = msg.data || {};
    var message = data.message || "Session shut down due to inactivity";
    addSystemMessage(chatContainer, "⚠ " + message, "error");
    isAgentActive = false;
    updateSessionStatus("idle");
    showIdleShutdownBanner(message);
    updateButtons();
  }

  function handleThoughtChunk(msg) {
    // Agent thinking/reasoning — show in collapsible container, NOT inline
    const data = msg.data || {};
    const content = data.content || {};
    let text = content.text || "";
    if (!text) return;
    
    // console.log(PREFIX, "Thought chunk (collapsible):", text.substring(0, 50));
    if (bubbleManager) {
      bubbleManager.addThinkingChunk(msg.session_id, text);
    }
  }

  function handleAgentChunk(msg) {
    const data = msg.data || {};
    const content = data.content || {};
    let text = content.text || "";
    const messageId = msg.data && msg.data.message_id ? msg.data.message_id : null;

    if (!text) return;

    // console.log(PREFIX, "Agent chunk:", text.substring(0, 40), "messageId:", messageId);

    if (bubbleManager) {
      bubbleManager.addTextChunk(msg.session_id, text, messageId);
    }
  }

  function handleToolCall(msg) {
    console.log(PREFIX, "handleToolCall:", msg.data.tool_call_id || msg.data.id, "title:", msg.data.title || msg.data.name, "type:", msg.type);
    const data = msg.data || {};
    if (bubbleManager) {
      bubbleManager.addToolCall(msg.session_id, data);
    }
  }

  function handleToolCallUpdate(msg) {
    console.log(PREFIX, "handleToolCallUpdate:", msg.data.tool_call_id || msg.data.id, "status:", msg.data.status, "type:", msg.type);
    const data = msg.data || {};
    if (bubbleManager) {
      bubbleManager.updateToolCall(msg.session_id, data);
    }
  }

  function handleAvailableCommands(msg) {
    console.log(PREFIX, "Available commands:", msg);
    const data = msg.data || {};
    const commands = data.available_commands || [];

    slashCommands = commands;

    if (bubbleManager) {
      bubbleManager.addAvailableCommands(msg.session_id, commands);
    }
  }

  function handleApprovalRequest(msg) {
      console.log(PREFIX, "Approval request received:", msg.request_id, msg.tool_call?.name);
      
      const { session_id, request_id, tool_call, options } = msg;
      
      // Try BubbleManager first (if implemented)
      if (bubbleManager && typeof bubbleManager.addApprovalRequest === "function") {
          bubbleManager.addApprovalRequest(session_id, request_id, tool_call, options);
          return;
      }
      
      // Fallback to global renderer (defined in session_chat.html)
      if (typeof window.renderApprovalRequest === "function") {
          window.renderApprovalRequest(request_id, tool_call);
          return;
      }
      
      // Last resort: system message
      const toolName = tool_call?.name || tool_call?.function?.name || "Unknown Tool";
      addSystemMessage(chatContainer, `⚠️ Permission required: ${toolName} (UI not implemented)`, "warning");
  }

  function sendApprovalResponse(requestId, result) {
    if (!isConnected) {
        console.error(PREFIX, "Cannot send approval response: WebSocket not connected");
        return;
    }
    console.log(PREFIX, "Sending approval response:", requestId, "Result:", result);
    
    ws.send(JSON.stringify({
        type: "approval_response",
        request_id: requestId,
        result: result
    }));
  }

  function handlePromptComplete(msg) {
    console.log(PREFIX, "Prompt complete:", msg.response);

    if (bubbleManager) {
      bubbleManager.finalize();
    }

    isAgentActive = false;

    if (msg.response && msg.response.stop_reason) {
      addSystemMessage(chatContainer, "Done (" + msg.response.stop_reason + ")");
    }

    // Clear file uploads after completion
    if (fileUploadManager) {
      fileUploadManager.clearAll();
    }

    updateButtons();
  }

  function handleCancelled(msg) {
    console.log(PREFIX, "Agent cancelled");

    if (bubbleManager) {
      bubbleManager.finalize();
    }

    isAgentActive = false;
    addSystemMessage(chatContainer, "Cancelled");
    updateButtons();
  }

  function handleStopped(msg) {
    console.log(PREFIX, "Agent stopped");

    if (bubbleManager) {
      bubbleManager.finalize();
    }

    isAgentActive = false;
    addSystemMessage(chatContainer, "Agent stopped");
    updateSessionStatus("stopped");
    showResumeBanner();
    updateButtons();
  }

  function handleRestarted(msg) {
    console.log(PREFIX, "Agent restarted:", msg);

    if (bubbleManager) {
      bubbleManager.finalize();
    }

    isAgentActive = false;
    addSystemMessage(chatContainer, "Agent process restarted (ACP: " + (msg.acp_session_id || "") + ")");
    updateSessionStatus("active");
    updateButtons();
  }

  function handleSettingChanged(msg) {
    console.log(PREFIX, "Setting changed:", msg);
    addSystemMessage(chatContainer, "Setting updated: " + JSON.stringify(msg));
  }

  function handleUsageUpdate(msg) {
    console.log(PREFIX, "Usage update:", msg.data);
    const session_tokens = document.getElementById("session-tokens");
    if (session_tokens && msg.data && msg.data.used) {
      session_tokens.textContent = msg.data.used;
    }
    const session_last_update = document.getElementById("session-last-update");
    if (session_last_update && msg.timestamp) {
      const date = new Date(msg.timestamp);
      session_last_update.textContent = date.toLocaleString();
    }
  }

  function handleError(msg) {
    console.error(PREFIX, "Server error:", msg.message);
    addSystemMessage(chatContainer, "Error: " + msg.message, "error");
  }

  // --- Slash Command Menu ---

  function showSlashMenu() {
    if (!slashMenu) return;
    if (slashCommands.length === 0) return;

    slashMenu.innerHTML = "";

    slashCommands.forEach(function (cmd, index) {
      const cmdName = typeof cmd === "string" ? cmd : cmd.name || cmd.command || String(cmd);
      const cmdDesc = typeof cmd === "object" ? (cmd.description || "") : "";

      const item = document.createElement("div");
      item.className = "slash-command-item";
      item.setAttribute("data-index", index);

      const nameSpan = document.createElement("span");
      nameSpan.className = "slash-command-name";
      nameSpan.textContent = cmdName;

      const descSpan = document.createElement("span");
      descSpan.className = "slash-command-desc";
      descSpan.textContent = cmdDesc;

      item.appendChild(nameSpan);
      item.appendChild(descSpan);

      item.addEventListener("click", function () {
        selectSlashCommand(cmdName);
      });

      slashMenu.appendChild(item);
    });

    slashMenu.classList.add("visible");
    slashActive = true;
    slashMenu._selectedIndex = 0;
    updateSlashSelection();

    console.log(PREFIX, "Slash menu shown with", slashCommands.length, "commands");
  }

  function hideSlashMenu() {
    if (!slashMenu) return;
    slashMenu.classList.remove("visible");
    slashMenu.innerHTML = "";
    slashActive = false;
    slashMenu._selectedIndex = 0;
    console.log(PREFIX, "Slash menu hidden");
  }

  function updateSlashSelection() {
    if (!slashMenu) return;
    const items = slashMenu.querySelectorAll(".slash-command-item");
    items.forEach(function (item, i) {
      if (i === slashMenu._selectedIndex) {
        item.classList.add("selected");
      } else {
        item.classList.remove("selected");
      }
    });
  }

  function selectSlashCommand(cmdName) {
    console.log(PREFIX, "Slash command selected:", cmdName);
    chatInput.value = cmdName + " ";
    hideSlashMenu();
    chatInput.focus();

    // Move cursor to end
    var len = chatInput.value.length;
    chatInput.setSelectionRange(len, len);
  }

  function navigateSlashMenu(direction) {
    if (!slashMenu || !slashActive) return;

    const count = slashCommands.length;
    if (direction === "down") {
      slashMenu._selectedIndex = (slashMenu._selectedIndex + 1) % count;
    } else if (direction === "up") {
      slashMenu._selectedIndex = (slashMenu._selectedIndex - 1 + count) % count;
    }
    updateSlashSelection();
  }

  // --- User input ---

  async function sendMessage(text) {
    text = text.trim();
    if (!text) return;
    if (!isConnected) {
      addSystemMessage(chatContainer, "Not connected. Please wait...", "error");
      return;
    }

    console.log(PREFIX, "Sending prompt:", text.substring(0, 80));

    // Wait for any in-progress uploads (files upload immediately on selection,
    // so most are already done — this just catches any stragglers)
    let uploadedRefs = [];
    if (fileUploadManager) {
      uploadedRefs = await fileUploadManager.uploadAll();
      console.log(PREFIX, "Files attached to prompt:", uploadedRefs.length);
    }

    // Display user message
    createUserBubble(text, uploadedRefs);

    // Prepare message payload
    const payload = { type: "prompt", text: text };
    if (uploadedRefs.length > 0) {
      payload.files = uploadedRefs;
    }

    // Reset agent state
    if (bubbleManager) {
      bubbleManager.finalize();
    }
    isAgentActive = true;
    updateButtons();

    // Send via WebSocket
    ws.send(JSON.stringify(payload));

    // Clear input
    chatInput.value = "";
    chatInput.style.height = "auto";
  }

  function createUserBubble(text, files) {
    const row = document.createElement("div");
    row.className = "message-row user-row bubble-enter";

    const bubble = document.createElement("div");
    bubble.className = "message-bubble user-bubble";

    bubble.textContent = text;

    // Show attached files
    if (files && files.length > 0) {
      const fileList = document.createElement("div");
      fileList.className = "attached-files";

      files.forEach(function (f) {
        const fileTag = document.createElement("span");
        fileTag.className = "attached-file-tag";
        fileTag.textContent = "📎 " + (f.filename || f.name);
        fileList.appendChild(fileTag);
      });

      bubble.appendChild(document.createElement("br"));
      bubble.appendChild(fileList);
    }

    row.appendChild(bubble);
    chatContainer.appendChild(row);
    chatContainer.scrollTop = chatContainer.scrollHeight;
  }

  function sendCancel() {
    if (!isConnected) return;
    console.log(PREFIX, "Sending cancel");
    ws.send(JSON.stringify({ type: "cancel" }));
  }

  function sendRestart() {
    if (!isConnected) return;
    console.log(PREFIX, "Sending restart");
    ws.send(JSON.stringify({ type: "restart" }));
    addSystemMessage(chatContainer, "Restarting agent process...");
  }

  function sendPing() {
    if (!isConnected) return;
    ws.send(JSON.stringify({ type: "ping" }));
  }

  function updateButtons() {
    // if (interruptBtn) {
    //   interruptBtn.disabled = !isAgentActive;
    // }
  }

  // --- Resume & Idle banners ---

  function showResumeBanner() {
    // Remove existing banner
    hideResumeBanner();

    var banner = document.createElement("div");
    banner.id = "resume-banner";
    banner.className = "flex items-center gap-2 px-3 py-2 bg-yellow-900/30 border-b border-yellow-800 text-sm";
    banner.innerHTML = `
      <span class="text-yellow-400">⚠</span>
      <span class="text-yellow-300 flex-1">Session is not active. Send a message or click Resume to reconnect.</span>
      <button id="resume-btn" class="px-3 py-1 text-xs bg-yellow-700 hover:bg-yellow-600 rounded-lg transition-colors">Resume</button>
    `;

    // Insert before chat messages
    if (chatContainer && chatContainer.parentNode) {
      chatContainer.parentNode.insertBefore(banner, chatContainer);
    }

    // Wire resume button
    var resumeBtn = document.getElementById("resume-btn");
    if (resumeBtn) {
      resumeBtn.addEventListener("click", function () {
        sendResume();
      });
    }
  }

  function hideResumeBanner() {
    var banner = document.getElementById("resume-banner");
    if (banner) banner.remove();

    var idleBanner = document.getElementById("idle-shutdown-banner");
    if (idleBanner) idleBanner.remove();
  }

  function showIdleShutdownBanner(message) {
    hideResumeBanner();

    var banner = document.createElement("div");
    banner.id = "idle-shutdown-banner";
    banner.className = "flex items-center gap-2 px-3 py-2 bg-red-900/30 border-b border-red-800 text-sm";
    banner.innerHTML = `
      <span class="text-red-400">⏹</span>
      <span class="text-red-300 flex-1">${AcpUtils.escapeHtml(message)}</span>
      <button id="idle-resume-btn" class="px-3 py-1 text-xs bg-red-700 hover:bg-red-600 rounded-lg transition-colors">Reconnect</button>
    `;

    if (chatContainer && chatContainer.parentNode) {
      chatContainer.parentNode.insertBefore(banner, chatContainer);
    }

    var resumeBtn = document.getElementById("idle-resume-btn");
    if (resumeBtn) {
      resumeBtn.addEventListener("click", function () {
        sendResume();
      });
    }

    // Disable input
    if (chatInput) chatInput.disabled = true;
    if (sendBtn) sendBtn.disabled = true;
  }

  function updateSessionStatus(status) {
    // Update the status indicator
    var statusBadge = document.getElementById("session-status-badge");
    if (!statusBadge) return;

    var statusMap = {
      "active": { text: "Active", cls: "bg-green-800 text-green-300" },
      "idle": { text: "Idle", cls: "bg-yellow-800 text-yellow-300" },
      "stopped": { text: "Stopped", cls: "bg-red-800 text-red-300" },
      "created": { text: "New", cls: "bg-gray-700 text-gray-300" },
      "resuming": { text: "Resuming…", cls: "bg-blue-800 text-blue-300" },
    };

    var info = statusMap[status] || statusMap["created"];
    statusBadge.textContent = info.text;
    statusBadge.className = "px-2 py-0.5 rounded-full text-xs " + info.cls;

    // Enable/disable input
    if (chatInput) chatInput.disabled = false;
    if (sendBtn) sendBtn.disabled = false;
  }

  function sendResume() {
    if (!isConnected) return;
    console.log(PREFIX, "Sending resume request");
    ws.send(JSON.stringify({ type: "resume" }));
  }

  // --- Settings panel ---

  function toggleSettings() {
    if (settingsPanel) {
      var isOpen = settingsPanel.classList.contains("open");
      if (isOpen) {
        settingsPanel.classList.remove("open");
        console.log(PREFIX, "Settings panel closed");
      } else {
        settingsPanel.classList.add("open");
        console.log(PREFIX, "Settings panel opened");
      }
    }
  }

  function sendSetModel(modelId) {
    if (!isConnected) return;
    console.log(PREFIX, "Setting model:", modelId);
    ws.send(JSON.stringify({ type: "set_model", model: modelId }));
  }

  function sendSetMode(modeId) {
    if (!isConnected) return;
    console.log(PREFIX, "Setting mode:", modeId);
    ws.send(JSON.stringify({ type: "set_mode", mode: modeId }));
  }

  function sendSetConfig(configId, value) {
    if (!isConnected) return;
    console.log(PREFIX, "Setting config:", configId, value);
    ws.send(JSON.stringify({ type: "set_config", config_id: configId, value: value }));
  }

  // --- Event listeners ---

  if (sendBtn) {
    sendBtn.addEventListener("click", function () {
      sendMessage(chatInput.value);
    });
  }

  if (chatInput) {
    chatInput.addEventListener("keydown", function (e) {
      // Slash command navigation
      if (slashActive) {
        if (e.key === "ArrowDown") {
          e.preventDefault();
          navigateSlashMenu("down");
          return;
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          navigateSlashMenu("up");
          return;
        } else if (e.key === "Enter") {
          e.preventDefault();
          const items = slashMenu.querySelectorAll(".slash-command-item");
          const selected = slashMenu._selectedIndex || 0;
          if (items[selected]) {
            items[selected].click();
          }
          return;
        } else if (e.key === "Escape") {
          e.preventDefault();
          hideSlashMenu();
          return;
        }
      }

      // Send on Enter (without Shift)
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        hideSlashMenu();
        sendMessage(chatInput.value);
        return;
      }
    });

    // Auto-resize and slash detection
    chatInput.addEventListener("input", function () {
      this.style.height = "auto";
      this.style.height = Math.min(this.scrollHeight, 200) + "px";

      // Detect slash command trigger: cursor at start and first char is /
      var val = this.value;
      var cursorPos = this.selectionStart;
      if (cursorPos === 1 && val === "/") {
        console.log(PREFIX, "Slash detected, showing command menu");
        showSlashMenu();
      } else if (slashActive && (!val || val[0] !== "/" || val.indexOf(" ") > 0)) {
        hideSlashMenu();
      }
    });
  }

  if (interruptBtn) {
    interruptBtn.addEventListener("click", sendCancel);
  }

  // Settings toggle buttons (both header and panel)
  var allSettingsToggles = document.querySelectorAll("#settings-toggle");
  allSettingsToggles.forEach(function (btn) {
    btn.addEventListener("click", toggleSettings);
  });

  // Settings panel buttons
  document.querySelectorAll("[data-action]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var action = this.getAttribute("data-action");
      var value = this.getAttribute("data-value");
      if (action === "set-model") sendSetModel(value);
      else if (action === "set-mode") sendSetMode(value);
      else if (action === "set-config") {
        var configId = this.getAttribute("data-config-id");
        var configValue = this.getAttribute("data-config-value");
        // Try to parse as bool
        if (configValue === "true") configValue = true;
        else if (configValue === "false") configValue = false;
        sendSetConfig(configId, configValue);
      }
    });
  });

  // --- Initialize ---

  initBubbleManager();
  initFileUploadManager();
  updateButtons();
  connect();

  // Reconnect on visibility change
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && !isConnected) {
      console.log(PREFIX, "Tab visible, attempting reconnect");
      reconnectAttempt = 0;
      connect();
    }
  });

  // Ping every 30 seconds to keep connection alive
  setInterval(function () {
    if (isConnected) {
      sendPing();
    }
  }, 30000);

  console.log(PREFIX, "Chat client initialized");

  // Expose for debugging and settings panel
  window.acpChat = {
    connect: connect,
    disconnect: disconnect,
    sendMessage: sendMessage,
    sendCancel: sendCancel,
    sendRestart: sendRestart,
    sendSetModel: sendSetModel,
    sendSetMode: sendSetMode,
    sendSetConfig: sendSetConfig,
    sendApprovalResponse: sendApprovalResponse,
    getSessionId: function () { return sessionId; },
    isConnected: function () { return isConnected; },
  };
})();
