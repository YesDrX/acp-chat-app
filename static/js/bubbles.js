/**
 * ACP Chat App — Bubble Manager
 *
 * Groups consecutive ACP text chunks into a single bubble and renders
 * tool calls as collapsible JSON cards. Pure vanilla JS, no framework.
 */

(function () {
  "use strict";

  const PREFIX = "[ACP Chat Bubbles]";

  /**
   * BubbleManager — manages message bubble creation and grouping.
   */
  class BubbleManager {
    constructor(chatContainer) {
      /** @type {HTMLElement} */
      this.container = chatContainer;

      /** @type {HTMLElement|null} Current text bubble for appending */
      this._currentTextBubble = null;

      /** @type {string|null} Current messageId being streamed */
      this._currentMessageId = null;

      /** @type {Array} Available slash commands from agent */
      this._availableCommands = [];

      /** @type {HTMLElement|null} Typing indicator element */
      this._typingIndicator = null;

      /** @type {Object<string, HTMLElement>} Tool call cards by id */
      this._toolCallCards = {};

      /** @type {HTMLElement|null} Current tool group container (groups adjacent tool calls) */
      this._toolGroupContainer = null;

      /** @type {number} Count of tools in current group */
      this._toolGroupCount = 0;

      console.log(PREFIX, "BubbleManager initialized");
    }

    // --- Public API ---

    /**
     * Add or append a text chunk from the agent.
     */
    addTextChunk(sessionId, text, messageId) {
      if (!text) return;
      // console.log(PREFIX, "addTextChunk:", { sessionId, textLen: text.length });

      if (this._currentTextBubble) {
        this._appendText(text);
      } else {
        this._currentTextBubble = this._createTextBubble();
        this._appendText(text);
      }

      // this._scrollToBottom();
    }

    /**
     * Add thinking/reasoning text in a collapsible container.
     */
    addThinkingChunk(sessionId, text) {
      if (!text || !text.trim()) return;
      // console.log(PREFIX, "addThinkingChunk:", text.substring(0, 50));

      if (!this._thinkingContainer) {
        this._thinkingContainer = this._createThinkingContainer();
      }

      const body = this._thinkingContainer.querySelector(".thinking-body-text");
      if (body) {
        // body.textContent += (body.textContent && !/[\s]$/.test(body.textContent) ? ' ' : '') + text;
        body.textContent += text;
      }

      // this._scrollToBottom();
    }

    /**
     * Add a tool call card. Adjacent tool calls are grouped into a
     * collapsed-by-default container to avoid cluttering the view.
     */
    addToolCall(sessionId, toolCallData) {
      const toolCallId = toolCallData.tool_call_id || toolCallData.id || "tool-" + Date.now();
      console.log(PREFIX, "addToolCall:", toolCallId, "title:", toolCallData.title || toolCallData.name, "status:", toolCallData.status, "type:", toolCallData.session_update);

      // Dedup: if this tool call was already rendered (e.g. duplicate from history), skip
      if (this._toolCallCards[toolCallId]) {
        console.log(PREFIX, "addToolCall: skipping duplicate", toolCallId);
        return this._toolCallCards[toolCallId];
      }

      const title = toolCallData.title || toolCallData.name || "Tool Call";
      const status = toolCallData.status || "pending";
      const card = this._createToolCallCard(toolCallId, title, status, toolCallData);

      this._toolCallCards[toolCallId] = card;

      // Group adjacent tool calls into a collapsed container
      this._addToToolGroup(card);

      // this._scrollToBottom();
    }

    /**
     * Add a tool call card to the current group, creating one if needed.
     */
    _addToToolGroup(card) {
      if (!this._toolGroupContainer) {
        console.log(PREFIX, "_addToToolGroup: CREATING NEW GROUP");
        this._toolGroupContainer = this._createToolGroup();
        this._toolGroupCount = 0;
      } else {
        console.log(PREFIX, "_addToToolGroup: ADDING TO EXISTING GROUP (count=" + this._toolGroupCount + ")");
      }
      this._toolGroupCount++;
      const body = this._toolGroupContainer.querySelector(".tool-group-body");
      if (body) {
        body.appendChild(card);
      }
      this._updateToolGroupHeader();
    }

    /** Create the collapsed tool group container. */
    _createToolGroup() {
      const row = document.createElement("div");
      row.className = "message-row agent-row bubble-enter tool-group-container";

      const card = document.createElement("div");
      card.className = "tool-group-card";

      const header = document.createElement("div");
      header.className = "tool-group-header";

      const toggle = document.createElement("span");
      toggle.className = "tool-group-toggle";
      toggle.textContent = "▶";

      const icon = document.createElement("span");
      icon.className = "tool-group-icon";
      icon.textContent = "🔧";

      const label = document.createElement("span");
      label.className = "tool-group-label";
      label.textContent = "Tools";

      header.appendChild(toggle);
      header.appendChild(icon);
      header.appendChild(label);

      header.addEventListener("click", function () {
        const body = card.querySelector(".tool-group-body");
        const tgl = card.querySelector(".tool-group-toggle");
        if (body) body.classList.toggle("collapsed");
        if (tgl) tgl.textContent = body && body.classList.contains("collapsed") ? "▶" : "▼";
      });

      const body = document.createElement("div");
      body.className = "tool-group-body collapsed";

      card.appendChild(header);
      card.appendChild(body);
      row.appendChild(card);

      // Insert before text bubble if one exists, otherwise append
      if (this._currentTextBubble && this._currentTextBubble.parentNode) {
        this.container.insertBefore(row, this._currentTextBubble.parentNode);
      } else {
        this.container.appendChild(row);
      }

      return row;
    }

    /** Update the tool group header count. */
    _updateToolGroupHeader() {
      if (!this._toolGroupContainer) return;
      const label = this._toolGroupContainer.querySelector(".tool-group-label");
      if (label) {
        label.textContent = "Tools (" + this._toolGroupCount + ")";
      }
    }

    /** Finalize the current tool group (collapse it, reset tracking). */
    _finishToolGroup() {
      if (!this._toolGroupContainer) return;
      console.log(PREFIX, "_finishToolGroup: closing group with " + this._toolGroupCount + " tools.");
      this._updateToolGroupHeader();
      this._toolGroupContainer = null;
      this._toolGroupCount = 0;
    }

    /**
     * Update an existing tool call card.
     */
    updateToolCall(sessionId, updateData) {
      const toolCallId = updateData.tool_call_id || updateData.id;
      console.log(PREFIX, "updateToolCall:", toolCallId, "status:", updateData.status);

      if (!toolCallId || !this._toolCallCards[toolCallId]) {
        return;
      }

      const card = this._toolCallCards[toolCallId];
      const status = updateData.status || "in_progress";

      const badge = card.querySelector(".tool-status-badge");
      if (badge) {
        badge.textContent = status;
        badge.className = "tool-status-badge status-" + status;
      }

      const contentEl = card.querySelector(".tool-json-content");
      if (contentEl && updateData) {
        contentEl.textContent = JSON.stringify(updateData, null, 2);
      }

      // this._scrollToBottom();
    }

    /**
     * Store available commands for slash command menu.
     */
    addAvailableCommands(sessionId, commands) {
      this._availableCommands = commands || [];
      window.dispatchEvent(new CustomEvent("acp:commands-updated", {
        detail: { commands: this._availableCommands },
      }));
    }

    /**
     * Clear all bubbles.
     */
    clearAll() {
      console.log(PREFIX, "clearAll — clearing all bubbles");
      while (this.container.firstChild) {
        this.container.removeChild(this.container.firstChild);
      }
      this._currentTextBubble = null;
      this._currentMessageId = null;
      this._thinkingContainer = null;
      this._toolCallCards = {};
      this._toolGroupContainer = null;
      this._toolGroupCount = 0;
      this._hideTypingIndicator();
    }

    /**
     * Finish the current streaming text bubble.
     */
    finalize() {
      console.log(PREFIX, "finalize: finishing current bubble.");
      this._finishToolGroup();
      this._finishTextBubble();
      this._finalizeThinking();
      this._hideTypingIndicator();

      // Catch-all safety: Render any lingering streaming containers
      try {
        const remainingStreamingBubbles = this.container.querySelectorAll(".agent-bubble._streaming");
        remainingStreamingBubbles.forEach(bubble => {
          bubble.classList.remove("_streaming");
          const textToRender = bubble._rawText || bubble.textContent;
          if (textToRender) {
            bubble.innerHTML = (typeof AcpUtils !== "undefined" && AcpUtils.renderMarkdown) 
              ? AcpUtils.renderMarkdown(textToRender) 
              : marked.parse(textToRender);
          }
        });
      } catch (e) {
        console.error(PREFIX, "Error in finalization catch-all:", e);
      }
    }

    /**
     * Show typing indicator.
     */
    showTypingIndicator() {
      if (!this._typingIndicator) {
        this._typingIndicator = this._createTypingIndicator();
      }
      this._typingIndicator.style.display = "flex";
      // this._scrollToBottom();
    }

    // --- Private: Text Bubbles ---

    _createTextBubble() {
      const row = document.createElement("div");
      row.className = "message-row agent-row bubble-enter";

      const bubble = document.createElement("div");
      bubble.className = "message-bubble agent-bubble _streaming";

      row.appendChild(bubble);
      this.container.appendChild(row);
      return bubble;
    }

    _appendText(text) {
      if (!this._currentTextBubble) return;

      if (!this._currentTextBubble._rawText) this._currentTextBubble._rawText = '';
      
      // Directly append chunks to preserve raw markdown data integrity
      this._currentTextBubble._rawText += text;

      // FIX: Preserve single newlines if a table format (|) is present in the text block
      let display = this._currentTextBubble._rawText;
      if (!display.includes('|')) {
        display = display
          .replace(/\n\n/g, '\u0000')
          .replace(/\n/g, ' ')
          .replace(/\u0000/g, '\n\n');
      }

      this._currentTextBubble.textContent = display;
    }

    _finishTextBubble() {
      if (!this._currentTextBubble) return;

      const bubble = this._currentTextBubble;
      this._currentTextBubble = null;

      bubble.classList.remove('_streaming');

      let textToRender = bubble._rawText || bubble.textContent;
      if (textToRender) {
        // Clean trailing system indicators
        textToRender = textToRender.replace(/Done\s*\(\s*end_turn\s*\)/g, '').trim();

        // FIX: Ensure spaces around **bold** spans adjacent to word characters.
        // LLMs sometimes emit "word**bold**word" without spaces, causing the
        // rendered HTML "word<strong>bold</strong>word" to run together visually.
        // Pass 1: add space before opening ** when preceded by a word char
        textToRender = textToRender.replace(/(\w)\*\*(.+?)\*\*/g, '$1 **$2**');
        // Pass 2: add space after closing ** when followed by a word char
        textToRender = textToRender.replace(/\*\*(.+?)\*\*(\w)/g, '**$1** $2');

        if (typeof AcpUtils !== "undefined" && AcpUtils.renderMarkdown) {
          bubble.innerHTML = AcpUtils.renderMarkdown(textToRender);
        } else if (typeof marked !== "undefined") {
          bubble.innerHTML = marked.parse(textToRender);
        }
      }

      this._scrollToBottom();
    }

    _finalizeThinking() {
      if (this._thinkingContainer) {
        const label = this._thinkingContainer.querySelector(".thinking-header-text");
        if (label) label.textContent = "Thought process";
        const body = this._thinkingContainer.querySelector(".thinking-body");
        if (body) body.classList.add("collapsed");
        const toggle = this._thinkingContainer.querySelector(".thinking-toggle");
        if (toggle) toggle.textContent = "▶";
        this._thinkingContainer = null;
      }
    }

    _createThinkingContainer() {
      const row = document.createElement("div");
      row.className = "message-row agent-row bubble-enter thinking-container";

      const card = document.createElement("div");
      card.className = "thinking-card";

      const header = document.createElement("div");
      header.className = "thinking-header";
      header.onclick = function() {
        const body = this.parentElement.querySelector(".thinking-body");
        const toggle = this.querySelector(".thinking-toggle");
        if (body) body.classList.toggle("collapsed");
        if (toggle) toggle.textContent = body.classList.contains("collapsed") ? "▶" : "▼";
      };

      const toggle = document.createElement("span");
      toggle.className = "thinking-toggle";
      toggle.textContent = "▼";

      const icon = document.createElement("span");
      icon.className = "thinking-icon";
      icon.textContent = "💭";

      const label = document.createElement("span");
      label.className = "thinking-header-text";
      label.textContent = "Thinking...";

      header.appendChild(toggle);
      header.appendChild(icon);
      header.appendChild(label);

      const body = document.createElement("div");
      body.className = "thinking-body";

      const text = document.createElement("div");
      text.className = "thinking-body-text";

      body.appendChild(text);
      card.appendChild(header);
      card.appendChild(body);
      row.appendChild(card);
      this.container.appendChild(row);
      return row;
    }

    _hideTypingIndicator() {
      if (this._typingIndicator) {
        this._typingIndicator.style.display = "none";
      }
      if (this._currentTextBubble) {
        this._currentTextBubble.classList.remove("_streaming");
      }
    }

    _createToolCallCard(toolCallId, title, status, data) {
      const card = document.createElement("div");
      card.className = "tool-call-card";
      card.id = "tool-" + toolCallId;

      const header = document.createElement("div");
      header.className = "tool-call-header";

      const icon = document.createElement("span");
      icon.className = "tool-call-icon";
      icon.textContent = "🔧";

      const nameEl = document.createElement("span");
      nameEl.className = "tool-call-name";
      nameEl.textContent = title;

      const badge = document.createElement("span");
      badge.className = "tool-status-badge status-" + status;
      badge.textContent = status;

      const toggle = document.createElement("span");
      toggle.className = "tool-call-toggle";
      toggle.textContent = "▶";

      header.appendChild(icon);
      header.appendChild(nameEl);
      header.appendChild(badge);

      const spacer = document.createElement("span");
      spacer.style.flex = "1";
      header.appendChild(spacer);
      header.appendChild(toggle);

      const body = document.createElement("div");
      body.className = "tool-call-body collapsed";

      const jsonPre = document.createElement("pre");
      jsonPre.className = "tool-json-content";
      jsonPre.innerHTML = this._syntaxHighlightJSON(data);
      body.appendChild(jsonPre);

      header.addEventListener("click", function () {
        const isCollapsed = body.classList.contains("collapsed");
        if (isCollapsed) {
          body.classList.remove("collapsed");
          toggle.textContent = "▼";
        } else {
          body.classList.add("collapsed");
          toggle.textContent = "▶";
        }
      });

      card.appendChild(header);
      card.appendChild(body);
      return card;
    }

    _createTypingIndicator() {
      const row = document.createElement("div");
      row.className = "message-row agent-row";

      const indicator = document.createElement("div");
      indicator.className = "typing-indicator-bubble";

      for (let i = 0; i < 3; i++) {
        const dot = document.createElement("span");
        indicator.appendChild(dot);
      }

      row.appendChild(indicator);
      this.container.appendChild(row);
      return row;
    }

    _syntaxHighlightJSON(data) {
      try {
        const json = JSON.stringify(data, null, 2);
        return json.replace(
          /("(\\u[\da-fA-F]{4}|\\[^u]|[^"\\])*"(\s*:)?|\b(true|false|null)\b|-?\d+(\.\d+)?([eE][+-]?\d+)?)/g,
          function (match) {
            let cls = "json-number";
            if (/^"/.test(match)) {
              if (/:$/.test(match)) {
                cls = "json-key";
              } else {
                cls = "json-string";
              }
            } else if (/true|false/.test(match)) {
              cls = "json-boolean";
            } else if (/null/.test(match)) {
              cls = "json-null";
            }
            return '<span class="' + cls + '">' + match + '</span>';
          }
        );
      } catch (e) {
        return String(data);
      }
    }

    _scrollToBottom() {
      if (this.container) {
        requestAnimationFrame(() => {
          this.container.scrollTop = this.container.scrollHeight;
        });
      }
    }
  
  /**
   * Show an approval request card with Approve/Deny buttons.
   */
  addApprovalRequest(sessionId, requestId, toolCall, options) {
    console.log(PREFIX, "addApprovalRequest:", requestId, toolCall?.title);
    console.log(PREFIX, toolCall);
    console.log(PREFIX, options);
    
    const toolName = 
        toolCall?.title ||                          
        toolCall?.name || 
        toolCall?.function?.name || 
        (toolCall?.kind === "switch_mode" ? "Plan Review" : "") ||  
        "Permission Request";
    
    const toolArgs = toolCall?.rawInput || toolCall?.arguments || toolCall?.input || toolCall || {};
    const formattedArgs = typeof toolArgs === "object" 
        ? JSON.stringify(toolArgs, null, 2) 
        : String(toolArgs);
    
    const row = document.createElement("div");
    row.className = "message-row agent-row bubble-enter";
    
    const card = document.createElement("div");
    card.className = "tool-call-card approval-card";
    card.id = "approval-" + requestId;
    
    const header = document.createElement("div");
    header.className = "tool-call-header";
    
    const icon = document.createElement("span");
    icon.className = "tool-call-icon";
    icon.textContent = "🔐";
    
    const nameEl = document.createElement("span");
    nameEl.className = "tool-call-name";
    nameEl.textContent = "Permission: " + toolName;
    
    header.appendChild(icon);
    header.appendChild(nameEl);
    
    const body = document.createElement("div");
    body.className = "tool-call-body";
    
    const jsonPre = document.createElement("pre");
    jsonPre.className = "tool-json-content";
    jsonPre.textContent = formattedArgs;
    body.appendChild(jsonPre);
    
    const actions = document.createElement("div");
    actions.className = "approval-actions flex gap-2 justify-end mt-3";
    
    // 🔑 Use actual options from backend, or fallback to simple Approve/Deny
    if (options && Array.isArray(options) && options.length > 0) {
        options.forEach(opt => {
            const btn = document.createElement("button");
            const isAllow = opt.kind?.includes("allow");
            btn.className = isAllow 
                ? "px-4 py-1.5 bg-green-600 hover:bg-green-500 text-white text-sm rounded-lg transition"
                : "px-4 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg transition";
            btn.textContent = opt.name || (isAllow ? "Allow" : "Deny");
            btn.onclick = function() {
                // Catch snake_case, camelCase, or 'id' fallbacks
                const targetOptionId = opt.optionId || opt.option_id || opt.id;
                
                // Route directly to the chat client method if available
                if (window.acpChat && typeof window.acpChat.sendApprovalResponse === "function") {
                    window.acpChat.sendApprovalResponse(requestId, targetOptionId);
                } else if (typeof window.respondToApproval === "function") {
                    window.respondToApproval(requestId, targetOptionId);
                } else {
                    console.error("[ACP Chat Bubbles] No approval handler found in window!");
                }
                
                _markApprovalResolved(card, opt.name || targetOptionId);
            };
            actions.appendChild(btn);
        });
    } else {
        // Fallback to simple buttons
        const denyBtn = document.createElement("button");
        denyBtn.className = "px-4 py-1.5 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded-lg transition";
        denyBtn.textContent = "Deny";
        denyBtn.onclick = function() {
            window.respondToApproval?.(requestId, false);
            _markApprovalResolved(card, false);
        };
        
        const approveBtn = document.createElement("button");
        approveBtn.className = "px-4 py-1.5 bg-green-600 hover:bg-green-500 text-white text-sm rounded-lg transition";
        approveBtn.textContent = "Approve";
        approveBtn.onclick = function() {
            window.respondToApproval?.(requestId, true);
            _markApprovalResolved(card, true);
        };
        
        actions.appendChild(denyBtn);
        actions.appendChild(approveBtn);
    }
    
    body.appendChild(actions);
    card.appendChild(header);
    card.appendChild(body);
    row.appendChild(card);
    
    if (this._currentTextBubble && this._currentTextBubble.parentNode) {
        this.container.insertBefore(row, this._currentTextBubble.parentNode);
    } else {
        this.container.appendChild(row);
    }
    
    this._scrollToBottom();
    return card;
  }

  }

  // --- Helpers & Global Methods ---

  // Helper to mark approval as resolved (disable buttons, show status)
  function _markApprovalResolved(card, optionName) {
      const buttons = card.querySelectorAll("button");
      buttons.forEach(btn => {
          btn.disabled = true;
          btn.classList.add("opacity-50", "cursor-not-allowed");
      });
      
      const actions = card.querySelector(".approval-actions");
      if (actions) {
          const status = document.createElement("span");
          const isApproved = optionName === true || 
                            (typeof optionName === "string" && optionName.includes("allow"));
          status.className = isApproved ? "text-green-400" : "text-red-400";
          status.textContent = isApproved ? "✅ Approved" : "❌ Denied";
          if (typeof optionName === "string" && !optionName.includes("allow")) {
              status.textContent = "Selected: " + optionName;
          }
          status.className += " text-sm font-semibold py-1.5";
          actions.innerHTML = "";
          actions.appendChild(status);
      }
  }

  function addSystemMessage(container, text, className) {
    const div = document.createElement("div");
    div.className = "system-message" + (className ? " " + className : "");
    div.textContent = text;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
  }

  window.BubbleManager = BubbleManager;
  window.addSystemMessage = addSystemMessage;

  window.addUserBubble = function(container, text) {
    const row = document.createElement("div");
    row.className = "message-row user-row";
    const bubble = document.createElement("div");
    bubble.className = "message-bubble user-bubble";
    bubble.textContent = text;
    row.appendChild(bubble);
    container.appendChild(row);
    container.scrollTop = container.scrollHeight;
  };

  window.addAgentBubble = function(container, text) {
    const row = document.createElement("div");
    row.className = "message-row agent-row";
    const bubble = document.createElement("div");
    bubble.className = "message-bubble agent-bubble";
    
    if (typeof AcpUtils !== "undefined" && AcpUtils.renderMarkdown) {
      bubble.innerHTML = AcpUtils.renderMarkdown(text);
    } else if (typeof marked !== "undefined") {
      bubble.innerHTML = marked.parse(text);
    } else {
      bubble.textContent = text;
    }
    
    row.appendChild(bubble);
    container.appendChild(row);
    container.scrollTop = container.scrollHeight;
  };

  console.log(PREFIX, "Module loaded");
})();