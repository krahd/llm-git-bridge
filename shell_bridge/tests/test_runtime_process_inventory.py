import pathlib
import unittest


class RuntimeProcessInventoryTests(unittest.TestCase):
    def test_inventory_is_bounded_and_read_only(self):
        text = (pathlib.Path(__file__).parents[1] / "audit_runtime_processes.sh").read_text()
        self.assertIn('launchctl print', text)
        self.assertIn('launchctl list', text)
        self.assertIn('ps -axo pid=,ppid=,comm=', text)
        self.assertIn('inventory_only_no_mutation=1', text)
        for prohibited in ('launchctl bootout', 'launchctl bootstrap', 'launchctl kickstart',
                           'kill -', 'rm -', 'git reset', 'rclone delete'):
            self.assertNotIn(prohibited, text)
