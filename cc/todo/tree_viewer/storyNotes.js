(function () {
  const cache = new Map();

  function normalizeEntry(entry) {
    return {
      sessions: Array.isArray(entry && entry.sessions) ? entry.sessions : [],
      notes: Array.isArray(entry && entry.notes) ? entry.notes : []
    };
  }

  async function load(storyId) {
    const refresh = arguments[1];
    if (!refresh && cache.has(storyId)) return cache.get(storyId);
    const response = await fetch(`/api/story-sessions/${encodeURIComponent(storyId)}`);
    if (!response.ok) throw new Error("Could not load notes");
    const entry = normalizeEntry(await response.json());
    cache.set(storyId, entry);
    if (window.todoData) {
      if (!window.todoData.story_sessions) {
        window.todoData.story_sessions = {};
      }
      window.todoData.story_sessions[storyId] = entry;
    }
    return entry;
  }

  async function add(storyId, text) {
    const response = await fetch("/api/story-note", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ story_id: storyId, text })
    });
    if (!response.ok) throw new Error("Could not add note");
    return load(storyId, true);
  }

  window.StoryNotes = { load, add };
})();
