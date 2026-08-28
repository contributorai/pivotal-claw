import os
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cc.pivotal import codex_sessions


class CodexSessionTests(unittest.TestCase):
    def story(self):
        return {
            "task_id": "t:abc1234",
            "text": "Build launch support #sessions ID: t:abc1234",
            "display_text": "Build launch support #sessions",
            "status": "Pending",
            "tags": ["sessions", "backend"],
            "source_file": "/tmp/todo.md",
            "section": "Launch",
            "children": [
                {"display_text": "Write tests", "status": "Done"},
                {"display_text": "Implement", "status": "Pending"},
            ],
        }

    def test_story_slug_is_safe_clean_and_bounded(self):
        slug = codex_sessions.story_slug(
            "  Build Launch!!! #sessions Completed 2026-07-03 ID: t:abc1234  ",
            max_length=24,
        )
        self.assertEqual("build-launch", slug)
        self.assertLessEqual(len(slug), 24)

    def test_build_story_prompt_contains_story_context_and_workflow(self):
        prompt = codex_sessions.build_story_prompt(self.story(), Path("/tmp/project"))
        for expected in [
            "# Build launch support",
            "t:abc1234",
            "Pending",
            "#sessions",
            "#backend",
            "/tmp/todo.md",
            "Launch",
            "Write tests — Done",
            "Implement — Pending",
            "AGENTS.md",
            "preserve unrelated changes",
        ]:
            self.assertIn(expected, prompt)

    def test_write_launch_script_quotes_arguments_and_sets_executable_mode(self):
        with tempfile.TemporaryDirectory() as td:
            path = codex_sessions.write_launch_script(
                "Prompt with 'quotes' and $shell",
                Path("/tmp/project with spaces"),
                "Story name",
                Path(td),
                timestamp="20260703T120000Z",
                codex_executable=Path("/Applications/Codex.app/Contents/Resources/codex"),
            )

            source = path.read_text(encoding="utf-8")
            self.assertIn("exec /Applications/Codex.app/Contents/Resources/codex --cd '/tmp/project with spaces'", source)
            self.assertIn("'Prompt with '\"'\"'quotes'\"'\"' and $shell'", source)
            self.assertEqual(0o755, os.stat(path).st_mode & 0o777)
            self.assertEqual("launch-story-name-20260703T120000Z.sh", path.name)

    def write_session(self, root, name, *, thread_id="thread-1", cwd="/tmp/project", timestamp="2026-07-03T12:00:01Z", malformed=False):
        path = root / "2026" / "07" / "03" / f"{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if malformed:
            path.write_text("{partial", encoding="utf-8")
        else:
            path.write_text(json.dumps({
                "timestamp": timestamp,
                "type": "session_meta",
                "payload": {"id": thread_id, "cwd": cwd, "timestamp": timestamp},
            }) + "\n", encoding="utf-8")
        return path

    def test_read_thread_metadata_extracts_session_meta(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.write_session(Path(td), "one")
            meta = codex_sessions.read_thread_metadata(path)
            self.assertEqual("thread-1", meta.thread_id)
            self.assertEqual(Path("/tmp/project").resolve(), meta.cwd)
            self.assertEqual(datetime(2026, 7, 3, 12, 0, 1, tzinfo=timezone.utc), meta.started_at)

    def test_find_new_thread_requires_exactly_one_matching_new_thread(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.write_session(root, "old", thread_id="old", timestamp="2026-07-03T11:59:59Z")
            self.write_session(root, "other", thread_id="other", cwd="/tmp/other")
            self.write_session(root, "new", thread_id="new")
            self.write_session(root, "partial", malformed=True)
            meta = codex_sessions.find_new_thread(
                root, {"old"}, Path("/tmp/project"), datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
            )
            self.assertEqual("new", meta.thread_id)

    def test_find_new_thread_returns_none_for_no_match_and_raises_for_ambiguity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            after = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
            self.assertIsNone(codex_sessions.find_new_thread(root, set(), Path("/tmp/project"), after))
            self.write_session(root, "one", thread_id="one")
            self.write_session(root, "two", thread_id="two")
            with self.assertRaises(codex_sessions.AmbiguousThreadError):
                codex_sessions.find_new_thread(root, set(), Path("/tmp/project"), after)

    def test_wait_for_new_thread_times_out_boundedly(self):
        with tempfile.TemporaryDirectory() as td:
            ticks = iter([0.0, 0.5, 1.1])
            with self.assertRaises(TimeoutError):
                codex_sessions.wait_for_new_thread(
                    Path(td), set(), Path("/tmp/project"),
                    datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc),
                    timeout=1.0, interval=0.0, monotonic=lambda: next(ticks), sleep=lambda _: None,
                )

    def test_runtime_initialization_does_not_require_installed_codex(self):
        with mock.patch.object(
            codex_sessions,
            "resolve_codex_executable",
            side_effect=FileNotFoundError("Codex CLI executable not found"),
        ):
            runtime = codex_sessions.CodexRuntime()

        self.assertIsNone(runtime.codex_executable)

    def test_resume_opens_terminal_with_resume_command(self):
        calls = []

        def runner(args, check=False):
            calls.append(args)
            class Result:
                returncode = 0
            return Result()

        runtime = codex_sessions.CodexRuntime(
            runner=runner,
            codex_executable=Path("/Applications/Codex.app/Contents/Resources/codex"),
        )
        runtime.resume("abc-123", Path("/tmp/my project"))

        self.assertEqual(1, len(calls))
        self.assertEqual("osascript", calls[0][0])
        script = calls[0][-1]
        self.assertIn("resume abc-123", script)
        self.assertIn("'/tmp/my project'", script)

    def test_resume_raises_when_terminal_fails(self):
        def runner(args, check=False):
            class Result:
                returncode = 1
            return Result()

        runtime = codex_sessions.CodexRuntime(
            runner=runner,
            codex_executable=Path("/Applications/Codex.app/Contents/Resources/codex"),
        )
        with self.assertRaises(RuntimeError):
            runtime.resume("abc-123", Path("/tmp"))


if __name__ == "__main__":
    unittest.main()
