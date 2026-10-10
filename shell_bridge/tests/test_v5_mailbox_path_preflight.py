import json
import pathlib
import re
import subprocess
import sys
import unittest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1] / "v6_release_once.sh").read_text()
MATCH = re.search(r"<<'PYV5PATH'\n(.*?)\nPYV5PATH", SCRIPT, re.DOTALL)
if MATCH is None:
    raise AssertionError("production mailbox path preflight missing")
PROGRAM = MATCH.group(1)


class V5MailboxPathPreflightTests(unittest.TestCase):
    def check(self, listing, base="ChatGPT Shell Bridge", root="pinned-root"):
        return subprocess.run(
            [sys.executable, "-c", PROGRAM, json.dumps(listing), base, root],
            text=True, capture_output=True, check=False,
        )

    def test_known_existing_mailbox_is_accepted(self):
        self.assertEqual(self.check([
            {"Name": "ChatGPT Shell Bridge", "IsDir": True, "ID": "pinned-root"},
            {"Name": "other", "IsDir": True, "ID": "other"},
        ]).returncode, 0)

    def test_wrong_id_missing_or_duplicate_mailbox_blocks_before_mutation(self):
        for listing in (
            [],
            [{"Name": "ChatGPT Shell Bridge", "IsDir": True, "ID": "not-pinned"}],
            [{"Name": "ChatGPT Shell Bridge", "IsDir": True, "ID": "pinned-root"},
             {"Name": "ChatGPT Shell Bridge", "IsDir": True, "ID": "pinned-root"}],
        ):
            with self.subTest(listing=listing):
                self.assertNotEqual(self.check(listing).returncode, 0)

    def test_gate_precedes_production_installation(self):
        self.assertLess(SCRIPT.index("PYV5PATH"),
                        SCRIPT.index('PROD_LABEL="net.laurenzo.local-executor-bridge-v6-production'))


if __name__ == "__main__":
    unittest.main()
