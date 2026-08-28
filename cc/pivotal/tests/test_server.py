import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys


PIVOTAL_DIR = Path(__file__).resolve().parents[1]
TODO_MODULE_DIR = Path(__file__).resolve().parents[2] / "todo"
sys.path.insert(0, str(PIVOTAL_DIR))
sys.path.insert(0, str(TODO_MODULE_DIR))

import server
from cc.pivotal.codex_sessions import ThreadMetadata


class ServerTests(unittest.TestCase):
    def test_favicon_request_returns_empty_success(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir(parents=True)
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")

            response = server.create_app(todo_dir=todo_dir).test_client().get("/favicon.ico")

            self.assertEqual(204, response.status_code)
            self.assertEqual(b"", response.data)

    def test_running_annotations_only_extend_linked_provider_sessions(self):
        records = {
            "t:story12": {
                "sessions": [
                    {"provider": "claude", "session_id": "live-session"},
                    {"provider": "codex", "session_id": "stopped-session"},
                    {"session_id": "legacy-session"},
                    {"provider": "shell", "session_id": "unsupported-session"},
                    {"provider": "claude", "session_id": ""},
                ],
                "notes": [],
            }
        }
        original = server.terminal_focus.list_running_sessions
        server.terminal_focus.list_running_sessions = lambda: {
            ("claude", "live-session"): {"tty": "ttys001"}
        }
        try:
            annotated = server.annotate_running_sessions(records)
        finally:
            server.terminal_focus.list_running_sessions = original

        sessions = annotated["t:story12"]["sessions"]
        self.assertIs(True, sessions[0]["running"])
        self.assertIs(False, sessions[1]["running"])
        for legacy_or_invalid in sessions[2:]:
            self.assertNotIn("running", legacy_or_invalid)

    def test_main_loop_executive_overview_route_serves_the_stable_project_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            todo_dir = root / "todo"
            tree = todo_dir / "tree_viewer"
            tree.mkdir(parents=True)
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            overview = root / "project-executive-overview.html"
            overview.write_text("<h1>Main Loop executive overview</h1>", encoding="utf-8")

            original = server.MAIN_LOOP_EXECUTIVE_OVERVIEW
            server.MAIN_LOOP_EXECUTIVE_OVERVIEW = overview
            try:
                response = server.create_app(todo_dir=todo_dir).test_client().get(
                    "/main-loop-executive-overview"
                )
            finally:
                server.MAIN_LOOP_EXECUTIVE_OVERVIEW = original

            self.assertEqual(200, response.status_code)
            self.assertIn("Main Loop executive overview", response.get_data(as_text=True))

    def test_welcome_and_skill_routes_serve_onboarding_files(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir(parents=True)
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            (tree / "welcome.html").write_text("<h1>Welcome to Pivotal</h1>", encoding="utf-8")
            (tree / "skill.md").write_text("---\nname: pivotal\n---\n", encoding="utf-8")

            client = server.create_app(todo_dir=todo_dir).test_client()

            welcome = client.get("/welcome")
            self.assertEqual(200, welcome.status_code)
            self.assertIn("Welcome to Pivotal", welcome.get_data(as_text=True))

            skill = client.get("/skill.md")
            self.assertEqual(200, skill.status_code)
            self.assertIn("text/markdown", skill.content_type)
            self.assertIn("name: pivotal", skill.get_data(as_text=True))

    def test_server_script_loads_from_its_own_directory(self):
        completed = subprocess.run(
            [sys.executable, "-c", "import runpy; runpy.run_path('server.py', run_name='server_test')"],
            cwd=PIVOTAL_DIR,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)

    def codex_fixture(self, td):
        todo_dir = Path(td)
        tree = todo_dir / "tree_viewer"
        tree.mkdir()
        (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
        (todo_dir / "todo.md").write_text("# Todo\n- [ ] Launch me #sessions ID: t:launch1\n", encoding="utf-8")
        for name, heading in [("doing.md", "Doing"), ("done.md", "Done"), ("icebox.md", "Icebox")]:
            (todo_dir / name).write_text(f"# {heading}\n", encoding="utf-8")
        (todo_dir / "epics.md").write_text(f"# Epics\n\n## #sessions\nSession work.\ndir: {todo_dir}\n", encoding="utf-8")
        return todo_dir

    def test_codex_prompt_preview_and_launch_register_then_move_story(self):
        class FakeRuntime:
            def preview(self, story, project_dir):
                return f"PROMPT {story['task_id']} {project_dir}"
            def start(self, story, project_dir):
                return {"story": story, "project_dir": project_dir}
            def wait(self, context):
                return ThreadMetadata("thread-123", Path(context["project_dir"]).resolve(), server.datetime.now(server.timezone.utc), Path("/tmp/thread.jsonl"))

        with tempfile.TemporaryDirectory() as td:
            todo_dir = self.codex_fixture(td)
            client = server.create_app(todo_dir=todo_dir, codex_runtime=FakeRuntime()).test_client()
            preview = client.post("/api/codex/prompt-preview", json={"story_id": "t:launch1"})
            self.assertEqual(200, preview.status_code)
            self.assertIn("PROMPT t:launch1", preview.get_json()["prompt"])

            launched = client.post("/api/codex/launch", json={"story_id": "t:launch1"})
            self.assertEqual(202, launched.status_code)
            token = launched.get_json()["token"]
            status = None
            for _ in range(50):
                status = client.get(f"/api/codex/launch/{token}").get_json()
                if status["status"] != "pending":
                    break
                time.sleep(0.01)
            self.assertEqual("registered", status["status"])
            self.assertEqual("thread-123", status["session_id"])
            sessions = json.loads((todo_dir / "story_sessions.json").read_text())
            self.assertEqual("codex resume thread-123", sessions["t:launch1"]["sessions"][0]["resume_cmd"])
            self.assertIn("session_start", sessions["t:launch1"]["notes"][0]["type"])
            self.assertIn("Launch me", (todo_dir / "doing.md").read_text())
            self.assertNotIn("Launch me", (todo_dir / "todo.md").read_text())

    def test_codex_launch_failure_does_not_move_or_register_story(self):
        class FailingRuntime:
            def preview(self, story, project_dir): return "prompt"
            def start(self, story, project_dir): return {}
            def wait(self, context): raise TimeoutError("no thread")

        with tempfile.TemporaryDirectory() as td:
            todo_dir = self.codex_fixture(td)
            client = server.create_app(todo_dir=todo_dir, codex_runtime=FailingRuntime()).test_client()
            token = client.post("/api/codex/launch", json={"story_id": "t:launch1"}).get_json()["token"]
            for _ in range(50):
                status = client.get(f"/api/codex/launch/{token}").get_json()
                if status["status"] != "pending": break
                time.sleep(0.01)
            self.assertEqual("failed", status["status"])
            self.assertIn("Launch me", (todo_dir / "todo.md").read_text())
            self.assertFalse((todo_dir / "story_sessions.json").exists())

    def test_codex_endpoints_reject_unknown_story(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = self.codex_fixture(td)
            client = server.create_app(todo_dir=todo_dir, codex_runtime=object()).test_client()
            for endpoint in ["/api/codex/prompt-preview", "/api/codex/launch"]:
                response = client.post(endpoint, json={"story_id": "t:missing"})
                self.assertEqual(404, response.status_code)

    def test_claude_prompt_preview_and_launch_register_then_move_story(self):
        class FakeClaudeRuntime:
            def preview(self, story, project_dir):
                return f"PROMPT {story['task_id']} {project_dir}"
            def start(self, story, project_dir):
                return {"story": story, "project_dir": project_dir}
            def wait(self, context):
                return SimpleNamespace(session_id="claude-session-123")

        with tempfile.TemporaryDirectory() as td:
            todo_dir = self.codex_fixture(td)
            client = server.create_app(todo_dir=todo_dir, claude_runtime=FakeClaudeRuntime()).test_client()
            preview = client.post("/api/codex/prompt-preview", json={"story_id": "t:launch1", "provider": "claude"})
            self.assertEqual(200, preview.status_code)
            self.assertIn("PROMPT t:launch1", preview.get_json()["prompt"])

            launched = client.post("/api/codex/launch", json={"story_id": "t:launch1", "provider": "claude"})
            self.assertEqual(202, launched.status_code)
            token = launched.get_json()["token"]
            status = None
            for _ in range(50):
                status = client.get(f"/api/codex/launch/{token}").get_json()
                if status["status"] != "pending":
                    break
                time.sleep(0.01)
            self.assertEqual("registered", status["status"])
            self.assertEqual("claude-session-123", status["session_id"])
            sessions = json.loads((todo_dir / "story_sessions.json").read_text())
            entry = sessions["t:launch1"]["sessions"][0]
            self.assertEqual("claude", entry["provider"])
            self.assertEqual("claude --resume claude-session-123", entry["resume_cmd"])
            self.assertIn("Launch me", (todo_dir / "doing.md").read_text())
            self.assertNotIn("Launch me", (todo_dir / "todo.md").read_text())

    def test_launch_endpoints_reject_unknown_provider(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = self.codex_fixture(td)
            client = server.create_app(todo_dir=todo_dir, codex_runtime=object(), claude_runtime=object()).test_client()
            for endpoint in ["/api/codex/prompt-preview", "/api/codex/launch"]:
                response = client.post(endpoint, json={"story_id": "t:launch1", "provider": "gemini"})
                self.assertEqual(400, response.status_code)
    def test_story_sessions_migrate_legacy_lists_and_preserve_global_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "story_sessions.json"
            path.write_text(
                json.dumps(
                    {
                        "t:legacy1": [{"session_id": "session-1"}],
                        "t:normal1": {
                            "sessions": [{"session_id": "session-2"}],
                            "notes": [{"type": "note", "text": "keep me"}],
                        },
                        "pin": {"session-1": True},
                        "pinned_epics": {"phase_3": True},
                    }
                ),
                encoding="utf-8",
            )

            data = server.read_story_sessions(path)

            self.assertEqual(
                {"sessions": [{"session_id": "session-1"}], "notes": []},
                data["t:legacy1"],
            )
            self.assertEqual("keep me", data["t:normal1"]["notes"][0]["text"])
            self.assertEqual({"session-1": True}, data["pin"])
            self.assertEqual({"phase_3": True}, data["pinned_epics"])

    def test_story_sessions_update_recovers_from_corrupt_json_and_writes_atomically(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "story_sessions.json"
            path.write_text("{bad json", encoding="utf-8")

            def add_story(data):
                data["t:new1234"] = {"sessions": [], "notes": []}
                return data

            resolved = server.update_story_sessions(path, add_story)

            self.assertEqual({"sessions": [], "notes": []}, resolved["t:new1234"])
            self.assertEqual(resolved, json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual([], list(path.parent.glob("story_sessions.json.*")))

    def test_history_update_recovers_from_missing_or_corrupt_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "history_events.json"

            def add_event(events):
                events.insert(0, {"type": "transition", "story_id": "t:event12"})
                return events

            first = server.update_history_events(path, add_event)
            self.assertEqual("t:event12", first[0]["story_id"])

            path.write_text("not json", encoding="utf-8")
            second = server.update_history_events(path, add_event)
            self.assertEqual([{"type": "transition", "story_id": "t:event12"}], second)

    def test_story_session_updates_do_not_lose_concurrent_changes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "story_sessions.json"

            def add_story(index):
                def modifier(data):
                    data[f"t:thread{index}"] = {"sessions": [], "notes": []}
                    return data

                server.update_story_sessions(path, modifier)

            threads = [threading.Thread(target=add_story, args=(index,)) for index in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            data = server.read_story_sessions(path)
            self.assertEqual(8, len(data))
            self.assertEqual({f"t:thread{index}" for index in range(8)}, set(data))

    def test_story_sessions_api_reads_unknown_and_appends_note_without_losing_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            (todo_dir / "story_sessions.json").write_text(
                json.dumps({"t:story12": [{"session_id": "session-1"}]}),
                encoding="utf-8",
            )
            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            unknown = client.get("/api/story-sessions/t:unknown")
            self.assertEqual(200, unknown.status_code)
            self.assertEqual({"sessions": [], "notes": []}, unknown.get_json())

            created = client.post(
                "/api/story-note",
                json={"story_id": "t:story12", "text": "  First durable note  "},
            )
            self.assertEqual(200, created.status_code)
            self.assertEqual({"ok": True}, created.get_json())

            story = client.get("/api/story-sessions/t:story12").get_json()
            self.assertEqual([{"session_id": "session-1"}], story["sessions"])
            self.assertEqual("First durable note", story["notes"][0]["text"])
            self.assertEqual("note", story["notes"][0]["type"])
            self.assertIsInstance(story["notes"][0]["ts"], int)
            self.assertRegex(story["notes"][0]["at"], r"^\d{4}-\d{2}-\d{2}$")

    def test_story_note_api_rejects_blank_fields_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            for payload in [
                {},
                {"story_id": "", "text": "note"},
                {"story_id": "t:story12", "text": "   "},
            ]:
                with self.subTest(payload=payload):
                    response = client.post("/api/story-note", json=payload)
                    self.assertEqual(400, response.status_code)

            self.assertFalse((todo_dir / "story_sessions.json").exists())

    def test_todo_data_normalizes_legacy_story_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            for name in ["todo.md", "doing.md", "done.md", "icebox.md", "epics.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "story_sessions.json").write_text(
                json.dumps({"t:legacy1": [{"session_id": "session-1"}]}),
                encoding="utf-8",
            )
            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            source = client.get("/todo_data.js").get_data(as_text=True)

            self.assertIn(
                '"t:legacy1": {"sessions": [{"session_id": "session-1"}], "notes": []}',
                source,
            )

    def test_index_and_static_file_serving(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>board</main>", encoding="utf-8")
            (tree / "pivotalState.js").write_text("window.PivotalState = {};\n", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            index_response = client.get("/")
            try:
                self.assertEqual(200, index_response.status_code)
            finally:
                index_response.close()

            static_response = client.get("/pivotalState.js")
            try:
                self.assertEqual(200, static_response.status_code)
                self.assertIn("window.PivotalState", static_response.get_data(as_text=True))
            finally:
                static_response.close()

    def test_history_handles_corrupt_json_and_days_bounds(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            (todo_dir / "tree_viewer").mkdir()
            (todo_dir / "tree_viewer" / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            (todo_dir / "history_events.json").write_text("{bad json", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            response = client.get("/api/history?days=9999")
            self.assertEqual(200, response.status_code)
            self.assertEqual([], response.get_json())

    def test_sync_with_empty_payload_returns_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            (todo_dir / "tree_viewer").mkdir()
            (todo_dir / "tree_viewer" / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            response = client.post("/api/sync", data="not-json", content_type="application/json")

            self.assertEqual(200, response.status_code)
            self.assertEqual(
                {
                    "appliedStatus": [],
                    "appliedTexts": [],
                    "appliedTags": [],
                    "appliedTagRemovals": [],
                    "appliedNewItems": [],
                    "skipped": [],
                    "conflicts": [],
                    "errors": [],
                },
                response.get_json(),
            )

    def test_todo_data_js_and_sync_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            for name in ["doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text("- [ ] Server story #api ID: t:server1\n", encoding="utf-8")
            (todo_dir / "epics.md").write_text("", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            data_response = client.get("/todo_data.js")
            self.assertEqual(200, data_response.status_code)
            self.assertIn("window.todoData =", data_response.get_data(as_text=True))

            sync_response = client.post(
                "/api/sync",
                data=json.dumps(
                    {
                        "version": 1,
                        "statusChanges": [
                            {
                                "task_id": "t:server1",
                                "source_file": str(todo_dir / "todo.md"),
                                "line_number": 1,
                                "expected_text": "Server story #api ID: t:server1",
                                "new_status": "Done",
                                "from_status": "Pending",
                            }
                        ],
                    }
                ),
                content_type="application/json",
            )

            self.assertEqual(200, sync_response.status_code)
            body = sync_response.get_json()
            self.assertEqual(["t:server1"], body["appliedStatus"])
            history_response = client.get("/api/history")
            self.assertEqual(200, history_response.status_code)
            self.assertEqual("transition", history_response.get_json()[0]["type"])

            failed_sync = client.post(
                "/api/sync",
                json={
                    "version": 1,
                    "statusChanges": [
                        {
                            "task_id": "t:server1",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Wrong expected text",
                            "new_status": "Pending",
                            "from_status": "Done",
                        }
                    ],
                },
            )
            self.assertEqual([], failed_sync.get_json()["appliedStatus"])
            self.assertEqual(1, len(client.get("/api/history").get_json()))

    def test_events_stream_yields_version_and_bumps_after_applied_sync(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            for name in ["doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text("- [ ] Stream me #api ID: t:stream1\n", encoding="utf-8")
            (todo_dir / "epics.md").write_text("", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            response = client.get("/api/events")
            try:
                self.assertEqual(200, response.status_code)
                self.assertEqual("text/event-stream", response.mimetype)
                first_chunk = next(iter(response.response))
                text = first_chunk.decode("utf-8") if isinstance(first_chunk, bytes) else first_chunk
                self.assertEqual("data: 0\n\n", text)
            finally:
                response.close()

            client.post(
                "/api/sync",
                json={
                    "version": 1,
                    "statusChanges": [
                        {
                            "task_id": "t:stream1",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Stream me #api ID: t:stream1",
                            "new_status": "Done",
                        }
                    ],
                },
            )

            response = client.get("/api/events")
            try:
                first_chunk = next(iter(response.response))
                text = first_chunk.decode("utf-8") if isinstance(first_chunk, bytes) else first_chunk
                self.assertEqual("data: 1\n\n", text)
            finally:
                response.close()

    def test_tag_removal_via_sync_bumps_version(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            for name in ["doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text(
                "- [ ] Regroup me #wrong_epic ID: t:vrsbump\n", encoding="utf-8"
            )
            (todo_dir / "epics.md").write_text("", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            response = client.post(
                "/api/sync",
                json={
                    "version": 1,
                    "tagRemovals": [
                        {
                            "task_id": "t:vrsbump",
                            "source_file": str(todo_dir / "todo.md"),
                            "expected_text": "Regroup me #wrong_epic ID: t:vrsbump",
                            "tags": ["wrong_epic"],
                        }
                    ],
                },
            )
            self.assertEqual(["t:vrsbump"], response.get_json()["appliedTagRemovals"])

            stream = client.get("/api/events")
            try:
                first_chunk = next(iter(stream.response))
                text = first_chunk.decode("utf-8") if isinstance(first_chunk, bytes) else first_chunk
                self.assertEqual("data: 1\n\n", text)
            finally:
                stream.close()

    def test_create_epic_api_writes_epics_md_and_bumps_version(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "epics.md").write_text("# Epics\n", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            response = client.post(
                "/api/epics",
                json={"tag": "new_epic", "dir": "/Users/example/Projects/pivotal-claw", "color": "8E6CEF"},
            )

            self.assertEqual(200, response.status_code)
            self.assertEqual({"status": "ok", "tag": "new_epic"}, response.get_json())
            text = (todo_dir / "epics.md").read_text(encoding="utf-8")
            self.assertIn("## #new_epic", text)
            self.assertIn("dir: /Users/example/Projects/pivotal-claw", text)
            self.assertIn("color: 8E6CEF", text)

            events_response = client.get("/api/events")
            try:
                first_chunk = next(iter(events_response.response))
                text = first_chunk.decode("utf-8") if isinstance(first_chunk, bytes) else first_chunk
                self.assertEqual("data: 1\n\n", text)
            finally:
                events_response.close()

    def test_create_epic_api_treats_json_null_fields_as_absent(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            (todo_dir / "epics.md").write_text("# Epics\n", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            response = client.post(
                "/api/epics",
                json={"tag": "null_fields_epic", "dir": None, "color": None, "portfolio_id": None},
            )

            self.assertEqual(200, response.status_code)
            text = (todo_dir / "epics.md").read_text(encoding="utf-8")
            self.assertIn("## #null_fields_epic", text)
            self.assertNotIn("dir: None", text)
            self.assertNotIn("color: None", text)

    def test_create_epic_api_rejects_invalid_or_duplicate_tag(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            (todo_dir / "epics.md").write_text("# Epics\n\n## #scheduler\n\ndir: /some/dir\n", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            invalid = client.post("/api/epics", json={"tag": "bad tag!"})
            self.assertEqual(400, invalid.status_code)

            duplicate = client.post("/api/epics", json={"tag": "scheduler"})
            self.assertEqual(400, duplicate.status_code)
            self.assertEqual(1, (todo_dir / "epics.md").read_text(encoding="utf-8").count("## #scheduler"))

    def test_create_epic_api_assigns_to_selected_portfolio(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            (todo_dir / "epics.md").write_text("# Epics\n", encoding="utf-8")
            (todo_dir / "portfolio.json").write_text(
                json.dumps({"items": [{"id": "board_ux", "name": "Board UX", "epic_tags": [], "focus": False}]}),
                encoding="utf-8",
            )

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            response = client.post(
                "/api/epics",
                json={"tag": "new_epic", "portfolio_id": "board_ux"},
            )

            self.assertEqual(200, response.status_code)
            portfolio_data = json.loads((todo_dir / "portfolio.json").read_text(encoding="utf-8"))
            self.assertEqual(["new_epic"], portfolio_data["items"][0]["epic_tags"])

    def test_create_epic_api_ignores_unknown_portfolio_id(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            tree = todo_dir / "tree_viewer"
            tree.mkdir()
            (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
            (todo_dir / "epics.md").write_text("# Epics\n", encoding="utf-8")

            app = server.create_app(todo_dir=todo_dir)
            client = app.test_client()

            response = client.post(
                "/api/epics",
                json={"tag": "new_epic", "portfolio_id": "does_not_exist"},
            )

            self.assertEqual(200, response.status_code)
            self.assertIn("## #new_epic", (todo_dir / "epics.md").read_text(encoding="utf-8"))


class PortfolioReorderApiTests(unittest.TestCase):
    """Stack rank mode drags rows; the order only survives if the server writes it."""

    def make_app(self, todo_dir, items):
        tree = todo_dir / "tree_viewer"
        tree.mkdir()
        (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
        (todo_dir / "epics.md").write_text("# Epics\n", encoding="utf-8")
        (todo_dir / "portfolio.json").write_text(json.dumps({"items": items}), encoding="utf-8")
        return server.create_app(todo_dir=todo_dir).test_client()

    @property
    def items(self):
        return [
            {"id": "a", "name": "A", "epic_tags": ["one", "two"], "focus": False},
            {"id": "b", "name": "B", "epic_tags": ["three"], "focus": False},
        ]

    def written(self, todo_dir):
        return json.loads((todo_dir / "portfolio.json").read_text(encoding="utf-8"))["items"]

    def test_reorder_writes_the_new_portfolio_order(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            client = self.make_app(todo_dir, self.items)

            response = client.post("/api/portfolio/reorder", json={"order": ["b", "a"]})

            self.assertEqual(200, response.status_code)
            self.assertEqual(["b", "a"], [item["id"] for item in self.written(todo_dir)])

    def test_reorder_writes_the_new_epic_order_within_a_portfolio(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            client = self.make_app(todo_dir, self.items)

            response = client.post(
                "/api/portfolio/reorder",
                json={"portfolio_id": "a", "epic_tags": ["two", "one"]},
            )

            self.assertEqual(200, response.status_code)
            self.assertEqual(["two", "one"], self.written(todo_dir)[0]["epic_tags"])

    def test_an_unknown_portfolio_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            client = self.make_app(todo_dir, self.items)

            response = client.post("/api/portfolio/reorder", json={"order": ["a", "ghost"]})

            self.assertEqual(400, response.status_code)
            self.assertIn("error", response.get_json())
            self.assertEqual(["a", "b"], [item["id"] for item in self.written(todo_dir)])

    def test_a_payload_with_nothing_to_reorder_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            client = self.make_app(todo_dir, self.items)

            self.assertEqual(400, client.post("/api/portfolio/reorder", json={}).status_code)


class SessionsApiTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        root = Path(self._td.name)
        self.todo_dir = root / "todo"
        tree = self.todo_dir / "tree_viewer"
        tree.mkdir(parents=True)
        (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")
        (tree / "sessions_history.html").write_text("<main>Sessions</main>", encoding="utf-8")

        self.claude_dir = root / "claude-projects"
        self.codex_dir = root / "codex-sessions"
        self._original_dirs = (
            server.session_scanner.CLAUDE_PROJECTS_DIR,
            server.session_scanner.CODEX_SESSIONS_DIR,
        )
        server.session_scanner.CLAUDE_PROJECTS_DIR = self.claude_dir
        server.session_scanner.CODEX_SESSIONS_DIR = self.codex_dir
        self.addCleanup(self._restore_dirs)
        self.client = server.create_app(todo_dir=self.todo_dir).test_client()

    def _restore_dirs(self):
        server.session_scanner.CLAUDE_PROJECTS_DIR, server.session_scanner.CODEX_SESSIONS_DIR = self._original_dirs

    def _write_claude_session(self, session_id, prompt="Fix parser bug"):
        path = self.claude_dir / "-demo" / f"{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [
            {
                "type": "user",
                "message": {"content": prompt},
                "timestamp": "2026-07-10T10:00:00Z",
                "cwd": "/Users/example/Projects/demo",
            },
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
        ]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return path

    def test_sessions_history_page_route_serves_sessions_history_html(self):
        response = self.client.get("/sessions_history")
        self.assertEqual(200, response.status_code)
        self.assertIn("Sessions", response.get_data(as_text=True))

    def test_sessions_api_lists_sessions_with_linked_story_annotations(self):
        self._write_claude_session("aaaa1111-0000-0000-0000-000000000001")
        (self.todo_dir / "story_sessions.json").write_text(
            json.dumps(
                {
                    "pin": {"kept": True},
                    "t:story12": {
                        "sessions": [
                            {"session_id": "aaaa1111-0000-0000-0000-000000000001", "provider": "claude"}
                        ],
                        "notes": [],
                    },
                }
            ),
            encoding="utf-8",
        )

        body = self.client.get("/api/sessions").get_json()

        self.assertEqual(1, len(body["sessions"]))
        record = body["sessions"][0]
        self.assertEqual("claude", record["provider"])
        self.assertEqual("Fix parser bug", record["title"])
        self.assertEqual(["t:story12"], record["linked_story_ids"])

    def test_sessions_link_writes_record_dedupes_and_preserves_pin(self):
        (self.todo_dir / "story_sessions.json").write_text(
            json.dumps({"pin": {"kept": True}}), encoding="utf-8"
        )
        payload = {
            "story_id": "t:story12",
            "session_id": "aaaa1111-0000-0000-0000-000000000001",
            "provider": "claude",
            "started_at": "2026-07-10T10:00:00Z",
            "title": "Fix parser bug",
            "cwd": "/Users/example/Projects/demo",
        }

        first = self.client.post("/api/sessions/link", json=payload)
        second = self.client.post("/api/sessions/link", json=payload)

        self.assertEqual(200, first.status_code)
        self.assertTrue(first.get_json()["ok"])
        self.assertEqual(200, second.status_code)
        stored = json.loads((self.todo_dir / "story_sessions.json").read_text(encoding="utf-8"))
        self.assertEqual({"kept": True}, stored["pin"])
        sessions = stored["t:story12"]["sessions"]
        self.assertEqual(1, len(sessions))
        self.assertEqual("aaaa1111-0000-0000-0000-000000000001", sessions[0]["session_id"])
        self.assertEqual("claude", sessions[0]["provider"])
        self.assertEqual("Fix parser bug", sessions[0]["title"])
        self.assertIn("linked_at", sessions[0])

    def test_sessions_link_rejects_invalid_payloads(self):
        for payload in [
            {},
            {"story_id": "t:story12", "session_id": "abc", "provider": "shell"},
            {"story_id": "pin", "session_id": "abc", "provider": "claude"},
        ]:
            with self.subTest(payload=payload):
                response = self.client.post("/api/sessions/link", json=payload)
                self.assertEqual(400, response.status_code)

    def test_sessions_resume_endpoint_validates_and_calls_runtime(self):
        calls = []

        class StubRuntime:
            def resume(self, session_id, cwd):
                calls.append((session_id, str(cwd)))

        client = server.create_app(
            todo_dir=self.todo_dir, codex_runtime=StubRuntime(), claude_runtime=StubRuntime()
        ).test_client()
        session_id = "aaaa1111-0000-0000-0000-000000000001"

        ok = client.post(
            "/api/sessions/resume",
            json={"provider": "claude", "session_id": session_id, "cwd": str(self.todo_dir)},
        )
        self.assertEqual(200, ok.status_code)
        self.assertEqual([(session_id, str(self.todo_dir))], calls)

        missing_cwd = client.post(
            "/api/sessions/resume",
            json={"provider": "codex", "session_id": session_id, "cwd": "/nope/gone"},
        )
        self.assertEqual(200, missing_cwd.status_code)
        self.assertEqual((session_id, str(Path.home())), calls[-1])

        for payload in [
            {"provider": "shell", "session_id": session_id},
            {"provider": "claude", "session_id": "../../etc/passwd"},
            {},
        ]:
            with self.subTest(payload=payload):
                response = client.post("/api/sessions/resume", json=payload)
                self.assertEqual(400, response.status_code)

    def test_sessions_resume_endpoint_reports_terminal_failure(self):
        class FailingRuntime:
            def resume(self, session_id, cwd):
                raise RuntimeError("Terminal could not resume Claude")

        client = server.create_app(
            todo_dir=self.todo_dir, codex_runtime=FailingRuntime(), claude_runtime=FailingRuntime()
        ).test_client()

        response = client.post(
            "/api/sessions/resume",
            json={"provider": "claude", "session_id": "aaaa1111-0000-0000-0000-000000000001"},
        )
        self.assertEqual(502, response.status_code)
        self.assertIn("Terminal", response.get_json()["error"])

    def test_session_preview_endpoint_returns_messages_or_errors(self):
        self._write_claude_session("aaaa1111-0000-0000-0000-000000000001")

        found = self.client.get("/api/sessions/claude/aaaa1111-0000-0000-0000-000000000001/preview")
        self.assertEqual(200, found.status_code)
        body = found.get_json()
        self.assertEqual(
            [("user", "Fix parser bug"), ("assistant", "Done.")],
            [(m["role"], m["text"]) for m in body["messages"]],
        )

        missing = self.client.get("/api/sessions/claude/ffff9999-0000-0000-0000-000000000009/preview")
        self.assertEqual(404, missing.status_code)

        bad_provider = self.client.get("/api/sessions/shell/aaaa1111/preview")
        self.assertEqual(400, bad_provider.status_code)


class PinsApiTests(unittest.TestCase):
    """Pins on /sessions_history — a durable shortlist of sessions and stories."""

    SESSION_ID = "019fb928-24b0-79d0-861c-3bf8342c764e"

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        root = Path(self._td.name)
        self.todo_dir = root / "todo"
        tree = self.todo_dir / "tree_viewer"
        tree.mkdir(parents=True)
        (tree / "pivotal_tasks.html").write_text("<main>ok</main>", encoding="utf-8")

        self.claude_dir = root / "claude-projects"
        self.codex_dir = root / "codex-sessions"
        self._original_dirs = (
            server.session_scanner.CLAUDE_PROJECTS_DIR,
            server.session_scanner.CODEX_SESSIONS_DIR,
        )
        server.session_scanner.CLAUDE_PROJECTS_DIR = self.claude_dir
        server.session_scanner.CODEX_SESSIONS_DIR = self.codex_dir
        self.addCleanup(self._restore_dirs)
        self.pins_path = self.todo_dir / "pins.json"
        self.client = server.create_app(todo_dir=self.todo_dir).test_client()

    def _restore_dirs(self):
        server.session_scanner.CLAUDE_PROJECTS_DIR, server.session_scanner.CODEX_SESSIONS_DIR = self._original_dirs

    def _toggle_session(self, session_id=None, **extra):
        payload = {
            "kind": "session",
            "provider": "codex",
            "session_id": session_id or self.SESSION_ID,
        }
        payload.update(extra)
        return self.client.post("/api/pins/toggle", json=payload)

    def _write_claude_session(self, session_id, prompt="Fix parser bug"):
        path = self.claude_dir / "-demo" / f"{session_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [
            {
                "type": "user",
                "message": {"content": prompt},
                "timestamp": "2026-07-10T10:00:00Z",
                "cwd": "/Users/example/Projects/demo",
            }
        ]
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    def test_pins_api_returns_empty_when_the_file_is_missing(self):
        self.assertFalse(self.pins_path.exists())

        body = self.client.get("/api/pins").get_json()

        self.assertEqual({"sessions": [], "stories": []}, body)

    def test_pins_api_degrades_to_empty_on_corrupt_json_instead_of_failing(self):
        self.pins_path.write_text("{not json at all", encoding="utf-8")

        response = self.client.get("/api/pins")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"sessions": [], "stories": []}, response.get_json())

    def test_toggling_a_session_pin_round_trips_through_disk(self):
        body = self._toggle_session(title="Rework pins", cwd="/Users/example/Projects/demo").get_json()

        self.assertTrue(body["pinned"])
        stored = json.loads(self.pins_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(stored["sessions"]))
        record = stored["sessions"][0]
        self.assertEqual("Rework pins", record["title"])
        self.assertEqual("/Users/example/Projects/demo", record["cwd"])
        self.assertIn("pinned_at", record)
        # Derived fields are recomputed on read, never persisted.
        self.assertNotIn("running", record)
        self.assertNotIn("project", record)

        again = self._toggle_session().get_json()

        self.assertFalse(again["pinned"])
        self.assertEqual([], json.loads(self.pins_path.read_text(encoding="utf-8"))["sessions"])

    def test_unpinning_one_session_leaves_the_others_pinned(self):
        other = "019fb928-24b0-79d0-861c-3bf8342c7640"
        self._toggle_session()
        self._toggle_session(session_id=other)

        self._toggle_session()

        body = self.client.get("/api/pins").get_json()
        self.assertEqual([other], [record["session_id"] for record in body["sessions"]])

    def test_pinned_sessions_are_annotated_with_project_and_running_on_read(self):
        self._toggle_session(cwd="/Users/example/Projects/demo")

        record = self.client.get("/api/pins").get_json()["sessions"][0]

        self.assertEqual("demo", record["project"])
        self.assertIs(False, record["running"])

    def test_story_pins_toggle_and_reject_the_reserved_global_keys(self):
        body = self.client.post("/api/pins/toggle", json={"kind": "story", "story_id": "t:abc1234"}).get_json()

        self.assertTrue(body["pinned"])
        self.assertEqual(["t:abc1234"], self.client.get("/api/pins").get_json()["stories"])

        for reserved in ("pin", "pinned_epics", ""):
            with self.subTest(story_id=reserved):
                response = self.client.post("/api/pins/toggle", json={"kind": "story", "story_id": reserved})
                self.assertEqual(400, response.status_code)

    def test_toggle_rejects_unknown_kinds_and_malformed_session_payloads(self):
        cases = [
            {"kind": "nonsense"},
            {},
            {"kind": "session", "provider": "gemini", "session_id": self.SESSION_ID},
            {"kind": "session", "provider": "codex", "session_id": "not-a-uuid"},
            {"kind": "session", "provider": "codex", "session_id": ""},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post("/api/pins/toggle", json=payload)
                self.assertEqual(400, response.status_code)
                self.assertIn("error", response.get_json())
        self.assertFalse(self.pins_path.exists())

    def test_pinned_session_outside_the_recent_scan_window_still_comes_back(self):
        self._toggle_session(title="Long gone", cwd="/Users/example/Projects/demo")

        self.assertEqual([], self.client.get("/api/sessions").get_json()["sessions"])
        self.assertEqual("Long gone", self.client.get("/api/pins").get_json()["sessions"][0]["title"])

    def test_sessions_api_marks_pinned_records(self):
        session_id = "aaaa1111-0000-0000-0000-000000000001"
        self._write_claude_session(session_id)

        self.assertIs(False, self.client.get("/api/sessions").get_json()["sessions"][0]["pinned"])

        self.client.post(
            "/api/pins/toggle",
            json={"kind": "session", "provider": "claude", "session_id": session_id},
        )

        self.assertIs(True, self.client.get("/api/sessions").get_json()["sessions"][0]["pinned"])

    def test_pin_updates_do_not_lose_concurrent_changes(self):
        path = Path(self._td.name) / "concurrent-pins.json"

        def pin_story(index):
            def modifier(data):
                data["stories"] = data["stories"] + [f"t:story{index}"]
                return data

            server.update_pins(path, modifier)

        threads = [threading.Thread(target=pin_story, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        stories = server.read_pins(path)["stories"]
        self.assertEqual(8, len(stories))
        self.assertEqual({f"t:story{index}" for index in range(8)}, set(stories))

    def test_pins_normalizer_drops_junk_and_dedupes(self):
        self.pins_path.write_text(
            json.dumps(
                {
                    "sessions": [
                        {"provider": "codex", "session_id": self.SESSION_ID, "running": True, "project": "stale"},
                        {"provider": "codex", "session_id": self.SESSION_ID},
                        {"provider": "gemini", "session_id": self.SESSION_ID},
                        {"provider": "codex"},
                        "not-a-dict",
                    ],
                    "stories": ["t:abc1234", "t:abc1234", "pin", "  "],
                }
            ),
            encoding="utf-8",
        )

        body = self.client.get("/api/pins").get_json()

        self.assertEqual([self.SESSION_ID], [record["session_id"] for record in body["sessions"]])
        self.assertEqual(["t:abc1234"], body["stories"])

    def test_pins_file_is_carried_by_export(self):
        self.assertIn("pins.json", server.EXPORT_FILENAMES)


if __name__ == "__main__":
    unittest.main()
