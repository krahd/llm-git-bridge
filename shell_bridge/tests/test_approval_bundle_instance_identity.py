import plistlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "install.sh").read_text(encoding="utf-8")


def generate_info(path: Path, label: str, approval_root: Path):
    fragment = INSTALLER.split("<<'PYAPPPLIST'\n", 1)[1].split("\nPYAPPPLIST", 1)[0]
    proc = subprocess.run(
        [sys.executable, "-c", fragment, str(path), str(approval_root),
         "/opt/bridge/approval_helper.py", sys.executable, label],
        text=True, capture_output=True, timeout=8,
    )
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return plistlib.loads(path.read_bytes())


class UniqueApprovalBundleIdentityTests(unittest.TestCase):
    def test_stage_and_production_have_independent_launch_services_identity(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            base = Path(temp)
            staging = generate_info(
                base / "stage.plist",
                "net.laurenzo.local-executor-bridge-v6-candidate-a1b2c3",
                base / "staging-approvals",
            )
            production = generate_info(
                base / "production.plist",
                "net.laurenzo.local-executor-bridge-v6-production-a1b2c3",
                base / "production-approvals",
            )
            self.assertNotEqual(staging["CFBundleIdentifier"],
                                production["CFBundleIdentifier"])
            self.assertTrue(staging["CFBundleIdentifier"].startswith(
                "net.laurenzo.local-executor-approval."))
            self.assertTrue(production["CFBundleIdentifier"].startswith(
                "net.laurenzo.local-executor-approval."))
            self.assertEqual(staging["ApprovalRoot"], str(base / "staging-approvals"))
            self.assertEqual(production["ApprovalRoot"], str(base / "production-approvals"))
            self.assertTrue(staging["LSUIElement"])
            self.assertTrue(production["LSUIElement"])

    def test_rebuild_of_same_agent_retains_bundle_identity(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp:
            root = Path(temp)
            first = generate_info(root / "one.plist", "v6-candidate-alpha", root / "a")
            second = generate_info(root / "two.plist", "v6-candidate-alpha", root / "b")
            self.assertEqual(first["CFBundleIdentifier"],
                             second["CFBundleIdentifier"])
            self.assertNotEqual(first["ApprovalRoot"], second["ApprovalRoot"])


if __name__ == "__main__":
    unittest.main()
