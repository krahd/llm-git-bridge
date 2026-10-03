import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("workspace_v6", ROOT / "workspace.py")
workspace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workspace)

class WorkspaceStateTests(unittest.TestCase):
    def test_local_executor_state_env_has_priority(self):
        with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
            with mock.patch.dict(os.environ, {
                "LOCAL_EXECUTOR_BRIDGE_STATE_DIR": td1,
                "CHATGPT_SHELL_BRIDGE_STATE_DIR": td2,
            }, clear=False):
                self.assertEqual(workspace.state_root(), Path(td1).resolve())

    def test_legacy_state_env_remains_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"CHATGPT_SHELL_BRIDGE_STATE_DIR": td}, clear=False):
                os.environ.pop("LOCAL_EXECUTOR_BRIDGE_STATE_DIR", None)
                self.assertEqual(workspace.state_root(), Path(td).resolve())

if __name__ == "__main__":
    unittest.main()
