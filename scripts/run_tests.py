#!/usr/bin/env python3
"""Run the unittest suite in a small bounded set of isolated processes.

The bridge's tests are intentionally Git-heavy and each test uses private temporary
repositories/state.  On the production Mac, process-launch latency dominates the
suite.  Running independent test IDs in four isolated Python workers overlaps that
host latency without changing bridge runtime concurrency or sharing mutable test
state between workers.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

DEFAULT_JOBS = 4
MAX_JOBS = 8


def _flatten(suite: unittest.TestSuite) -> list[str]:
    test_ids: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            test_ids.extend(_flatten(item))
        else:
            test_ids.append(item.id())
    return test_ids


def _jobs_from_env() -> int:
    raw = os.environ.get("LLM_GIT_BRIDGE_TEST_JOBS", str(DEFAULT_JOBS))
    try:
        jobs = int(raw)
    except ValueError:
        raise SystemExit("LLM_GIT_BRIDGE_TEST_JOBS must be an integer")
    if not 1 <= jobs <= MAX_JOBS:
        raise SystemExit(f"LLM_GIT_BRIDGE_TEST_JOBS must be between 1 and {MAX_JOBS}")
    return jobs


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    tests_dir = root / "tests"
    os.chdir(root)
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(tests_dir))

    suite = unittest.defaultTestLoader.discover(str(tests_dir), top_level_dir=str(tests_dir))
    test_ids = _flatten(suite)
    if not test_ids:
        print("no tests discovered", file=sys.stderr)
        return 1

    jobs = min(_jobs_from_env(), len(test_ids))
    shards = [[] for _ in range(jobs)]
    for index, test_id in enumerate(test_ids):
        shards[index % jobs].append(test_id)

    env = os.environ.copy()
    pythonpath = [str(root / "src"), str(tests_dir)]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    duration_args = ["--durations", "20"] if sys.version_info >= (3, 12) else []
    processes: list[tuple[int, subprocess.Popen[str]]] = []
    try:
        for index, shard in enumerate(shards, start=1):
            argv = [sys.executable, "-m", "unittest", "-v", *duration_args, *shard]
            proc = subprocess.Popen(
                argv,
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            processes.append((index, proc))

        failed = False
        for index, proc in processes:
            output, _ = proc.communicate()
            print(f"=== test shard {index}/{jobs} ===")
            if output:
                print(output, end="" if output.endswith("\n") else "\n")
            if proc.returncode != 0:
                failed = True
        return 1 if failed else 0
    except KeyboardInterrupt:
        for _index, proc in processes:
            if proc.poll() is None:
                proc.terminate()
        for _index, proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
