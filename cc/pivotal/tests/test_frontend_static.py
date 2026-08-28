from pathlib import Path
import re
import unittest


TREE_VIEWER_DIR = Path(__file__).resolve().parents[2] / "todo" / "tree_viewer"
MAIN_PAGE = TREE_VIEWER_DIR / "pivotal_tasks.html"


def read_main_page():
    return MAIN_PAGE.read_text(encoding="utf-8")


class FrontendStaticTests(unittest.TestCase):
    def test_main_page_loads_phase_1_modules_in_order(self):
        source = read_main_page()
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', source)

        self.assertEqual(
            scripts,
            [
                "/todo_data.js",
                "pivotalState.js",
                "pivotalUtils.js",
                "focusLogic.js",
                "iceboxLogic.js",
                "pivotalData.js",
                "pivotalEdit.js",
                "pivotalDND.js",
                "storyNotes.js",
                "codexLaunch.js",
                "syncLogic.js",
                "syncReceipt.js",
                "pivotalSyncUI.js",
                "pivotalSyncStream.js",
            ],
        )

    def test_story_launch_split_button_popover_and_prefs_are_wired(self):
        page = read_main_page()

        for hook in [
            'dataset.testid = "story-launch"',
            'dataset.testid = "story-launch-menu"',
            'dataset.testid = "story-resume-last"',
            'data-testid="prefs-toggle"',
            'id="prefs-menu"',
            'name="default-agent"',
            'localStorage.setItem("pivotalDefaultAgent"',
            'CodexLaunch.launch(task, launchMain, getDefaultAgent())',
            '"/api/sessions/resume"',
            "change in Preferences",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_story_codex_launch_module_previews_polls_and_reports(self):
        module = (TREE_VIEWER_DIR / "codexLaunch.js").read_text(encoding="utf-8")
        for hook in [
            'jsonPost("/api/codex/prompt-preview"',
            'jsonPost("/api/codex/launch"',
            'fetch(`/api/codex/launch/${token}`)',
            'status === "pending"',
            'status === "registered"',
            'global.location.reload()',
            'button.textContent = `Launch failed:',
        ]:
            self.assertIn(hook, module)

    def test_main_page_contains_phase_1_lane_status_strings(self):
        source = read_main_page()

        for status in ["On schedule", "Pending", "In progress", "Done"]:
            with self.subTest(status=status):
                self.assertIn(status, source)

    def test_pane_definitions_are_four_phase_1_lanes(self):
        source = (TREE_VIEWER_DIR / "pivotalState.js").read_text(encoding="utf-8")

        for label in ['label: "Icebox"', 'label: "Todo"', 'label: "Doing"', 'label: "Done"']:
            with self.subTest(label=label):
                self.assertIn(label, source)

        for legacy_label in ['label: "Mine"', 'label: "Backlog"', 'label: "Current"', 'status: "My backlog"']:
            with self.subTest(legacy_label=legacy_label):
                self.assertNotIn(legacy_label, source)

    def test_main_page_contains_sync_controls_and_todo_data(self):
        source = read_main_page()

        self.assertIn("/todo_data.js", source)
        self.assertIn('id="status-bar"', source)
        self.assertIn("PivotalSyncUI", source)
        self.assertIn("Sync", source)

    def test_escape_cancel_marks_inline_editor_closed_before_blur(self):
        source = (TREE_VIEWER_DIR / "pivotalEdit.js").read_text(encoding="utf-8")

        self.assertIn("let cancelled = false", source)
        self.assertIn("cancelled = true", source)
        self.assertIn("if (saved || cancelled) return", source)

    def test_story_linkifier_supports_http_and_local_file_urls_safely(self):
        source = (TREE_VIEWER_DIR / "pivotalUtils.js").read_text(encoding="utf-8")

        self.assertIn("(?:https?:\\/\\/|file:\\/\\/\\/)", source)
        self.assertIn('target="_blank"', source)
        self.assertIn('rel="noreferrer"', source)
        self.assertIn("let html = escapeHTML(text || \"\")", source)

    def test_epic_helper_functions_are_exported(self):
        pivotal_data = (TREE_VIEWER_DIR / "pivotalData.js").read_text(encoding="utf-8")
        focus_logic = (TREE_VIEWER_DIR / "focusLogic.js").read_text(encoding="utf-8")

        for function_name in [
            "getKnownEpicTags",
            "getTaskEpicTags",
            "taskMatchesEpicFilter",
            "countTasksByEpic",
        ]:
            with self.subTest(function_name=function_name):
                self.assertIn(function_name, pivotal_data)

        get_task_epics = re.search(
            r"function getTaskEpicTags\(task, epicMeta\) \{(?P<body>.*?)\n  \}",
            pivotal_data,
            re.DOTALL,
        )
        self.assertIsNotNone(get_task_epics)
        body = get_task_epics.group("body")
        self.assertIn('.map((tag) => String(tag).replace(/^#/, ""))', body)
        self.assertIn(".filter((tag) => known.has(tag))", body)
        self.assertLess(body.index(".map("), body.index(".filter("))

        self.assertIn("epicColorFor", focus_logic)

    def test_main_page_contains_epic_filter_chips_and_sync_hooks(self):
        page = read_main_page()
        sync_logic = (TREE_VIEWER_DIR / "syncLogic.js").read_text(encoding="utf-8")

        for hook in [
            'id="epic-filter"',
            'data-testid="epic-filter"',
            'dataset.testid = "epic-chip"',
            'dataset.testid = "epic-picker"',
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

        self.assertIn("tagAdditions", sync_logic)
        self.assertIn("loadTagOverrides", sync_logic)

    def test_epic_filter_uses_pending_tags_and_queue_tags(self):
        page = read_main_page()

        for hook in [
            "function taskWithEffectiveTags",
            "function tasksWithEffectiveTags",
            "visibleTasksForPane(pane, statusOverrides, textOverrides, tagOverrides)",
            "PivotalData.countTasksByEpic(tasksWithEffectiveTags(window.todoData.tasks || [], tagOverrides), epicMeta)",
            "PivotalData.taskMatchesEpicFilter({ tags: item.tags || [] }, PS.selectedEpic, window.todoData.epic_meta || {})",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_epic_filter_resets_stale_selected_epic_state(self):
        page = read_main_page()

        for hook in [
            "const selectedExists = options.some((option) => option.value === PS.selectedEpic)",
            'if (!selectedExists) PS.selectedEpic = "all"',
            "epicFilterEl.value = PS.selectedEpic",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_dir_filter_helpers_are_exported(self):
        pivotal_data = (TREE_VIEWER_DIR / "pivotalData.js").read_text(encoding="utf-8")

        for function_name in [
            "getKnownDirs",
            "getEpicsForDir",
            "getTaskDirs",
            "taskMatchesDirFilter",
            "countTasksByDir",
        ]:
            with self.subTest(function_name=function_name):
                self.assertIn(function_name, pivotal_data)

    def test_dir_filter_hooks_and_stale_selection_reset(self):
        page = read_main_page()

        for hook in [
            'id="dir-filter"',
            'data-testid="dir-filter"',
            "function renderDirFilter",
            "PivotalData.countTasksByDir(tasksWithEffectiveTags(window.todoData.tasks || [], tagOverrides), epicMeta)",
            "PivotalData.taskMatchesDirFilter(effectiveTask, PS.selectedDir, window.todoData.epic_meta || {})",
            "const selectedExists = options.some((option) => option.value === PS.selectedDir)",
            'if (!selectedExists) PS.selectedDir = "all"',
            "dirFilterEl.value = PS.selectedDir",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_epic_detail_metadata_lists_sibling_epics_in_same_dir(self):
        page = read_main_page()

        self.assertIn("PivotalData.getEpicsForDir(meta.dir, epicMeta || {})", page)
        self.assertIn('row.dataset.testid = "epic-detail-dir-siblings"', page)

    def test_panes_have_close_buttons_that_use_visibility_state(self):
        page = read_main_page()

        for hook in [
            "function closePane(paneId)",
            "PS.paneVisibility[paneId] = false",
            'closeButton.dataset.testid = "pane-close"',
            'closeButton.setAttribute("aria-label", `Close ${pane.label} pane`)',
            "PivotalState.savePaneVisibility(PS.paneVisibility)",
            "renderToggles()",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_epics_pane_is_a_toggleable_pane_listing_all_known_epics(self):
        page = read_main_page()
        state = (TREE_VIEWER_DIR / "pivotalState.js").read_text(encoding="utf-8")

        self.assertIn('{ id: "epics", label: "Epics" }', state)
        for hook in [
            'pane.dataset.testid = "epics-pane"',
            "PivotalData.getKnownEpicTags(epicMeta)",
            'row.dataset.testid = "epic-pane-row"',
            'bar.className = "epic-bar"',
            "PS.selectedEpic = tag",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_epics_pane_hides_done_epics_behind_footer_toggle(self):
        page = read_main_page()

        for hook in [
            "let showDoneEpics = false",
            "stats.total > 0 && stats.done === stats.total",
            "const visibleTags = showDoneEpics ? activeTags.concat(doneTags) : activeTags",
            "renderPaneHeader(paneDef, activeTags.length)",
            'footer.dataset.testid = "epic-done-footer"',
            "showDoneEpics = !showDoneEpics",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_epic_detail_panes_have_state_openers_and_readonly_sections(self):
        page = read_main_page()
        focus_logic = (TREE_VIEWER_DIR / "focusLogic.js").read_text(encoding="utf-8")

        for helper in [
            "loadEpicPanes",
            "saveEpicPanes",
            "openEpicPane",
            "closeEpicPane",
        ]:
            with self.subTest(helper=helper):
                self.assertIn(helper, focus_logic)

        for hook in [
            "function openEpicPane(tag)",
            'tagEl.dataset.testid = "epic-chip"',
            "tagEl.addEventListener(\"click\",",
            "openEpicPane(clean)",
            "PivotalData.taskMatchesEpicFilter(taskWithEffectiveTags(task, tagOverrides), tag, epicMeta)",
            'detailPane.dataset.testid = "epic-detail-pane"',
            'metaList.dataset.testid = "epic-detail-metadata"',
            'form.setAttribute("data-testid", "epic-detail-quick-add")',
            'PivotalDND.wireDropTarget(storyList, paneId, { onDrop: render })',
            'activity.dataset.testid = "epic-detail-activity"',
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_epic_detail_quick_add_queues_story_with_epic_tag(self):
        page = read_main_page()

        for hook in [
            "function renderEpicQuickAdd(tag)",
            "IceboxLogic.addToQueue(text, \"Pending\", [tag])",
            "renderEpicDetailPanes(statusOverrides, textOverrides, tagOverrides)",
            "FocusLogic.loadEpicPanes().forEach((tag)",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_selected_epic_uses_one_mixed_state_focus_pane(self):
        page = read_main_page()

        toggles = page[page.index("function renderToggles()") : page.index("function closePane(")]
        self.assertIn("if (PS.focusEpic)", toggles)
        self.assertIn('button.textContent = `#${PS.focusEpic}`', toggles)

        deep_link = page[page.index("const epic = params.get(\"epic\")") : page.index("const focus = params.get(\"focus\")")]
        self.assertIn("PS.focusEpic = clean;", deep_link)
        self.assertNotIn("FocusLogic.openEpicPane(clean)", deep_link)

        focus_pane = page[page.index("function renderFocusPane(") : page.index("let searchTimer = null")]
        self.assertIn('focusPane.dataset.testid = "focus-pane"', focus_pane)
        self.assertIn("const displayItems = [", focus_pane)
        self.assertIn("ranked.filter((item) => !isDoneItem(item))", focus_pane)
        self.assertNotIn("epic-story-group-label", focus_pane)

    def test_kanban_drag_drop_ranking_contracts_are_wired(self):
        page = read_main_page()
        focus_logic = (TREE_VIEWER_DIR / "focusLogic.js").read_text(encoding="utf-8")
        dnd = (TREE_VIEWER_DIR / "pivotalDND.js").read_text(encoding="utf-8")

        for helper in [
            "loadRanks",
            "saveRanks",
            "rankTasksForPane",
            "moveTaskRank",
        ]:
            with self.subTest(helper=helper):
                self.assertIn(helper, focus_logic)

        for hook in [
            "function attachDragHandlers(card, taskId, paneId)",
            "function wireDropTarget(list, paneId, options)",
            "function handleDrop(targetTaskId)",
            "FocusLogic.moveTaskRank",
            "FocusLogic.saveStatusOverride",
            "card.draggable = true",
            "card.dataset.testid = \"kanban-card\"",
            "list.dataset.testid = \"kanban-drop-target\"",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, dnd)

        for hook in [
            "PivotalDND.attachDragHandlers(card, task.task_id, paneId)",
            "PivotalDND.wireDropTarget(list, pane.id, { onDrop: render })",
            "const tasks = FocusLogic.rankTasksForPane(",
            "const queued = FocusLogic.rankTasksForPane(",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_pointer_drag_engine_and_edit_polish_are_wired(self):
        page = read_main_page()
        dnd = (TREE_VIEWER_DIR / "pivotalDND.js").read_text(encoding="utf-8")
        edit = (TREE_VIEWER_DIR / "pivotalEdit.js").read_text(encoding="utf-8")

        for hook in [
            "pointerdown",
            "pointermove",
            "pointerup",
            "isDragActive",
            "deferRender",
            "event.preventDefault()",
            "classList.add(\"lifted\")",
            "className = \"gap\"",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, dnd)

        for hook in [
            "PivotalDND.deferRender(render)",
            ".task-card.lifted",
            ".task-card.saved-flash",
            "body.dragging-now",
            "function flashSavedCard(taskId)",
            "function nextEditableCardId(taskId, backwards)",
            "function openTitleEditorById(taskId)",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

        self.assertIn("commitMeta = { tab: true, shift: event.shiftKey }", edit)
        self.assertIn("onSave(text, commitMeta)", edit)

    def test_epic_pane_reordering_contracts_are_wired(self):
        page = read_main_page()
        focus_logic = (TREE_VIEWER_DIR / "focusLogic.js").read_text(encoding="utf-8")
        dnd = (TREE_VIEWER_DIR / "pivotalDND.js").read_text(encoding="utf-8")

        for helper in [
            "loadEpicRanks",
            "saveEpicRanks",
            "rankEpicTags",
            "moveEpicRank",
        ]:
            with self.subTest(helper=helper):
                self.assertIn(helper, focus_logic)

        for hook in [
            "function attachEpicDragHandlers(row, tag, allTags, options)",
            "function wireEpicDropTarget(list, options)",
            "function handleEpicDrop(targetTag, options)",
            "FocusLogic.moveEpicRank",
            "row.draggable = true",
            "row.dataset.epicTag = tag",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, dnd)

        for hook in [
            "PivotalDND.attachEpicDragHandlers(row, tag, allTags, { onDrop: render })",
            "PivotalDND.wireEpicDropTarget(list, { onDrop: render })",
            # Both lists are further narrowed by the portfolio filter, so match
            # the ranked source call rather than the closing parens.
            "FocusLogic.rankEpicTags(PivotalData.getEpicsForDirFilter(PS.selectedDir, epicMeta)",
            "FocusLogic.rankEpicTags(PivotalData.getEpicsForPortfolioFilter(PS.selectedPortfolio, epicMeta, data))",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_story_notes_module_and_inline_thread_are_wired(self):
        page = read_main_page()
        notes_path = TREE_VIEWER_DIR / "storyNotes.js"
        notes = notes_path.read_text(encoding="utf-8") if notes_path.exists() else ""

        for hook in [
            'fetch(`/api/story-sessions/${encodeURIComponent(storyId)}`)',
            'fetch("/api/story-note"',
            'method: "POST"',
            "async function load(storyId)",
            "async function add(storyId, text)",
            "window.StoryNotes",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, notes)

        for hook in [
            "function renderStoryNotesThread(task)",
            'notesButton.dataset.testid = "story-notes-toggle"',
            'thread.dataset.testid = "story-notes-thread"',
            "StoryNotes.load(task.task_id)",
            "StoryNotes.add(task.task_id, text)",
            'loading.textContent = "Loading notes..."',
            'empty.textContent = "No notes yet."',
            'error.textContent = "Could not load notes."',
            '["dragstart", "dragover", "drop"].forEach',
            "event.stopPropagation()",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_compact_scan_cards_use_selection_double_click_edit_and_reduced_actions(self):
        page = read_main_page()
        edit = (TREE_VIEWER_DIR / "pivotalEdit.js").read_text(encoding="utf-8")
        state = (TREE_VIEWER_DIR / "pivotalState.js").read_text(encoding="utf-8")

        for hook in [
            "selectedTaskId: null",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, state)

        for hook in [
            'area.className = "task-text inline-editor editing"',
            'area.contentEditable = "plaintext-only"',
            'area.addEventListener("keydown"',
            'event.key === "Enter"',
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, edit)

        for hook in [
            'card.classList.toggle("selected", PS.selectedTaskId === task.task_id)',
            'textEl.addEventListener("dblclick"',
            "PivotalEdit.startInlineEdit(card, task",
            'menuButton.dataset.testid = "story-menu"',
            'expanded.className = "card-expanded"',
            'statusSelect.dataset.testid = "compact-status-select"',
            'notesButton.textContent = `Notes ${noteCount}`',
            'PS.selectedTaskId = PS.selectedTaskId === task.task_id ? null : task.task_id',
            'input.placeholder = "+ Add story"',
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

        for removed in [
            'edit.textContent = "Edit"',
            "actions.append(edit, tagButton, link, notesButton)",
        ]:
            with self.subTest(removed=removed):
                self.assertNotIn(removed, page)

    def test_epic_story_pane_is_story_first_and_opens_beside_epics(self):
        page = read_main_page()

        for hook in [
            'detailPane.classList.add("epic-story-pane")',
            "PivotalDND.wireDropTarget(storyList, paneId, { onDrop: render })",
            "FocusLogic.rankTasksForPane([...queued, ...tasks], paneId)",
            'metadataDetails.dataset.testid = "epic-story-metadata"',
            "panesEl.appendChild(renderEpicPane())",
            "renderEpicDetailPanes(statusOverrides, textOverrides, tagOverrides)",
            'panesEl.scrollTo({ left: Math.max(0, target.offsetLeft - 12), behavior: "smooth" })',
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

        self.assertLess(
            page.index("panesEl.appendChild(renderEpicPane())"),
            page.rindex("renderEpicDetailPanes(statusOverrides, textOverrides, tagOverrides)"),
        )
        self.assertLess(
            page.rindex("renderEpicDetailPanes(statusOverrides, textOverrides, tagOverrides)"),
            page.index("PivotalState.paneDefs.filter((pane) => pane.id !== \"epics\""),
        )

        for removed in [
            'data-filter="all"',
            'data-filter="open"',
            'data-filter="done"',
            'openStories.dataset.testid = "epic-open-stories"',
            'doneStories.dataset.testid = "epic-done-stories"',
            'openLabel.textContent = "Open stories"',
            'doneLabel.textContent = "Completed stories"',
        ]:
            with self.subTest(removed=removed):
                self.assertNotIn(removed, page)

    def test_epic_detail_pane_supports_drag_reorder(self):
        page = read_main_page()

        for hook in [
            "const paneId = `epic-${tag}`;",
            "PivotalDND.wireDropTarget(storyList, paneId, { onDrop: render });",
            "FocusLogic.rankTasksForPane([...queued, ...tasks], paneId);",
            "storyList.appendChild(renderTask(item, statusOverrides, textOverrides, tagOverrides, paneId));",
            "PivotalDND.attachDragHandlers(card, item.id, paneId);",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

        # Done stories must always sort after open ones, regardless of rank order.
        render_epic_detail_pane = page[
            page.index("function renderEpicDetailPane(tag"):
            page.index("function renderEpicDetailPanes(")
        ]
        self.assertIn('=== "Done"', render_epic_detail_pane)
        self.assertIn("displayItems", render_epic_detail_pane)

    def test_sessions_history_page_loads_expected_modules(self):
        source = (TREE_VIEWER_DIR / "sessions_history.html").read_text(encoding="utf-8")
        scripts = re.findall(r'<script[^>]+src="([^"]+)"', source)

        self.assertEqual(scripts, ["/todo_data.js", "sessionsHistoryPage.js"])
        self.assertIn('id="sessions-list"', source)
        self.assertIn('id="refresh-sessions"', source)
        self.assertIn("<h1>Sessions</h1>", source)
        self.assertIn('id="pinned-list"', source)
        self.assertIn('id="pin-chip"', source)
        self.assertIn(".pin-btn", source)

    def test_deep_link_scroll_survives_the_render_that_follows_it(self):
        """A deep-linked story must be scrolled into view, not just selected.

        render() captures panesEl.scrollLeft and restores it after rebuilding the panes.
        highlightDeepLink() scrolls on the next animation frame, so the restore in a later
        render cancelled it: the card was selected, expanded and highlighted, but left
        off-screen horizontally (measured live at left=2028px in a 1512px viewport).
        The restore must be skipped while a deep-link scroll is still outstanding.
        """
        source = read_main_page()

        self.assertIn("deepLinkOwnsScroll", source)
        # the deep link must release scroll on user interaction, not hold it forever
        self.assertIn("dismissDeepLinkScroll", source)
        # the horizontal restore is conditional, not unconditional
        self.assertNotRegex(source, r"\n\s*panesEl\.scrollLeft = prevScrollLeft;")
        self.assertRegex(source, r"if \(![^)]*deepLink[^)]*\)[^\n]*\n?\s*panesEl\.scrollLeft = prevScrollLeft;")

    def test_sessions_history_page_script_hits_sessions_apis_and_board_deep_links(self):
        source = (TREE_VIEWER_DIR / "sessionsHistoryPage.js").read_text(encoding="utf-8")

        self.assertIn("/api/sessions?limit=25", source)
        self.assertIn("/api/sessions/link", source)
        self.assertIn("/api/sessions/resume", source)
        self.assertIn('dataset.testid = "session-resume"', source)
        self.assertIn("/preview", source)
        self.assertIn("/#id=", source)

    def test_sessions_history_page_renders_agent_work_pulse_metrics(self):
        page = (TREE_VIEWER_DIR / "sessions_history.html").read_text(encoding="utf-8")
        source = (TREE_VIEWER_DIR / "sessionsHistoryPage.js").read_text(encoding="utf-8")

        for hook in [
            'id="agent-work-pulse"',
            'data-testid="pulse-active-sessions"',
            'data-testid="pulse-completed-stories"',
            'data-testid="pulse-median-cycle"',
        ]:
            self.assertIn(hook, page)
        self.assertIn('fetch("/api/agent-work-pulse")', source)
        self.assertIn("renderPulse", source)
        self.assertIn('pulse.classList.add("is-unavailable")', source)

    def test_sessions_history_page_script_pins_sessions_and_stories(self):
        source = (TREE_VIEWER_DIR / "sessionsHistoryPage.js").read_text(encoding="utf-8")

        self.assertIn('fetch("/api/pins")', source)
        self.assertIn('"/api/pins/toggle"', source)
        self.assertIn('dataset.testid = "session-pin"', source)
        self.assertIn('kind: "session"', source)
        self.assertIn('kind: "story"', source)
        self.assertIn("renderPinnedSection", source)

    def test_main_page_links_to_sessions_history_page(self):
        source = read_main_page()

        self.assertIn('href="/sessions_history"', source)

    def test_epics_pane_has_new_epic_composer_posting_to_the_epics_api(self):
        page = read_main_page()

        for hook in [
            "let epicComposerOpen = false",
            'toggle.dataset.testid = "epic-new-toggle"',
            'form.dataset.testid = "epic-composer"',
            'tagInput.dataset.testid = "epic-composer-tag"',
            'dirSelect.dataset.testid = "epic-composer-dir"',
            'colorSelect.dataset.testid = "epic-composer-color"',
            'error.dataset.testid = "epic-composer-error"',
            'fetch("/api/epics"',
            "PivotalData.getKnownDirs(epicMeta)",
            "PivotalSyncUI.refreshFromServer()",
        ]:
            with self.subTest(hook=hook):
                self.assertIn(hook, page)

    def test_new_epic_composer_scopes_to_the_selected_portfolio(self):
        page = read_main_page()

        self.assertIn(
            'PS.selectedPortfolio !== "all" && PS.selectedPortfolio !== "__unassigned" ? PS.selectedPortfolio : null',
            page,
        )

    def test_e_key_shortcut_opens_epic_composer_and_focuses_tag_input(self):
        page = read_main_page()

        keydown_block = page[page.index('document.addEventListener("keydown", (e) =>'):]
        self.assertIn('e.key === "e" && !e.ctrlKey && !e.metaKey && !e.altKey', keydown_block[:2000])
        self.assertIn('epic-composer-tag', keydown_block[:2000])


if __name__ == "__main__":
    unittest.main()
