/**
 * ACP Chat App — File Upload Manager
 *
 * Handles file uploads in the chat interface:
 * - Drag & drop files onto the chat area
 * - Click paperclip icon to select files
 * - File preview with live upload status
 * - Upload via POST /api/files (multipart FormData)
 * - Include file reference in the prompt message
 *
 * ★ Key design: files are uploaded IMMEDIATELY upon selection (not batched
 *   at send time).  This gives responsive UI — the user sees upload progress
 *   in the preview strip while they type their message, and the prompt
 *   sends instantly because uploads are already done.
 *
 * All events logged with console.log prefixed "[ACP Chat Files]".
 */

(function () {
  "use strict";

  const PREFIX = "[ACP Chat Files]";

  /**
   * FileUploadManager — manages file selection, preview, and upload.
   */
  class FileUploadManager {
    /**
     * @param {Object} options
     * @param {HTMLElement} options.dropZone — drop target (usually chat-messages)
     * @param {HTMLInputElement} options.fileInput — hidden file input
     * @param {HTMLElement} options.triggerBtn — button that opens file picker
     * @param {HTMLElement} options.previewContainer — where previews appear
     * @param {string} options.sessionId — current session ID
     */
    constructor(options) {
      this.dropZone = options.dropZone;
      this.fileInput = options.fileInput;
      this.triggerBtn = options.triggerBtn;
      this.previewContainer = options.previewContainer;
      this.sessionId = options.sessionId;

      /**
       * Upload queue — one entry per file.  Each entry tracks the file,
       * its upload promise, and the resolved ref (null until done).
       *
       * @type {Array<{
       *   file: File,
       *   status: 'uploading' | 'done' | 'error',
       *   promise: Promise,
       *   ref: Object|null,
       *   previewId: string,
       *   abortController: AbortController,
       * }>}
       */
      this._uploadQueue = [];

      this._setupEventListeners();

      console.log(PREFIX, "FileUploadManager initialized", {
        sessionId: this.sessionId,
      });
    }

    /**
     * Get uploaded file references formatted as context for the prompt.
     * Only returns successfully uploaded files.
     * @returns {Object[]}
     */
    getUploadedRefs() {
      return this._uploadQueue
        .filter(entry => entry.status === 'done' && entry.ref)
        .map(entry => entry.ref);
    }

    /**
     * Clear all files — removes previews, aborts in-flight uploads,
     * and resets the upload queue.
     */
    clearAll() {
      // Abort any in-flight uploads
      for (const entry of this._uploadQueue) {
        if (entry.status === 'uploading') {
          entry.abortController.abort();
        }
      }
      this._uploadQueue = [];
      this._clearPreviews();
      console.log(PREFIX, "Cleared all files (aborted in-flight uploads)");
    }

    // --- Event Listeners ---

    _setupEventListeners() {
      // File picker is opened natively by the <label for="file-input">
      // — no JS click handler needed (it would double-trigger the picker).

      // File selected via file input
      if (this.fileInput) {
        this.fileInput.addEventListener("change", (e) => {
          this._handleFiles(e.target.files);
          this.fileInput.value = ""; // Reset so same file can be re-selected
        });
      }

      // Drag & drop
      if (this.dropZone) {
        this.dropZone.addEventListener("dragover", (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.dropZone.classList.add("drag-over");
        });

        this.dropZone.addEventListener("dragleave", (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.dropZone.classList.remove("drag-over");
        });

        this.dropZone.addEventListener("drop", (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.dropZone.classList.remove("drag-over");
          this._handleFiles(e.dataTransfer.files);
        });
      }

      // Paste files
      document.addEventListener("paste", (e) => {
        const items = e.clipboardData && e.clipboardData.items;
        if (!items) return;

        const files = [];
        for (let i = 0; i < items.length; i++) {
          if (items[i].kind === "file") {
            files.push(items[i].getAsFile());
          }
        }

        if (files.length > 0) {
          this._handleFiles(files);
        }
      });
    }

    // --- File Handling ---

    /**
     * Called when files are selected (drag/drop, paste, file picker).
     * Starts uploading each file IMMEDIATELY — no batching at send time.
     */
    _handleFiles(fileList) {
      if (!fileList || fileList.length === 0) return;

      const files = Array.from(fileList);
      console.log(PREFIX, "Files selected:", files.map(f => ({ name: f.name, size: f.size, type: f.type })));

      for (const file of files) {
        this._startUpload(file);
      }
    }

    /**
     * Begin uploading a single file immediately.
     * Creates a preview entry (with spinner), fires the upload,
     * and updates the preview to ✅ or ❌ when done.
     */
    _startUpload(file) {
      const previewId = "file-preview-" + Date.now() + "-" + Math.random().toString(36).substr(2, 5);
      const abortController = new AbortController();

      // Show preview immediately with ⏳ spinner
      this._showPreview(file, previewId, 'uploading');

      // Create the queue entry first so the promise callbacks can mutate it
      const entry = {
        file: file,
        status: 'uploading',
        promise: null,   // filled in below
        ref: null,
        previewId: previewId,
        abortController: abortController,
      };
      this._uploadQueue.push(entry);

      // Fire the upload immediately — attach handlers to mutate the entry
      entry.promise = this._uploadOne(file, abortController.signal)
        .then(ref => {
          entry.status = 'done';
          entry.ref = ref;
          this._updatePreviewStatus(previewId, 'done');
          console.log(PREFIX, "Upload complete:", file.name, "→", ref && ref.id);
        })
        .catch(err => {
          if (err && err.name === 'AbortError') {
            console.log(PREFIX, "Upload aborted:", file.name);
          } else {
            entry.status = 'error';
            this._updatePreviewStatus(previewId, 'error');
            console.error(PREFIX, "Upload failed for", file.name, ":", err);
          }
        });

      console.log(PREFIX, "Started upload:", file.name, "(" + file.size + " bytes) →", previewId);
    }

    // --- Preview ---

    /**
     * Build and append a preview DOM element for a file.
     *
     * @param {File} file
     * @param {string} previewId - unique DOM id
     * @param {'uploading'|'done'|'error'} status - initial status
     */
    _showPreview(file, previewId, status) {
      if (!this.previewContainer) return;

      const el = document.createElement("div");
      el.className = "file-preview-item";
      el.id = previewId;

      // Status icon — ⏳ while uploading, ✅ when done, ❌ on error
      const statusIcon = document.createElement("span");
      statusIcon.className = "file-preview-icon";
      statusIcon.id = previewId + "-icon";
      statusIcon.textContent = status === 'uploading' ? '⏳' : (status === 'done' ? '✅' : '❌');
      el.appendChild(statusIcon);

      // Filename
      const nameEl = document.createElement("span");
      nameEl.className = "file-preview-name";
      nameEl.textContent = file.name;
      nameEl.title = file.name; // Tooltip shows full name on hover
      el.appendChild(nameEl);

      // Remove button
      const removeBtn = document.createElement("button");
      removeBtn.className = "file-preview-remove";
      removeBtn.innerHTML = "&times;";
      removeBtn.title = "Remove file";
      removeBtn.addEventListener("click", () => {
        this._removePreview(previewId);
      });
      el.appendChild(removeBtn);

      this.previewContainer.appendChild(el);
    }

    /**
     * Update the status icon for a preview entry.
     * @param {string} previewId
     * @param {'uploading'|'done'|'error'} status
     */
    _updatePreviewStatus(previewId, status) {
      const icon = document.getElementById(previewId + "-icon");
      if (!icon) return;

      if (status === 'done') {
        icon.textContent = '✅';
      } else if (status === 'error') {
        icon.textContent = '❌';
        icon.title = 'Upload failed — this file will not be sent';
      } else {
        icon.textContent = '⏳';
      }
    }

    /**
     * Remove a preview and cancel its upload (if still in flight).
     * @param {string} previewId
     */
    _removePreview(previewId) {
      // Remove DOM element
      const el = document.getElementById(previewId);
      if (el) el.remove();

      // Find and abort the upload entry
      const idx = this._uploadQueue.findIndex(e => e.previewId === previewId);
      if (idx !== -1) {
        const entry = this._uploadQueue[idx];
        if (entry.status === 'uploading') {
          entry.abortController.abort();
        }
        this._uploadQueue.splice(idx, 1);
        console.log(PREFIX, "Removed file:", entry.file.name);
      }
    }

    /**
     * Remove all preview DOM elements.
     */
    _clearPreviews() {
      if (this.previewContainer) {
        this.previewContainer.innerHTML = "";
      }
    }

    // --- Upload ---

    /**
     * Wait for any still-in-progress uploads and return all successful refs.
     *
     * Files are uploaded as soon as they're added, so by the time the user
     * hits Send most (or all) uploads are already done.  This method just
     * catches any stragglers.
     *
     * @returns {Promise<Object[]>} Array of uploaded file refs
     */
    async uploadAll() {
      if (this._uploadQueue.length === 0) {
        return [];
      }

      // Count how many are still uploading
      const pending = this._uploadQueue.filter(e => e.status === 'uploading');
      if (pending.length > 0) {
        console.log(PREFIX, "Waiting for", pending.length, "in-progress upload(s) to finish…");
        // Wait for all promises to settle (some may reject)
        await Promise.allSettled(pending.map(e => e.promise));
        console.log(PREFIX, "All in-progress uploads settled");
      }

      // Collect refs from successfully uploaded files
      const refs = [];
      for (const entry of this._uploadQueue) {
        if (entry.status === 'done' && entry.ref) {
          refs.push(entry.ref);
        }
      }

      const errors = this._uploadQueue.filter(e => e.status === 'error');
      if (errors.length > 0) {
        console.warn(PREFIX, errors.length, "file(s) failed to upload — they will be omitted");
      }

      console.log(PREFIX, "uploadAll →", refs.length, "succeeded,", errors.length, "failed");

      // Clear previews (but keep queue state until clearAll is called after prompt completion)
      this._clearPreviews();

      return refs;
    }

    /**
     * Upload a single file to the backend.
     *
     * @param {File} file
     * @param {AbortSignal} signal - aborted when the user removes the file
     * @returns {Promise<Object|null>} uploaded file ref, or null on failure
     */
    async _uploadOne(file, signal) {
      console.log(PREFIX, "Uploading:", file.name, file.size);

      const formData = new FormData();
      formData.append("file", file);
      formData.append("folder", "chat_uploads/" + (new Date()).toISOString().slice(0,10).replace(/-/g, ""));

      try {
        const response = await fetch("/api/files", {
          method: "POST",
          body: formData,
          signal: signal,   // ← allows cancellation via AbortController
        });

        if (!response.ok) {
          const err = await response.json().catch(() => ({ detail: response.statusText }));
          console.error(PREFIX, "Upload failed for", file.name, ":", err);
          return null;
        }

        const result = await response.json();
        console.log(PREFIX, "Uploaded:", file.name, "→", result);

        return {
          id: result.id,
          filename: file.name,
          path: result.path || result.filename,
          url: result.url || ("/api/files/" + result.id + "/download"),
          mime_type: file.type,
          size: file.size,
        };
      } catch (e) {
        // Re-throw AbortError so the caller can distinguish cancellation from failure
        if (e.name === 'AbortError') throw e;
        console.error(PREFIX, "Upload error for", file.name, ":", e);
        return null;
      }
    }
  }

  // --- Exports ---

  window.FileUploadManager = FileUploadManager;

  console.log(PREFIX, "Module loaded");
})();
