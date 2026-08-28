import unittest
from pathlib import Path


TREE_VIEWER_DIR = Path(__file__).resolve().parents[2] / "todo" / "tree_viewer"


class DirectoryFilterEpicTests(unittest.TestCase):
    def test_epics_pane_uses_the_selected_directory(self):
        page = (TREE_VIEWER_DIR / "pivotal_tasks.html").read_text(encoding="utf-8")

        # The portfolio filter chains onto this call, so match the call itself
        # rather than the whole statement.
        self.assertIn(
            "const tags = FocusLogic.rankEpicTags(PivotalData.getEpicsForDirFilter(PS.selectedDir, epicMeta)",
            page,
        )

    def test_open_epic_panes_use_the_selected_directory(self):
        page = (TREE_VIEWER_DIR / "pivotal_tasks.html").read_text(encoding="utf-8")

        self.assertIn(
            "PivotalData.epicMatchesDirFilter(tag, PS.selectedDir, epicMeta)",
            page,
        )

    def test_directory_filter_epic_helpers_are_exported(self):
        source = (TREE_VIEWER_DIR / "pivotalData.js").read_text(encoding="utf-8")

        self.assertIn("function epicMatchesDirFilter", source)
        self.assertIn("function getEpicsForDirFilter", source)
        self.assertIn("epicMatchesDirFilter,", source)
        self.assertIn("getEpicsForDirFilter,", source)


if __name__ == "__main__":
    unittest.main()
