import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_public_release


class PublicReleaseAuditTests(unittest.TestCase):
    def test_clean_release_tree_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Example project\n", encoding="utf-8")

            self.assertEqual([], audit_public_release.scan_tree(root))

    def test_personal_and_secret_material_is_reported_without_echoing_values(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "unsafe.txt").write_text(
                "Mi" + "sha owns this file\n"
                "/Users/private-user/project\n"  # public-audit: allow
                "owner@example.com\n"
                "postgres" + "ql://reader:" + "plain-text-password" + "@db.example.invalid/app\n",
                encoding="utf-8",
            )

            findings = audit_public_release.scan_tree(root)

        self.assertEqual(
            ["credential-url", "email-address", "macos-home-path", "personal-name"],
            sorted(finding.rule for finding in findings),
        )
        self.assertTrue(all(finding.path == "unsafe.txt" for finding in findings))
        self.assertNotIn("plain-text-password", repr(findings))

    def test_binary_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "asset.bin").write_bytes(b"\x00/Users/private-user\x00")

            self.assertEqual([], audit_public_release.scan_tree(root))


if __name__ == "__main__":
    unittest.main()
