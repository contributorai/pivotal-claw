(function () {
  if (!window.EventSource) return;

  let lastVersion = null;

  function connect() {
    const source = new EventSource("/api/events");
    source.onmessage = (event) => {
      // First event on connect just establishes the baseline version;
      // only a change after that means the board data changed elsewhere
      // (another tab's sync, or a file edited outside the browser).
      if (lastVersion === null) {
        lastVersion = event.data;
        return;
      }
      if (event.data === lastVersion) return;
      lastVersion = event.data;
      PivotalSyncUI.refreshFromServer();
    };
  }

  connect();
})();
