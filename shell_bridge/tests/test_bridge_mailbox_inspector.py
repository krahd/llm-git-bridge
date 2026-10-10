import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from bridge_mailbox_inspector import FIELDS, LABELS, inspect


class BridgeMailboxInspectorTests(unittest.TestCase):
    def test_redacts_secrets_and_detects_collisions(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            for i, relative in enumerate(LABELS.values()):
                path = home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({
                    "drive_root_folder_id": "same" if i < 2 else "other-" + str(i),
                    "state_dir": "/state/" + str(i),
                    "bridge_instance_id": "instance-" + str(i),
                    "results_folder_id": "results-" + str(i),
                    "remote": "sensitive:server",
                    "oauth_token": "SECRET-DONT-PRINT",
                    "shell": "/bin/zsh",
                    "requests_folder_id": "requests-" + str(i),
                }))
            answer = inspect(home, launchctl=lambda label: "loaded")
            encoded = json.dumps(answer)
            self.assertNotIn("SECRET-DONT-PRINT", encoded)
            self.assertNotIn("sensitive:server", encoded)
            self.assertEqual(len(answer["shared_drive_roots"]["same"]), 2)
            self.assertTrue(all(x["config_status"] == "readable" for x in answer["records"]))
            self.assertTrue(all(x["service_state"] == "loaded" for x in answer["records"]))
            self.assertFalse(answer["mutations_performed"])

    def test_missing_and_symlinked_config_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            first = next(iter(LABELS.values()))
            target = home / "target"
            target.write_text('{"drive_root_folder_id":"should-not-be-read"}')
            link = home / first
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
            answer = inspect(home, launchctl=lambda label: "unavailable")
            self.assertEqual(answer["records"][0]["config_status"], "unreadable_or_invalid")
            self.assertNotIn("should-not-be-read", json.dumps(answer))
            self.assertEqual(answer["shared_drive_roots"], {})

    def test_only_allowlisted_fields_and_fixed_inventory(self):
        self.assertNotIn("remote", FIELDS)
        self.assertNotIn("token", FIELDS)
        self.assertEqual(len(LABELS), 4)

    def test_symlinked_config_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "home"
            other = pathlib.Path(td) / "other"
            other.mkdir()
            (other / "config.json").write_text('{"drive_root_folder_id":"hidden"}')
            link_dir = home / ".config" / "chatgpt-shell-bridge"
            link_dir.parent.mkdir(parents=True)
            link_dir.symlink_to(other, target_is_directory=True)
            answer = inspect(home, launchctl=lambda label: "loaded")
            self.assertEqual(answer["records"][0]["config_status"], "unreadable_or_invalid")
            self.assertNotIn("hidden", json.dumps(answer))

    def test_control_characters_not_exposed(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            first = home / next(iter(LABELS.values()))
            first.parent.mkdir(parents=True)
            first.write_text(json.dumps({"drive_root_folder_id": "secret\ncredential"}))
            answer = inspect(home, launchctl=lambda label: "loaded")
            self.assertNotIn("secret", json.dumps(answer))

    def test_realistic_drive_ids_are_preserved_without_dropping_digits(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            for i, relative in enumerate(LABELS.values()):
                path = home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({
                    "bridge_instance_id": f"bridge-0x7f-{i}",
                    "drive_root_folder_id": "15ql2yACOq7H6qo0IgzosySqW8nHgUKXv"
                                            if i < 2 else f"other-{i}",
                    "requests_folder_id": f"requests-2026-{i}",
                    "results_folder_id": f"results-001-{i}",
                    "state_dir": f"/tmp/state-1f7-{i}"
                }))
            result = inspect(home, launchctl=lambda _: "loaded")
            self.assertTrue(all(x['config_status'] == 'readable'
                                for x in result['records']))
            self.assertEqual(len(result['shared_drive_roots']
                                 ['15ql2yACOq7H6qo0IgzosySqW8nHgUKXv']), 2)

    def test_missing_or_control_character_identity_blocks_success(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td)
            name = next(iter(LABELS.values()))
            path = home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "bridge_instance_id": "bridge",
                "drive_root_folder_id": "root\u000auntrusted",
                "requests_folder_id": "requests",
                "state_dir": "/tmp/state",
                # results_folder_id deliberately missing
            }))
            result = inspect(home, launchctl=lambda _: "loaded")
            self.assertEqual(result['records'][0]['config_status'],
                             'incomplete_or_invalid')
            self.assertNotIn('untrusted', json.dumps(result))
