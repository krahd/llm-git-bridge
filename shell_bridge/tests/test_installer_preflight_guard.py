import json
from pathlib import Path
import subprocess
import sys
import unittest


INSTALL = (Path(__file__).resolve().parents[1] / "install.sh").read_text()
BEGIN = INSTALL.index("<<'PYOWNER'") + len("<<'PYOWNER'") + 1
END = INSTALL.index("PYOWNER", BEGIN)
VERIFY_OWNER = INSTALL[BEGIN:END]


class InstallPreflightGuardTests(unittest.TestCase):
    def run_guard(self, report, root="root123"):
        return subprocess.run(
            [sys.executable, "-c", VERIFY_OWNER, json.dumps(report), root],
            capture_output=True, text=True, timeout=6)

    @staticmethod
    def report(loaded=None, problems=None, safe=True):
        return {
            "schema": 1, "kind": "loaded_bridge_ownership",
            "loaded": [] if loaded is None else loaded,
            "problems": [] if problems is None else problems,
            "safe_to_stage": safe,
            "mutations_performed": False,
        }

    def test_new_mailbox_without_loaded_owner_is_allowed(self):
        self.assertEqual(self.run_guard(self.report()).returncode, 0)

    def test_even_single_running_v6_owner_requires_immutable_stage(self):
        owner = {"label": "net.laurenzo.local-executor-bridge",
                 "pid": "1786", "drive_root_folder_id": "root123"}
        result = self.run_guard(self.report([owner]))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stage-only and guarded cutover", result.stderr)

    def test_parked_keepalive_job_is_not_ignored(self):
        owner = {"label": "io.llm-git-bridge.daemon",
                 "pid": None, "drive_root_folder_id": "root123"}
        self.assertNotEqual(self.run_guard(self.report([owner])).returncode, 0)

    def test_unrelated_loaded_mailbox_is_not_target_collision(self):
        owner = {"label": "bridge-other", "pid": "2000",
                 "drive_root_folder_id": "unrelated"}
        self.assertEqual(self.run_guard(self.report([owner])).returncode, 0)

    def test_malformed_unverified_or_mutating_inventory_fails_closed(self):
        cases = (
            {"kind": "wrong", "loaded": [], "problems": [],
             "safe_to_stage": True, "mutations_performed": False},
            self.report(problems=["unknown bridge"], safe=False),
            {**self.report(), "mutations_performed": True},
            {**self.report(), "safe_to_stage": None},
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertNotEqual(self.run_guard(case).returncode, 0)

    def test_installer_checks_target_immutability_before_any_remote_mutation(self):
        self.assertLess(INSTALL.index("for protected_target in"),
                        INSTALL.index("choose_allowed_root\nchoose_remote"))
        self.assertLess(INSTALL.index("loaded bridge ownership is ambiguous; stage an isolated build"),
                        INSTALL.index("ensure_mailbox\n"))
        self.assertLess(INSTALL.index("verify_unclaimed_target_mailbox ||"),
                        INSTALL.index('cp "$SCRIPT_DIR/bridge.py"'))

    def test_fresh_owner_inventory_rechecked_before_activation(self):
        self.assertGreaterEqual(INSTALL.count("verify_unclaimed_target_mailbox"), 3)
        self.assertLess(
            INSTALL.rindex("verify_unclaimed_target_mailbox"),
            INSTALL.rindex('launchctl bootout "gui/${UID_NOW}" "$PLIST"'),
        )


if __name__ == "__main__":
    unittest.main()
