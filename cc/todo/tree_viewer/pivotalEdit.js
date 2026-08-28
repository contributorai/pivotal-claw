(function () {
  function cycleStatus(current, direction) {
    const cycle = PivotalState.statusCycle;
    const idx = Math.max(0, cycle.indexOf(current));
    return cycle[(idx + (direction || 1) + cycle.length) % cycle.length];
  }

  function parseInlineTags(raw) {
    const found = [...raw.matchAll(/#([A-Za-z0-9_]+)/g)].map((m) => m[1]);
    const text = raw.replace(/#([A-Za-z0-9_]+)/g, "").replace(/\s+/g, " ").trim();
    return { text, tags: Array.from(new Set(found)) };
  }

  function caretOffset(editor) {
    const sel = window.getSelection();
    if (!sel.rangeCount || !editor.contains(sel.anchorNode)) return editor.textContent.length;
    const range = sel.getRangeAt(0).cloneRange();
    range.selectNodeContents(editor);
    range.setEnd(sel.getRangeAt(0).startContainer, sel.getRangeAt(0).startOffset);
    return range.toString().length;
  }

  function setCaret(editor, offset) {
    const range = document.createRange();
    let remaining = offset;
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node && remaining > node.textContent.length) {
      remaining -= node.textContent.length;
      node = walker.nextNode();
    }
    if (node) range.setStart(node, remaining);
    else range.selectNodeContents(editor);
    range.collapse(true);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function startInlineEdit(card, task, epicMeta, onSave, clickEvent) {
    const textNode = card.querySelector(".task-text");
    const wrap = document.createElement("div");
    wrap.className = "inline-edit-wrap";
    // Same node class as display mode: identical font, weight, size, and wrap
    // shape — edit mode only adds the .editing ring, never retypesets.
    const area = document.createElement("div");
    area.className = "task-text inline-editor editing";
    area.contentEditable = "plaintext-only";
    area.textContent = IceboxLogic.getEffectiveText(task, IceboxLogic.loadTextOverrides());
    wrap.appendChild(area);
    textNode.replaceWith(wrap);
    area.focus();
    let caretRange = null;
    if (clickEvent && document.caretRangeFromPoint) {
      caretRange = document.caretRangeFromPoint(clickEvent.clientX, clickEvent.clientY);
    }
    if (caretRange && area.contains(caretRange.startContainer)) {
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(caretRange);
    } else {
      setCaret(area, area.textContent.length);
    }

    const knownTags = PivotalData.getKnownEpicTags(epicMeta || {});
    let dropdown = null;
    let matches = [];
    let selIdx = 0;
    let saved = false;
    let cancelled = false;
    let commitMeta = null;

    function closeDropdown() {
      if (dropdown) {
        dropdown.remove();
        dropdown = null;
      }
    }

    function renderDropdown() {
      dropdown.innerHTML = "";
      matches.forEach((tag, i) => {
        const row = document.createElement("div");
        row.className = i === selIdx ? "selected" : "";
        row.dataset.tag = tag;
        const dot = document.createElement("span");
        dot.className = "dot";
        dot.style.background = FocusLogic.epicColorFor(tag, epicMeta);
        row.append(dot, document.createTextNode(`#${tag}`));
        dropdown.appendChild(row);
      });
    }

    function openDropdown(query) {
      matches = knownTags.filter((tag) => tag.toLowerCase().startsWith(query));
      closeDropdown();
      if (!matches.length) return;
      selIdx = 0;
      dropdown = document.createElement("div");
      dropdown.className = "tag-autocomplete";
      dropdown.dataset.testid = "tag-autocomplete";
      dropdown.addEventListener("mousedown", (event) => {
        const row = event.target.closest("[data-tag]");
        if (row) {
          event.preventDefault();
          insertTag(row.dataset.tag);
        }
      });
      wrap.appendChild(dropdown);
      renderDropdown();
    }

    function insertTag(tag) {
      const cursor = caretOffset(area);
      const before = area.textContent.slice(0, cursor).replace(/#[A-Za-z0-9_]*$/, "");
      const after = area.textContent.slice(cursor);
      area.textContent = `${before}#${tag} ${after}`.replace(/\s+/g, " ");
      closeDropdown();
      area.focus();
      setCaret(area, `${before}#${tag} `.replace(/\s+/g, " ").length);
    }

    function commit() {
      if (saved || cancelled) return;
      saved = true;
      closeDropdown();
      const { text, tags } = parseInlineTags(area.textContent.trim());
      tags.forEach((tag) => FocusLogic.saveTagOverride(task.task_id, tag));
      onSave(text, commitMeta);
    }

    area.addEventListener("input", () => {
      const cursor = caretOffset(area);
      const match = area.textContent.slice(0, cursor).match(/#([A-Za-z0-9_]*)$/);
      if (match) openDropdown(match[1].toLowerCase());
      else closeDropdown();
    });

    area.addEventListener("keydown", (event) => {
      if (dropdown && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
        event.preventDefault();
        selIdx = (selIdx + (event.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length;
        renderDropdown();
        return;
      }
      if (dropdown && (event.key === "Tab" || event.key === "Enter")) {
        event.preventDefault();
        insertTag(matches[selIdx]);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        commit();
      }
      if (event.key === "Tab") {
        // Commit and hop into the next/previous card's editor (handled by
        // the onSave callback via the meta argument).
        event.preventDefault();
        commitMeta = { tab: true, shift: event.shiftKey };
        commit();
        return;
      }
      if (event.key === "Escape") {
        if (dropdown) {
          closeDropdown();
          return;
        }
        cancelled = true;
        onSave(null);
      }
    });
    area.addEventListener("blur", commit);
  }

  window.PivotalEdit = { cycleStatus, startInlineEdit, parseInlineTags };
})();
