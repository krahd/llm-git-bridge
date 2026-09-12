from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "google_drive_oauth.py"
spec = importlib.util.spec_from_file_location("google_drive_oauth", SCRIPT)
assert spec and spec.loader
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class OAuthHelperTests(unittest.TestCase):
    def make_credentials(self, root: Path, *, name: str = "client_secret_test.json") -> Path:
        path = root / name
        path.write_text(
            json.dumps(
                {
                    "installed": {
                        "client_id": "1234567890-example.apps.googleusercontent.com",
                        "project_id": "example",
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "client_secret": "secret-value",
                        "redirect_uris": ["http://localhost"],
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_validate_desktop_credentials_accepts_installed_client(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            path = self.make_credentials(Path(tmp))
            client_id, secret = helper.validate_desktop_credentials(path)
            self.assertTrue(client_id.endswith(".apps.googleusercontent.com"))
            self.assertEqual(secret, "secret-value")

    def test_validate_desktop_credentials_rejects_web_client(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            path = Path(tmp) / "client_secret_web.json"
            path.write_text(json.dumps({"web": {"client_id": "x", "client_secret": "y"}}), encoding="utf-8")
            with self.assertRaises(helper.SetupError):
                helper.validate_desktop_credentials(path)

    def test_validate_desktop_credentials_rejects_config_injection_secret(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            path = self.make_credentials(Path(tmp))
            data = json.loads(path.read_text(encoding="utf-8"))
            data["installed"]["client_secret"] = "secret\nroot_folder_id = attacker"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(helper.SetupError):
                helper.validate_desktop_credentials(path)

    def test_json_loader_rejects_unreasonably_large_credential_file(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            path = Path(tmp) / "client_secret_huge.json"
            path.write_text("x" * 100, encoding="utf-8")
            with patch.object(helper, "MAX_JSON_BYTES", 32):
                with self.assertRaises(helper.SetupError):
                    helper._load_json_object(path)

    def test_find_credentials_chooses_newest_valid_recent_file(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            root = Path(tmp)
            old = self.make_credentials(root, name="client_secret_old.json")
            new = self.make_credentials(root, name="client_secret_new.json")
            now = time.time()
            os.utime(old, (now - 60, now - 60))
            os.utime(new, (now, now))
            self.assertEqual(helper.find_credentials(root), new)

    def test_find_credentials_accepts_standard_credentials_json_name(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            root = Path(tmp)
            path = self.make_credentials(root, name="credentials.json")
            self.assertEqual(helper.find_credentials(root), path)

    def test_find_credentials_can_require_prepared_project(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            root = Path(tmp)
            wrong = self.make_credentials(root, name="client_secret_wrong.json")
            data = json.loads(wrong.read_text())
            data["installed"]["project_id"] = "wrong-project"
            wrong.write_text(json.dumps(data), encoding="utf-8")
            right = self.make_credentials(root, name="client_secret_right.json")
            data = json.loads(right.read_text())
            data["installed"]["project_id"] = "right-project"
            right.write_text(json.dumps(data), encoding="utf-8")
            os.utime(wrong, (time.time() + 1, time.time() + 1))
            self.assertEqual(helper.find_credentials(root, expected_project_id="right-project"), right)

    def test_prepared_project_state_round_trips_privately(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            old = helper.OAUTH_STATE
            helper.OAUTH_STATE = Path(tmp) / "state" / "oauth-setup.json"
            try:
                helper._save_prepared_project("right-project")
                self.assertEqual(helper._prepared_project_id(), "right-project")
                self.assertEqual(helper.OAUTH_STATE.stat().st_mode & 0o777, 0o600)
            finally:
                helper.OAUTH_STATE = old

    def test_update_rclone_config_preserves_non_oauth_settings(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            path = Path(tmp) / "rclone.conf"
            path.write_text(
                "[chatgpt-git-bridge]\n"
                "type = drive\n"
                "scope = drive\n"
                "root_folder_id = ROOT123\n"
                "token = {\\\"access_token\\\":\\\"old\\\"}\n"
                "# keep this comment exactly\n"
                "\n[other]\n"
                "type = drive\n"
                "root_folder_id = OTHER\n",
                encoding="utf-8",
            )
            original, before = helper.update_plaintext_rclone_config(
                path,
                "chatgpt-git-bridge",
                "1234567890-example.apps.googleusercontent.com",
                "new-secret",
            )
            self.assertIn(b"ROOT123", original)
            parser = helper._parse_rclone_config_text(path.read_text(encoding="utf-8"))
            section = dict(parser.items("chatgpt-git-bridge"))
            self.assertEqual(section["client_id"], "1234567890-example.apps.googleusercontent.com")
            self.assertEqual(section["client_secret"], "new-secret")
            self.assertEqual(section["root_folder_id"], "ROOT123")
            self.assertNotIn("token", section)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(dict(parser.items("other"))["root_folder_id"], "OTHER")
            updated_text = path.read_text(encoding="utf-8")
            self.assertIn("# keep this comment exactly\n", updated_text)
            self.assertEqual(
                updated_text[updated_text.index("[other]"):],
                "[other]\ntype = drive\nroot_folder_id = OTHER\n",
            )
            self.assertEqual(before["root_folder_id"], "ROOT123")
            self.assertIn("access_token", before["token"])

    def test_verify_rclone_config_rejects_changed_root(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            path = Path(tmp) / "rclone.conf"
            before = {
                "type": "drive",
                "scope": "drive",
                "root_folder_id": "ROOT123",
                "token": "old",
            }
            path.write_text(
                "[chatgpt-git-bridge]\n"
                "type=drive\n"
                "scope=drive\n"
                "root_folder_id=CHANGED\n"
                "client_id=1234567890-example.apps.googleusercontent.com\n"
                "client_secret=secret\n"
                "token=new\n",
                encoding="utf-8",
            )
            with self.assertRaises(helper.SetupError):
                helper.verify_rclone_config(
                    path,
                    "chatgpt-git-bridge",
                    "1234567890-example.apps.googleusercontent.com",
                    "secret",
                    before,
                )

    def test_finish_keeps_client_secret_out_of_subprocess_argv(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            root = Path(tmp)
            cred = self.make_credentials(root)
            config = root / "rclone.conf"
            config.write_text(
                "[chatgpt-git-bridge]\n"
                "type=drive\n"
                "scope=drive\n"
                "root_folder_id=ROOT123\n"
                "token=old-token\n"
                "# bridge comment\n"
                "\n"
                "[GoogleDriveTom]\n"
                "type=drive\n"
                "root_folder_id=PERSONAL\n"
                "# unrelated bytes must survive exactly\n",
                encoding="utf-8",
            )
            unrelated = config.read_text(encoding="utf-8").split("[GoogleDriveTom]", 1)[1]
            seen: list[list[str]] = []

            def fake_run(argv, **_kwargs):
                seen.append(list(argv))
                self.assertNotIn("secret-value", argv)
                if "reconnect" in argv:
                    temp_config = Path(argv[argv.index("--config") + 1])
                    parser = helper._parse_rclone_config_text(temp_config.read_text(encoding="utf-8"))
                    self.assertEqual(parser.get("chatgpt-git-bridge", "client_secret"), "secret-value")
                    parser.set("chatgpt-git-bridge", "token", json.dumps({"access_token": "new-token", "refresh_token": "refresh"}))
                    with temp_config.open("w", encoding="utf-8") as fh:
                        parser.write(fh, space_around_delimiters=False)
                return __import__("subprocess").CompletedProcess(argv, 0)

            nonexistent_plist = root / "daemon.plist"
            with patch.object(helper.shutil, "which", return_value="/opt/homebrew/bin/rclone"), \
                 patch.object(helper, "PLIST_PATH", nonexistent_plist), \
                 patch.object(helper, "bridge_remote", return_value="chatgpt-git-bridge"), \
                 patch.object(helper, "rclone_config_path", return_value=config), \
                 patch.object(helper, "_assert_watcher_stopped"), \
                 patch.object(helper, "validate_mailbox"), \
                 patch.object(helper, "_run", side_effect=fake_run):
                self.assertEqual(helper.finish(cred, keep_credentials=True), 0)
            self.assertTrue(any("reconnect" in argv for argv in seen))
            parser = helper._parse_rclone_config_text(config.read_text(encoding="utf-8"))
            self.assertEqual(parser.get("chatgpt-git-bridge", "client_id"), "1234567890-example.apps.googleusercontent.com")
            self.assertEqual(parser.get("chatgpt-git-bridge", "client_secret"), "secret-value")
            self.assertIn("new-token", parser.get("chatgpt-git-bridge", "token"))
            self.assertEqual(config.read_text(encoding="utf-8").split("[GoogleDriveTom]", 1)[1], unrelated)

    def test_temporary_reconnect_config_contains_only_bridge_remote(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            root = Path(tmp)
            config = root / "rclone.conf"
            config.write_text(
                "[chatgpt-git-bridge]\n"
                "type=drive\n"
                "scope=drive\n"
                "root_folder_id=ROOT123\n"
                "token=old-token\n"
                "\n[GoogleDriveTom]\n"
                "type=drive\n"
                "token=personal-secret-token\n",
                encoding="utf-8",
            )
            temp_path, before = helper.create_temporary_remote_config(
                config,
                "chatgpt-git-bridge",
                "1234567890-example.apps.googleusercontent.com",
                "secret-value",
            )
            try:
                parser = helper._parse_rclone_config_text(temp_path.read_text(encoding="utf-8"))
                self.assertEqual(parser.sections(), ["chatgpt-git-bridge"])
                self.assertFalse(parser.has_option("chatgpt-git-bridge", "token"))
                self.assertEqual(parser.get("chatgpt-git-bridge", "root_folder_id"), "ROOT123")
                self.assertEqual(temp_path.stat().st_mode & 0o777, 0o600)
                self.assertNotIn("personal-secret-token", temp_path.read_text(encoding="utf-8"))
                self.assertEqual(before["root_folder_id"], "ROOT123")
            finally:
                __import__("shutil").rmtree(temp_path.parent, ignore_errors=True)

    def test_oauth_config_lock_rejects_concurrent_migration(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            config = Path(tmp) / "rclone.conf"
            config.write_text("[x]\ntype=drive\n", encoding="utf-8")
            first = helper._acquire_config_lock(config)
            try:
                with self.assertRaisesRegex(helper.SetupError, "another OAuth migration"):
                    helper._acquire_config_lock(config)
            finally:
                helper._release_config_lock(first)

    def test_oauth_temp_directory_is_private(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            config = Path(tmp) / "rclone.conf"
            config.write_text(
                "[chatgpt-git-bridge]\ntype=drive\nscope=drive\nroot_folder_id=ROOT123\ntoken=old\n",
                encoding="utf-8",
            )
            temp_path, _before = helper.create_temporary_remote_config(
                config, "chatgpt-git-bridge",
                "1234567890-example.apps.googleusercontent.com", "secret-value"
            )
            try:
                self.assertEqual(temp_path.parent.stat().st_mode & 0o777, 0o700)
                self.assertEqual(temp_path.stat().st_mode & 0o777, 0o600)
            finally:
                __import__("shutil").rmtree(temp_path.parent, ignore_errors=True)

    def test_oauth_token_must_be_valid_json_with_access_token(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            path = Path(tmp) / "rclone.conf"
            path.write_text(
                "[chatgpt-git-bridge]\ntype=drive\ntoken=not-json\n",
                encoding="utf-8",
            )
            with self.assertRaises(helper.SetupError):
                helper.oauth_token_from_config(path, "chatgpt-git-bridge")

    def test_finish_rolls_back_exact_config_if_reconnect_fails(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            root = Path(tmp)
            cred = self.make_credentials(root)
            config = root / "rclone.conf"
            original = (
                "[chatgpt-git-bridge]\n"
                "type = drive\n"
                "scope = drive\n"
                "root_folder_id = ROOT123\n"
                "token = old-token\n"
            ).encode()
            config.write_bytes(original)
            with patch.object(helper.shutil, "which", return_value="/opt/homebrew/bin/rclone"), \
                 patch.object(helper, "PLIST_PATH", root / "daemon.plist"), \
                 patch.object(helper, "bridge_remote", return_value="chatgpt-git-bridge"), \
                 patch.object(helper, "rclone_config_path", return_value=config), \
                 patch.object(helper, "_assert_watcher_stopped"), \
                 patch.object(helper, "_run", side_effect=helper.SetupError("reconnect failed")):
                with self.assertRaises(helper.SetupError):
                    helper.finish(cred, keep_credentials=True)
            self.assertEqual(config.read_bytes(), original)

    def test_finish_restores_config_on_keyboard_interrupt(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            root = Path(tmp)
            cred = self.make_credentials(root)
            config = root / "rclone.conf"
            original = (
                "[chatgpt-git-bridge]\n"
                "type=drive\n"
                "scope=drive\n"
                "root_folder_id=ROOT123\n"
                "token=old-token\n"
            ).encode()
            config.write_bytes(original)
            with patch.object(helper.shutil, "which", return_value="/opt/homebrew/bin/rclone"), \
                 patch.object(helper, "PLIST_PATH", root / "daemon.plist"), \
                 patch.object(helper, "bridge_remote", return_value="chatgpt-git-bridge"), \
                 patch.object(helper, "rclone_config_path", return_value=config), \
                 patch.object(helper, "_assert_watcher_stopped"), \
                 patch.object(helper, "_run", side_effect=KeyboardInterrupt):
                with self.assertRaises(helper.SetupError):
                    helper.finish(cred, keep_credentials=True)
            self.assertEqual(config.read_bytes(), original)

    def test_encrypted_rclone_config_is_not_modified(self):
        with self.assertRaises(helper.SetupError):
            helper._parse_rclone_config_text("RCLONE_ENCRYPT_V0:\\nabc")

    def test_bridge_remote_uses_only_configured_bridge_remote(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-oauth-") as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"transport": {"type": "rclone", "remote": "chatgpt-git-bridge"}}),
                encoding="utf-8",
            )
            self.assertEqual(helper.bridge_remote(path), "chatgpt-git-bridge")


if __name__ == "__main__":
    unittest.main()
