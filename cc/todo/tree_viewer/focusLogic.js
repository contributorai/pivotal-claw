(function () {
  const statusKey = "todo.pivotal.statusOverrides";
  const tagKey = "todo.pivotal.tagOverrides";
  const epicPaneKey = "todo.pivotal.epicPanes";
  const rankKey = "todo.pivotal.ranks";
  const epicRankKey = "todo.pivotal.epicRanks";
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

  function loadStatusOverrides() {
    return loadJSON(statusKey, {});
  }

  function saveStatusOverrides(value) {
    saveJSON(statusKey, value);
  }

  function saveStatusOverride(tid, status) {
    const overrides = loadStatusOverrides();
    overrides[tid] = status;
    saveStatusOverrides(overrides);
  }

  function clearStatusOverrides() {
    delete memoryStore[statusKey];
    try {
      localStorage.removeItem(statusKey);
    } catch {}
  }

  function loadTagOverrides() {
    return loadJSON(tagKey, {});
  }

  function saveTagOverrides(value) {
    saveJSON(tagKey, value);
  }

  function saveTagOverride(tid, tag) {
    const overrides = loadTagOverrides();
    overrides[tid] = Array.from(new Set([...(overrides[tid] || []), tag.replace(/^#/, "")]));
    saveTagOverrides(overrides);
  }

  function cleanEpicTag(tag) {
    return String(tag || "").replace(/^#/, "");
  }

  function loadEpicPanes() {
    return loadJSON(epicPaneKey, []).map(cleanEpicTag).filter(Boolean);
  }

  function saveEpicPanes(panes) {
    saveJSON(epicPaneKey, Array.from(new Set((panes || []).map(cleanEpicTag).filter(Boolean))));
  }

  function openEpicPane(tag) {
    const panes = loadEpicPanes();
    const clean = cleanEpicTag(tag);
    if (!clean || panes.includes(clean)) return panes;
    const next = [...panes, clean];
    saveEpicPanes(next);
    return next;
  }

  function closeEpicPane(tag) {
    const clean = cleanEpicTag(tag);
    const next = loadEpicPanes().filter((pane) => pane !== clean);
    saveEpicPanes(next);
    return next;
  }

  function taskRankId(item) {
    return item && (item.task_id || item.id);
  }

  function loadRanks() {
    return loadJSON(rankKey, {});
  }

  function saveRanks(ranks) {
    saveJSON(rankKey, ranks || {});
  }

  function rankTasksForPane(items, paneId) {
    const order = loadRanks()[paneId] || [];
    const rank = Object.fromEntries(order.map((id, index) => [id, index]));
    return [...(items || [])].sort((a, b) => {
      const aId = taskRankId(a);
      const bId = taskRankId(b);
      const aRanked = Object.prototype.hasOwnProperty.call(rank, aId);
      const bRanked = Object.prototype.hasOwnProperty.call(rank, bId);
      // Unranked items (never manually dragged, including brand-new stories)
      // float above ranked ones so new stories land at the top by default.
      if (aRanked !== bRanked) return aRanked ? 1 : -1;
      if (aRanked && bRanked) return rank[aId] - rank[bId];
      return 0;
    });
  }

  function moveTaskRank(taskId, fromPaneId, toPaneId, beforeTaskId, fullOrder) {
    const ranks = loadRanks();
    const fromOrder = (ranks[fromPaneId] || []).filter((id) => id !== taskId);
    const toOrder = fullOrder
      ? fullOrder.filter((id) => id !== taskId)
      : fromPaneId === toPaneId ? fromOrder : (ranks[toPaneId] || []).filter((id) => id !== taskId);
    const beforeIndex = beforeTaskId ? toOrder.indexOf(beforeTaskId) : -1;
    if (beforeIndex >= 0) {
      toOrder.splice(beforeIndex, 0, taskId);
    } else {
      toOrder.push(taskId);
    }
    ranks[fromPaneId] = fromOrder;
    ranks[toPaneId] = toOrder;
    saveRanks(ranks);
    return ranks;
  }

  function loadEpicRanks() {
    return loadJSON(epicRankKey, []).map(cleanEpicTag).filter(Boolean);
  }

  function saveEpicRanks(order) {
    saveJSON(epicRankKey, Array.from(new Set((order || []).map(cleanEpicTag).filter(Boolean))));
  }

  function rankEpicTags(tags) {
    const order = loadEpicRanks();
    const rank = Object.fromEntries(order.map((tag, index) => [tag, index]));
    return [...(tags || [])].sort((a, b) => {
      const aRanked = Object.prototype.hasOwnProperty.call(rank, a);
      const bRanked = Object.prototype.hasOwnProperty.call(rank, b);
      // Unlike card ranks, ranked epics sort above unranked ones: the point of
      // reordering is to pin the most important epic on top, so a dragged epic
      // must not be displaced by epics that were never reordered.
      if (aRanked !== bRanked) return aRanked ? -1 : 1;
      if (aRanked && bRanked) return rank[a] - rank[b];
      return 0;
    });
  }

  function moveEpicRank(tag, beforeTag, allTags) {
    const clean = cleanEpicTag(tag);
    const before = cleanEpicTag(beforeTag);
    // Snapshot the full current order (not just the visible subset) so a move
    // made while the pane is dir-filtered keeps every other epic in place.
    const order = rankEpicTags(allTags).filter((item) => item !== clean);
    const beforeIndex = before ? order.indexOf(before) : -1;
    if (beforeIndex >= 0) {
      order.splice(beforeIndex, 0, clean);
    } else {
      order.push(clean);
    }
    saveEpicRanks(order);
    return order;
  }

  function getEffectiveStatus(task, overrides) {
    return overrides[task.task_id] || task.status;
  }

  function epicColor(name) {
    const palette = ["#007AFF", "#FF9500", "#34C759", "#AF52DE", "#FF3B30", "#00A6A6", "#A2845E", "#5E5CE6"];
    let hash = 0;
    for (const ch of String(name || "")) hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
    return palette[Math.abs(hash) % palette.length];
  }

  function epicColorFor(tag, epicMeta) {
    const clean = String(tag || "").replace(/^#/, "");
    const configured = epicMeta && epicMeta[clean] && epicMeta[clean].color;
    if (/^[0-9A-Fa-f]{6}$/.test(configured || "")) return `#${configured}`;
    return epicColor(clean);
  }

  window.FocusLogic = {
    loadStatusOverrides,
    saveStatusOverrides,
    saveStatusOverride,
    clearStatusOverrides,
    loadTagOverrides,
    saveTagOverrides,
    saveTagOverride,
    loadEpicPanes,
    saveEpicPanes,
    openEpicPane,
    closeEpicPane,
    loadRanks,
    saveRanks,
    rankTasksForPane,
    moveTaskRank,
    loadEpicRanks,
    saveEpicRanks,
    rankEpicTags,
    moveEpicRank,
    getEffectiveStatus,
    epicColor,
    epicColorFor
  };
})();
