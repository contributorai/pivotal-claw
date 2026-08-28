(function () {
  // Pointer-based row reordering for the /portfolio stack rank. The board's
  // pivotalDND.js is welded to panes, lanes and PivotalState, so it can't be
  // reused here; this is the same idiom (pointer events do the work, native
  // dragging never starts) cut down to "move a row within one list".
  const DRAG_THRESHOLD_PX = 4;
  // A pointerup that ends a drag is followed by a click on the row that was just
  // dragged. Rows are clickable (collapse/expand), so that click has to be
  // ignored — time-boxed, as pivotalDND.js does, so it can never eat a later
  // intentional click.
  const CLICK_SUPPRESS_MS = 250;

  function moveWithin(list, fromIndex, toIndex) {
    const result = list.slice();
    if (fromIndex < 0 || fromIndex >= result.length || fromIndex === toIndex) return result;
    const clamped = Math.max(0, Math.min(result.length - 1, toIndex));
    const moved = result.splice(fromIndex, 1)[0];
    result.splice(clamped, 0, moved);
    return result;
  }

  function rowsIn(container, rowSelector) {
    return Array.from(container.querySelectorAll(":scope > " + rowSelector));
  }

  function orderedIds(container, rowSelector) {
    return rowsIn(container, rowSelector).map((row) => row.dataset.rankId);
  }

  // Which slot the cursor is over, by midpoint: dragging past half of a
  // neighbour is what swaps with it, so tall and short rows feel the same.
  function targetIndex(rows, clientY) {
    for (let i = 0; i < rows.length; i += 1) {
      const rect = rows[i].getBoundingClientRect();
      if (clientY < rect.top + rect.height / 2) return i;
    }
    return rows.length - 1;
  }

  function isClickAfterDrag(container) {
    const endedAt = Number(container.dataset.dragEndedAt || 0);
    return endedAt > 0 && performance.now() - endedAt < CLICK_SUPPRESS_MS;
  }

  function attachRowReorder(container, options) {
    const rowSelector = options.rowSelector;
    const handleSelector = options.handleSelector;
    const onCommit = options.onCommit || function () {};
    let state = null;

    function finish(commit) {
      if (!state) return;
      const current = state;
      state = null;
      if (current.started) {
        current.row.classList.remove("dragging");
        container.classList.remove("reordering");
        container.dataset.dragEndedAt = String(performance.now());
      }
      try {
        current.row.releasePointerCapture(current.pointerId);
      } catch (err) {
        /* the capture is already gone; nothing to release */
      }
      const order = orderedIds(container, rowSelector);
      if (commit && current.started && current.startOrder.join(" ") !== order.join(" ")) {
        onCommit(order);
      }
    }

    container.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const handle = event.target.closest(handleSelector);
      if (!handle || !container.contains(handle)) return;
      const row = handle.closest(rowSelector);
      if (!row || row.parentNode !== container) return;
      state = {
        row,
        pointerId: event.pointerId,
        startY: event.clientY,
        started: false,
        startOrder: orderedIds(container, rowSelector),
      };
      row.setPointerCapture(event.pointerId);
      event.preventDefault();
    });

    container.addEventListener("pointermove", (event) => {
      if (!state || event.pointerId !== state.pointerId) return;
      if (!state.started) {
        if (Math.abs(event.clientY - state.startY) < DRAG_THRESHOLD_PX) return;
        state.started = true;
        state.row.classList.add("dragging");
        container.classList.add("reordering");
      }
      const rows = rowsIn(container, rowSelector);
      const from = rows.indexOf(state.row);
      const to = targetIndex(rows, event.clientY);
      if (to === from) return;
      moveWithin(rows, from, to).forEach((row) => container.appendChild(row));
      if (options.onMove) options.onMove();
    });

    container.addEventListener("pointerup", () => finish(true));
    container.addEventListener("pointercancel", () => finish(false));
  }

  window.PortfolioRank = {
    moveWithin,
    orderedIds,
    isClickAfterDrag,
    attachRowReorder,
  };
})();
