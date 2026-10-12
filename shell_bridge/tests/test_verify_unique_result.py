import json
import subprocess
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_unique_result import verify

SCRIPT = Path(__file__).resolve().parents[1] / "verify_unique_result.py"
RID = "v6-check-001"


def entry(file_id="id-abc", name=RID + ".json"):
    return {"Name": name, "ID": file_id, "IsDir": False}


class UniqueReleaseResultTests(unittest.TestCase):
    def test_one_exact_file_with_drive_id_accepted(self):
        self.assertEqual(verify(RID, [entry()]), "id-abc")

    def test_live_duplicate_filename_rejected_even_if_different_drive_ids(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            verify(RID, [entry("file-one"), entry("file-two")])

    def test_missing_file_rejected(self):
        with self.assertRaisesRegex(ValueError, "exactly one"):
            verify(RID, [])

    def test_other_name_extra_rows_or_missing_id_rejected(self):
        for rows in (
            [entry(name="other.json")],
            [entry(), entry(name="other.json")],
            [{"Name": RID+".json", "IsDir": False}],
            [{"Name": RID+".json", "ID": "", "IsDir": False}],
            [{"Name": RID+".json", "ID": "ok", "IsDir": True}],
            [entry()] * 51,
            "malformed", [{"Other": "field"}],
        ):
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    verify(RID, rows)

    def test_cli_rejects_duplicate_and_requires_exact_id(self):
        cp = subprocess.run([sys.executable, str(SCRIPT), RID],
                            input=json.dumps([entry("one"),entry("two")]),
                            text=True, capture_output=True, timeout=5)
        self.assertEqual(cp.returncode, 1)
        self.assertIn("DRIVE_RESULT_CARDINALITY_HOLD", cp.stderr)
        self.assertNotIn("UNIQUE_DRIVE_RESULT_ID", cp.stdout)

    def test_invalid_request_id_rejected(self):
        for rid in ("../unsafe", "", "contains space", "a" * 129):
            with self.assertRaises(ValueError):
                verify(rid, [entry(name=rid+".json")])

    def test_release_canaries_check_unique_result_before_acceptance(self):
        source = (SCRIPT.parent / "v6_release_once.sh").read_text()
        self.assertIn("unique_result_canary", source)
        self.assertEqual(source.count('unique_result_canary "$root" "$rid"'), 2)
        self.assertIn('verify_unique_result.py "$rid"', source)
        cutover = (SCRIPT.parent / "cutover.sh").read_text()
        self.assertIn("production smoke result is missing or duplicated", cutover)
        self.assertIn('verify_unique_result.py" "$RID"', cutover)
        self.assertLess(cutover.index("production smoke result is missing or duplicated"),
                        cutover.index("CUTOVER_COMMITTED=1"))


if __name__ == "__main__":
    unittest.main()
