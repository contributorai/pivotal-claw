(function () {
  function taskIndex(tasks) {
    return Object.fromEntries(PivotalData.getAllFlat(tasks).map((task) => [task.task_id, task]));
  }

  function fingerprintIndex(tasks) {
    const index = {};
    PivotalData.getAllFlat(tasks).forEach((task) => {
      index[PivotalUtils.normalizeText(task.display_text || task.text)] = task;
    });
    return index;
  }

  function buildSyncPayload() {
    const tasks = window.todoData.tasks || [];
    const byId = taskIndex(tasks);
    const fingerprints = fingerprintIndex(tasks);
    const statusOverrides = FocusLogic.loadStatusOverrides();
    const textOverrides = IceboxLogic.loadTextOverrides();
    const tagOverrides = FocusLogic.loadTagOverrides();
    const queue = IceboxLogic.loadQueue();

    const statusChanges = Object.entries(statusOverrides).filter(([tid]) => byId[tid]).map(([tid, status]) => {
      const task = byId[tid];
      return {
        task_id: tid,
        source_file: task.source_file,
        line_number: task.line_number,
        expected_text: task.text,
        from_status: task.status,
        new_status: status
      };
    });
    const textEdits = Object.entries(textOverrides).filter(([tid]) => byId[tid]).map(([tid, text]) => {
      const task = byId[tid];
      return { task_id: tid, source_file: task.source_file, line_number: task.line_number, expected_text: task.text, new_text: text };
    });
    const tagAdditions = Object.entries(tagOverrides).filter(([tid]) => byId[tid]).map(([tid, tags]) => {
      const task = byId[tid];
      const missing = tags.filter((tag) => !(task.tags || []).includes(tag));
      return { task_id: tid, source_file: task.source_file, line_number: task.line_number, expected_text: task.text, tags: missing };
    }).filter((item) => item.tags.length);
    const newItems = queue.filter((item) => !fingerprints[PivotalUtils.normalizeText(item.text)]).map((item) => ({
      text: item.text,
      status: item.status || "Pending",
      tags: item.tags || []
    }));

    return { version: 1, timestamp: Date.now(), statusChanges, textEdits, tagAdditions, newItems };
  }

  function processReceipt(receipt) {
    // Conflicted overrides are stale -- the story changed elsewhere since this
    // tab last saw it, so the queued edit can't be safely applied. Drop it
    // rather than retry it forever; the fresh data about to be reloaded
    // already reflects the current state.
    const conflictedIds = new Set((receipt.conflicts || []).map((entry) => entry.task_id));

    const status = FocusLogic.loadStatusOverrides();
    (receipt.appliedStatus || []).forEach((tid) => delete status[tid]);
    conflictedIds.forEach((tid) => delete status[tid]);
    FocusLogic.saveStatusOverrides(status);

    const text = IceboxLogic.loadTextOverrides();
    (receipt.appliedTexts || []).forEach((tid) => delete text[tid]);
    conflictedIds.forEach((tid) => delete text[tid]);
    IceboxLogic.saveTextOverrides(text);

    const tags = FocusLogic.loadTagOverrides();
    (receipt.appliedTags || []).forEach((tid) => delete tags[tid]);
    conflictedIds.forEach((tid) => delete tags[tid]);
    FocusLogic.saveTagOverrides(tags);

    const appliedNew = new Set((receipt.appliedNewItems || []).map(PivotalUtils.normalizeText));
    IceboxLogic.saveQueue(IceboxLogic.loadQueue().filter((item) => !appliedNew.has(PivotalUtils.normalizeText(item.text))));
  }

  window.syncLogic = { buildSyncPayload, processReceipt };
})();
