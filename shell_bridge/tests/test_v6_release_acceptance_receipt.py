import json
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "v6_release_once.sh").read_text()
MATCH = re.search(r"<<'PYACCEPT'\n(.*?)\nPYACCEPT", SOURCE, re.DOTALL)
if MATCH is None:
    raise AssertionError("release acceptance writer missing")
PROGRAM = MATCH.group(1)


class ReleaseReceiptTests(unittest.TestCase):
    def test_production_receipt_is_valid_and_records_unverified_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "receipt.json"
            result = subprocess.run(
                [sys.executable, "-c", PROGRAM, str(path), "a" * 40,
                 "net.laurenzo.local-executor-bridge-v6-production", "/archive"],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["source_commit"], "a" * 40)
            self.assertTrue(data["legacy_state_preserved"])
            self.assertFalse(data["recovery_fault_injection_live_verified"])


if __name__ == "__main__":
    unittest.main()
