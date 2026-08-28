import tempfile
import unittest
from pathlib import Path
import sys


TODO_MODULE_DIR = Path(__file__).resolve().parents[2] / "todo"
sys.path.insert(0, str(TODO_MODULE_DIR))
sys.modules.pop("generate_todo_viewer", None)

import generate_todo_viewer as gtv


class GenerateTodoViewerTests(unittest.TestCase):
    def test_seed_files_are_created_without_overwriting(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            gtv.ensure_seed_files(todo_dir)

            for name in ["todo.md", "doing.md", "done.md", "icebox.md", "epics.md"]:
                self.assertTrue((todo_dir / name).exists())

            existing = todo_dir / "todo.md"
            existing.write_text("- [ ] keep me\n", encoding="utf-8")
            gtv.ensure_seed_files(todo_dir)
            self.assertEqual("- [ ] keep me\n", existing.read_text(encoding="utf-8"))

    def test_parse_tasks_skips_fenced_code_and_keeps_children_sections_tags_ids(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            path = todo_dir / "todo.md"
            path.write_text(
                "\n".join(
                    [
                        "# Product",
                        "- [ ] Parent story #alpha ID: t:abc1234",
                        "  - [ ] Child story #beta",
                        "```",
                        "- [ ] Not a task #code",
                        "```",
                        "## Later",
                        "- [x] Completed story #done Completed 2026-06-20 ID: t:done999",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            tasks = gtv.parse_todo_file(path)

            self.assertEqual(2, len(tasks))
            self.assertEqual("Product", tasks[0].section)
            self.assertEqual("Pending", tasks[0].status)
            self.assertEqual(["alpha"], tasks[0].tags)
            self.assertEqual("t:abc1234", tasks[0].task_id)
            self.assertEqual(1, len(tasks[0].children))
            self.assertEqual("Child story #beta", tasks[0].children[0].text)
            self.assertEqual("Later", tasks[1].section)
            self.assertEqual("Done", tasks[1].status)
            self.assertEqual("2026-06-20", tasks[1].completion_date)

    def test_build_data_assigns_ids_deduplicates_and_parses_epics(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            gtv.ensure_seed_files(todo_dir)
            (todo_dir / "todo.md").write_text("- [ ] Same story #alpha\n", encoding="utf-8")
            (todo_dir / "doing.md").write_text("- [ ] Same story #alpha ID: t:doing1x\n", encoding="utf-8")
            (todo_dir / "epics.md").write_text(
                "# #alpha\n"
                "Alpha workstream.\n"
                "dir: /tmp/example\n"
                "color: #FF5733\n"
                "resume: 11111111-2222-3333-4444-555555555555\n",
                encoding="utf-8",
            )

            data = gtv.build_todo_data(todo_dir)

            self.assertEqual(1, len(data["tasks"]))
            self.assertEqual("In progress", data["tasks"][0]["status"])
            self.assertIn("ID: t:", (todo_dir / "todo.md").read_text(encoding="utf-8"))
            self.assertEqual("/tmp/example", data["epic_meta"]["alpha"]["dir"])
            self.assertEqual({"In progress": 1}, data["counts"])

    def test_malformed_existing_ids_are_replaced_with_canonical_ids(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            gtv.ensure_seed_files(todo_dir)
            todo_path = todo_dir / "todo.md"
            todo_path.write_text("- [ ] Bad old ID #alpha ID: T-OLD-1234\n", encoding="utf-8")

            data = gtv.build_todo_data(todo_dir)
            rewritten = todo_path.read_text(encoding="utf-8")

            self.assertRegex(data["tasks"][0]["task_id"], r"^t:[a-z0-9]{7}$")
            self.assertRegex(rewritten, r"ID: t:[a-z0-9]{7}\n$")
            self.assertNotIn("T-OLD-1234", rewritten)

    def test_parse_status_markers_and_tilde_fences(self):
        with tempfile.TemporaryDirectory() as td:
            todo_dir = Path(td)
            path = todo_dir / "done.md"
            path.write_text(
                "\n".join(
                    [
                        "# Done Items",
                        "~~~",
                        "- [ ] Hidden code task #skip",
                        "~~~",
                        "- [/] In flight from done file #work ID: t:flight1",
                        "- [-] Also in progress #work ID: t:flight2",
                        "✓ Legacy completed #done ID: t:legacy1",
                        "- [ ] File fallback done #done ID: t:filedn1",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            tasks = gtv.parse_todo_file(path)

            self.assertEqual(4, len(tasks))
            self.assertEqual("In progress", tasks[0].status)
            self.assertEqual("In progress", tasks[1].status)
            self.assertEqual("Done", tasks[2].status)
            self.assertEqual("Done", tasks[3].status)
            self.assertNotIn("Hidden code task", [task.text for task in tasks])

    def test_epics_parse_description_log_and_sessions(self):
        with tempfile.TemporaryDirectory() as td:
            epics = Path(td) / "epics.md"
            epics.write_text(
                "# #my_epic\n"
                "First description line.\n"
                "Second description line.\n"
                "dir: /tmp/my-epic\n"
                "log: /tmp/my-epic/session.log\n"
                "session: 2026-06-28 11111111-2222-3333-4444-555555555555 initial pass\n",
                encoding="utf-8",
            )

            meta = gtv.parse_epics_md(epics)

            self.assertEqual("my_epic", meta["my_epic"]["tag"])
            self.assertEqual("/tmp/my-epic", meta["my_epic"]["dir"])
            self.assertEqual("/tmp/my-epic/session.log", meta["my_epic"]["session_log"])
            self.assertEqual(
                "First description line.\nSecond description line.",
                meta["my_epic"]["description"],
            )
            self.assertEqual("11111111-2222-3333-4444-555555555555", meta["my_epic"]["sessions"][0]["uuid"])

    def test_epics_parse_hash_headings_resume_color_and_sessions_newest_first(self):
        with tempfile.TemporaryDirectory() as td:
            epics = Path(td) / "epics.md"
            epics.write_text(
                "## #my_epic\n"
                "First line.\n"
                "Second line.\n"
                "dir: /tmp/my-epic\n"
                "resume: 11111111-2222-3333-4444-555555555555\n"
                "color: #ff5733\n"
                "session_log: /tmp/my-epic/session.log\n"
                "session: 2026-06-27 22222222-3333-4444-5555-666666666666 older pass\n"
                "session: 2026-06-28 33333333-4444-5555-6666-777777777777 newer pass\n",
                encoding="utf-8",
            )

            meta = gtv.parse_epics_md(epics)

            self.assertEqual("my_epic", meta["my_epic"]["tag"])
            self.assertEqual("First line.\nSecond line.", meta["my_epic"]["description"])
            self.assertEqual("/tmp/my-epic", meta["my_epic"]["dir"])
            self.assertEqual("11111111-2222-3333-4444-555555555555", meta["my_epic"]["resume"])
            self.assertEqual("FF5733", meta["my_epic"]["color"])
            self.assertEqual("/tmp/my-epic/session.log", meta["my_epic"]["session_log"])
            self.assertEqual("33333333-4444-5555-6666-777777777777", meta["my_epic"]["sessions"][0]["uuid"])
            self.assertEqual("newer pass", meta["my_epic"]["sessions"][0]["summary"])

    def test_epics_missing_file_and_invalid_color_are_safe(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.md"
            self.assertEqual({}, gtv.parse_epics_md(missing))

            epics = Path(td) / "epics.md"
            epics.write_text(
                "# #bad_color\n"
                "Description survives.\n"
                "color: not-a-color\n",
                encoding="utf-8",
            )

            meta = gtv.parse_epics_md(epics)

            self.assertEqual("Description survives.", meta["bad_color"]["description"])
            self.assertNotIn("color", meta["bad_color"])

            epics.write_text(
                "# #extra_hash\n"
                "color: ##ff5733\n",
                encoding="utf-8",
            )

            meta = gtv.parse_epics_md(epics)

            self.assertNotIn("color", meta["extra_hash"])

    def test_epics_sessions_are_sorted_newest_first(self):
        with tempfile.TemporaryDirectory() as td:
            epics = Path(td) / "epics.md"
            epics.write_text(
                "# #my_epic\n"
                "session: 2026-06-28 33333333-4444-5555-6666-777777777777 newer pass\n"
                "session: 2026-06-27 22222222-3333-4444-5555-666666666666 older pass\n",
                encoding="utf-8",
            )

            meta = gtv.parse_epics_md(epics)

            self.assertEqual("33333333-4444-5555-6666-777777777777", meta["my_epic"]["sessions"][0]["uuid"])
            self.assertEqual("22222222-3333-4444-5555-666666666666", meta["my_epic"]["sessions"][1]["uuid"])


if __name__ == "__main__":
    unittest.main()
