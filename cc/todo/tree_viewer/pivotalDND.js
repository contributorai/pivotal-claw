(function () {
  // Pointer-based drag engine (replaces HTML5 drag events): the real card is
  // lifted out of the flow and rides the cursor 1:1 while a gap placeholder
  // marks the insertion point and neighbours animate aside. Native dragstart
  // is cancelled on every draggable element; `draggable = true` is kept so
  // the elements stay discoverable as drag sources.
  const DRAG_THRESHOLD_PX = 4;
  const SCROLL_EDGE_PX = 48;
  const SCROLL_MAX_PX = 14;

  let dragState = null;
  let pendingDrop = null;
  let pendingEpicDrop = null;
  let deferredRender = null;
  let suppressClickUntil = 0;
  const listMeta = new WeakMap();

  function paneStatus(paneId) {
    const pane = (window.PivotalState && PivotalState.paneDefs || []).find((item) => item.id === paneId);
    return pane && pane.status;
  }

  function isDragActive() {
    return !!(dragState && dragState.started);
  }

  function deferRender(fn) {
    deferredRender = fn;
  }

  function flushDeferredRender() {
    const fn = deferredRender;
    deferredRender = null;
    if (fn) fn();
  }

  // A completed drag must not fall through as a click (selection toggle,
  // epic filter). Click suppression is time-boxed so it can never eat a
  // later intentional click.
  document.addEventListener("click", (event) => {
    if (performance.now() < suppressClickUntil) {
      event.stopPropagation();
      event.preventDefault();
    }
  }, true);

  function attachDragHandlers(card, taskId, paneId) {
    card.draggable = true;
    card.dataset.testid = "kanban-card";
    card.dataset.taskId = taskId;
    card.dataset.paneId = paneId;
    card.addEventListener("dragstart", (event) => event.preventDefault());
    card.addEventListener("pointerdown", (event) => {
      if (event.target.closest("button, select, input, textarea, a, .epic-chip, .inline-edit-wrap, [contenteditable], .story-notes-thread, .tag-autocomplete, .card-expanded")) return;
      beginPointerDrag(event, { kind: "card", el: card, id: taskId, paneId });
    });
  }

  function wireDropTarget(list, paneId, options) {
    list.dataset.testid = "kanban-drop-target";
    list.dataset.paneId = paneId;
    listMeta.set(list, { paneId, options: options || {} });
  }

  function handleDrop(targetTaskId) {
    const targetPaneId = arguments[1];
    const options = arguments[2];
    const fullOrder = arguments[3];
    if (!pendingDrop || !pendingDrop.taskId || pendingDrop.taskId === targetTaskId) return;
    FocusLogic.moveTaskRank(pendingDrop.taskId, pendingDrop.paneId, targetPaneId, targetTaskId, fullOrder);
    if (pendingDrop.paneId !== targetPaneId) {
      const status = paneStatus(targetPaneId);
      if (status) FocusLogic.saveStatusOverride(pendingDrop.taskId, status);
    }
    pendingDrop = null;
    if (options && typeof options.onDrop === "function") options.onDrop();
  }

  function attachEpicDragHandlers(row, tag, allTags, options) {
    row.draggable = true;
    row.dataset.epicTag = tag;
    row.addEventListener("dragstart", (event) => event.preventDefault());
    row.addEventListener("pointerdown", (event) => {
      beginPointerDrag(event, { kind: "epic", el: row, id: tag, tag, allTags, options: options || {} });
    });
  }

  function wireEpicDropTarget(list, options) {
    list.dataset.epicDropTarget = "1";
    listMeta.set(list, { options: options || {} });
  }

  function handleEpicDrop(targetTag, options) {
    if (!pendingEpicDrop || !pendingEpicDrop.tag || pendingEpicDrop.tag === targetTag) return;
    FocusLogic.moveEpicRank(pendingEpicDrop.tag, targetTag, pendingEpicDrop.allTags);
    pendingEpicDrop = null;
    if (options && typeof options.onDrop === "function") options.onDrop();
  }

  /* ---- pointer engine ---- */

  function beginPointerDrag(event, config) {
    if (dragState || event.button !== 0) return;
    dragState = Object.assign({}, config, {
      started: false,
      startX: event.clientX,
      startY: event.clientY,
      x: event.clientX,
      y: event.clientY,
      pointerId: event.pointerId,
      raf: 0,
    });
  }

  document.addEventListener("pointermove", (event) => {
    if (!dragState) return;
    if (!dragState.started) {
      if (Math.hypot(event.clientX - dragState.startX, event.clientY - dragState.startY) < DRAG_THRESHOLD_PX) return;
      lift();
    }
    dragState.x = event.clientX;
    dragState.y = event.clientY;
    positionLifted();
    placeGapUnderPointer();
  });

  document.addEventListener("pointerup", () => {
    if (!dragState) return;
    if (!dragState.started) {
      dragState = null;
      return;
    }
    finishDrop(false);
  });

  document.addEventListener("pointercancel", () => {
    if (dragState && dragState.started) finishDrop(true);
    else dragState = null;
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dragState && dragState.started) finishDrop(true);
  });

  function lift() {
    // Mark active before committing an open editor: its onSave triggers
    // render(), which must be deferred, not executed under our feet.
    dragState.started = true;
    // Capture only once a drag is real. Capturing on pointerdown would
    // retarget the compatibility mouse events of a plain click to the card,
    // so clicks on inner elements (title, buttons) would never reach them.
    try {
      dragState.el.setPointerCapture(dragState.pointerId);
    } catch (err) {
      /* capture is a nicety; dragging works without it */
    }
    const editor = document.querySelector(".inline-editor");
    if (editor) editor.blur();
    const sel = window.getSelection();
    if (sel) sel.removeAllRanges();

    const el = dragState.el;
    const rect = el.getBoundingClientRect();
    dragState.offsetX = dragState.startX - rect.left;
    dragState.offsetY = dragState.startY - rect.top;
    dragState.originList = el.parentElement;
    dragState.originNext = el.nextElementSibling;

    const gap = document.createElement("div");
    gap.className = "gap";
    gap.style.height = rect.height + "px";
    el.before(gap);
    dragState.gap = gap;

    el.classList.add("lifted");
    el.style.width = rect.width + "px";
    el.style.left = rect.left + "px";
    el.style.top = rect.top + "px";
    document.body.appendChild(el);
    document.body.classList.add("dragging-now");
    positionLifted();
    autoScrollLoop();
  }

  function positionLifted() {
    if (!dragState.started) return;
    const el = dragState.el;
    const dx = dragState.x - dragState.offsetX - parseFloat(el.style.left);
    const dy = dragState.y - dragState.offsetY - parseFloat(el.style.top);
    el.style.transform = `translate(${dx}px, ${dy}px) rotate(1.5deg) scale(1.02)`;
  }

  function targetListUnderPointer() {
    const el = document.elementFromPoint(dragState.x, dragState.y);
    if (!el) return null;
    const pane = el.closest("section.pane");
    if (!pane) return null;
    return dragState.kind === "epic"
      ? pane.querySelector("[data-epic-drop-target]")
      : pane.querySelector('[data-testid="kanban-drop-target"]');
  }

  function rowsOf(list) {
    return [...list.children].filter((child) => child.classList.contains("task-card"));
  }

  function flipLists(lists, mutate) {
    const els = [];
    for (const list of new Set(lists)) {
      if (list) els.push(...rowsOf(list));
    }
    const before = new Map(els.map((el) => [el, el.getBoundingClientRect()]));
    mutate();
    for (const el of els) {
      const b = before.get(el);
      if (!b || !el.isConnected) continue;
      const a = el.getBoundingClientRect();
      const dx = b.left - a.left;
      const dy = b.top - a.top;
      if (dx || dy) {
        el.animate(
          [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
          { duration: 180, easing: "cubic-bezier(0.2, 0.7, 0.3, 1)" }
        );
      }
    }
  }

  function placeGapUnderPointer() {
    if (!dragState.started) return;
    const list = targetListUnderPointer();
    if (!list) return;
    const gap = dragState.gap;
    const rows = rowsOf(list);
    let index = 0;
    for (const row of rows) {
      const rect = row.getBoundingClientRect();
      if (dragState.y > rect.top + rect.height / 2) index++;
    }
    const anchor = rows[index] || null;
    const already = gap.parentElement === list &&
      (anchor ? gap.nextElementSibling === anchor : gap === list.lastElementChild);
    if (already) return;
    flipLists([gap.parentElement, list], () => {
      anchor ? list.insertBefore(gap, anchor) : list.appendChild(gap);
    });
  }

  function autoScrollLoop() {
    if (!dragState || !dragState.started) return;
    const list = targetListUnderPointer();
    if (list) {
      const rect = list.getBoundingClientRect();
      if (dragState.y < rect.top + SCROLL_EDGE_PX) {
        list.scrollTop -= SCROLL_MAX_PX * (1 - (dragState.y - rect.top) / SCROLL_EDGE_PX);
      } else if (dragState.y > rect.bottom - SCROLL_EDGE_PX) {
        list.scrollTop += SCROLL_MAX_PX * (1 - (rect.bottom - dragState.y) / SCROLL_EDGE_PX);
      }
    }
    const panes = document.getElementById("panes");
    if (panes) {
      const rect = panes.getBoundingClientRect();
      if (dragState.x < rect.left + SCROLL_EDGE_PX) panes.scrollLeft -= SCROLL_MAX_PX;
      else if (dragState.x > rect.right - SCROLL_EDGE_PX) panes.scrollLeft += SCROLL_MAX_PX;
    }
    dragState.raf = requestAnimationFrame(autoScrollLoop);
  }

  function animateInto(el, fromRect, settle) {
    const to = el.getBoundingClientRect();
    el.animate(
      [{ transform: `translate(${fromRect.left - to.left}px, ${fromRect.top - to.top}px) rotate(1.5deg) scale(1.02)` },
       { transform: "none" }],
      { duration: 200, easing: "cubic-bezier(0.2, 0.7, 0.3, 1)" }
    );
    if (settle) {
      el.classList.add("settled");
      el.addEventListener("animationend", () => el.classList.remove("settled"), { once: true });
    }
  }

  function finishDrop(cancelled) {
    const state = dragState;
    cancelAnimationFrame(state.raf);
    suppressClickUntil = performance.now() + 350;
    document.body.classList.remove("dragging-now");

    if (cancelled) {
      const from = state.el.getBoundingClientRect();
      state.el.classList.remove("lifted");
      state.el.style.cssText = "";
      const backInOrigin = state.originList && state.originList.isConnected;
      if (backInOrigin) {
        if (state.originNext && state.originNext.isConnected && state.originNext.parentElement === state.originList) {
          state.originList.insertBefore(state.el, state.originNext);
        } else {
          state.originList.appendChild(state.el);
        }
      } else {
        state.el.remove();
      }
      state.gap.remove();
      if (backInOrigin) animateInto(state.el, from, false);
      dragState = null;
      flushDeferredRender();
      return;
    }

    const from = state.el.getBoundingClientRect();
    const gap = state.gap;
    const targetList = gap.parentElement;
    const siblings = [...targetList.children];
    const nextRow = siblings
      .slice(siblings.indexOf(gap) + 1)
      .find((child) => child.classList.contains("task-card"));
    const fullOrder = state.kind === "card" ? rowsOf(targetList).map((row) => row.dataset.taskId) : null;
    gap.remove();
    state.el.remove();

    // Drag is over before the data mutation so the resulting render (via
    // onDrop) runs immediately; any render deferred mid-drag is superseded.
    dragState = null;
    deferredRender = null;

    if (state.kind === "card") {
      const meta = listMeta.get(targetList) || { paneId: state.paneId, options: {} };
      const beforeTaskId = nextRow ? nextRow.dataset.taskId : null;
      pendingDrop = { taskId: state.id, paneId: state.paneId };
      handleDrop(beforeTaskId, meta.paneId, meta.options, fullOrder);
    } else {
      const beforeTag = nextRow ? nextRow.dataset.epicTag : null;
      pendingEpicDrop = { tag: state.tag, allTags: state.allTags };
      handleEpicDrop(beforeTag, state.options);
    }

    const selector = state.kind === "card"
      ? `[data-task-id="${CSS.escape(String(state.id))}"]`
      : `[data-epic-tag="${CSS.escape(String(state.tag))}"]`;
    const panes = document.getElementById("panes");
    const landed = panes && panes.querySelector(selector);
    if (landed) animateInto(landed, from, true);
  }

  window.PivotalDND = {
    attachDragHandlers,
    wireDropTarget,
    handleDrop,
    attachEpicDragHandlers,
    wireEpicDropTarget,
    handleEpicDrop,
    isDragActive,
    deferRender,
  };
})();
