import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from verify_upgrade_stage_source import StageSourceError, verify


class UpgradeStageSourceTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.home = pathlib.Path(t.name) / "home"
        self.home.mkdir()
        self.config_path = self.home / ".config" / "local-executor-bridge" / "config.json"
        self.config_path.parent.mkdir(parents=True)
        self.state = self.home / ".local" / "state" / "chatgpt-shell-bridge"
        self.state.mkdir(parents=True)
        self.allowed = self.home / "tom-repos"
        self.allowed.mkdir()
        self.source = {
            "remote": "chatgpt-git-bridge:",
            "base_path": "ChatGPT Shell Bridge",
            "drive_root_folder_id": "1PRsQHgVsgXhIYT_8akGFRGdhdlw9Lmk6",
            "requests_folder_id": "requests-id",
            "results_folder_id": "results-id",
            "bridge_instance_id": "pinned-instance-id",
            "state_dir": str(self.state),
            "allowed_root": str(self.allowed),
            "shell": "/bin/zsh",
            "oauth_token": "NEVER_EXPOSE_SECRET",
        }
        self.config_path.write_text(json.dumps(self.source))
        self.expected = {k: v for k, v in self.source.items()
                         if k != "oauth_token"}

    def test_existing_identity_is_accepted_without_mutation(self):
        before = self.config_path.read_bytes()
        verify(self.config_path, self.expected, home=self.home)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_different_production_identity_is_rejected(self):
        for key in self.expected:
            with self.subTest(key=key):
                changed = dict(self.expected, **{key: "different"})
                with self.assertRaises(StageSourceError):
                    verify(self.config_path, changed, home=self.home)

    def test_remote_alias_with_missing_colon_is_accepted(self):
        expected = dict(self.expected, remote="chatgpt-git-bridge")
        verify(self.config_path, expected, home=self.home)

    def test_linked_source_file_or_ancestor_rejected(self):
        alias = self.config_path.parent / "other.json"
        alias.write_bytes(self.config_path.read_bytes())
        self.config_path.unlink()
        self.config_path.symlink_to(alias)
        with self.assertRaisesRegex(StageSourceError, "trusted"):
            verify(self.config_path, self.expected, home=self.home)

    def test_foreign_or_writable_state_directory_is_rejected(self):
        self.state.chmod(0o777)
        with self.assertRaisesRegex(StageSourceError, "private"):
            verify(self.config_path, self.expected, home=self.home)

    def test_state_symlink_outside_managed_directory_rejected(self):
        outside = self.home / "elsewhere"
        outside.mkdir()
        self.state.rmdir()
        self.state.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StageSourceError):
            verify(self.config_path, self.expected, home=self.home)

    def test_source_config_must_be_private_under_home_config(self):
        outside = self.home / "another.json"
        outside.write_text(json.dumps(self.source))
        with self.assertRaises(StageSourceError):
            verify(outside, self.expected, home=self.home)

    def test_incomplete_or_malformed_source_fails_without_dumping_secret(self):
        old = dict(self.source)
        old.pop("bridge_instance_id")
        self.config_path.write_text(json.dumps(old))
        with self.assertRaises(StageSourceError) as exc:
            verify(self.config_path, self.expected, home=self.home)
        self.assertNotIn("NEVER_EXPOSE_SECRET", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
