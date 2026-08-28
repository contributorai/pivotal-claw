import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import build_public_release


class BuildPublicReleaseTests(unittest.TestCase):
    def test_release_manifest_keeps_internal_roadmap_private(self):
        manifest = (REPO_ROOT / "PUBLIC_RELEASE_FILES.txt").read_text(encoding="utf-8")

        self.assertNotIn("docs/roadmap.md", manifest.splitlines())

    def test_manifest_globs_copy_only_selected_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "source"
            destination = Path(td) / "release"
            (root / "app" / "tests").mkdir(parents=True)
            (root / "README.md").write_text("read me", encoding="utf-8")
            (root / "private.txt").write_text("private", encoding="utf-8")
            (root / "app" / "main.py").write_text("print('ok')", encoding="utf-8")
            (root / "app" / "tests" / "test_main.py").write_text("pass", encoding="utf-8")
            manifest = root / "PUBLIC_RELEASE_FILES.txt"
            manifest.write_text("README.md\napp/*.py\napp/tests/*.py\n", encoding="utf-8")

            copied = build_public_release.build(root, manifest, destination)

            self.assertEqual(
                ["README.md", "app/main.py", "app/tests/test_main.py"], copied
            )
            self.assertFalse((destination / "private.txt").exists())

    def test_manifest_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "source"
            root.mkdir()
            manifest = root / "PUBLIC_RELEASE_FILES.txt"
            manifest.write_text("../secret.txt\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                build_public_release.build(root, manifest, Path(td) / "release")


if __name__ == "__main__":
    unittest.main()
