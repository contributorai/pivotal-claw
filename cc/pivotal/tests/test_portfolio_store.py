import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TODO_MODULE_DIR = REPO_ROOT / "cc" / "todo"
DEMO_DATA_DIR = REPO_ROOT / "demo-data"
CLI = REPO_ROOT / ".agents" / "skills" / "pivotal_portfolio" / "scripts" / "portfolio.py"

sys.path.insert(0, str(TODO_MODULE_DIR))

import portfolio_store as store  # noqa: E402


class PortfolioStoreTestCase(unittest.TestCase):
    """Works on a throwaway copy of demo-data/, which is tracked and always present."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.todo_dir = self.tmp / "todo"
        self.todo_dir.mkdir()
        for name in ("portfolio.json", "epics.md"):
            shutil.copy2(DEMO_DATA_DIR / name, self.todo_dir / name)
        self.items = store.read_portfolios(self.todo_dir)
        self.epic_meta = store.load_epic_meta(self.todo_dir)

    def ids(self, items):
        return [item["id"] for item in items]

    def tags_of(self, items, portfolio_id):
        item = next(entry for entry in items if entry["id"] == portfolio_id)
        return [store.normalize_tag(tag) for tag in item["epic_tags"]]

    def owner_of(self, items, tag):
        for item in items:
            if tag in [store.normalize_tag(t) for t in item["epic_tags"]]:
                return item["id"]
        return None


class ReadWriteTests(PortfolioStoreTestCase):
    def test_the_demo_dataset_is_valid_to_begin_with(self):
        self.assertEqual(store.validate(self.items, self.epic_meta), [])

    def test_round_trip_preserves_content_and_unknown_keys(self):
        items = store.create_portfolio(self.items, "scratch", "Scratch")
        items[-1]["custom_field"] = "kept"
        store.write_portfolios(items, self.todo_dir)

        reloaded = store.read_portfolios(self.todo_dir)
        self.assertEqual(self.ids(reloaded), self.ids(items))
        self.assertEqual(reloaded[-1]["custom_field"], "kept")
        # Known fields come first, in FIELD_ORDER, so the file stays readable when
        # hand-edited. Fields left at their default (archived) simply aren't written.
        keys = list(reloaded[-1])
        known = [key for key in keys if key in store.FIELD_ORDER]
        self.assertEqual(known, [key for key in store.FIELD_ORDER if key in known])
        self.assertEqual(keys[-1], "custom_field")

    def test_writing_backs_up_and_leaves_no_temp_files(self):
        store.write_portfolios(self.items, self.todo_dir)

        backups = list((self.todo_dir / ".sync_backups").glob("portfolio.json.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(
            json.loads(backups[0].read_text(encoding="utf-8")),
            json.loads((DEMO_DATA_DIR / "portfolio.json").read_text(encoding="utf-8")),
        )
        strays = [p.name for p in self.todo_dir.iterdir() if p.name.startswith("portfolio.json.")]
        self.assertEqual(strays, [])

    def test_reading_a_missing_file_yields_no_portfolios(self):
        (self.todo_dir / "portfolio.json").unlink()

        self.assertEqual(store.read_portfolios(self.todo_dir), [])

    def test_reading_malformed_json_explains_the_problem(self):
        (self.todo_dir / "portfolio.json").write_text("{ not json", encoding="utf-8")

        with self.assertRaises(ValueError):
            store.read_portfolios(self.todo_dir)


class MutatorTests(PortfolioStoreTestCase):
    def test_assign_moves_an_epic_out_of_its_previous_portfolio(self):
        tag = self.tags_of(self.items, self.items[0]["id"])[0]
        destination = self.items[1]["id"]

        updated = store.assign_epics(self.items, [tag], to=destination)

        self.assertEqual(self.owner_of(updated, tag), destination)
        self.assertNotIn(tag, self.tags_of(updated, self.items[0]["id"]))
        self.assertEqual(store.validate(updated, self.epic_meta), [])

    def test_assign_accepts_a_leading_hash(self):
        tag = self.tags_of(self.items, self.items[0]["id"])[0]

        updated = store.assign_epics(self.items, ["#" + tag], to=self.items[1]["id"])

        self.assertEqual(self.owner_of(updated, tag), self.items[1]["id"])

    def test_assign_is_idempotent(self):
        tag = self.tags_of(self.items, self.items[0]["id"])[0]
        once = store.assign_epics(self.items, [tag], to=self.items[1]["id"])
        twice = store.assign_epics(once, [tag], to=self.items[1]["id"])

        self.assertEqual(self.tags_of(once, self.items[1]["id"]), self.tags_of(twice, self.items[1]["id"]))

    def test_unassign_removes_an_epic_from_every_portfolio(self):
        tag = self.tags_of(self.items, self.items[0]["id"])[0]

        updated = store.assign_epics(self.items, [tag], to=None)

        self.assertIsNone(self.owner_of(updated, tag))
        self.assertIn(tag, store.unassigned_epics(updated, self.epic_meta))

    def test_assign_to_an_unknown_portfolio_is_rejected(self):
        with self.assertRaises(ValueError):
            store.assign_epics(self.items, ["anything"], to="does_not_exist")

    def test_delete_leaves_its_epics_unassigned(self):
        victim = self.items[0]
        freed = self.tags_of(self.items, victim["id"])

        updated = store.delete_portfolio(self.items, victim["id"])

        self.assertNotIn(victim["id"], self.ids(updated))
        for tag in freed:
            self.assertIsNone(self.owner_of(updated, tag))
        self.assertEqual(store.validate(updated, self.epic_meta), [])

    def test_set_focus_clears_portfolios_left_out(self):
        self.assertTrue(any(item.get("focus") for item in self.items))
        chosen = self.items[-1]["id"]

        updated = store.set_focus(self.items, [chosen])

        self.assertEqual([item["id"] for item in updated if item["focus"]], [chosen])

    def test_set_focus_with_no_ids_clears_everything(self):
        updated = store.set_focus(self.items, [])

        self.assertEqual([item["id"] for item in updated if item["focus"]], [])

    def test_create_rejects_a_duplicate_id(self):
        with self.assertRaises(ValueError):
            store.create_portfolio(self.items, self.items[0]["id"], "Clashing")

    def test_create_normalizes_the_color(self):
        updated = store.create_portfolio(self.items, "scratch", "Scratch", color="#f39c12")

        self.assertEqual(updated[-1]["color"], "F39C12")

    def test_update_changes_only_the_fields_passed(self):
        target = self.items[0]

        updated = store.update_portfolio(self.items, target["id"], name="Renamed")
        changed = next(item for item in updated if item["id"] == target["id"])

        self.assertEqual(changed["name"], "Renamed")
        self.assertEqual(changed["epic_tags"], target["epic_tags"])
        self.assertEqual(changed["focus"], target["focus"])

    def test_mutators_do_not_modify_the_original_list(self):
        before = json.dumps(self.items, sort_keys=True)

        store.assign_epics(self.items, [self.tags_of(self.items, self.items[0]["id"])[0]], to=None)
        store.set_focus(self.items, [])
        store.delete_portfolio(self.items, self.items[0]["id"])

        self.assertEqual(json.dumps(self.items, sort_keys=True), before)


class ArchiveTests(PortfolioStoreTestCase):
    def focused_id(self):
        return next(item["id"] for item in self.items if item.get("focus"))

    def test_archiving_sets_the_flag_and_clears_focus(self):
        target = self.focused_id()

        updated = store.set_archived(self.items, [target])
        changed = next(item for item in updated if item["id"] == target)

        self.assertTrue(changed["archived"])
        self.assertFalse(changed["focus"])
        self.assertEqual(store.validate(updated, self.epic_meta), [])

    def test_unarchiving_restores_without_refocusing(self):
        target = self.focused_id()
        archived = store.set_archived(self.items, [target])

        restored = store.set_archived(archived, [target], archived=False)
        changed = next(item for item in restored if item["id"] == target)

        self.assertFalse(changed["archived"])
        self.assertFalse(changed["focus"])

    def test_archiving_leaves_other_portfolios_alone(self):
        target = self.items[0]["id"]

        updated = store.set_archived(self.items, [target])

        for item in updated:
            if item["id"] != target:
                self.assertFalse(item.get("archived", False))

    def test_an_archived_portfolio_still_owns_its_epics(self):
        before = store.unassigned_epics(self.items, self.epic_meta)

        updated = store.set_archived(self.items, [self.items[0]["id"]])

        self.assertEqual(store.unassigned_epics(updated, self.epic_meta), before)
        self.assertEqual(len(store.archived_portfolios(updated)), 1)
        self.assertEqual(len(store.active_portfolios(updated)), len(self.items) - 1)

    def test_focusing_an_archived_portfolio_is_refused(self):
        target = self.items[0]["id"]
        archived = store.set_archived(self.items, [target])

        with self.assertRaises(ValueError) as ctx:
            store.set_focus(archived, [target])

        self.assertIn("unarchive", str(ctx.exception))

    def test_archiving_an_unknown_portfolio_is_rejected(self):
        with self.assertRaises(ValueError):
            store.set_archived(self.items, ["does_not_exist"])

    def test_archived_survives_a_write_read_round_trip(self):
        updated = store.set_archived(self.items, [self.items[0]["id"]])
        store.write_portfolios(updated, self.todo_dir)

        reloaded = store.read_portfolios(self.todo_dir)

        self.assertTrue(reloaded[0]["archived"])


class ValidationTests(PortfolioStoreTestCase):
    def test_unknown_epic_tag_is_reported(self):
        broken = store.assign_epics(self.items, ["not_a_real_epic"], to=self.items[0]["id"])

        errors = store.validate(broken, self.epic_meta)

        self.assertTrue(any("not_a_real_epic" in error for error in errors))

    def test_duplicate_id_is_reported(self):
        broken = self.items + [dict(self.items[0])]

        self.assertTrue(any("duplicate id" in error for error in store.validate(broken, self.epic_meta)))

    def test_epic_claimed_twice_is_reported(self):
        tag = self.tags_of(self.items, self.items[0]["id"])[0]
        broken = [dict(item, epic_tags=list(item["epic_tags"])) for item in self.items]
        broken[1]["epic_tags"].append(tag)

        self.assertTrue(any("claimed by both" in error for error in store.validate(broken, self.epic_meta)))

    def test_malformed_color_and_missing_name_are_reported(self):
        broken = [dict(item) for item in self.items]
        broken[0]["color"] = "nope"
        broken[0]["name"] = "  "

        errors = store.validate(broken, self.epic_meta)

        self.assertTrue(any("not 6 hex digits" in error for error in errors))
        self.assertTrue(any("missing name" in error for error in errors))

    def test_non_boolean_focus_is_reported(self):
        broken = [dict(item) for item in self.items]
        broken[0]["focus"] = "yes"

        self.assertTrue(any("focus must be" in error for error in store.validate(broken, self.epic_meta)))

    def test_non_boolean_archived_is_reported(self):
        broken = [dict(item) for item in self.items]
        broken[0]["archived"] = "yes"

        self.assertTrue(any("archived must be" in error for error in store.validate(broken, self.epic_meta)))

    def test_archived_and_focused_at_once_is_reported(self):
        broken = [dict(item) for item in self.items]
        broken[0]["archived"] = True
        broken[0]["focus"] = True

        errors = store.validate(broken, self.epic_meta)

        self.assertTrue(any("archived and in focus" in error for error in errors))


class ShortcutTests(PortfolioStoreTestCase):
    """`shortcut` pins a digit key to a portfolio for the board's number-key switcher."""

    def setUp(self):
        super().setUp()
        # demo-data ships bindings; start from a clean slate so each case owns its digits.
        self.items = [dict(item, shortcut="") for item in self.items]

    def test_create_and_update_round_trip_the_shortcut(self):
        created = store.create_portfolio(self.items, "scratch", "Scratch", shortcut="4")
        self.assertEqual(created[-1]["shortcut"], "4")
        self.assertEqual(store.validate(created, self.epic_meta), [])

        store.write_portfolios(created, self.todo_dir)
        reloaded = store.read_portfolios(self.todo_dir)
        self.assertEqual(reloaded[-1]["shortcut"], "4")

    def test_update_can_clear_the_shortcut(self):
        target = self.items[0]["id"]
        assigned = store.update_portfolio(self.items, target, shortcut="7")

        cleared = store.update_portfolio(assigned, target, shortcut="")

        self.assertEqual(next(item for item in cleared if item["id"] == target)["shortcut"], "")

    def test_update_leaves_the_shortcut_alone_when_not_passed(self):
        target = self.items[0]["id"]
        assigned = store.update_portfolio(self.items, target, shortcut="7")

        renamed = store.update_portfolio(assigned, target, name="Renamed")

        self.assertEqual(next(item for item in renamed if item["id"] == target)["shortcut"], "7")

    def test_a_non_digit_shortcut_is_reported(self):
        for bad in ("x", "12", "0", " "):
            with self.subTest(shortcut=bad):
                broken = [dict(item) for item in self.items]
                broken[0]["shortcut"] = bad

                errors = store.validate(broken, self.epic_meta)

                self.assertTrue(any("shortcut must be" in error for error in errors), errors)

    def test_a_shortcut_claimed_twice_is_reported(self):
        broken = [dict(item) for item in self.items]
        broken[0]["shortcut"] = "3"
        broken[1]["shortcut"] = "3"

        errors = store.validate(broken, self.epic_meta)

        self.assertTrue(any("shortcut 3 is claimed by both" in error for error in errors), errors)

    def test_an_archived_portfolio_still_reserves_its_shortcut(self):
        # Its digit is inert on the board, but unarchiving must never introduce a clash.
        broken = [dict(item) for item in self.items]
        broken[0]["shortcut"] = "3"
        broken[0]["archived"] = True
        broken[0]["focus"] = False
        broken[1]["shortcut"] = "3"

        errors = store.validate(broken, self.epic_meta)

        self.assertTrue(any("shortcut 3 is claimed by both" in error for error in errors), errors)

    def test_no_shortcut_at_all_is_valid(self):
        self.assertEqual(store.validate(self.items, self.epic_meta), [])


class StackRankTests(PortfolioStoreTestCase):
    """The /portfolio page's stack rank mode is array order, so reordering is a write."""

    def test_reordering_portfolios_rewrites_the_item_order(self):
        items = store.reorder_portfolios(self.items, ["plan_the_work", "build_the_board"])

        self.assertEqual(self.ids(items)[:2], ["plan_the_work", "build_the_board"])
        self.assertEqual(sorted(self.ids(items)), sorted(self.ids(self.items)))

    def test_portfolios_left_out_of_the_order_keep_their_slots(self):
        # The stack rank list only drags active portfolios; an archived one must not
        # be swept to the end just because it never appeared in the payload.
        items = store.set_archived(self.items, ["onboarding"])
        before = self.ids(items).index("onboarding")

        reordered = store.reorder_portfolios(items, ["plan_the_work", "build_the_board"])

        self.assertEqual(self.ids(reordered).index("onboarding"), before)

    def test_reordering_an_unknown_portfolio_is_refused(self):
        with self.assertRaises(ValueError):
            store.reorder_portfolios(self.items, ["nope"])

    def test_a_repeated_id_is_refused(self):
        with self.assertRaises(ValueError):
            store.reorder_portfolios(self.items, ["build_the_board", "build_the_board"])

    def test_reordering_epics_rewrites_the_tags_within_one_portfolio(self):
        items = store.reorder_epics(self.items, "build_the_board", ["agent_api", "board"])

        self.assertEqual(self.tags_of(items, "build_the_board"), ["agent_api", "board"])
        self.assertEqual(self.tags_of(items, "plan_the_work"), ["portfolio"])

    def test_reordering_epics_accepts_hash_prefixed_tags(self):
        items = store.reorder_epics(self.items, "build_the_board", ["#agent_api", "#board"])

        self.assertEqual(self.tags_of(items, "build_the_board"), ["agent_api", "board"])

    def test_reordering_an_epic_the_portfolio_does_not_own_is_refused(self):
        with self.assertRaises(ValueError):
            store.reorder_epics(self.items, "build_the_board", ["portfolio"])

    def test_reordering_epics_in_an_unknown_portfolio_is_refused(self):
        with self.assertRaises(ValueError):
            store.reorder_epics(self.items, "nope", [])

    def test_reordering_never_mutates_the_input(self):
        snapshot = json.loads(json.dumps(self.items))

        store.reorder_portfolios(self.items, ["plan_the_work", "build_the_board"])
        store.reorder_epics(self.items, "build_the_board", ["agent_api", "board"])

        self.assertEqual(self.items, snapshot)


class SummarizeTests(PortfolioStoreTestCase):
    def test_rollups_cover_every_portfolio_and_report_orphans(self):
        todo_data = store.load_todo_data(DEMO_DATA_DIR)

        summary = store.summarize(self.items, todo_data)

        self.assertEqual(
            sorted(entry["id"] for entry in summary["portfolios"]),
            sorted(self.ids(self.items)),
        )
        for entry in summary["portfolios"]:
            self.assertLessEqual(entry["done"], entry["total"])
        self.assertEqual(
            summary["unassigned_epics"],
            store.unassigned_epics(self.items, todo_data["epic_meta"]),
        )


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), "--todo-dir", str(DEMO_DATA_DIR), *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    def test_show_succeeds_against_the_demo_dataset(self):
        result = self.run_cli("show")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("In focus", result.stdout)

    def test_audit_reports_a_clean_demo_dataset(self):
        result = self.run_cli("audit")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no problems found", result.stdout)

    def test_assign_rejects_an_unknown_epic_without_writing(self):
        before = (DEMO_DATA_DIR / "portfolio.json").read_text(encoding="utf-8")

        result = self.run_cli("assign", "definitely_not_an_epic", "--to", "build_the_board")

        self.assertEqual(result.returncode, 1)
        self.assertIn("epics.md", result.stderr + result.stdout)
        self.assertEqual((DEMO_DATA_DIR / "portfolio.json").read_text(encoding="utf-8"), before)


class ArchiveCommandTests(PortfolioStoreTestCase):
    """Mutating CLI runs, against the throwaway copy rather than tracked demo-data."""

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), "--todo-dir", str(self.todo_dir), *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    def test_archive_hides_it_from_show_but_names_it_on_the_summary_line(self):
        target = next(item for item in self.items if item.get("focus"))

        archive = self.run_cli("archive", target["id"])
        self.assertEqual(archive.returncode, 0, archive.stderr)
        self.assertIn("Dropped from focus", archive.stdout)

        listing = self.run_cli("show")
        self.assertEqual(listing.returncode, 0, listing.stderr)
        body, _, tail = listing.stdout.partition("Archived (1)")
        self.assertNotIn(target["name"], body)
        self.assertIn(target["name"], tail)

    def test_show_archived_expands_them_into_a_section(self):
        target = self.items[0]
        self.run_cli("archive", target["id"])

        listing = self.run_cli("show", "--archived")

        self.assertIn("\nArchived\n", listing.stdout)
        self.assertIn(target["name"], listing.stdout)

    def test_unarchive_restores_it_to_the_listing(self):
        target = self.items[0]
        self.run_cli("archive", target["id"])

        restore = self.run_cli("unarchive", target["id"])
        listing = self.run_cli("show")

        self.assertEqual(restore.returncode, 0, restore.stderr)
        self.assertNotIn("Archived (", listing.stdout)
        self.assertIn(target["name"], listing.stdout)

    def test_archiving_does_not_orphan_epics(self):
        self.run_cli("archive", self.items[0]["id"])

        audit = self.run_cli("audit")

        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        self.assertIn("no problems found", audit.stdout)

    def test_focus_refuses_an_archived_portfolio(self):
        target = self.items[0]
        self.run_cli("archive", target["id"])

        result = self.run_cli("focus", target["id"])

        self.assertEqual(result.returncode, 1)
        self.assertIn("unarchive", (result.stderr + result.stdout).lower())

    def test_archiving_an_unknown_id_fails_without_writing(self):
        before = (self.todo_dir / "portfolio.json").read_text(encoding="utf-8")

        result = self.run_cli("archive", "does_not_exist")

        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.todo_dir / "portfolio.json").read_text(encoding="utf-8"), before)


class ShortcutCommandTests(PortfolioStoreTestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), "--todo-dir", str(self.todo_dir), *args],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    def test_edit_assigns_a_shortcut_and_show_reports_it(self):
        target = self.items[0]

        result = self.run_cli("edit", target["id"], "--shortcut", "9")
        self.assertEqual(result.returncode, 0, result.stderr)

        stored = store.read_portfolios(self.todo_dir)
        self.assertEqual(next(item for item in stored if item["id"] == target["id"])["shortcut"], "9")
        self.assertIn("[9]", self.run_cli("show").stdout)

    def test_edit_with_an_empty_shortcut_clears_it(self):
        target = self.items[0]
        self.run_cli("edit", target["id"], "--shortcut", "9")

        result = self.run_cli("edit", target["id"], "--shortcut", "")

        self.assertEqual(result.returncode, 0, result.stderr)
        stored = store.read_portfolios(self.todo_dir)
        self.assertEqual(next(item for item in stored if item["id"] == target["id"])["shortcut"], "")

    def test_a_clashing_shortcut_is_refused_without_writing(self):
        self.run_cli("edit", self.items[0]["id"], "--shortcut", "8")
        before = (self.todo_dir / "portfolio.json").read_text(encoding="utf-8")

        result = self.run_cli("edit", self.items[1]["id"], "--shortcut", "8")

        self.assertEqual(result.returncode, 1)
        self.assertIn("claimed by both", result.stderr + result.stdout)
        self.assertEqual((self.todo_dir / "portfolio.json").read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
