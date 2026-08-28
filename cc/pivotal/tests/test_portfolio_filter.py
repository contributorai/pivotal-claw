import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
TODO_DIR = Path(__file__).resolve().parents[2] / "todo"
TREE_VIEWER_DIR = TODO_DIR / "tree_viewer"

# demo-data/ is tracked; cc/todo/ is gitignored local board data that only
# exists on a working machine. Both are checked when present.
DATA_DIRS = (REPO_ROOT / "demo-data", TODO_DIR)


def read_main_page():
    return (TREE_VIEWER_DIR / "pivotal_tasks.html").read_text(encoding="utf-8")


class PortfolioFilterHelperTests(unittest.TestCase):
    def test_portfolio_filter_helpers_are_defined_and_exported(self):
        source = (TREE_VIEWER_DIR / "pivotalData.js").read_text(encoding="utf-8")

        for name in (
            "getPortfolios",
            "getActivePortfolios",
            "getPortfolioEpicTags",
            "getPortfolioForEpic",
            "epicMatchesPortfolioFilter",
            "getEpicsForPortfolioFilter",
            "getTaskPortfolios",
            "taskMatchesPortfolioFilter",
            "countTasksByPortfolio",
            "getPortfolioShortcuts",
            "resolvePortfolioShortcut",
        ):
            self.assertIn(f"function {name}", source)
            self.assertIn(f"{name},", source)


class PortfolioFilterWiringTests(unittest.TestCase):
    def test_header_exposes_the_portfolio_select(self):
        page = read_main_page()

        self.assertIn('id="portfolio-filter" data-testid="portfolio-filter"', page)
        self.assertIn('id="portfolio-filter-wrap"', page)

    def test_portfolio_filter_reads_the_url_parameter(self):
        page = read_main_page()

        self.assertIn(
            'const portfolioFromUrl = new URLSearchParams(window.location.search).get("portfolio");',
            page,
        )
        self.assertIn('PS.selectedPortfolio = portfolioFromUrl || PS.selectedPortfolio || "all";', page)

    def test_lanes_filter_by_the_selected_portfolio(self):
        page = read_main_page()

        self.assertIn(
            "PivotalData.taskMatchesPortfolioFilter(effectiveTask, PS.selectedPortfolio, window.todoData.epic_meta || {}, portfolioData())",
            page,
        )

    def test_queued_icebox_stories_filter_by_the_selected_portfolio(self):
        page = read_main_page()

        self.assertIn(
            "PivotalData.taskMatchesPortfolioFilter({ tags: item.tags || [] }, PS.selectedPortfolio, window.todoData.epic_meta || {}, portfolioData())",
            page,
        )

    def test_epic_dropdown_is_scoped_to_the_selected_portfolio(self):
        page = read_main_page()

        self.assertIn(
            "PivotalData.getEpicsForPortfolioFilter(PS.selectedPortfolio, epicMeta, data)",
            page,
        )

    def test_epics_pane_and_detail_panes_respect_the_selected_portfolio(self):
        page = read_main_page()

        self.assertIn(
            "PivotalData.epicMatchesPortfolioFilter(tag, PS.selectedPortfolio, portfolioData())",
            page,
        )
        self.assertEqual(
            page.count("PivotalData.epicMatchesPortfolioFilter(tag, PS.selectedPortfolio, portfolioData())"),
            2,
        )

    def test_only_the_dropdown_hides_archived_portfolios(self):
        page = read_main_page()
        source = (TREE_VIEWER_DIR / "pivotalData.js").read_text(encoding="utf-8")

        # The dropdown lists active portfolios only...
        self.assertIn("const items = PivotalData.getActivePortfolios(data);", page)
        # ...but ownership and counts must still see archived ones, or their stories
        # would fall into the Unassigned bucket.
        self.assertIn("function getPortfolios(portfolioData) {\n    return ((portfolioData || {}).items || [])", source)
        self.assertIn("getPortfolios(portfolioData).find((item) => getPortfolioEpicTags", source)

    def test_an_archived_portfolio_stays_selectable_when_it_is_the_scope(self):
        page = read_main_page()

        self.assertIn("(item) => item.archived && item.id === PS.selectedPortfolio", page)
        self.assertIn('{ label: "Archived", entries: selectedArchived ? [selectedArchived] : [], keys: false }', page)

    def test_portfolio_page_collapses_archived_portfolios(self):
        page = (TREE_VIEWER_DIR / "portfolio.html").read_text(encoding="utf-8")

        self.assertIn('portfolioItems.filter((item) => item.archived)', page)
        self.assertIn('details.dataset.testid = "archived-portfolios"', page)
        self.assertIn("details.archived > summary", page)

    def test_portfolio_filter_is_rendered_each_pass(self):
        page = read_main_page()

        self.assertIn("renderPortfolioFilter(tagOverrides);", page)
        self.assertIn("selectPortfolio(portfolioFilterEl.value);", page)


class PortfolioShortcutTests(unittest.TestCase):
    """Digit keys switch portfolio scope; the mapping is pinned in portfolio.json."""

    def test_shortcut_lookup_only_sees_active_portfolios(self):
        source = (TREE_VIEWER_DIR / "pivotalData.js").read_text(encoding="utf-8")
        body = source.split("function getPortfolioShortcuts", 1)[1].split("function resolvePortfolioShortcut", 1)[0]

        # A shortcut is a dropdown-class surface, so archived portfolios are hidden
        # here — but never in the ownership lookups. See portfolio/AGENTS.md.
        self.assertIn("getActivePortfolios(portfolioData)", body)

    def test_zero_resets_the_scope_to_all_portfolios(self):
        source = (TREE_VIEWER_DIR / "pivotalData.js").read_text(encoding="utf-8")
        body = source.split("function resolvePortfolioShortcut", 1)[1][:400]

        self.assertIn('"0"', body)
        self.assertIn('"all"', body)

    def test_mouse_and_keyboard_share_one_switch_path(self):
        page = read_main_page()

        self.assertIn("function selectPortfolio(id) {", page)
        self.assertIn("selectPortfolio(portfolioFilterEl.value);", page)
        self.assertIn("selectPortfolio(target);", page)
        # Switching portfolio must keep resetting the epic filter, whichever path ran.
        switch = page.split("function selectPortfolio(id) {", 1)[1][:400]
        self.assertIn('PS.selectedEpic = "all";', switch)

    def test_digit_keys_are_bound_in_the_global_keydown_handler(self):
        page = read_main_page()
        handler = page.split('document.addEventListener("keydown", (e) => {', 1)[1]

        self.assertIn('/^[0-9]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey', handler)
        self.assertIn("PivotalData.resolvePortfolioShortcut(e.key, portfolioData())", handler)
        # The handler's existing INPUT/TEXTAREA/SELECT guard is what keeps digits from
        # firing while typing, so the branch must sit inside that same handler.
        self.assertIn('["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)', page)

    def test_the_dropdown_labels_show_each_shortcut(self):
        page = read_main_page()

        self.assertIn("function shortcutPrefix(", page)
        self.assertIn("shortcutPrefix(", page.split("function renderPortfolioFilter", 1)[1][:2000])

    def test_the_portfolio_page_shows_each_card_its_key(self):
        page = (TREE_VIEWER_DIR / "portfolio.html").read_text(encoding="utf-8")

        # The board dropdown is not the only place the bindings have to be legible.
        self.assertIn('const shortcut = item.archived ? "" : String(item.shortcut || "");', page)
        self.assertIn('class="p-key"', page)
        self.assertIn(".p-key {", page)
        # The 0 = all portfolios binding has no card of its own to sit on.
        self.assertIn('id="shortcut-hint"', page)
        self.assertIn('active.some((item) => item.shortcut)', page)


class PortfolioStackRankTests(unittest.TestCase):
    """/portfolio has two modes: the original cards grid, and a ranked single column."""

    def page(self):
        return (TREE_VIEWER_DIR / "portfolio.html").read_text(encoding="utf-8")

    def rank_js(self):
        return (TREE_VIEWER_DIR / "portfolioRank.js").read_text(encoding="utf-8")

    def test_rank_helpers_are_defined_and_exported(self):
        source = self.rank_js()

        for name in ("moveWithin", "orderedIds", "attachRowReorder"):
            self.assertIn(f"function {name}", source)
            self.assertIn(f"{name},", source)

    def test_moving_a_row_keeps_every_other_row(self):
        # The order math is what gets POSTed, so a dropped or duplicated entry here
        # would silently rewrite portfolio.json.
        body = self.rank_js().split("function moveWithin", 1)[1].split("function ", 1)[0]

        self.assertIn("splice", body)

    def test_the_page_offers_both_modes(self):
        page = self.page()

        self.assertIn('data-testid="portfolio-mode"', page)
        self.assertIn('data-mode="cards"', page)
        self.assertIn('data-mode="stack"', page)

    def test_the_selected_mode_is_remembered_and_url_addressable(self):
        page = self.page()

        self.assertIn('"pivotalPortfolioMode"', page)
        self.assertIn('new URLSearchParams(window.location.search).get("mode")', page)

    def test_stack_rank_nests_epics_under_their_portfolio(self):
        page = self.page()

        # Ranks read 1, 1.1, 1.2 — the epic's number only means anything under its
        # portfolio's, which is the whole point of the nested layout.
        self.assertIn('class="rank"', page)
        self.assertIn('class="sub-rank"', page)
        self.assertIn("renderStackRow", page)
        self.assertIn("renderStackEpicRow", page)

    def test_stack_rank_rows_are_draggable_and_persist_their_order(self):
        page = self.page()

        self.assertIn("PortfolioRank.attachRowReorder", page)
        self.assertIn('"/api/portfolio/reorder"', page)
        self.assertIn('order:', page)
        self.assertIn('epic_tags:', page)

    def test_archived_portfolios_are_not_rankable(self):
        page = self.page()
        # Archiving retires a portfolio; it has no place in the priority order, but
        # it still has to stay reachable, as in cards mode.
        self.assertIn("stackActive", page)

    def test_a_portfolio_row_collapses_its_epics(self):
        page = self.page()

        self.assertIn("function setCollapsed", page)
        body = page.split("function setCollapsed", 1)[1].split("function stackBlocks", 1)[0]
        self.assertIn('classList.toggle("collapsed", collapsed)', body)
        self.assertIn('setAttribute("aria-expanded", String(!collapsed))', body)
        self.assertIn(".stack-block.collapsed .stack-epics { display: none; }", page)
        # Collapsing hides the epics and nothing else: the one-line summary stays.
        self.assertIn('<span class="chevron">', page)

    def test_the_collapsed_set_is_remembered_between_visits(self):
        page = self.page()

        self.assertIn('"pivotalPortfolioCollapsed"', page)
        # A corrupt or absent value must not take the whole page down with it.
        self.assertIn("function readCollapsed", page)
        self.assertIn("catch", page.split("function readCollapsed", 1)[1][:400])

    def test_finishing_a_drag_does_not_toggle_the_row(self):
        page = self.page()
        rank_js = self.rank_js()

        # pointerup is followed by a click on the same row; without this guard,
        # every reorder would also collapse or expand what was just dragged.
        self.assertIn("isClickAfterDrag", rank_js)
        self.assertIn("isClickAfterDrag,", rank_js)
        self.assertIn("PortfolioRank.isClickAfterDrag(stack)", page)

    def test_the_grip_and_epic_links_do_not_toggle_the_row(self):
        page = self.page()
        handler = page.split("head.addEventListener(\"click\"", 1)[1][:400]

        self.assertIn('closest(".grip")', handler)
        self.assertIn('closest("a")', handler)

    def test_one_control_collapses_and_expands_every_portfolio(self):
        page = self.page()

        self.assertIn('id="collapse-all"', page)
        self.assertIn("function setAllCollapsed", page)
        # One write path to the stored set: collapse-all must go through the same
        # setCollapsed the row click uses, or the two can disagree about state.
        body = page.split("function setAllCollapsed", 1)[1][:300]
        self.assertIn("setCollapsed(", body)
        self.assertNotIn("writeCollapsed(", body)

    def test_the_control_label_follows_the_rows(self):
        page = self.page()

        # Collapsing the last expanded row by hand has to flip the label too.
        self.assertIn("function refreshCollapseAll", page)
        body = page.split("function setCollapsed", 1)[1].split("function stackBlocks", 1)[0]
        self.assertIn("refreshCollapseAll();", body)

    def test_the_control_is_hidden_in_cards_mode(self):
        page = self.page()
        apply_mode = page.split("function applyMode", 1)[1][:600]

        self.assertIn("collapseAllBtn.hidden = !stackMode;", apply_mode)

    def test_the_page_loads_the_rank_module(self):
        self.assertIn('<script src="portfolioRank.js"></script>', self.page())


class PortfolioDataTests(unittest.TestCase):
    """Portfolio membership drives the filter, so a typo silently hides stories."""

    def datasets(self):
        found = False
        for data_dir in DATA_DIRS:
            portfolio_path = data_dir / "portfolio.json"
            epics_path = data_dir / "epics.md"
            if not portfolio_path.exists() or not epics_path.exists():
                continue
            found = True
            items = json.loads(portfolio_path.read_text(encoding="utf-8"))["items"]
            epics = set(
                re.findall(r"^## #([a-z0-9_]+)", epics_path.read_text(encoding="utf-8"), re.M)
            )
            yield data_dir.name, items, epics
        if not found:
            self.skipTest("no portfolio dataset available")

    def test_every_portfolio_has_the_expected_shape(self):
        for name, items, _ in self.datasets():
            for item in items:
                with self.subTest(dataset=name, portfolio=item.get("id")):
                    self.assertTrue(item.get("id"), f"portfolio missing id: {item}")
                    self.assertTrue(item.get("name"), f"portfolio missing name: {item['id']}")
                    self.assertIsInstance(item.get("epic_tags"), list)
                    self.assertIsInstance(item.get("focus"), bool)

    def test_portfolio_ids_are_unique(self):
        for name, items, _ in self.datasets():
            with self.subTest(dataset=name):
                ids = [item["id"] for item in items]
                self.assertEqual(len(ids), len(set(ids)))

    def test_shortcuts_are_single_digits_and_unique(self):
        for name, items, _ in self.datasets():
            owner = {}
            for item in items:
                shortcut = item.get("shortcut") or ""
                if not shortcut:
                    continue
                with self.subTest(dataset=name, portfolio=item["id"]):
                    self.assertIn(shortcut, list("123456789"))
                    self.assertNotIn(
                        shortcut,
                        owner,
                        f"shortcut {shortcut} is claimed by both {owner.get(shortcut)} and {item['id']}",
                    )
                owner[shortcut] = item["id"]

    def test_every_epic_tag_exists_in_epics_md(self):
        for name, items, epics in self.datasets():
            for item in items:
                for tag in item["epic_tags"]:
                    with self.subTest(dataset=name, tag=tag):
                        self.assertIn(tag, epics, f"{item['id']} references unknown epic #{tag}")

    def test_no_epic_belongs_to_two_portfolios(self):
        for name, items, _ in self.datasets():
            owner = {}
            for item in items:
                for tag in item["epic_tags"]:
                    with self.subTest(dataset=name, tag=tag):
                        self.assertNotIn(
                            tag,
                            owner,
                            f"#{tag} is claimed by both {owner.get(tag)} and {item['id']}",
                        )
                    owner[tag] = item["id"]


if __name__ == "__main__":
    unittest.main()
