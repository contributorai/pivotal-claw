(function () {
  const paneDefs = [
    { id: "epics", label: "Epics" },
    { id: "icebox", label: "Icebox", status: "On schedule" },
    { id: "todo", label: "Todo", status: "Pending" },
    { id: "doing", label: "Doing", status: "In progress" },
    { id: "done", label: "Done", status: "Done" }
  ];
  const statusCycle = ["On schedule", "Pending", "In progress", "Done"];
  const visibilityKey = "todo.pivotal.panes";
  const defaultVisibility = Object.fromEntries(paneDefs.map((pane) => [pane.id, true]));
  const memoryStore = {};

  function loadPaneVisibility() {
    try {
      return { ...defaultVisibility, ...JSON.parse(localStorage.getItem(visibilityKey) || "{}") };
    } catch {
      return { ...defaultVisibility, ...(memoryStore[visibilityKey] || {}) };
    }
  }

  function savePaneVisibility(visibility) {
    memoryStore[visibilityKey] = visibility;
    try {
      localStorage.setItem(visibilityKey, JSON.stringify(visibility));
    } catch {}
  }

  const ps = {
    allTasks: [],
    query: "",
    selectedTaskId: null,
    focusEpic: null,
    paneVisibility: loadPaneVisibility(),
    render: null
  };

  window.PivotalState = { paneDefs, statusCycle, ps, loadPaneVisibility, savePaneVisibility };
})();
