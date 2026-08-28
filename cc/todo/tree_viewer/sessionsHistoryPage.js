(function () {
  const listEl = document.getElementById("sessions-list");
  const pinnedEl = document.getElementById("pinned-list");
  const pinChipEl = document.getElementById("pin-chip");
  const statusEl = document.getElementById("sessions-status");
  const refreshBtn = document.getElementById("refresh-sessions");
  const pulse = document.getElementById("agent-work-pulse");
  const pulseStatus = document.getElementById("pulse-status");
  const pulseActive = pulse.querySelector('[data-testid="pulse-active-sessions"]');
  const pulseCompleted = pulse.querySelector('[data-testid="pulse-completed-stories"]');
  const pulseCycle = pulse.querySelector('[data-testid="pulse-median-cycle"]');
  const previewCache = new Map();
  const STATUS_ORDER = { "In progress": 0, "Pending": 1, "On schedule": 2, "Done": 3 };
  const PIN_SVG =
    '<svg class="pin-ico" viewBox="0 0 24 24" aria-hidden="true">' +
    '<path class="body" d="M9 3h6l-1 5 4 4v2H6v-2l4-4z"></path>' +
    '<path d="M12 14v7"></path></svg>';

  let pins = { sessions: [], stories: [] };
  let recentSessions = [];

  function pinKey(record) {
    return `${record.provider}:${record.session_id}`;
  }

  function pinnedSessionKeys() {
    return new Set(pins.sessions.map(pinKey));
  }

  async function togglePin(payload) {
    const response = await fetch("/api/pins/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "Could not update pin");
    // Apply what the server actually stored, never an optimistic guess.
    pins = body.pins || pins;
    return body.pinned;
  }

  function makePinButton(className, isPinned, payload, nounForTitle) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = isPinned ? `${className} pinned` : className;
    button.dataset.testid = "session-pin";
    button.innerHTML = PIN_SVG;
    button.title = `${isPinned ? "Unpin" : "Pin"} this ${nounForTitle}`;
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      button.disabled = true;
      try {
        await togglePin(payload);
      } catch (error) {
        setStatus(String(error.message || error), true);
        button.disabled = false;
        return;
      }
      renderAll();
    });
    return button;
  }

  function flatStories() {
    const flat = [];
    function visit(task) {
      flat.push(task);
      (task.children || []).forEach(visit);
    }
    ((window.todoData && window.todoData.tasks) || []).forEach(visit);
    return flat.sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9));
  }

  function storyById(storyId) {
    return flatStories().find((task) => task.task_id === storyId) || null;
  }

  function relativeAge(iso) {
    const then = Date.parse(iso);
    if (!Number.isFinite(then)) return "";
    const seconds = Math.max(0, (Date.now() - then) / 1000);
    if (seconds < 60) return "now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
    return `${Math.floor(seconds / 86400)}d`;
  }

  function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.style.color = isError ? "var(--red)" : "";
  }

  function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${(seconds / 3600).toFixed(1)}h`;
  }

  function renderPulse(body) {
    const available = body && body.status === "available";
    pulse.classList.toggle("is-unavailable", !available);
    pulseActive.textContent = available ? String(body.active_sessions) : "—";
    pulseCompleted.textContent = available ? String(body.stories_completed_24h) : "—";
    pulseCycle.textContent = available ? formatDuration(body.median_cycle_seconds) : "—";
    pulseStatus.textContent = available
      ? "Live from ClickHouse"
      : (body && body.message) || "ClickHouse metrics unavailable";
  }

  async function loadPulse() {
    try {
      const response = await fetch("/api/agent-work-pulse");
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Could not load Agent Work Pulse");
      renderPulse(body);
    } catch (_error) {
      pulse.classList.add("is-unavailable");
      renderPulse({ status: "unavailable", message: "ClickHouse metrics unavailable" });
    }
  }

  function makeStoryChip(storyId) {
    // The pin sits beside the link rather than inside it — a button nested in an
    // anchor would still navigate when clicked.
    const chip = document.createElement("span");
    chip.className = "story-chip";
    const story = storyById(storyId);
    const link = document.createElement("a");
    link.className = "chip-text";
    link.href = `/#id=${encodeURIComponent(storyId)}`;
    link.textContent = story ? story.display_text : storyId;
    chip.title = link.textContent;
    chip.addEventListener("click", (event) => event.stopPropagation());
    // Stories have no rows of their own here, so the chip carries their pin.
    chip.append(
      link,
      makePinButton("chip-pin", pins.stories.includes(storyId), { kind: "story", story_id: storyId }, "story")
    );
    return chip;
  }

  async function linkSession(session, storyId) {
    const response = await fetch("/api/sessions/link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        story_id: storyId,
        session_id: session.session_id,
        provider: session.provider,
        started_at: session.started_at,
        title: session.title,
        cwd: session.cwd
      })
    });
    if (!response.ok) throw new Error("Could not link session");
  }

  function openStoryPicker(session, cell) {
    const existing = document.querySelector(".story-picker");
    if (existing) existing.remove();
    const picker = document.createElement("div");
    picker.className = "story-picker";
    picker.addEventListener("click", (event) => event.stopPropagation());
    const input = document.createElement("input");
    input.type = "search";
    input.placeholder = "Link to story…";
    const results = document.createElement("div");
    results.className = "story-picker-results";

    function renderResults() {
      const query = input.value.trim().toLowerCase();
      results.innerHTML = "";
      const matches = flatStories()
        .filter((task) => !query || task.display_text.toLowerCase().includes(query))
        .slice(0, 30);
      matches.forEach((task) => {
        const item = document.createElement("div");
        item.className = "story-picker-item";
        const status = document.createElement("span");
        status.className = "status";
        status.textContent = task.status;
        const title = document.createElement("span");
        title.className = "story-title";
        title.textContent = task.display_text;
        item.append(status, title);
        item.addEventListener("click", async () => {
          try {
            await linkSession(session, task.task_id);
            session.linked_story_ids = (session.linked_story_ids || []).concat(task.task_id);
            picker.remove();
            cell.innerHTML = "";
            renderLinkCell(session, cell);
          } catch (error) {
            setStatus(String(error.message || error), true);
          }
        });
        results.appendChild(item);
      });
      if (!matches.length) {
        const none = document.createElement("div");
        none.className = "story-picker-item";
        none.textContent = "No matching stories";
        results.appendChild(none);
      }
    }

    input.addEventListener("input", renderResults);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") picker.remove();
    });
    picker.append(input, results);
    cell.appendChild(picker);
    renderResults();
    input.focus();
    setTimeout(() => {
      document.addEventListener("click", function close(event) {
        if (!picker.contains(event.target)) {
          picker.remove();
          document.removeEventListener("click", close);
        }
      });
    }, 0);
  }

  function renderLinkCell(session, cell) {
    const linked = session.linked_story_ids || [];
    if (linked.length) {
      linked.forEach((storyId) => cell.appendChild(makeStoryChip(storyId)));
      return;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-story-btn";
    button.textContent = "link";
    button.title = "Link this session to a story";
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openStoryPicker(session, cell);
    });
    cell.appendChild(button);
  }

  async function togglePreview(session, row) {
    const open = row.nextElementSibling;
    if (open && open.classList.contains("session-preview")) {
      open.remove();
      return;
    }
    document.querySelectorAll(".session-preview").forEach((el) => el.remove());
    const pane = document.createElement("div");
    pane.className = "session-preview";
    pane.textContent = "Loading…";
    row.after(pane);

    const key = `${session.provider}:${session.session_id}`;
    try {
      let preview = previewCache.get(key);
      if (!preview) {
        const response = await fetch(
          `/api/sessions/${encodeURIComponent(session.provider)}/${encodeURIComponent(session.session_id)}/preview`
        );
        if (!response.ok) throw new Error("Could not load preview");
        preview = await response.json();
        previewCache.set(key, preview);
      }
      pane.innerHTML = "";
      const meta = document.createElement("div");
      meta.className = "preview-meta";
      meta.textContent = `${session.session_id} · ${session.cwd || "unknown dir"}${session.git_branch ? " · " + session.git_branch : ""}`;
      pane.appendChild(meta);
      const messages = preview.messages || [];
      if (!messages.length) {
        const empty = document.createElement("div");
        empty.textContent = "No messages found.";
        pane.appendChild(empty);
      }
      messages.forEach((message) => {
        const msg = document.createElement("div");
        msg.className = `preview-msg ${message.role}`;
        const role = document.createElement("span");
        role.className = "role";
        role.textContent = message.role === "user" ? "you" : "ai";
        const text = document.createElement("span");
        text.className = "text";
        text.textContent = message.text;
        msg.append(role, text);
        pane.appendChild(msg);
      });
    } catch (error) {
      pane.textContent = String(error.message || error);
    }
  }

  function renderRow(session, options) {
    const row = document.createElement("div");
    row.className = "session-row";
    if (options && options.nested) row.classList.add("nested");
    row.dataset.testid = "session-row";

    const pinned = pinnedSessionKeys().has(pinKey(session));
    row.classList.toggle("is-pinned", pinned);
    const pinBtn = makePinButton("pin-btn", pinned, {
      kind: "session",
      provider: session.provider,
      session_id: session.session_id,
      title: session.title,
      cwd: session.cwd,
      started_at: session.started_at
    }, "session");

    const badge = document.createElement("span");
    badge.className = `provider-badge provider-${session.provider}`;
    badge.textContent = session.provider === "claude" ? "CC" : "CX";
    badge.title = session.provider;

    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = session.title || "(untitled)";
    title.title = session.first_prompt || session.title;

    const project = document.createElement("span");
    project.className = "session-project";
    project.textContent = session.project || "";
    project.title = session.cwd || "";

    const age = document.createElement("span");
    age.className = "session-age";
    const ageAt = session.modified_at || session.started_at;
    age.textContent = relativeAge(ageAt);
    age.title = ageAt || "";

    const idleLabel = session.running ? "open" : "resume";
    const resumeBtn = document.createElement("button");
    resumeBtn.type = "button";
    resumeBtn.className = session.running ? "resume-btn running" : "resume-btn";
    resumeBtn.dataset.testid = "session-resume";
    resumeBtn.textContent = idleLabel;
    resumeBtn.title = session.running
      ? "Bring the Terminal window running this session to the front"
      : "Reopen this session in Terminal";
    resumeBtn.addEventListener("click", async (event) => {
      event.stopPropagation();
      resumeBtn.disabled = true;
      resumeBtn.textContent = "opening…";
      try {
        const response = await fetch("/api/sessions/resume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider: session.provider,
            session_id: session.session_id,
            cwd: session.cwd
          })
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || "Could not resume session");
        resumeBtn.textContent = body.focused ? "focused" : "opened";
      } catch (error) {
        setStatus(String(error.message || error), true);
        resumeBtn.textContent = idleLabel;
        resumeBtn.disabled = false;
        return;
      }
      setTimeout(() => {
        resumeBtn.textContent = idleLabel;
        resumeBtn.disabled = false;
      }, 3000);
    });

    const linkCell = document.createElement("span");
    linkCell.className = "session-link-cell";
    renderLinkCell(session, linkCell);

    if (session.running) {
      const dot = document.createElement("span");
      dot.className = "running-dot";
      dot.title = "Running in Terminal now";
      row.appendChild(dot);
    }
    row.append(pinBtn, badge, title, project, age, resumeBtn, linkCell);
    row.addEventListener("click", () => togglePreview(session, row));
    return row;
  }

  function freshest(session) {
    const recent = recentSessions.find((item) => pinKey(item) === pinKey(session));
    return recent ? { ...session, ...recent } : session;
  }

  function sessionsForStory(storyId) {
    const entry = ((window.todoData && window.todoData.story_sessions) || {})[storyId];
    return ((entry && entry.sessions) || []).filter((item) => item && item.session_id);
  }

  function renderPinnedStory(storyId) {
    const group = document.createElement("div");
    group.className = "pinned-list-group";
    const row = document.createElement("div");
    row.className = "pinned-story-row";
    row.appendChild(
      makePinButton("pin-btn", true, { kind: "story", story_id: storyId }, "story")
    );
    const story = storyById(storyId);
    const link = document.createElement("a");
    link.className = "story-link";
    link.href = `/#id=${encodeURIComponent(storyId)}`;
    link.textContent = story ? story.display_text : storyId;
    link.title = link.textContent;
    const sessions = sessionsForStory(storyId);
    const status = document.createElement("span");
    status.className = "story-status";
    const count = `${sessions.length} session${sessions.length === 1 ? "" : "s"}`;
    status.textContent = story ? `${story.status} · ${count}` : count;
    row.append(link, status);
    group.appendChild(row);
    sessions.forEach((session) => group.appendChild(renderRow(freshest(session), { nested: true })));
    return group;
  }

  function renderPinnedSection() {
    pinnedEl.innerHTML = "";
    const total = pins.stories.length + pins.sessions.length;
    pinChipEl.querySelector(".n").textContent = String(total);
    pinChipEl.classList.toggle("on", total > 0);
    if (!total) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "Nothing pinned — use the pin button in any row to keep a session or story in view.";
      pinnedEl.appendChild(empty);
      return;
    }
    pins.stories.forEach((storyId) => pinnedEl.appendChild(renderPinnedStory(storyId)));
    const group = document.createElement("div");
    group.className = "pinned-list-group";
    pins.sessions.forEach((session) => group.appendChild(renderRow(freshest(session))));
    if (pins.sessions.length) pinnedEl.appendChild(group);
  }

  function renderRecent() {
    // Anything already shown above — pinned itself, or nested under a pinned
    // story — is left out so the two lists never repeat a session.
    const alreadyPinned = pinnedSessionKeys();
    pins.stories.forEach((storyId) =>
      sessionsForStory(storyId).forEach((session) => alreadyPinned.add(pinKey(session)))
    );
    const sessions = recentSessions.filter((session) => !alreadyPinned.has(pinKey(session)));
    listEl.innerHTML = "";
    if (!sessions.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = recentSessions.length
        ? "Every recent session is pinned above."
        : "No sessions found on this machine.";
      listEl.appendChild(empty);
      return;
    }
    sessions.forEach((session) => listEl.appendChild(renderRow(session)));
  }

  function renderAll() {
    renderPinnedSection();
    renderRecent();
  }

  async function loadSessions() {
    setStatus("Loading…");
    try {
      const [sessionsResponse, pinsResponse] = await Promise.all([
        fetch("/api/sessions?limit=25"),
        fetch("/api/pins")
      ]);
      if (!sessionsResponse.ok) throw new Error("Could not load sessions");
      if (!pinsResponse.ok) throw new Error("Could not load pins");
      const body = await sessionsResponse.json();
      const pinsBody = await pinsResponse.json();
      recentSessions = body.sessions || [];
      pins = { sessions: pinsBody.sessions || [], stories: pinsBody.stories || [] };
      renderAll();
      setStatus(`${recentSessions.length} sessions · updated ${new Date().toLocaleTimeString()}`);
    } catch (error) {
      listEl.innerHTML = "";
      const failed = document.createElement("div");
      failed.className = "error-state";
      failed.textContent = String(error.message || error);
      listEl.appendChild(failed);
      setStatus("Load failed", true);
    }
  }

  function refreshPage() {
    return Promise.all([loadSessions(), loadPulse()]);
  }

  refreshBtn.addEventListener("click", refreshPage);
  refreshPage();
})();
