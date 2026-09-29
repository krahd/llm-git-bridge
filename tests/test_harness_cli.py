from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class HarnessCLITests(unittest.TestCase):
    def test_module_entrypoint_exposes_cli(self):
        proc = subprocess.run(
            [sys.executable, "-m", "llm_git_bridge.harness.cli", "--help"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("llm-git-harness", proc.stdout)

    def test_pyproject_installs_harness_command(self):
        text = Path("pyproject.toml").read_text()
        self.assertIn("llm-git-harness = \"llm_git_bridge.harness.cli:main\"", text)


if __name__ == "__main__":
    unittest.main()
