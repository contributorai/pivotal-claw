"""Tests for the /pivotal-groom skill script.

Design note: this skill reports only *exact* findings — set arithmetic and string
matching. An earlier draft scored stories for "neglect" using lane depth, last-touched
data and focus-portfolio membership; measured against the real board those signals fire
on 40-64% of stories and lane depth is anti-correlated with attention (lane moves append
to the bottom). The scoring was cut. Judgement about which stories matter, and how to
reword them, belongs to Claude reading the inventory, not to a number.
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
GROOM = REPO_ROOT / ".agents" / "skills" / "pivotal-groom" / "scripts" / "groom.py"

EPICS = """# Epics

## #job_hunt

## #board_ux

## #housekeeping

## #ghost_town
"""

PORTFOLIO = {
    "items": [
        {"id": "job_search", "name": "Job Search", "color": "1F7A8C",
         "epic_tags": ["job_hunt"], "focus": True},
        {"id": "board", "name": "Board", "color": "4DA3FF",
         "epic_tags": ["board_ux"], "focus": False},
        {"id": "attic", "name": "Attic", "color": "888888",
         "epic_tags": ["housekeeping"], "focus": False, "archived": True},
    ]
}


def make_board(td: Path, todo="", doing="", done="", icebox=""):
    (td / "todo.md").write_text("# Todo\n" + todo, encoding="utf-8")
    (td / "doing.md").write_text("# Doing\n" + doing, encoding="utf-8")
    (td / "done.md").write_text("# Done\n" + done, encoding="utf-8")
    (td / "icebox.md").write_text("# Icebox\n" + icebox, encoding="utf-8")
    (td / "epics.md").write_text(EPICS, encoding="utf-8")
    (td / "portfolio.json").write_text(json.dumps(PORTFOLIO), encoding="utf-8")
    return td


def run(*args, todo_dir=None):
    cmd = [sys.executable, str(GROOM), *args]
    if todo_dir is not None:
        cmd += ["--todo-dir", str(todo_dir)]
    return subprocess.run(cmd, capture_output=True, text=True)


def scan_json(todo_dir, *extra):
    result = run("scan", "--json", *extra, todo_dir=todo_dir)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class GroomInventoryTests(unittest.TestCase):
    def test_scan_reports_every_open_story_with_lane_tags_and_portfolio(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo="- [ ] Rewrite the resume exporter #job_hunt ID: t:aaaaaa1\n",
                icebox="- [ ] Tidy the sidebar #board_ux ID: t:aaaaaa2\n",
            )

            by_id = {s["id"]: s for s in scan_json(todo_dir)["stories"]}

            self.assertEqual("todo", by_id["t:aaaaaa1"]["lane"])
            self.assertEqual(["job_hunt"], by_id["t:aaaaaa1"]["tags"])
            self.assertEqual(["Job Search"], by_id["t:aaaaaa1"]["portfolios"])
            self.assertEqual("Rewrite the resume exporter", by_id["t:aaaaaa1"]["title"])
            self.assertEqual("icebox", by_id["t:aaaaaa2"]["lane"])

    def test_scan_skips_done_unless_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo="- [ ] Still open #job_hunt ID: t:bbbbbb1\n",
                done="- [x] Shipped it #job_hunt ID: t:bbbbbb2 Completed 2026-08-01\n",
            )

            self.assertEqual({"t:bbbbbb1"}, {s["id"] for s in scan_json(todo_dir)["stories"]})
            self.assertEqual(
                {"t:bbbbbb1", "t:bbbbbb2"},
                {s["id"] for s in scan_json(todo_dir, "--include-done")["stories"]},
            )

    def test_epic_filter_narrows_the_review_to_one_epic(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo=(
                    "- [ ] Resume work #job_hunt ID: t:ccccccc\n"
                    "- [ ] Sidebar work #board_ux ID: t:ddddddd\n"
                ),
            )

            ids = {s["id"] for s in scan_json(todo_dir, "--epic", "job_hunt")["stories"]}
            self.assertEqual({"t:ccccccc"}, ids)


class GroomFindingTests(unittest.TestCase):
    def test_identical_stories_group_into_one_cluster_not_many_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            dupe = "- [ ] Add a dark mode toggle to the board header #board_ux ID: t:dup000{}\n"
            todo_dir = make_board(
                Path(tmp), todo="".join(dupe.format(n) for n in (1, 2, 3))
            )

            clusters = scan_json(todo_dir)["findings"]["duplicates"]

            self.assertEqual(1, len(clusters), "three identical stories must be one cluster")
            self.assertEqual({"t:dup0001", "t:dup0002", "t:dup0003"}, set(clusters[0]["ids"]))

    def test_distinct_stories_are_not_paired(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo=(
                    "- [ ] pull in old resumes from google drive #job_hunt ID: t:eeeeee1\n"
                    "- [ ] pull in old resumes from hard drive #job_hunt ID: t:eeeeee2\n"
                ),
            )

            self.assertEqual([], scan_json(todo_dir)["findings"]["duplicates"])

    def test_junk_rows_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo=(
                    "- [ ] ===== ID: t:junk001\n"
                    "- [ ] test ID: t:junk002\n"
                    "- [ ] Add a real story about the exporter #job_hunt ID: t:junk003\n"
                ),
            )

            junk = {j["id"] for j in scan_json(todo_dir)["findings"]["junk"]}
            self.assertEqual({"t:junk001", "t:junk002"}, junk)

    def test_untagged_and_undeclared_tags_are_separated(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo=(
                    "- [ ] no epic at all here ID: t:ffffff1\n"
                    "- [ ] when adding an epic it should go into #icebox ID: t:ffffff2\n"
                ),
            )

            findings = scan_json(todo_dir)["findings"]

            self.assertEqual(["t:ffffff1"], [s["id"] for s in findings["no_epic"]])
            undeclared = {u["tag"]: u for u in findings["undeclared_tags"]}
            self.assertIn("icebox", undeclared)
            self.assertEqual(["t:ffffff2"], undeclared["icebox"]["ids"])

    def test_epics_without_portfolio_and_dead_epics_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo=(
                    "- [ ] Some housekeeping chore #housekeeping ID: t:ggggggg\n"
                    "- [ ] Board polish #board_ux ID: t:hhhhhhh\n"
                ),
            )
            (todo_dir / "epics.md").write_text(EPICS + "\n## #orphan_epic\n", encoding="utf-8")
            (todo_dir / "todo.md").write_text(
                (todo_dir / "todo.md").read_text(encoding="utf-8")
                + "- [ ] Orphan work #orphan_epic ID: t:iiiiiii\n",
                encoding="utf-8",
            )

            findings = scan_json(todo_dir)["findings"]

            orphans = {o["epic"]: o["stories"] for o in findings["epics_without_portfolio"]}
            self.assertEqual(1, orphans.get("orphan_epic"))
            self.assertIn("ghost_town", [d["epic"] for d in findings["dead_epics"]])
            self.assertNotIn("board_ux", orphans)

    def test_archived_portfolio_and_wip_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo="- [ ] Sweep old logs #housekeeping ID: t:jjjjjjj\n",
                doing="- [/] Actively building the thing #board_ux ID: t:kkkkkkk\n",
            )

            findings = scan_json(todo_dir)["findings"]

            self.assertEqual(["t:jjjjjjj"], [s["id"] for s in findings["archived_portfolio"]])
            self.assertEqual(["t:kkkkkkk"], [s["id"] for s in findings["wip"]])

    def test_clarity_shape_flags_mark_terse_bloated_and_url_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            long_title = ("please " * 30).strip()
            todo_dir = make_board(
                Path(tmp),
                todo=(
                    "- [ ] fix it #board_ux ID: t:lllllll\n"
                    f"- [ ] {long_title} #board_ux ID: t:mmmmmmm\n"
                    "- [ ] See https://example.com/a/very/long/link?q=1&r=2 #board_ux ID: t:nnnnnnn\n"
                ),
            )

            by_id = {s["id"]: s for s in scan_json(todo_dir)["stories"]}
            self.assertIn("terse", by_id["t:lllllll"]["flags"])
            self.assertIn("overlong", by_id["t:mmmmmmm"]["flags"])
            self.assertIn("raw-url", by_id["t:nnnnnnn"]["flags"])

    def test_text_output_carries_the_three_grooming_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] fix it ID: t:ooooooo\n")

            result = run("scan", todo_dir=todo_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("NEEDS A DECISION", result.stdout)
            self.assertIn("CLARITY", result.stdout)
            self.assertIn("CATEGORIZATION", result.stdout)
            self.assertIn("t:ooooooo", result.stdout)

    def test_html_review_is_written_and_self_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] fix it #board_ux ID: t:ppppppp\n")
            out = Path(tmp) / "review.html"

            result = run("scan", "--html", str(out), todo_dir=todo_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            page = out.read_text(encoding="utf-8")
            self.assertIn("t:ppppppp", page)
            self.assertIn("<style>", page)
            # without this the page renders "·" as "Â·" when opened from file://
            self.assertIn('<meta charset="utf-8">', page)
            self.assertNotIn("http://", page.split("</style>")[0])


class GroomHtmlLinkTests(unittest.TestCase):
    """Every story reference in the review must open that story on the board.

    The board already deep-links: processDeepLink() in pivotal_tasks.html reads ?id=,
    force-opens the pane the story's status belongs to, selects and scroll-highlights it.
    The portfolio/epic/dir params matter as much as the id — each filter is sticky, and a
    story excluded by one is never rendered, so the link would silently do nothing.
    """

    def test_every_story_mention_links_to_the_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo=(
                    "- [ ] Add a dark mode toggle to the board header #board_ux ID: t:lnk0001\n"
                    "- [ ] Add a dark mode toggle to the board header #board_ux ID: t:lnk0002\n"
                    "- [ ] no epic here ID: t:lnk0003\n"
                ),
            )
            out = Path(tmp) / "review.html"

            self.assertEqual(0, run("scan", "--html", str(out), todo_dir=todo_dir).returncode)
            page = out.read_text(encoding="utf-8")

            for story_id in ("t:lnk0001", "t:lnk0002", "t:lnk0003"):
                self.assertIn(f"http://localhost:5056/?portfolio=all&amp;epic=all&amp;dir=all"
                              f"&amp;id={story_id}", page)

            # no story id may appear as bare text — every mention is clickable
            bare = re.findall(r'<code class="id">(t:[a-z0-9]{7})</code>', page)
            self.assertEqual([], bare)

    def test_sticky_filters_are_cleared_by_the_link(self):
        """Without these params a portfolio-filtered board renders no card to jump to."""
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] Something #board_ux ID: t:lnk0005\n")
            out = Path(tmp) / "review.html"

            run("scan", "--html", str(out), todo_dir=todo_dir)
            href = re.search(r'href="([^"]*t:lnk0005)"', out.read_text(encoding="utf-8")).group(1)

            self.assertIn("portfolio=all", href)
            self.assertIn("epic=all", href)
            self.assertIn("dir=all", href)

    def test_board_url_is_overridable(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] Something #board_ux ID: t:lnk0006\n")
            out = Path(tmp) / "review.html"

            run("scan", "--html", str(out), "--board-url", "https://demo.example.com",
                todo_dir=todo_dir)
            page = out.read_text(encoding="utf-8")

            self.assertIn("https://demo.example.com/?portfolio=all", page)
            self.assertNotIn("http://localhost:5056", page)

    def test_appendix_lists_every_story_with_its_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo="- [ ] Rewrite the resume exporter #job_hunt ID: t:lnk0004\n",
            )
            out = Path(tmp) / "review.html"

            run("scan", "--html", str(out), todo_dir=todo_dir)
            page = out.read_text(encoding="utf-8")

            self.assertIn("Rewrite the resume exporter", page)
            self.assertIn("Job Search", page)


class GroomTodoDirTests(unittest.TestCase):
    """--todo-dir must win wherever it appears on the command line.

    Regression: --todo-dir was accepted both before and after the subcommand, but the
    subparser's default silently overwrote the value given before it — so
    `groom.py --todo-dir /tmp/copy apply ...` reported success while editing the REAL
    board. Any flag that selects which board gets written must be tested in both
    positions.
    """

    def test_todo_dir_before_subcommand_is_honoured_by_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] Only here #board_ux ID: t:pos0001\n")

            result = subprocess.run(
                [sys.executable, str(GROOM), "--todo-dir", str(todo_dir), "scan", "--json"],
                capture_output=True, text=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(str(todo_dir), report["board"]["todo_dir"])
            self.assertEqual(["t:pos0001"], [s["id"] for s in report["stories"]])

    def test_todo_dir_before_subcommand_is_honoured_by_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] fix it #board_ux ID: t:pos0002\n")

            result = subprocess.run(
                [sys.executable, str(GROOM), "--todo-dir", str(todo_dir),
                 "apply", "t:pos0002", "--retitle", "Fix the header overlap"],
                capture_output=True, text=True,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(
                "- [ ] Fix the header overlap #board_ux ID: t:pos0002",
                (todo_dir / "todo.md").read_text(encoding="utf-8"),
            )

    def test_apply_never_silently_writes_a_different_board(self):
        """A no-op write must not be reported as success."""
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] fix it #board_ux ID: t:pos0003\n")
            other = make_board(Path(tempfile.mkdtemp()), todo="- [ ] untouched ID: t:pos0004\n")

            subprocess.run(
                [sys.executable, str(GROOM), "--todo-dir", str(todo_dir),
                 "apply", "t:pos0003", "--retitle", "Renamed here only"],
                capture_output=True, text=True,
            )

            self.assertIn("Renamed here only", (todo_dir / "todo.md").read_text(encoding="utf-8"))
            self.assertNotIn("Renamed here only", (other / "todo.md").read_text(encoding="utf-8"))


class GroomApplyTests(unittest.TestCase):
    def test_retitle_preserves_tags_and_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] fix it #board_ux ID: t:qqqqqqq\n")

            result = run(
                "apply", "t:qqqqqqq",
                "--retitle", "Fix the board header overlapping the filter bar",
                todo_dir=todo_dir,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(
                "- [ ] Fix the board header overlapping the filter bar #board_ux ID: t:qqqqqqq",
                (todo_dir / "todo.md").read_text(encoding="utf-8").splitlines()[1],
            )

    def test_retitle_and_reepic_together_do_not_half_apply(self):
        """The core grooming move: clearer wording plus a corrected epic, in one write."""
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] fix it #board_ux ID: t:rrrrrrr\n")

            result = run(
                "apply", "t:rrrrrrr",
                "--retitle", "Fix the resume exporter dropping the last page",
                "--add-epic", "job_hunt", "--drop-epic", "board_ux",
                todo_dir=todo_dir,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            line = (todo_dir / "todo.md").read_text(encoding="utf-8").splitlines()[1]
            self.assertEqual(
                "- [ ] Fix the resume exporter dropping the last page #job_hunt ID: t:rrrrrrr",
                line,
            )

    def test_lane_move_relocates_the_story(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), icebox="- [ ] Ship the fix #job_hunt ID: t:sssssss\n")

            result = run("apply", "t:sssssss", "--lane", "todo", todo_dir=todo_dir)

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertNotIn("t:sssssss", (todo_dir / "icebox.md").read_text(encoding="utf-8"))
            self.assertIn("t:sssssss", (todo_dir / "todo.md").read_text(encoding="utf-8"))

    def test_dropping_a_tag_that_is_not_a_declared_epic_is_refused(self):
        """Guards prose hashtags: '...should go into #icebox' must never lose the word."""
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(
                Path(tmp),
                todo="- [ ] when adding an epic it should go into #icebox ID: t:ttttttt\n",
            )
            before = (todo_dir / "todo.md").read_text(encoding="utf-8")

            result = run("apply", "t:ttttttt", "--drop-epic", "icebox", todo_dir=todo_dir)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("icebox", result.stderr)
            self.assertEqual(before, (todo_dir / "todo.md").read_text(encoding="utf-8"))

    def test_unknown_epic_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] Polish it #board_ux ID: t:uuuuuuu\n")
            before = (todo_dir / "todo.md").read_text(encoding="utf-8")

            result = run("apply", "t:uuuuuuu", "--add-epic", "not_an_epic", todo_dir=todo_dir)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("not_an_epic", result.stderr)
            self.assertEqual(before, (todo_dir / "todo.md").read_text(encoding="utf-8"))

    def test_dry_run_reports_the_resulting_line_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] fix it #board_ux ID: t:vvvvvvv\n")
            before = (todo_dir / "todo.md").read_text(encoding="utf-8")

            result = run(
                "apply", "t:vvvvvvv", "--retitle", "Fix the header overlap", "--dry-run",
                todo_dir=todo_dir,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Fix the header overlap", result.stdout)
            self.assertEqual(before, (todo_dir / "todo.md").read_text(encoding="utf-8"))

    def test_unknown_story_id_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] Something #board_ux ID: t:wwwwwww\n")

            result = run("apply", "t:zzzzzzz", "--retitle", "Nope", todo_dir=todo_dir)

            self.assertNotEqual(0, result.returncode)
            self.assertIn("t:zzzzzzz", result.stderr)

    def test_apply_backs_up_the_lane_file_before_changing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo_dir = make_board(Path(tmp), todo="- [ ] fix it #board_ux ID: t:xxxxxxx\n")
            before = (todo_dir / "todo.md").read_text(encoding="utf-8")

            run("apply", "t:xxxxxxx", "--retitle", "Fix the header overlap", todo_dir=todo_dir)

            backups = list((todo_dir / ".sync_backups").glob("todo.md.*"))
            self.assertTrue(backups, "expected a backup of todo.md before the write")
            self.assertIn(before, [b.read_text(encoding="utf-8") for b in backups])


if __name__ == "__main__":
    unittest.main()
