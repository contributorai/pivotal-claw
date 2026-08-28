import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cc.pivotal import claude_sessions


class ClaudeSessionTests(unittest.TestCase):
    def test_encode_cwd_replaces_non_alnum_runs_with_a_dash(self):
        self.assertEqual(
            "-Users-example-Projects-pivotal-claw",
            claude_sessions.encode_cwd("/Users/example/Projects/pivotal-claw"),
        )

    def test_write_launch_script_quotes_arguments_sets_executable_mode_and_cds_first(self):
        with tempfile.TemporaryDirectory() as td:
            path = claude_sessions.write_launch_script(
                "Prompt with 'quotes' and $shell",
                Path("/tmp/project with spaces"),
                "Story name",
                "session-1234",
                Path(td),
                timestamp="20260703T120000Z",
                claude_executable=Path("/usr/local/bin/claude"),
            )

            source = path.read_text(encoding="utf-8")
            self.assertIn("cd '/tmp/project with spaces'", source)
            self.assertIn("exec /usr/local/bin/claude --session-id session-1234", source)
            self.assertIn("'Prompt with '\"'\"'quotes'\"'\"' and $shell'", source)
            self.assertEqual(0o755, os.stat(path).st_mode & 0o777)
            self.assertEqual("launch-story-name-20260703T120000Z.sh", path.name)

    def test_find_session_file_matches_by_encoded_cwd_and_session_id(self):
        with tempfile.TemporaryDirectory() as td:
            claude_home = Path(td)
            cwd = Path(td) / "project"
            cwd.mkdir()
            session_dir = claude_home / "projects" / claude_sessions.encode_cwd(cwd.resolve())
            session_dir.mkdir(parents=True)
            (session_dir / "abc-123.jsonl").write_text('{"type":"mode","sessionId":"abc-123"}\n', encoding="utf-8")

            meta = claude_sessions.find_session_file(claude_home, cwd, "abc-123")
            self.assertIsNotNone(meta)
            self.assertEqual("abc-123", meta.session_id)
            self.assertEqual(cwd.resolve(), meta.cwd)

    def test_find_session_file_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            claude_home = Path(td)
            cwd = Path(td) / "project"
            cwd.mkdir()
            self.assertIsNone(claude_sessions.find_session_file(claude_home, cwd, "missing"))

    def test_wait_for_session_file_times_out_boundedly(self):
        with tempfile.TemporaryDirectory() as td:
            claude_home = Path(td)
            cwd = Path(td) / "project"
            cwd.mkdir()
            ticks = iter([0.0, 0.5, 1.1])
            with self.assertRaises(TimeoutError):
                claude_sessions.wait_for_session_file(
                    claude_home, cwd, "missing",
                    timeout=1.0, interval=0.0, monotonic=lambda: next(ticks), sleep=lambda _: None,
                )

    def test_wait_for_session_file_returns_once_file_appears(self):
        with tempfile.TemporaryDirectory() as td:
            claude_home = Path(td)
            cwd = Path(td) / "project"
            cwd.mkdir()
            session_dir = claude_home / "projects" / claude_sessions.encode_cwd(cwd.resolve())
            session_dir.mkdir(parents=True)
            (session_dir / "abc-123.jsonl").write_text('{"type":"mode","sessionId":"abc-123"}\n', encoding="utf-8")

            meta = claude_sessions.wait_for_session_file(
                claude_home, cwd, "abc-123", timeout=1.0, interval=0.0,
                monotonic=lambda: 0.0, sleep=lambda _: None,
            )
            self.assertEqual("abc-123", meta.session_id)

    def test_runtime_initialization_does_not_require_installed_claude(self):
        with mock.patch.object(
            claude_sessions,
            "resolve_claude_executable",
            side_effect=FileNotFoundError("Claude CLI executable not found"),
        ):
            runtime = claude_sessions.ClaudeRuntime()

        self.assertIsNone(runtime.claude_executable)

    def test_resume_opens_terminal_with_resume_flag(self):
        calls = []

        def runner(args, check=False):
            calls.append(args)
            class Result:
                returncode = 0
            return Result()

        runtime = claude_sessions.ClaudeRuntime(
            runner=runner,
            claude_executable=Path("/usr/local/bin/claude"),
        )
        runtime.resume("abc-123", Path("/tmp/my project"))

        self.assertEqual(1, len(calls))
        self.assertEqual("osascript", calls[0][0])
        script = calls[0][-1]
        self.assertIn("--resume abc-123", script)
        self.assertIn("'/tmp/my project'", script)

    def test_resume_raises_when_terminal_fails(self):
        def runner(args, check=False):
            class Result:
                returncode = 1
            return Result()

        runtime = claude_sessions.ClaudeRuntime(
            runner=runner,
            claude_executable=Path("/usr/local/bin/claude"),
        )
        with self.assertRaises(RuntimeError):
            runtime.resume("abc-123", Path("/tmp"))


if __name__ == "__main__":
    unittest.main()
