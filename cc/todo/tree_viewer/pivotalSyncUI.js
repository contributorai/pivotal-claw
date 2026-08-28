(function () {
  const AUTOSAVE_DELAY_MS = 1500;
  const CONFLICT_MESSAGE_MS = 4000;

  let renderFn = null;
  let syncing = false;
  let conflictMessage = null;
  let conflictTimer = null;

  function setRenderFn(fn) {
    renderFn = fn;
  }

  function pendingCount() {
    const payload = syncLogic.buildSyncPayload();
    return payload.statusChanges.length + payload.textEdits.length + payload.tagAdditions.length + payload.newItems.length;
  }

  function reloadDataFile() {
    return new Promise((resolve, reject) => {
      const old = document.querySelector("script[data-todo-data]");
      if (old) old.remove();
      const script = document.createElement("script");
      script.src = "/todo_data.js?t=" + Date.now();
      script.dataset.todoData = "true";
      script.onload = resolve;
      script.onerror = reject;
      document.body.appendChild(script);
    });
  }

  function showConflictMessage(count) {
    conflictMessage = `${count} change${count === 1 ? "" : "s"} couldn't be saved`;
    clearTimeout(conflictTimer);
    conflictTimer = setTimeout(() => {
      conflictMessage = null;
      if (renderFn) renderFn();
    }, CONFLICT_MESSAGE_MS);
  }

  async function doSync() {
    if (syncing) return window.syncReceipt;
    syncing = true;
    scheduleSync.cancel();
    if (renderFn) renderFn();
    try {
      const payload = syncLogic.buildSyncPayload();
      const response = await fetch("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const receipt = await response.json();
      window.syncReceipt = receipt;
      syncLogic.processReceipt(receipt);
      if ((receipt.conflicts || []).length) showConflictMessage(receipt.conflicts.length);
      await reloadDataFile();
      return receipt;
    } finally {
      syncing = false;
      if (renderFn) renderFn();
    }
  }

  const scheduleSync = PivotalUtils.debounce(() => {
    doSync().catch((error) => {
      console.error("Autosave failed", error);
      if (renderFn) renderFn();
    });
  }, AUTOSAVE_DELAY_MS);

  // Flush any pending autosave immediately when the tab is about to be hidden
  // (backgrounded or closed) so edits aren't lost waiting for the debounce.
  // sendBeacon can't read a response, so on next load the normal autosave
  // path reconciles overrides against the real receipt as usual.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "hidden") return;
    if (syncing || pendingCount() === 0) return;
    scheduleSync.cancel();
    const payload = syncLogic.buildSyncPayload();
    navigator.sendBeacon("/api/sync", new Blob([JSON.stringify(payload)], { type: "application/json" }));
  });

  // Overrides live in localStorage, shared across same-origin tabs. Each tab's
  // reads already go straight to localStorage (see focusLogic.js/iceboxLogic.js
  // loadJSON), so a tab's own next write never loses another tab's prior write --
  // but without this listener a tab's *rendered view* (and pending-count bar)
  // stays stale until the user interacts with that tab again. The native
  // "storage" event fires in other same-origin tabs whenever localStorage
  // changes, so we just need to re-render on it.
  const WATCHED_OVERRIDE_KEYS = new Set([
    "todo.pivotal.statusOverrides",
    "todo.pivotal.tagOverrides",
    "todo.pivotal.textOverrides",
    "todo.pivotal.queue",
    "todo.pivotal.ranks",
    "todo.pivotal.epicPanes"
  ]);
  window.addEventListener("storage", (event) => {
    if (event.key && !WATCHED_OVERRIDE_KEYS.has(event.key)) return;
    if (renderFn) renderFn();
  });

  function resetPending() {
    scheduleSync.cancel();
    FocusLogic.clearStatusOverrides();
    IceboxLogic.clearTextOverrides();
    FocusLogic.saveTagOverrides({});
    IceboxLogic.saveQueue([]);
    if (renderFn) renderFn();
  }

  function renderStatusBar(container) {
    const count = pendingCount();
    if (count > 0 && !syncing) scheduleSync();
    container.innerHTML = "";
    delete container.dataset.state;
    const label = document.createElement("span");
    if (conflictMessage) {
      container.dataset.state = "error";
      label.textContent = conflictMessage;
    } else if (syncing || count > 0) {
      container.dataset.state = "busy";
      label.textContent = "Saving…";
    } else {
      label.textContent = "Saved";
    }
    const sync = document.createElement("button");
    sync.className = "primary";
    sync.textContent = "Sync now";
    sync.disabled = count === 0 && !syncing;
    sync.addEventListener("click", () => doSync().catch((error) => {
      container.dataset.state = "error";
      label.textContent = error.message;
    }));
    const reset = document.createElement("button");
    reset.textContent = "Reset";
    reset.disabled = count === 0;
    reset.addEventListener("click", resetPending);
    container.append(label, sync, reset);
  }

  function refreshFromServer() {
    return reloadDataFile().then(() => {
      if (renderFn) renderFn();
    });
  }

  window.PivotalSyncUI = { setRenderFn, reloadDataFile, refreshFromServer, renderStatusBar, doSync, pendingCount };
})();
