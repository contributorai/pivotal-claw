import tempfile
import unittest
from pathlib import Path
import sys
from unittest import mock


TODO_MODULE_DIR = Path(__file__).resolve().parents[2] / "todo"
sys.path.insert(0, str(TODO_MODULE_DIR))
sys.modules.pop("sync_changes", None)

import sync_changes


class SyncChangesTests(unittest.TestCase):
    def test_status_move_text_edit_tag_addition_new_item_and_skipped_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text(
                "- [ ] Move me #alpha ID: t:move123\n"
                "- [ ] Edit me #alpha ID: t:edit123\n"
                "- [ ] Tag me #alpha ID: t:tag1234\n",
                encoding="utf-8",
            )

            receipt = sync_changes.apply_sync(
                {
                    "version": 1,
                    "statusChanges": [
                        {
                            "task_id": "t:move123",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Move me #alpha ID: t:move123",
                            "new_status": "In progress",
                        },
                        {
                            "task_id": "t:missing",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 99,
                            "expected_text": "not present",
                            "new_status": "Done",
                        },
                    ],
                    "textEdits": [
                        {
                            "task_id": "t:edit123",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 2,
                            "expected_text": "Edit me #alpha ID: t:edit123",
                            "new_text": "Edited story #alpha",
                        }
                    ],
                    "tagAdditions": [
                        {
                            "task_id": "t:tag1234",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 3,
                            "expected_text": "Tag me #alpha ID: t:tag1234",
                            "tags": ["beta"],
                        }
                    ],
                    "newItems": [{"text": "Fresh item", "status": "On schedule", "tags": ["gamma"]}],
                },
                todo_dir=todo_dir,
            )

            todo_text = (todo_dir / "todo.md").read_text(encoding="utf-8")
            doing_text = (todo_dir / "doing.md").read_text(encoding="utf-8")
            icebox_text = (todo_dir / "icebox.md").read_text(encoding="utf-8")

            self.assertIn("t:move123", receipt["appliedStatus"])
            self.assertIn("t:edit123", receipt["appliedTexts"])
            self.assertIn("t:tag1234", receipt["appliedTags"])
            self.assertEqual(["Fresh item"], receipt["appliedNewItems"])
            self.assertEqual("t:missing", receipt["skipped"][0]["task_id"])
            self.assertNotIn("Move me", todo_text)
            self.assertIn("- [/] Move me #alpha ID: t:move123", doing_text)
            self.assertIn("Edited story #alpha ID: t:edit123", todo_text)
            self.assertIn("Tag me #alpha ID: t:tag1234 #beta", todo_text)
            self.assertRegex(icebox_text, r"- \[ \] Fresh item #gamma ID: t:[a-z0-9]{7}")
            self.assertTrue((todo_dir / ".sync_backups").exists())

    def test_done_status_adds_completion_date_once(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text("- [ ] Ship it ID: t:ship123 Completed 2020-01-01\n", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "version": 1,
                    "statusChanges": [
                        {
                            "task_id": "t:ship123",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Ship it ID: t:ship123",
                            "new_status": "Done",
                        }
                    ],
                },
                todo_dir=todo_dir,
                today="2026-06-28",
            )

            self.assertEqual([], receipt["errors"])
            done_text = (todo_dir / "done.md").read_text(encoding="utf-8")
            self.assertIn("Completed 2026-06-28", done_text)
            self.assertNotIn("2020-01-01", done_text)

    def test_same_task_status_text_and_tags_are_applied_as_one_change(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text("- [ ] Original #alpha ID: t:abc1234\n", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "version": 1,
                    "statusChanges": [
                        {
                            "task_id": "t:abc1234",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Original #alpha ID: t:abc1234",
                            "new_status": "In progress",
                        }
                    ],
                    "textEdits": [
                        {
                            "task_id": "t:abc1234",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Original #alpha ID: t:abc1234",
                            "new_text": "Edited #alpha",
                        }
                    ],
                    "tagAdditions": [
                        {
                            "task_id": "t:abc1234",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Original #alpha ID: t:abc1234",
                            "tags": ["beta"],
                        }
                    ],
                },
                todo_dir=todo_dir,
            )

            self.assertEqual(["t:abc1234"], receipt["appliedStatus"])
            self.assertEqual(["t:abc1234"], receipt["appliedTexts"])
            self.assertEqual(["t:abc1234"], receipt["appliedTags"])
            self.assertEqual([], receipt["skipped"])
            self.assertEqual("", (todo_dir / "todo.md").read_text(encoding="utf-8"))
            self.assertEqual("- [/] Edited #alpha #beta ID: t:abc1234\n", (todo_dir / "doing.md").read_text(encoding="utf-8"))

    def test_failed_cross_file_append_keeps_source_line(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text("- [ ] Keep me ID: t:keep123\n", encoding="utf-8")

            with mock.patch.object(sync_changes, "prepend_locked", side_effect=OSError("disk full")):
                receipt = sync_changes.apply_sync(
                    {
                        "version": 1,
                        "statusChanges": [
                            {
                                "task_id": "t:keep123",
                                "source_file": str(todo_dir / "todo.md"),
                                "line_number": 1,
                                "expected_text": "Keep me ID: t:keep123",
                                "new_status": "In progress",
                            }
                        ],
                    },
                    todo_dir=todo_dir,
                )

            self.assertIn("disk full", receipt["errors"][0]["error"])
            self.assertIn("Keep me", (todo_dir / "todo.md").read_text(encoding="utf-8"))
            self.assertEqual("", (todo_dir / "doing.md").read_text(encoding="utf-8"))

    def test_status_change_to_in_progress_inserts_at_top_of_doing(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "doing.md").write_text(
                "# Doing\n"
                "- [/] Already working #alpha ID: t:old0001\n"
                "- [/] Also in progress #alpha ID: t:old0002\n",
                encoding="utf-8",
            )
            (todo_dir / "todo.md").write_text("- [ ] Start me now ID: t:new0003\n", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "version": 1,
                    "statusChanges": [
                        {
                            "task_id": "t:new0003",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Start me now ID: t:new0003",
                            "new_status": "In progress",
                        }
                    ],
                },
                todo_dir=todo_dir,
            )

            self.assertEqual(["t:new0003"], receipt["appliedStatus"])
            doing_lines = (todo_dir / "doing.md").read_text(encoding="utf-8").splitlines()
            self.assertEqual("# Doing", doing_lines[0])
            self.assertIn("Start me now", doing_lines[1])
            self.assertIn("t:old0001", doing_lines[2])
            self.assertIn("t:old0002", doing_lines[3])

    def test_cross_file_move_conflict_does_not_append_destination(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            source = todo_dir / "todo.md"
            destination = todo_dir / "doing.md"
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            source.write_text("- [ ] Race me ID: t:race123\n", encoding="utf-8")
            original_write_lines_locked = sync_changes.write_lines_locked

            def stale_before_remove(path, mutate):
                if path.resolve() == source.resolve():
                    source.write_text("- [ ] Changed elsewhere ID: t:race123\n", encoding="utf-8")
                return original_write_lines_locked(path, mutate)

            with mock.patch.object(sync_changes, "write_lines_locked", side_effect=stale_before_remove):
                receipt = sync_changes.apply_sync(
                    {
                        "version": 1,
                        "statusChanges": [
                            {
                                "task_id": "t:race123",
                                "source_file": str(source),
                                "line_number": 1,
                                "expected_text": "Race me ID: t:race123",
                                "new_status": "In progress",
                            }
                        ],
                    },
                    todo_dir=todo_dir,
                )

            # The story's own ID is still found (it wasn't moved/deleted), but its
            # text drifted since the client last saw it -- this is a conflict, not
            # a "line not found" skip: the ID-based lookup that replaced find_line
            # is precisely what makes this distinguishable now.
            self.assertEqual([], receipt["errors"])
            self.assertEqual([], receipt["skipped"])
            self.assertEqual(
                {"task_id": "t:race123", "current_text": "Changed elsewhere ID: t:race123"},
                receipt["conflicts"][0],
            )
            self.assertEqual("- [ ] Changed elsewhere ID: t:race123\n", source.read_text(encoding="utf-8"))
            self.assertEqual("", destination.read_text(encoding="utf-8"))

    def test_status_change_survives_line_shift_above_target(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            # The client's snapshot had "Target me" as the first line (line_number 1),
            # but two lines have since been inserted above it -- the old find_line
            # heuristic would search near line 1 and could easily miss or misfire;
            # ID-based lookup must find it regardless of position.
            (todo_dir / "todo.md").write_text(
                "- [ ] Inserted first #alpha ID: t:new0001\n"
                "- [ ] Inserted second #alpha ID: t:new0002\n"
                "- [ ] Target me #alpha ID: t:target1\n",
                encoding="utf-8",
            )

            receipt = sync_changes.apply_sync(
                {
                    "version": 1,
                    "statusChanges": [
                        {
                            "task_id": "t:target1",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Target me #alpha ID: t:target1",
                            "new_status": "In progress",
                        }
                    ],
                },
                todo_dir=todo_dir,
            )

            self.assertEqual(["t:target1"], receipt["appliedStatus"])
            self.assertEqual([], receipt["skipped"])
            self.assertEqual([], receipt["conflicts"])
            todo_text = (todo_dir / "todo.md").read_text(encoding="utf-8")
            self.assertIn("Inserted first", todo_text)
            self.assertIn("Inserted second", todo_text)
            self.assertNotIn("Target me", todo_text)
            self.assertIn(
                "- [/] Target me #alpha ID: t:target1",
                (todo_dir / "doing.md").read_text(encoding="utf-8"),
            )

    def test_text_edit_reports_conflict_without_writing_when_line_drifted(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text(
                "- [ ] Already changed #alpha ID: t:drift12\n", encoding="utf-8"
            )

            receipt = sync_changes.apply_sync(
                {
                    "textEdits": [
                        {
                            "task_id": "t:drift12",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Stale remembered text #alpha ID: t:drift12",
                            "new_text": "My new text",
                        }
                    ]
                },
                todo_dir=todo_dir,
            )

            self.assertEqual([], receipt["appliedTexts"])
            self.assertEqual([], receipt["skipped"])
            self.assertEqual(
                {"task_id": "t:drift12", "current_text": "Already changed #alpha ID: t:drift12"},
                receipt["conflicts"][0],
            )
            self.assertEqual(
                "- [ ] Already changed #alpha ID: t:drift12\n",
                (todo_dir / "todo.md").read_text(encoding="utf-8"),
            )

    def test_sync_rejects_source_paths_outside_todo_dir(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            todo_dir = root / "todo"
            todo_dir.mkdir()
            external = root / "outside.md"
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            external.write_text("- [ ] Outside ID: t:outside\n", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "textEdits": [
                        {
                            "task_id": "t:outside",
                            "source_file": str(external),
                            "line_number": 1,
                            "expected_text": "Outside ID: t:outside",
                            "new_text": "Mutated",
                        }
                    ]
                },
                todo_dir=todo_dir,
            )

            self.assertEqual([], receipt["appliedTexts"])
            self.assertIn("outside todo_dir", receipt["errors"][0]["error"])
            self.assertEqual("- [ ] Outside ID: t:outside\n", external.read_text(encoding="utf-8"))

    def test_same_file_status_change_updates_marker_without_moving_file(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text("- [ ] Stay here ID: t:stay123\n", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "statusChanges": [
                        {
                            "task_id": "t:stay123",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Stay here ID: t:stay123",
                            "new_status": "My backlog",
                        }
                    ]
                },
                todo_dir=todo_dir,
            )

            self.assertEqual(["t:stay123"], receipt["appliedStatus"])
            self.assertIn("- [ ] Stay here ID: t:stay123", (todo_dir / "todo.md").read_text(encoding="utf-8"))
            self.assertEqual("", (todo_dir / "doing.md").read_text(encoding="utf-8"))

    def test_duplicate_tag_and_empty_new_item_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text("- [ ] Tagged #alpha ID: t:tagdup1\n", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "tagAdditions": [
                        {
                            "task_id": "t:tagdup1",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Tagged #alpha ID: t:tagdup1",
                            "tags": ["alpha"],
                        }
                    ],
                    "newItems": [{"text": "   ", "status": "Pending", "tags": []}],
                },
                todo_dir=todo_dir,
            )

            reasons = [item["reason"] for item in receipt["skipped"]]
            self.assertIn("tags already present", reasons)
            self.assertIn("empty text", reasons)
            self.assertEqual("- [ ] Tagged #alpha ID: t:tagdup1\n", (todo_dir / "todo.md").read_text(encoding="utf-8"))

    def test_missing_source_file_is_reported_without_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "statusChanges": [
                        {
                            "task_id": "t:gone123",
                            "source_file": str(todo_dir / "missing.md"),
                            "line_number": 1,
                            "expected_text": "Gone ID: t:gone123",
                            "new_status": "Done",
                        }
                    ]
                },
                todo_dir=todo_dir,
            )

            self.assertEqual([], receipt["errors"])
            self.assertEqual({"task_id": "t:gone123", "reason": "line not found"}, receipt["skipped"][0])

    def test_epic_tag_addition_preserves_id_and_unrelated_tags(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text("- [ ] Plan slice #ui ID: t:epic123\n", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "tagAdditions": [
                        {
                            "task_id": "t:epic123",
                            "source_file": str(todo_dir / "todo.md"),
                            "line_number": 1,
                            "expected_text": "Plan slice #ui ID: t:epic123",
                            "tags": ["my_epic"],
                        }
                    ]
                },
                todo_dir=todo_dir,
            )

            self.assertEqual(["t:epic123"], receipt["appliedTags"])
            self.assertEqual([], receipt["errors"])
            written = (todo_dir / "todo.md").read_text(encoding="utf-8")
            self.assertIn("#ui", written)
            self.assertIn("#my_epic", written)
            self.assertIn("ID: t:epic123", written)

    def test_epic_tag_addition_to_icebox_and_duplicate_skip(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            icebox = todo_dir / "icebox.md"
            icebox.write_text("- [ ] Later slice #my_epic ID: t:icepic1\n", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "tagAdditions": [
                        {
                            "task_id": "t:icepic1",
                            "source_file": str(icebox),
                            "line_number": 1,
                            "expected_text": "Later slice #my_epic ID: t:icepic1",
                            "tags": ["#my_epic"],
                        }
                    ]
                },
                todo_dir=todo_dir,
            )

            self.assertEqual([], receipt["appliedTags"])
            self.assertEqual({"task_id": "t:icepic1", "reason": "tags already present"}, receipt["skipped"][0])
            self.assertEqual("- [ ] Later slice #my_epic ID: t:icepic1\n", icebox.read_text(encoding="utf-8"))

    def test_tag_removal_drops_only_named_tags(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "todo.md").write_text(
                "- [ ] Regroup slice #old_epic #keep_me ID: t:regrp01\n", encoding="utf-8"
            )

            receipt = sync_changes.apply_sync(
                {
                    "tagRemovals": [
                        {
                            "task_id": "t:regrp01",
                            "source_file": str(todo_dir / "todo.md"),
                            "expected_text": "Regroup slice #old_epic #keep_me ID: t:regrp01",
                            "tags": ["#old_epic"],
                        }
                    ]
                },
                todo_dir=todo_dir,
            )

            self.assertEqual(["t:regrp01"], receipt["appliedTagRemovals"])
            self.assertEqual([], receipt["errors"])
            written = (todo_dir / "todo.md").read_text(encoding="utf-8")
            self.assertEqual("- [ ] Regroup slice #keep_me ID: t:regrp01\n", written)

    def test_tag_removal_of_absent_tag_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            todo = todo_dir / "todo.md"
            todo.write_text("- [ ] Untouched #keep_me ID: t:regrp02\n", encoding="utf-8")

            receipt = sync_changes.apply_sync(
                {
                    "tagRemovals": [
                        {
                            "task_id": "t:regrp02",
                            "source_file": str(todo),
                            "expected_text": "Untouched #keep_me ID: t:regrp02",
                            "tags": ["gone_epic"],
                        }
                    ]
                },
                todo_dir=todo_dir,
            )

            self.assertEqual([], receipt["appliedTagRemovals"])
            self.assertEqual({"task_id": "t:regrp02", "reason": "tags not present"}, receipt["skipped"][0])
            self.assertEqual("- [ ] Untouched #keep_me ID: t:regrp02\n", todo.read_text(encoding="utf-8"))

    def test_status_change_applies_tag_removal_in_the_same_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "icebox.md").write_text(
                "- [ ] Promote me #stale_epic #ui ID: t:regrp03\n", encoding="utf-8"
            )

            receipt = sync_changes.apply_sync(
                {
                    "statusChanges": [
                        {
                            "task_id": "t:regrp03",
                            "source_file": str(todo_dir / "icebox.md"),
                            "expected_text": "Promote me #stale_epic #ui ID: t:regrp03",
                            "new_status": "Pending",
                        }
                    ],
                    "tagRemovals": [
                        {
                            "task_id": "t:regrp03",
                            "source_file": str(todo_dir / "icebox.md"),
                            "expected_text": "Promote me #stale_epic #ui ID: t:regrp03",
                            "tags": ["stale_epic"],
                        }
                    ],
                    "tagAdditions": [
                        {
                            "task_id": "t:regrp03",
                            "source_file": str(todo_dir / "icebox.md"),
                            "expected_text": "Promote me #stale_epic #ui ID: t:regrp03",
                            "tags": ["fresh_epic"],
                        }
                    ],
                },
                todo_dir=todo_dir,
            )

            self.assertEqual(["t:regrp03"], receipt["appliedStatus"])
            self.assertEqual(["t:regrp03"], receipt["appliedTagRemovals"])
            self.assertEqual(["t:regrp03"], receipt["appliedTags"])
            self.assertEqual("", (todo_dir / "icebox.md").read_text(encoding="utf-8"))
            written = (todo_dir / "todo.md").read_text(encoding="utf-8")
            self.assertNotIn("#stale_epic", written)
            self.assertIn("#fresh_epic", written)
            self.assertIn("#ui", written)
            self.assertIn("ID: t:regrp03", written)

    def test_retitle_and_reepic_together_apply_in_one_rewrite(self):
        """The core grooming operation: clearer wording + a corrected epic, at once."""
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            todo = todo_dir / "todo.md"
            todo.write_text("- [ ] fix it #wrong_epic ID: t:groom01\n", encoding="utf-8")
            expected = "fix it #wrong_epic ID: t:groom01"

            receipt = sync_changes.apply_sync(
                {
                    "statusChanges": [
                        {
                            "task_id": "t:groom01",
                            "source_file": str(todo),
                            "expected_text": expected,
                            "new_status": "Pending",
                        }
                    ],
                    "textEdits": [
                        {
                            "task_id": "t:groom01",
                            "source_file": str(todo),
                            "expected_text": expected,
                            "new_text": "Fix the board header overlapping the filter bar",
                        }
                    ],
                    "tagAdditions": [
                        {
                            "task_id": "t:groom01",
                            "source_file": str(todo),
                            "expected_text": expected,
                            "tags": ["right_epic"],
                        }
                    ],
                    "tagRemovals": [
                        {
                            "task_id": "t:groom01",
                            "source_file": str(todo),
                            "expected_text": expected,
                            "tags": ["wrong_epic"],
                        }
                    ],
                },
                todo_dir=todo_dir,
            )

            self.assertEqual([], receipt["conflicts"])
            self.assertEqual([], receipt["errors"])
            self.assertEqual(["t:groom01"], receipt["appliedTexts"])
            self.assertEqual(["t:groom01"], receipt["appliedTags"])
            self.assertEqual(["t:groom01"], receipt["appliedTagRemovals"])
            self.assertEqual(
                "- [ ] Fix the board header overlapping the filter bar #right_epic ID: t:groom01\n",
                todo.read_text(encoding="utf-8"),
            )

    def test_generate_task_id_avoids_collisions_with_existing_set(self):
        with mock.patch.object(sync_changes.random, "choices", side_effect=[list("aaaaaaa"), list("bbbbbbb")]):
            task_id = sync_changes.generate_task_id({"t:aaaaaaa"})
        self.assertEqual("t:bbbbbbb", task_id)

    def test_collect_existing_ids_reads_all_lane_files(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            (todo_dir / "todo.md").write_text("- [ ] One ID: t:aaaaaaa\n", encoding="utf-8")
            (todo_dir / "doing.md").write_text("- [ ] Two ID: t:bbbbbbb\n", encoding="utf-8")
            (todo_dir / "done.md").write_text("- [x] Three ID: t:ccccccc\n", encoding="utf-8")
            (todo_dir / "icebox.md").write_text("- [ ] Four ID: t:ddddddd\n", encoding="utf-8")

            existing = sync_changes.collect_existing_ids(todo_dir)

            self.assertEqual({"t:aaaaaaa", "t:bbbbbbb", "t:ccccccc", "t:ddddddd"}, existing)

    def test_new_item_id_never_collides_with_existing_ids_in_board(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            (todo_dir / "icebox.md").write_text("- [ ] Existing ID: t:aaaaaaa\n", encoding="utf-8")

            with mock.patch.object(sync_changes.random, "choices", side_effect=[list("aaaaaaa"), list("bbbbbbb")]):
                receipt = sync_changes.apply_sync(
                    {"newItems": [{"text": "New item", "status": "On schedule", "tags": []}]},
                    todo_dir=todo_dir,
                )

            self.assertEqual(["New item"], receipt["appliedNewItems"])
            icebox_text = (todo_dir / "icebox.md").read_text(encoding="utf-8")
            self.assertIn("t:bbbbbbb", icebox_text)
            self.assertNotIn("New item ID: t:aaaaaaa", icebox_text)

    def test_backlog_later_someday_map_to_icebox(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")

            for status in ["Backlog", "Later", "Someday"]:
                self.assertEqual(
                    todo_dir / "icebox.md",
                    sync_changes.destination_for(status, todo_dir),
                )

    def test_new_item_prepends_before_existing_tasks(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            for name in ["todo.md", "doing.md", "done.md", "icebox.md"]:
                (todo_dir / name).write_text("", encoding="utf-8")
            icebox = todo_dir / "icebox.md"
            icebox.write_text(
                "# Icebox\n"
                "- [ ] Existing task one ID: t:existing1\n"
                "- [ ] Existing task two ID: t:existing2\n",
                encoding="utf-8",
            )

            receipt = sync_changes.apply_sync(
                {
                    "version": 1,
                    "newItems": [{"text": "Brand new at top", "status": "On schedule", "tags": ["fresh"]}],
                },
                todo_dir=todo_dir,
            )

            self.assertEqual(["Brand new at top"], receipt["appliedNewItems"])
            self.assertEqual([], receipt["errors"])
            icebox_text = icebox.read_text(encoding="utf-8")
            lines = icebox_text.split("\n")
            # Find the new item line (should be line 2, after header)
            self.assertEqual("# Icebox", lines[0])
            self.assertRegex(lines[1], r"- \[ \] Brand new at top #fresh ID: t:[a-z0-9]{7}")
            self.assertIn("Existing task one", icebox_text)
            self.assertIn("Existing task two", icebox_text)

    def test_create_epic_appends_block_with_dir_and_color(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            (todo_dir / "epics.md").write_text(
                "# Epics\n\n## #existing\n\ndir: /Users/example/Projects/pivotal-claw\n",
                encoding="utf-8",
            )

            ok, reason = sync_changes.create_epic(
                "new_epic", dir="/Users/example/Projects/pivotal-claw", color="8E6CEF", todo_dir=todo_dir,
            )

            self.assertTrue(ok)
            self.assertIsNone(reason)
            text = (todo_dir / "epics.md").read_text(encoding="utf-8")
            self.assertIn("## #new_epic", text)
            self.assertIn("dir: /Users/example/Projects/pivotal-claw", text)
            self.assertIn("color: 8E6CEF", text)
            self.assertIn("## #existing", text)

    def test_create_epic_strips_leading_hash_and_omits_blank_fields(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            (todo_dir / "epics.md").write_text("# Epics\n", encoding="utf-8")

            ok, reason = sync_changes.create_epic("#bare_tag", dir=None, color=None, todo_dir=todo_dir)

            self.assertTrue(ok)
            self.assertIsNone(reason)
            text = (todo_dir / "epics.md").read_text(encoding="utf-8")
            self.assertIn("## #bare_tag", text)
            self.assertNotIn("dir:", text)
            self.assertNotIn("color:", text)

    def test_create_epic_rejects_invalid_tag(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            (todo_dir / "epics.md").write_text("# Epics\n", encoding="utf-8")

            ok, reason = sync_changes.create_epic("bad tag!", dir=None, color=None, todo_dir=todo_dir)

            self.assertFalse(ok)
            self.assertEqual("invalid tag", reason)
            self.assertNotIn("bad tag", (todo_dir / "epics.md").read_text(encoding="utf-8"))

    def test_create_epic_rejects_duplicate_tag(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            (todo_dir / "epics.md").write_text(
                "# Epics\n\n## #scheduler\n\ndir: /some/dir\n",
                encoding="utf-8",
            )

            ok, reason = sync_changes.create_epic("scheduler", dir=None, color=None, todo_dir=todo_dir)

            self.assertFalse(ok)
            self.assertEqual("tag already exists", reason)
            text = (todo_dir / "epics.md").read_text(encoding="utf-8")
            self.assertEqual(1, text.count("## #scheduler"))

    def test_create_epic_creates_epics_md_when_missing(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)

            ok, reason = sync_changes.create_epic("fresh_start", dir=None, color=None, todo_dir=todo_dir)

            self.assertTrue(ok)
            self.assertIsNone(reason)
            self.assertIn("## #fresh_start", (todo_dir / "epics.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
