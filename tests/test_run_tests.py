from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest


def _load_runner():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py"
    spec = importlib.util.spec_from_file_location("llmgb_run_tests", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()


class RunTestsTests(unittest.TestCase):
    def test_parallel_partition_isolates_timing_sensitive_tests(self):
        ids = [
            "test_core.CoreTests.test_alpha",
            "test_core.CoreTests.test_configured_command_timeout_kills_process_group",
            "test_app.AppTests.test_beta",
            "test_core.CoreTests.test_configured_command_cancellation_kills_process_group",
            "test_core.CoreTests.test_configured_command_timeout_kills_same_group_descendant",
        ]
        shards, serial = RUNNER._partition_test_ids(ids, 4)
        flattened = [test_id for shard in shards for test_id in shard]
        self.assertEqual(flattened, [ids[0], ids[2]])
        self.assertEqual(serial, [ids[1], ids[3], ids[4]])

    def test_single_worker_preserves_original_order_without_serial_tail(self):
        ids = [
            "test_core.CoreTests.test_configured_command_timeout_kills_process_group",
            "test_core.CoreTests.test_alpha",
        ]
        shards, serial = RUNNER._partition_test_ids(ids, 1)
        self.assertEqual(shards, [ids])
        self.assertEqual(serial, [])

    def test_partition_preserves_every_test_exactly_once(self):
        ids = [f"pkg.Case.test_{i}" for i in range(17)]
        shards, serial = RUNNER._partition_test_ids(ids, 4)
        combined = [test_id for shard in shards for test_id in shard] + serial
        self.assertCountEqual(combined, ids)
        self.assertEqual(len(combined), len(set(combined)))

    def test_shard_collection_does_not_wait_for_descendant_held_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            test_file = root / "test_descendant_stdout.py"
            test_file.write_text(
                "import subprocess\n"
                "import sys\n"
                "import unittest\n"
                "class DescendantStdoutTests(unittest.TestCase):\n"
                "    def test_returns_before_descendant_closes_stdout(self):\n"
                "        subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'])\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            )
            env = os.environ.copy()
            pythonpath = [str(root)]
            if env.get("PYTHONPATH"):
                pythonpath.append(env["PYTHONPATH"])
            env["PYTHONPATH"] = os.pathsep.join(pythonpath)

            started = time.monotonic()
            failed = RUNNER._run_shards(
                [["test_descendant_stdout.DescendantStdoutTests.test_returns_before_descendant_closes_stdout"]],
                root=root,
                env=env,
                duration_args=[],
                label="regression shard",
            )
            elapsed = time.monotonic() - started

            self.assertFalse(failed)
            self.assertLess(elapsed, 2.0)


if __name__ == "__main__":
    unittest.main()
