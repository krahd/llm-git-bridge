"""Verify the installer-generated provenance and native About contact controls."""
import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "install.sh").read_text(encoding="utf-8")
GUI = (ROOT / "approval_gui.swift").read_text(encoding="utf-8")


def generate_plist(version="6", commit="a" * 40, updated="2026-10-30T09:45:00-06:00"):
    fragment = INSTALLER.split("<<'PYAPPPLIST'\n", 1)[1].split("\nPYAPPPLIST", 1)[0]
    with tempfile.TemporaryDirectory() as temp:
        info = Path(temp) / "Info.plist"
        result = subprocess.run(
            [sys.executable, "-c", fragment, str(info), str(Path(temp) / "approvals"),
             "/opt/bridge/approval_helper.py", sys.executable,
             "net.laurenzo.local-executor-bridge", version, commit, updated],
            text=True, capture_output=True, timeout=10,
        )
        if result.returncode:
            return None, result.stderr
        return plistlib.loads(info.read_bytes()), ""


class AboutDialogMetadataTests(unittest.TestCase):
    def test_embeds_installed_provenance_not_latest_remote(self):
        obj, error = generate_plist()
        self.assertFalse(error)
        self.assertEqual(obj["BridgeVersion"], "6")
        self.assertEqual(obj["CFBundleShortVersionString"], "6.0")
        self.assertEqual(obj["BridgeSourceCommit"], "a" * 40)
        self.assertEqual(obj["BridgeSourceUpdatedAt"], "2026-10-30T15:45:00Z")

    def test_version_comes_from_repo_version_file(self):
        self.assertIn('BRIDGE_VERSION="$(tr -d', INSTALLER)
        self.assertIn('"$SCRIPT_DIR/VERSION"', INSTALLER)
        self.assertIn('git -C "$SCRIPT_DIR/.." show -s --format=%cI HEAD', INSTALLER)

    def test_requires_valid_version_source_and_timezone(self):
        for version, sha, updated in (
            ("6-beta", "a" * 40, "2026-10-30T00:00:00Z"),
            ("6", "not-a-commit", "2026-10-30T00:00:00Z"),
            ("6", "a" * 40, "2026-10-30T00:00:00"),
            ("6", "a" * 40, "not-a-date"),
        ):
            with self.subTest(version=version, sha=sha, updated=updated):
                obj, error = generate_plist(version, sha, updated)
                self.assertIsNone(obj)
                self.assertTrue(error)

    def test_native_about_includes_installed_version_date_and_author(self):
        for fragment in (
            'info["BridgeVersion"]', 'info["BridgeSourceCommit"]',
            'info["BridgeSourceUpdatedAt"]', "Last updated (installed source):",
            "Developed by Tomas Laurenzo", "https://laurenzo.net",
            "tomas@laurenzo.net", "#selector(openAboutWebsite(_:))",
            "#selector(openAboutEmail(_:))",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, GUI)
        self.assertNotIn("tomas.laurenzo@colorado.edu", GUI)

    def test_missing_build_date_is_explicit_not_synthetic(self):
        self.assertIn('return "Unknown (build metadata unavailable)"', GUI)
        self.assertNotIn("Date()", GUI.split("private func aboutUpdateDate()", 1)[1].split("@objc private func showAbout", 1)[0])


if __name__ == "__main__":
    unittest.main()
