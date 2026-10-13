"""Exercise the real embedded source-owner classifier without any launchctl calls."""
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

CUTOVER = Path(__file__).resolve().parents[1] / "cutover.sh"


class DynamicSourceOwnerHandoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cutover = CUTOVER.read_text()
        match = re.search(
            r'KNOWN_CONSUMERS="\$\(python3 - "\$OWNERSHIP" "\$ROOT_ID" "\$NEW_LABEL" "\$CUTOVER_SOURCE_VERSION" <<\x27PYOWNERS\x27\n(.*?)\nPYOWNERS',
            cls.cutover, flags=re.S,
        )
        if not match:
            raise AssertionError("source owner classifier was not found")
        cls.classifier = match.group(1)

    def classify(self, source_version, owners, *, problems=None, safe=True):
        inventory = {
            "kind": "loaded_bridge_ownership",
            "safe_to_stage": safe, "problems": problems or [],
            "loaded": [
                {"label": label, "drive_root_folder_id": "PRODUCTION"}
                for label in owners
            ],
        }
        return subprocess.run(
            [sys.executable, "-c", self.classifier,
             json.dumps(inventory), "PRODUCTION",
             "net.laurenzo.local-executor-bridge-v6-next",
             source_version],
            capture_output=True, text=True, timeout=5,
        )

    def test_v5_label_remains_accepted_for_v5_first(self):
        r = self.classify("5", ["io.llm-git-bridge.daemon"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "io.llm-git-bridge.daemon")

    def test_dynamic_v6_source_allowed_only_for_v6_upgrader(self):
        label = "net.laurenzo.local-executor-bridge-v6-a1b2c3d4e5f6"
        good = self.classify("6", [label])
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(good.stdout.strip(), label)
        bad = self.classify("5", [label])
        self.assertNotEqual(bad.returncode, 0)

    def test_old_static_v6_source_still_supported(self):
        r = self.classify("6", ["net.laurenzo.local-executor-bridge"])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_two_v6_claimants_are_not_promoted(self):
        r = self.classify("6", [
            "net.laurenzo.local-executor-bridge-v6-staging",
            "net.laurenzo.local-executor-bridge-v6-manual-bootstrap",
        ])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exactly one", r.stderr)

    def test_unrelated_or_target_label_is_not_promoted(self):
        for label in (
            "com.example.unknown-agent",
            "net.laurenzo.local-executor-bridge-v6-next",
            "net.laurenzo.local-executor-bridge-v6-",
            "net.laurenzo.local-executor-bridge-v6-unsafe/label",
        ):
            with self.subTest(label=label):
                self.assertNotEqual(self.classify("6", [label]).returncode, 0)

    def test_unsafe_owner_inventory_stays_a_hard_hold(self):
        label = "net.laurenzo.local-executor-bridge-v6-old"
        self.assertNotEqual(self.classify("6", [label], safe=False).returncode, 0)
        self.assertNotEqual(self.classify("6", [label], problems=["shared production"]).returncode, 0)

    def test_classification_does_not_switch_services(self):
        s = self.cutover
        self.assertLess(s.index("KNOWN_CONSUMERS="), s.index("# Stop old consumers first."))
        self.assertIn('source_version==\x276\x27', self.classifier)
        syntax = subprocess.run(["bash", "-n", str(CUTOVER)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
    unittest.main()
