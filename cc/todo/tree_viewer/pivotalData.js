(function () {
  function getAllFlat(tasks) {
    const flat = [];
    function visit(task) {
      flat.push(task);
      (task.children || []).forEach(visit);
    }
    (tasks || []).forEach(visit);
    return flat;
  }

  function getTasksByStatus(tasks, status, overrides) {
    return getAllFlat(tasks).filter((task) => FocusLogic.getEffectiveStatus(task, overrides) === status);
  }

  function extractEpicTags(tasks) {
    const tags = new Set();
    getAllFlat(tasks).forEach((task) => (task.tags || []).forEach((tag) => tags.add(tag)));
    return Array.from(tags).sort();
  }

  function getKnownEpicTags(epicMeta) {
    return Object.keys(epicMeta || {}).sort((a, b) => a.localeCompare(b));
  }

  function getTaskEpicTags(task, epicMeta) {
    const known = new Set(getKnownEpicTags(epicMeta));
    return (task.tags || [])
      .map((tag) => String(tag).replace(/^#/, ""))
      .filter((tag) => known.has(tag));
  }

  function taskMatchesEpicFilter(task, selectedEpic, epicMeta) {
    if (!selectedEpic || selectedEpic === "all") return true;
    const taskEpics = getTaskEpicTags(task, epicMeta);
    if (selectedEpic === "__uncategorized") return taskEpics.length === 0;
    return taskEpics.includes(selectedEpic);
  }

  function getKnownDirs(epicMeta) {
    const dirs = new Set();
    Object.values(epicMeta || {}).forEach((meta) => {
      if (meta && meta.dir) dirs.add(meta.dir);
    });
    return Array.from(dirs).sort((a, b) => a.localeCompare(b));
  }

  function getEpicsForDir(dir, epicMeta) {
    return getKnownEpicTags(epicMeta).filter((tag) => (epicMeta[tag] || {}).dir === dir);
  }

  function epicMatchesDirFilter(tag, selectedDir, epicMeta) {
    if (!selectedDir || selectedDir === "all") return true;
    const dir = (epicMeta[tag] || {}).dir;
    if (selectedDir === "__no_dir") return !dir;
    return dir === selectedDir;
  }

  function getEpicsForDirFilter(selectedDir, epicMeta) {
    return getKnownEpicTags(epicMeta).filter((tag) => epicMatchesDirFilter(tag, selectedDir, epicMeta));
  }

  function getTaskDirs(task, epicMeta) {
    const dirs = new Set();
    getTaskEpicTags(task, epicMeta).forEach((tag) => {
      const dir = (epicMeta[tag] || {}).dir;
      if (dir) dirs.add(dir);
    });
    return Array.from(dirs);
  }

  function taskMatchesDirFilter(task, selectedDir, epicMeta) {
    if (!selectedDir || selectedDir === "all") return true;
    const taskDirs = getTaskDirs(task, epicMeta);
    if (selectedDir === "__no_dir") return taskDirs.length === 0;
    return taskDirs.includes(selectedDir);
  }

  function getPortfolios(portfolioData) {
    return ((portfolioData || {}).items || []).filter((item) => item && item.id);
  }

  // Archived portfolios still own their epics, so ownership and counts must keep
  // seeing them — dropping them from getPortfolios would push every story they
  // own into the "unassigned" bucket. Only the filter dropdown hides them.
  function getActivePortfolios(portfolioData) {
    return getPortfolios(portfolioData).filter((item) => !item.archived);
  }

  function getPortfolioEpicTags(id, portfolioData) {
    const portfolio = getPortfolios(portfolioData).find((item) => item.id === id);
    return ((portfolio || {}).epic_tags || []).map((tag) => String(tag).replace(/^#/, ""));
  }

  function getPortfolioForEpic(tag, portfolioData) {
    const owner = getPortfolios(portfolioData).find((item) => getPortfolioEpicTags(item.id, portfolioData).includes(tag));
    return owner ? owner.id : null;
  }

  function epicMatchesPortfolioFilter(tag, selectedPortfolio, portfolioData) {
    if (!selectedPortfolio || selectedPortfolio === "all") return true;
    const owner = getPortfolioForEpic(tag, portfolioData);
    if (selectedPortfolio === "__unassigned") return !owner;
    return owner === selectedPortfolio;
  }

  function getEpicsForPortfolioFilter(selectedPortfolio, epicMeta, portfolioData) {
    return getKnownEpicTags(epicMeta).filter((tag) => epicMatchesPortfolioFilter(tag, selectedPortfolio, portfolioData));
  }

  function getTaskPortfolios(task, epicMeta, portfolioData) {
    const ids = new Set();
    getTaskEpicTags(task, epicMeta).forEach((tag) => {
      const owner = getPortfolioForEpic(tag, portfolioData);
      if (owner) ids.add(owner);
    });
    return Array.from(ids);
  }

  function taskMatchesPortfolioFilter(task, selectedPortfolio, epicMeta, portfolioData) {
    if (!selectedPortfolio || selectedPortfolio === "all") return true;
    const taskPortfolios = getTaskPortfolios(task, epicMeta, portfolioData);
    if (selectedPortfolio === "__unassigned") return taskPortfolios.length === 0;
    return taskPortfolios.includes(selectedPortfolio);
  }

  function countTasksByPortfolio(tasks, epicMeta, portfolioData) {
    const counts = { all: 0, __unassigned: 0 };
    getPortfolios(portfolioData).forEach((item) => {
      counts[item.id] = 0;
    });
    getAllFlat(tasks || []).forEach((task) => {
      counts.all += 1;
      const taskPortfolios = getTaskPortfolios(task, epicMeta, portfolioData);
      if (!taskPortfolios.length) {
        counts.__unassigned += 1;
      }
      taskPortfolios.forEach((id) => {
        counts[id] = (counts[id] || 0) + 1;
      });
    });
    return counts;
  }

  // Digit-key switching. The mapping is pinned per portfolio in portfolio.json rather
  // than derived from list order, so archiving or reordering never silently rebinds a
  // key. Archived portfolios are hidden here for the same reason they are hidden from
  // the dropdown — this is presentation, not ownership.
  function getPortfolioShortcuts(portfolioData) {
    const map = {};
    getActivePortfolios(portfolioData).forEach((item) => {
      const key = String(item.shortcut || "");
      if (key && !map[key]) map[key] = item.id;
    });
    return map;
  }

  function resolvePortfolioShortcut(key, portfolioData) {
    if (key === "0") return "all";
    return getPortfolioShortcuts(portfolioData)[key] || null;
  }

  function countTasksByDir(tasks, epicMeta) {
    const counts = { all: 0, __no_dir: 0 };
    getKnownDirs(epicMeta).forEach((dir) => {
      counts[dir] = 0;
    });
    getAllFlat(tasks || []).forEach((task) => {
      counts.all += 1;
      const taskDirs = getTaskDirs(task, epicMeta);
      if (!taskDirs.length) {
        counts.__no_dir += 1;
      }
      taskDirs.forEach((dir) => {
        counts[dir] = (counts[dir] || 0) + 1;
      });
    });
    return counts;
  }

  function countTasksByEpic(tasks, epicMeta) {
    const counts = { all: 0, __uncategorized: 0 };
    getKnownEpicTags(epicMeta).forEach((tag) => {
      counts[tag] = 0;
    });
    getAllFlat(tasks || []).forEach((task) => {
      counts.all += 1;
      const taskEpics = getTaskEpicTags(task, epicMeta);
      if (!taskEpics.length) {
        counts.__uncategorized += 1;
      }
      taskEpics.forEach((tag) => {
        counts[tag] = (counts[tag] || 0) + 1;
      });
    });
    return counts;
  }

  function countTasksByEpicStatus(tasks, epicMeta, statusOverrides) {
    const counts = {};
    getKnownEpicTags(epicMeta).forEach((tag) => {
      counts[tag] = { total: 0, done: 0 };
    });
    getAllFlat(tasks || []).forEach((task) => {
      const isDone = FocusLogic.getEffectiveStatus(task, statusOverrides) === "Done";
      getTaskEpicTags(task, epicMeta).forEach((tag) => {
        counts[tag].total += 1;
        if (isDone) counts[tag].done += 1;
      });
    });
    return counts;
  }

  function taskMatches(task, query, textOverrides) {
    if (!query) return true;
    const haystack = [
      IceboxLogic.getEffectiveText(task, textOverrides),
      task.section,
      task.project,
      ...(task.tags || [])
    ].join(" ").toLowerCase();
    return haystack.includes(query.toLowerCase());
  }

  window.PivotalData = {
    getAllFlat,
    getTasksByStatus,
    extractEpicTags,
    getKnownEpicTags,
    getTaskEpicTags,
    taskMatchesEpicFilter,
    countTasksByEpic,
    countTasksByEpicStatus,
    getKnownDirs,
    getEpicsForDir,
    epicMatchesDirFilter,
    getEpicsForDirFilter,
    getTaskDirs,
    taskMatchesDirFilter,
    countTasksByDir,
    getPortfolios,
    getActivePortfolios,
    getPortfolioEpicTags,
    getPortfolioForEpic,
    epicMatchesPortfolioFilter,
    getEpicsForPortfolioFilter,
    getTaskPortfolios,
    taskMatchesPortfolioFilter,
    countTasksByPortfolio,
    getPortfolioShortcuts,
    resolvePortfolioShortcut,
    taskMatches
  };
})();
