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
