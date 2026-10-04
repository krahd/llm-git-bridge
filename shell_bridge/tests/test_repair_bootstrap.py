from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
class RepairBootstrapTests(unittest.TestCase):
  def test_repair_is_narrow(self):
    s=(ROOT/'repair_staging.sh').read_text()
    self.assertIn('net.laurenzo.local-executor-bridge-v6-staging',s)
    self.assertIn('15ql2yACOq7H6qo0IgzosySqW8nHgUKXv',s)
    self.assertNotIn('com.tom.chatgpt-shell-bridge',s)
    self.assertNotIn('io.llm-git-bridge.daemon',s)
    self.assertIn('EXPECTED_SOURCE_COMMIT',s)
  def test_app_has_no_generic_command_input(self):
    s=(ROOT/'bridge_repair.swift').read_text()
    self.assertIn('deviceOwnerAuthentication',s)
    self.assertIn('repair_staging.sh',s)
    self.assertNotIn('CommandLine.arguments',s)
if __name__=='__main__': unittest.main()
