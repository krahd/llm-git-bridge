import pathlib
import unittest


class CutoverIdentityTests(unittest.TestCase):
    def test_cutover_targets_canonical_v6_identity_and_retires_old_services(self):
        text = (pathlib.Path(__file__).parents[1] / "cutover.sh").read_text()
        self.assertIn("net.laurenzo.local-executor-bridge", text)
        self.assertIn("LOCAL_EXECUTOR_BRIDGE_CUTOVER=1", text)
        self.assertIn("com.tom.chatgpt-shell-bridge", text)
        self.assertIn("io.llm-git-bridge.daemon", text)
        self.assertIn("net.laurenzo.mac-executor-bridge", text)
        self.assertNotIn("MAC_EXECUTOR_BRIDGE_LABEL", text)
        self.assertNotIn("MAC_EXECUTOR_BRIDGE_CUTOVER=1", text)


if __name__ == "__main__":
    unittest.main()
