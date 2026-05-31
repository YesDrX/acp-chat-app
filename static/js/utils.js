/**
 * ACP Chat App — shared utilities
 *
 * Common helper functions used across pages.
 * Loaded from base.html, available before page-specific scripts.
 */

console.log("[ACP Chat App] utils.js loaded");

window.AcpUtils = {
  /**
   * Format a timestamp string into a relative time description.
   * @param {string} timestamp - ISO timestamp or similar
   * @returns {string} human-readable relative time
   */
  formatTimestamp: function (timestamp) {
    if (!timestamp) return "";

    try {
      var date = new Date(timestamp);
      var now = new Date();
      var diffMs = now - date;
      var diffSec = Math.floor(diffMs / 1000);
      var diffMin = Math.floor(diffSec / 60);
      var diffHr = Math.floor(diffMin / 60);
      var diffDay = Math.floor(diffHr / 24);

      if (diffSec < 60) return "just now";
      if (diffMin < 60) return diffMin + "m ago";
      if (diffHr < 24) return diffHr + "h ago";
      if (diffDay < 7) return diffDay + "d ago";
      return date.toLocaleDateString();
    } catch (e) {
      return timestamp;
    }
  },

  /**
   * Escape HTML special characters to prevent XSS.
   * @param {string} text
   * @returns {string} escaped text
   */
  escapeHtml: function (text) {
    if (!text) return "";
    var div = document.createElement("div");
    div.appendChild(document.createTextNode(text));
    return div.innerHTML;
  },

  /**
   * Render markdown to HTML using marked library.
   * @param {string} text — raw markdown text
   * @returns {string} HTML string
   */
  renderMarkdown: function (text) {
    if (!text) return "";
    if (typeof marked === "undefined") return AcpUtils.escapeHtml(text);
    try {
      // Strip spaces inside markdown formatting delimiters (pi-acp sends "** text **")
      var cleaned = text
        .replace(/\*\* +([^*]+) +\*\*/g, '**$1**')
        .replace(/\* +([^*]+) +\*/g, '*$1*')
        .replace(/__ +([^_]+) +__/g, '__$1__')
        .replace(/_ +([^_]+) +_/g, '_$1_')
        .replace(/~~ +([^~]+) +~~/g, '~~$1~~')
        .replace(/` +([^`]+) +`/g, '`$1`');
      return marked.parse(cleaned);
    } catch (e) {
      console.warn("[ACP Chat] markdown render error:", e);
      return AcpUtils.escapeHtml(text);
    }
  },
};
