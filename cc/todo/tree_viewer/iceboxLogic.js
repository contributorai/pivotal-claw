(function () {
  const queueKey = "todo.pivotal.queue";
  const textKey = "todo.pivotal.textOverrides";
  const memoryStore = {};

  function loadJSON(key, fallback) {
    try {
      return JSON.parse(localStorage.getItem(key) || JSON.stringify(fallback));
    } catch {
      return memoryStore[key] || fallback;
    }
  }

  function saveJSON(key, value) {
    memoryStore[key] = value;
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {}
  }

  function loadQueue() {
    return loadJSON(queueKey, []);
  }

  function saveQueue(items) {
    saveJSON(queueKey, items);
  }

  function addToQueue(text, status, tags) {
    const items = loadQueue();
    const item = {
      id: "Q" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
      text,
      status,
      tags: tags || [],
      added: new Date().toISOString()
    };
    items.unshift(item);
    saveQueue(items);
    return item;
  }

  function loadTextOverrides() {
    return loadJSON(textKey, {});
  }

  function saveTextOverrides(value) {
    saveJSON(textKey, value);
  }

  function saveTextOverride(tid, text) {
    const overrides = loadTextOverrides();
    overrides[tid] = text;
    saveTextOverrides(overrides);
  }

  function clearTextOverrides() {
    delete memoryStore[textKey];
    try {
      localStorage.removeItem(textKey);
    } catch {}
  }

  function getEffectiveText(task, overrides) {
    return overrides[task.task_id] || task.display_text || task.text || "";
  }

  window.IceboxLogic = {
    loadQueue,
    saveQueue,
    addToQueue,
    loadTextOverrides,
    saveTextOverrides,
    saveTextOverride,
    clearTextOverrides,
    getEffectiveText
  };
})();
