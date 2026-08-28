import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


PIVOTAL_DIR = Path(__file__).resolve().parents[1]
TODO_MODULE_DIR = Path(__file__).resolve().parents[2] / "todo"
REAL_TREE_VIEWER_DIR = TODO_MODULE_DIR / "tree_viewer"
sys.path.insert(0, str(PIVOTAL_DIR))
sys.path.insert(0, str(TODO_MODULE_DIR))

import server


def parse_todo_data_js(source: str) -> dict:
    match = re.match(r"window\.todoData = (.*);\s*$", source, re.S)
    if not match:
        raise AssertionError("todo_data.js did not contain a window.todoData assignment")
    return json.loads(match.group(1))


def flatten_tasks(tasks):
    for task in tasks:
        yield task
        yield from flatten_tasks(task.get("children", []))


class BrowserRegressionTests(unittest.TestCase):
    def test_phase_1_board_loads_and_syncs_new_icebox_item(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            shutil.copytree(REAL_TREE_VIEWER_DIR, todo_dir / "tree_viewer")
            (todo_dir / "todo.md").write_text("- [ ] Existing story #seed #my_epic ID: t:seed123\n", encoding="utf-8")
            (todo_dir / "doing.md").write_text("", encoding="utf-8")
            (todo_dir / "done.md").write_text("", encoding="utf-8")
            (todo_dir / "icebox.md").write_text("- [ ] Icebox story ID: t:ice1234\n", encoding="utf-8")
            (todo_dir / "epics.md").write_text(
                "## #my_epic\n"
                "Main phase epic.\n"
                "dir: /tmp/my-epic\n"
                "resume: 11111111-2222-3333-4444-555555555555\n"
                "session_log: /tmp/my-epic/session.log\n"
                "color: FF5733\n",
                encoding="utf-8",
            )

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            page = client.get("/")
            try:
                self.assertEqual(200, page.status_code)
                page_source = page.get_data(as_text=True)
            finally:
                page.close()
            for hook in [
                'data-testid="kanban-board"',
                'data-testid="pane-toggles"',
                'data-testid="sync-status"',
                'data-testid="epic-filter"',
                'dataset.testid = "epic-chip"',
                'dataset.testid = "epic-picker"',
                'detailPane.dataset.testid = "epic-detail-pane"',
                'form.setAttribute("data-testid", "epic-detail-quick-add")',
                'PivotalDND.wireDropTarget(storyList, paneId, { onDrop: render })',
                'closeButton.dataset.testid = "pane-close"',
                'form.setAttribute("data-testid", "quick-add")',
                'PivotalDND.attachDragHandlers(card, task.task_id, paneId)',
                'PivotalDND.wireDropTarget(list, pane.id, { onDrop: render })',
                '<script src="storyNotes.js"></script>',
                'notesButton.dataset.testid = "story-notes-toggle"',
                'thread.dataset.testid = "story-notes-thread"',
                'menuButton.dataset.testid = "story-menu"',
                'statusSelect.dataset.testid = "compact-status-select"',
                'textEl.addEventListener("dblclick"',
                'card.classList.toggle("selected", PS.selectedTaskId === task.task_id)',
            ]:
                with self.subTest(hook=hook):
                    self.assertIn(hook, page_source)

            dnd_response = client.get("/pivotalDND.js")
            try:
                self.assertEqual(200, dnd_response.status_code)
                dnd_source = dnd_response.get_data(as_text=True)
            finally:
                dnd_response.close()
            for hook in [
                'card.dataset.testid = "kanban-card"',
                'list.dataset.testid = "kanban-drop-target"',
                "FocusLogic.moveTaskRank",
                "FocusLogic.saveStatusOverride",
            ]:
                with self.subTest(hook=hook):
                    self.assertIn(hook, dnd_source)

            notes_response = client.get("/storyNotes.js")
            try:
                self.assertEqual(200, notes_response.status_code)
                notes_source = notes_response.get_data(as_text=True)
            finally:
                notes_response.close()
            for hook in [
                "/api/story-sessions/",
                "/api/story-note",
                "window.StoryNotes",
            ]:
                with self.subTest(hook=hook):
                    self.assertIn(hook, notes_source)

            data_response = client.get("/todo_data.js")
            try:
                self.assertEqual(200, data_response.status_code)
                data = parse_todo_data_js(data_response.get_data(as_text=True))
            finally:
                data_response.close()
            existing = {task["task_id"]: task for task in flatten_tasks(data["tasks"])}
            self.assertEqual("Main phase epic.", data["epic_meta"]["my_epic"]["description"])
            self.assertEqual("FF5733", data["epic_meta"]["my_epic"]["color"])
            self.assertEqual("/tmp/my-epic", data["epic_meta"]["my_epic"]["dir"])
            self.assertEqual(
                "11111111-2222-3333-4444-555555555555",
                data["epic_meta"]["my_epic"]["resume"],
            )
            self.assertEqual("/tmp/my-epic/session.log", data["epic_meta"]["my_epic"]["session_log"])
            self.assertEqual("Pending", existing["t:seed123"]["status"])
            self.assertIn("my_epic", existing["t:seed123"]["tags"])

            sync_response = client.post(
                "/api/sync",
                data=json.dumps(
                    {
                        "version": 1,
                        "newItems": [
                            {
                                "text": "Smoke test icebox item #smoke",
                                "status": "On schedule",
                                "tags": ["smoke"],
                            }
                        ],
                    }
                ),
                content_type="application/json",
            )
            try:
                self.assertEqual(200, sync_response.status_code)
                self.assertEqual(["Smoke test icebox item #smoke"], sync_response.get_json()["appliedNewItems"])
            finally:
                sync_response.close()
            self.assertRegex(
                (todo_dir / "icebox.md").read_text(encoding="utf-8"),
                r"- \[ \] Smoke test icebox item #smoke ID: t:[a-z0-9]{7}",
            )
            epic_sync_response = client.post(
                "/api/sync",
                data=json.dumps(
                    {
                        "version": 1,
                        "tagAdditions": [
                            {
                                "task_id": "t:ice1234",
                                "source_file": str(todo_dir / "icebox.md"),
                                "line_number": 1,
                                "expected_text": "Icebox story ID: t:ice1234",
                                "tags": ["my_epic"],
                            }
                        ],
                    }
                ),
                content_type="application/json",
            )
            try:
                self.assertEqual(200, epic_sync_response.status_code)
                self.assertEqual(["t:ice1234"], epic_sync_response.get_json()["appliedTags"])
            finally:
                epic_sync_response.close()
            self.assertIn("#my_epic", (todo_dir / "icebox.md").read_text(encoding="utf-8"))

            refreshed_response = client.get("/todo_data.js")
            try:
                refreshed = parse_todo_data_js(refreshed_response.get_data(as_text=True))
            finally:
                refreshed_response.close()
            synced_tasks = list(flatten_tasks(refreshed["tasks"]))
            self.assertTrue(
                any(
                    task["display_text"] == "Smoke test icebox item #smoke"
                    and task["status"] == "On schedule"
                    for task in synced_tasks
                )
            )


if __name__ == "__main__":
    unittest.main()
