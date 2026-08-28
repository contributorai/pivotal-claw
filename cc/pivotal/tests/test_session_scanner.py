import json
import os
import tempfile
import unittest
from pathlib import Path
import sys


PIVOTAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIVOTAL_DIR))

import session_scanner


def write_jsonl(path: Path, records) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def claude_records(session_id, prompt="Fix the login bug", ai_title=None, cwd="/Users/example/Projects/demo"):
    records = [
        {"type": "mode", "mode": "normal", "sessionId": session_id},
        {
            "type": "user",
            "isMeta": True,
            "message": {"content": "meta noise"},
            "timestamp": "2026-07-10T10:00:00Z",
            "cwd": cwd,
        },
        {
            "type": "user",
            "message": {"content": "<local-command-caveat>Caveat text</local-command-caveat>"},
            "timestamp": "2026-07-10T10:00:01Z",
            "cwd": cwd,
        },
        {
            "type": "user",
            "message": {"content": prompt},
            "timestamp": "2026-07-10T10:00:02Z",
            "cwd": cwd,
            "gitBranch": "main",
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "On it."}, {"type": "tool_use", "name": "Bash"}]},
            "timestamp": "2026-07-10T10:00:03Z",
        },
    ]
    if ai_title:
        records.append({"type": "ai-title", "aiTitle": ai_title, "sessionId": session_id})
    return records


def codex_records(thread_id, prompt="Refactor the parser", cwd="/Users/example/Projects/demo"):
    return [
        {
            "timestamp": "2026-07-11T09:00:00Z",
            "type": "session_meta",
            "payload": {"id": thread_id, "cwd": cwd, "timestamp": "2026-07-11T09:00:00Z"},
        },
        {
            "timestamp": "2026-07-11T09:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": "You are an agent."}],
            },
        },
        {
            "timestamp": "2026-07-11T09:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "<environment_context>stuff</environment_context>"}],
            },
        },
        {
            "timestamp": "2026-07-11T09:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "# AGENTS.md instructions for /Users/example/Projects/demo\n\nstuff"}],
            },
        },
        {
            "timestamp": "2026-07-11T09:00:03Z",
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]},
        },
        {
            "timestamp": "2026-07-11T09:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Done refactoring."}],
            },
        },
    ]


class SessionScannerTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        root = Path(self._td.name)
        self.claude_dir = root / "claude" / "projects"
        self.codex_dir = root / "codex" / "sessions"

    def test_merges_providers_sorted_by_mtime_and_caps_at_limit(self):
        older = write_jsonl(
            self.claude_dir / "-demo" / "aaaa1111-0000-0000-0000-000000000001.jsonl",
            claude_records("aaaa1111-0000-0000-0000-000000000001", prompt="old claude"),
        )
        newer = write_jsonl(
            self.codex_dir / "2026" / "07" / "11" / "rollout-2026-07-11T09-00-00-bbbb2222.jsonl",
            codex_records("bbbb2222-0000-0000-0000-000000000002"),
        )
        extra = write_jsonl(
            self.claude_dir / "-demo" / "cccc3333-0000-0000-0000-000000000003.jsonl",
            claude_records("cccc3333-0000-0000-0000-000000000003", prompt="newest claude"),
        )
        os.utime(older, (1_000, 1_000))
        os.utime(newer, (2_000, 2_000))
        os.utime(extra, (3_000, 3_000))

        sessions = session_scanner.list_recent_sessions(limit=2, claude_dir=self.claude_dir, codex_dir=self.codex_dir)

        self.assertEqual(2, len(sessions))
        self.assertEqual(["claude", "codex"], [s["provider"] for s in sessions])
        self.assertEqual("newest claude", sessions[0]["first_prompt"])
        self.assertEqual("demo", sessions[0]["project"])

    def test_claude_title_prefers_ai_title_and_skips_noise_records(self):
        write_jsonl(
            self.claude_dir / "-demo" / "aaaa1111-0000-0000-0000-000000000001.jsonl",
            claude_records("aaaa1111-0000-0000-0000-000000000001", prompt="Fix the login bug", ai_title="Login fix"),
        )

        sessions = session_scanner.list_recent_sessions(claude_dir=self.claude_dir, codex_dir=self.codex_dir)

        self.assertEqual(1, len(sessions))
        self.assertEqual("Login fix", sessions[0]["title"])
        self.assertEqual("Fix the login bug", sessions[0]["first_prompt"])
        self.assertEqual("main", sessions[0]["git_branch"])
        self.assertEqual("2026-07-10T10:00:00Z", sessions[0]["started_at"])

    def test_claude_title_falls_back_to_first_real_prompt(self):
        write_jsonl(
            self.claude_dir / "-demo" / "aaaa1111-0000-0000-0000-000000000001.jsonl",
            claude_records("aaaa1111-0000-0000-0000-000000000001", prompt="Fix the login bug"),
        )

        sessions = session_scanner.list_recent_sessions(claude_dir=self.claude_dir, codex_dir=self.codex_dir)

        self.assertEqual("Fix the login bug", sessions[0]["title"])

    def test_codex_metadata_comes_from_session_meta_and_skips_developer_noise(self):
        write_jsonl(
            self.codex_dir / "2026" / "07" / "11" / "rollout-2026-07-11T09-00-00-bbbb2222.jsonl",
            codex_records("bbbb2222-0000-0000-0000-000000000002"),
        )

        sessions = session_scanner.list_recent_sessions(claude_dir=self.claude_dir, codex_dir=self.codex_dir)

        self.assertEqual(1, len(sessions))
        record = sessions[0]
        self.assertEqual("codex", record["provider"])
        self.assertEqual("bbbb2222-0000-0000-0000-000000000002", record["session_id"])
        self.assertEqual("Refactor the parser", record["title"])
        self.assertEqual("/Users/example/Projects/demo", record["cwd"])
        self.assertEqual("2026-07-11T09:00:00Z", record["started_at"])

    def test_corrupt_and_empty_files_are_skipped_without_error(self):
        corrupt = self.claude_dir / "-demo" / "aaaa1111-0000-0000-0000-000000000001.jsonl"
        corrupt.parent.mkdir(parents=True)
        corrupt.write_text("{not json\n", encoding="utf-8")
        (self.claude_dir / "-demo" / "bbbb2222-0000-0000-0000-000000000002.jsonl").write_text("", encoding="utf-8")
        write_jsonl(
            self.codex_dir / "2026" / "07" / "11" / "rollout-2026-07-11T09-00-00-cccc3333.jsonl",
            codex_records("cccc3333-0000-0000-0000-000000000003"),
        )

        sessions = session_scanner.list_recent_sessions(claude_dir=self.claude_dir, codex_dir=self.codex_dir)

        self.assertEqual(["cccc3333-0000-0000-0000-000000000003"], [s["session_id"] for s in sessions])

    def test_preview_returns_last_messages_from_file_larger_than_tail_cap(self):
        session_id = "aaaa1111-0000-0000-0000-000000000001"
        records = claude_records(session_id, prompt="Start big work")
        filler = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": f"step {i} " + "x" * 900}]}}
            for i in range(1200)
        ]
        records.extend(filler)
        records.append({"type": "user", "message": {"content": "final question"}, "timestamp": "2026-07-10T11:00:00Z"})
        path = write_jsonl(self.claude_dir / "-demo" / f"{session_id}.jsonl", records)
        self.assertGreater(path.stat().st_size, session_scanner.TAIL_BYTE_CAP)

        preview = session_scanner.read_session_preview(
            "claude", session_id, claude_dir=self.claude_dir, codex_dir=self.codex_dir
        )

        self.assertIsNotNone(preview)
        self.assertEqual(10, len(preview["messages"]))
        self.assertEqual("final question", preview["messages"][-1]["text"])
        self.assertEqual("user", preview["messages"][-1]["role"])

    def test_preview_rejects_bad_provider_and_traversal_ids(self):
        self.assertIsNone(
            session_scanner.read_session_preview("shell", "aaaa1111", claude_dir=self.claude_dir, codex_dir=self.codex_dir)
        )
        self.assertIsNone(
            session_scanner.read_session_preview(
                "claude", "../../etc/passwd", claude_dir=self.claude_dir, codex_dir=self.codex_dir
            )
        )
        self.assertIsNone(
            session_scanner.read_session_preview(
                "codex", "aaaa1111-0000-0000-0000-00000000000f", claude_dir=self.claude_dir, codex_dir=self.codex_dir
            )
        )

    def test_codex_preview_returns_user_and_assistant_messages(self):
        thread_id = "bbbb2222-0000-0000-0000-000000000002"
        write_jsonl(
            self.codex_dir / "2026" / "07" / "11" / f"rollout-2026-07-11T09-00-00-{thread_id}.jsonl",
            codex_records(thread_id),
        )

        preview = session_scanner.read_session_preview(
            "codex", thread_id, claude_dir=self.claude_dir, codex_dir=self.codex_dir
        )

        self.assertIsNotNone(preview)
        self.assertEqual(
            [("user", "Refactor the parser"), ("assistant", "Done refactoring.")],
            [(m["role"], m["text"]) for m in preview["messages"]],
        )


if __name__ == "__main__":
    unittest.main()
