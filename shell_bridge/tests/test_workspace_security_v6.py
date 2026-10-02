import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workspace_v6", ROOT / "workspace.py")
workspace = importlib.util.module_from_spec(spec); spec.loader.exec_module(workspace)

class WorkspaceV6SecurityTests(unittest.TestCase):
    def test_profile_denies_network_and_other_home_reads(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            # Not necessarily a Git repo; profile must still confine to cwd + temp.
            profile = workspace._workspace_sandbox_profile(p, p)
            self.assertIn("(deny network*)", profile)
            self.assertIn("(deny file-read*", profile)
            self.assertIn(str(Path.home().resolve()), profile)

if __name__ == "__main__": unittest.main()
