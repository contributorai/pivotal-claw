"""The server's use of the Postgres operational event store.

The board's Markdown and JSON files stay authoritative; Postgres is a mirror.
These tests pin both halves of that contract: with the store off nothing about
the board changes, and with it on every status transition and session launch
also lands in the ledger.
"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

PIVOTAL_DIR = Path(__file__).resolve().parents[1]
TODO_MODULE_DIR = Path(__file__).resolve().parents[2] / "todo"
sys.path.insert(0, str(PIVOTAL_DIR))
sys.path.insert(0, str(TODO_MODULE_DIR))

import event_store
import server


class RecordingStore:
    """Stand-in for an enabled EventStore, recording what the server asks of it."""

    def __init__(self, enabled=True, fail=False):
        self.enabled = enabled
        self.migrated = 0
        self.events = []
        self.sessions = []
        self.fail = fail
        self.last_error = None

    def migrate(self):
        self.migrated += 1
        return not self.fail

    def record_transition(self, **kwargs):
        if self.fail:
            raise RuntimeError("database is down")
        self.events.append(kwargs)
        return "event-id"

    def record_session_start(self, **kwargs):
        if self.fail:
            raise RuntimeError("database is down")
        self.sessions.append(kwargs)
        return True


def make_board(root: Path) -> Path:
    todo_dir = root / "todo"
    tree = todo_dir / "tree_viewer"
    tree.mkdir(parents=True)
    (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
    (todo_dir / "todo.md").write_text(
        "# Todo\n- [ ] Ship the pulse #pulse ID: t:pulse01\n", encoding="utf-8"
    )
    for name in ("doing.md", "done.md", "icebox.md"):
        (todo_dir / name).write_text(f"# {name[:-3].title()}\n", encoding="utf-8")
    (todo_dir / "epics.md").write_text("# Epics\n\n## #pulse\n", encoding="utf-8")
    return todo_dir


def status_change_payload(todo_dir: Path) -> dict:
    return {
        "version": 1,
        "statusChanges": [{
            "task_id": "t:pulse01",
            "source_file": str(todo_dir / "todo.md"),
            "line_number": 2,
            "expected_text": "Ship the pulse #pulse ID: t:pulse01",
            "from_status": "Pending",
            "new_status": "In progress",
        }],
    }


class EventStoreWiringTests(unittest.TestCase):
    def test_the_store_is_off_by_default_and_the_board_still_works(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = make_board(Path(td))
            app = server.create_app(todo_dir=todo_dir)
            self.assertFalse(app.config["EVENT_STORE"].enabled)

            client = app.test_client()
            response = client.post("/api/sync", json=status_change_payload(todo_dir))

            self.assertEqual(200, response.status_code)
            self.assertEqual(["t:pulse01"], response.get_json()["appliedStatus"])
            self.assertIn("- [/] Ship the pulse", (todo_dir / "doing.md").read_text(encoding="utf-8"))
            events = json.loads((todo_dir / "history_events.json").read_text(encoding="utf-8"))
            self.assertEqual("t:pulse01", events[0]["story_id"])

    def test_an_enabled_store_is_migrated_once_at_startup(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = make_board(Path(td))
            store = RecordingStore()
            server.create_app(todo_dir=todo_dir, event_store_instance=store)
            self.assertEqual(1, store.migrated)

    def test_applied_status_changes_are_mirrored_into_the_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = make_board(Path(td))
            store = RecordingStore()
            app = server.create_app(todo_dir=todo_dir, event_store_instance=store)

            app.test_client().post("/api/sync", json=status_change_payload(todo_dir))

            self.assertEqual(1, len(store.events))
            event = store.events[0]
            self.assertEqual("t:pulse01", event["story_id"])
            self.assertEqual("Pending", event["from_status"])
            self.assertEqual("In progress", event["to_status"])
            self.assertEqual("api/sync", event["source"])

    def test_the_ledger_mirrors_exactly_what_the_json_history_records(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = make_board(Path(td))
            store = RecordingStore()
            app = server.create_app(todo_dir=todo_dir, event_store_instance=store)

            app.test_client().post("/api/sync", json=status_change_payload(todo_dir))

            history = json.loads((todo_dir / "history_events.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history), len(store.events))
            self.assertEqual(history[0]["ts"], store.events[0]["occurred_at"])

    def test_rejected_status_changes_are_not_mirrored(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = make_board(Path(td))
            store = RecordingStore()
            app = server.create_app(todo_dir=todo_dir, event_store_instance=store)
            payload = status_change_payload(todo_dir)
            payload["statusChanges"][0]["expected_text"] = "something else entirely"

            app.test_client().post("/api/sync", json=payload)

            self.assertEqual([], store.events)

    def test_a_failing_store_never_breaks_a_board_write(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = make_board(Path(td))
            store = RecordingStore(fail=True)
            app = server.create_app(todo_dir=todo_dir, event_store_instance=store)

            response = app.test_client().post("/api/sync", json=status_change_payload(todo_dir))

            self.assertEqual(200, response.status_code)
            self.assertEqual(["t:pulse01"], response.get_json()["appliedStatus"])
            self.assertIn("- [/] Ship the pulse", (todo_dir / "doing.md").read_text(encoding="utf-8"))

    def test_a_failing_migration_does_not_stop_the_server_from_starting(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = make_board(Path(td))
            app = server.create_app(todo_dir=todo_dir, event_store_instance=RecordingStore(fail=True))
            self.assertEqual(200, app.test_client().get("/").status_code)


class SessionLaunchMirrorTests(unittest.TestCase):
    class FakeRuntime:
        def preview(self, story, project_dir):
            return "PROMPT"

        def start(self, story, project_dir):
            return {"story": story, "project_dir": project_dir}

        def wait(self, context):
            from cc.pivotal.codex_sessions import ThreadMetadata

            return ThreadMetadata(
                "thread-777", Path(context["project_dir"]).resolve(),
                server.datetime.now(server.timezone.utc), Path("/tmp/thread.jsonl"),
            )

    def launch(self, todo_dir, store):
        app = server.create_app(
            todo_dir=todo_dir, codex_runtime=self.FakeRuntime(), event_store_instance=store
        )
        client = app.test_client()
        token = client.post("/api/codex/launch", json={"story_id": "t:pulse01"}).get_json()["token"]
        for _ in range(50):
            status = client.get(f"/api/codex/launch/{token}").get_json()
            if status["status"] != "pending":
                return status
            time.sleep(0.01)
        return status

    def test_a_registered_session_is_mirrored_into_agent_session_state(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = make_board(Path(td))
            (todo_dir / "epics.md").write_text(
                f"# Epics\n\n## #pulse\ndir: {todo_dir}\n", encoding="utf-8"
            )
            store = RecordingStore()

            self.assertEqual("registered", self.launch(todo_dir, store)["status"])

            self.assertEqual(1, len(store.sessions))
            session = store.sessions[0]
            self.assertEqual("codex", session["provider"])
            self.assertEqual("thread-777", session["session_id"])
            self.assertEqual("t:pulse01", session["story_id"])
            self.assertEqual("codex resume thread-777", session["resume_cmd"])

    def test_a_failing_store_still_registers_the_session_on_the_board(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = make_board(Path(td))
            (todo_dir / "epics.md").write_text(
                f"# Epics\n\n## #pulse\ndir: {todo_dir}\n", encoding="utf-8"
            )

            self.assertEqual("registered", self.launch(todo_dir, RecordingStore(fail=True))["status"])

            sessions = json.loads((todo_dir / "story_sessions.json").read_text(encoding="utf-8"))
            self.assertEqual("thread-777", sessions["t:pulse01"]["sessions"][0]["session_id"])


class DefaultStoreTests(unittest.TestCase):
    def test_the_default_store_is_built_from_the_environment(self):
        self.assertIs(event_store.EventStore, type(event_store.EventStore.from_env(env={})))


if __name__ == "__main__":
    unittest.main()
