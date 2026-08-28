import unittest
from pathlib import Path


TREE_VIEWER_DIR = Path(__file__).resolve().parents[2] / "todo" / "tree_viewer"


class StoryTitleLayoutTests(unittest.TestCase):
    def test_collapsed_story_titles_show_up_to_three_lines(self):
        source = (TREE_VIEWER_DIR / "pivotal_tasks.html").read_text(encoding="utf-8")

        self.assertIn("-webkit-line-clamp: 3", source)
        self.assertIn("-webkit-box-orient: vertical", source)
        self.assertNotIn("white-space: nowrap;\n      overflow: hidden;\n      text-overflow: ellipsis", source)

    def test_title_click_expands_first_then_edits_on_second_click(self):
        source = (TREE_VIEWER_DIR / "pivotal_tasks.html").read_text(encoding="utf-8")
        render_task = source[source.index("function renderTask("):source.index("function renderQuickAdd(")]

        # First click selects the card (which unclamps the title); a second
        # click on an already-selected card starts the inline title editor.
        self.assertIn('textEl.addEventListener("click"', render_task)
        self.assertIn("if (PS.selectedTaskId !== task.task_id) return;", render_task)
        self.assertIn('textEl.title = "Click to show full title, click again to edit"', render_task)
        # Never re-enter edit mode while the inline editor is already open.
        self.assertIn('if (card.querySelector(".inline-edit-wrap")) return;', render_task)
        # The double-click fast path stays available.
        self.assertIn('textEl.addEventListener("dblclick"', render_task)

    def test_inline_title_editor_keeps_display_typography(self):
        page = (TREE_VIEWER_DIR / "pivotal_tasks.html").read_text(encoding="utf-8")
        edit = (TREE_VIEWER_DIR / "pivotalEdit.js").read_text(encoding="utf-8")

        # The editor is the same .task-text typography, edited in place —
        # not a swapped-in single-line input.
        self.assertIn('area.className = "task-text inline-editor editing"', edit)
        self.assertIn('area.contentEditable = "plaintext-only"', edit)
        self.assertNotIn('"inline-editor compact-editor"', edit)
        # Caret goes to the click point instead of select-all.
        self.assertNotIn("area.select()", edit)
        self.assertIn("caretRangeFromPoint", edit)

        # Edit-mode CSS keeps the wrap shape identical to display mode.
        editing_css = page[page.index(".task-text.editing"):]
        editing_css = editing_css[:editing_css.index("}")]
        self.assertIn("min-width: 0", editing_css)
        self.assertIn("overflow-wrap: normal", editing_css)
        self.assertIn("word-break: normal", editing_css)

        # Clicks inside the open editor or expanded panel must not toggle
        # card selection.
        self.assertIn('event.target.closest("button, select, input, textarea, .epic-chip, .drag-handle, .inline-edit-wrap, .card-expanded")', page)

    def test_expanded_card_has_no_redundant_title_field(self):
        source = (TREE_VIEWER_DIR / "pivotal_tasks.html").read_text(encoding="utf-8")

        # Titles edit in place on the card itself; the expanded field stack
        # must not repeat the title as a labeled input.
        self.assertNotIn("field-title-input", source)
        self.assertNotIn('makeField("Title"', source)
        self.assertIn('makeField("State"', source)

    def test_collapsed_story_row_does_not_include_an_epic_chip(self):
        source = (TREE_VIEWER_DIR / "pivotal_tasks.html").read_text(encoding="utf-8")
        render_task = source[source.index("function renderTask("):source.index("function renderQuickAdd(")]
        row_meta = render_task[render_task.index('const rowMeta = document.createElement("span");'):render_task.index('const tagWrap = document.createElement("div");')]

        self.assertNotIn("epic-chip", row_meta)
        self.assertNotIn("openEpicPane", row_meta)


if __name__ == "__main__":
    unittest.main()
