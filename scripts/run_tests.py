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
import tempfile
import unittest
from typing import IO

DEFAULT_JOBS = 4
MAX_JOBS = 8

# These tests deliberately assert real process-cancellation/timeout latency. Running
# them concurrently with Git-heavy shards makes their wall-clock upper bounds a
# measurement of host scheduler contention instead of the code under test. Keep
# them fully enabled, but execute them after the parallel shards have drained.
SERIAL_TEST_SUFFIXES = (
    ".test_configured_command_cancellation_kills_process_group",
    ".test_configured_command_timeout_kills_process_group",
    ".test_configured_command_timeout_kills_same_group_descendant",
    ".test_c3_two_worker_external_process_canary_reduces_makespan",
)


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


def _partition_test_ids(test_ids: list[str], jobs: int) -> tuple[list[list[str]], list[str]]:
    if jobs <= 1:
        return [list(test_ids)], []
    serial = [test_id for test_id in test_ids if test_id.endswith(SERIAL_TEST_SUFFIXES)]
    parallel = [test_id for test_id in test_ids if test_id not in serial]
    shard_count = min(jobs, max(1, len(parallel)))
    shards = [[] for _ in range(shard_count)]
    for index, test_id in enumerate(parallel):
        shards[index % shard_count].append(test_id)
    return [shard for shard in shards if shard], serial


def _run_shards(
    shards: list[list[str]],
    *,
    root: Path,
    env: dict[str, str],
    duration_args: list[str],
    label: str,
) -> bool:
    processes: list[tuple[int, subprocess.Popen[str], IO[str]]] = []
    try:
        for index, shard in enumerate(shards, start=1):
            argv = [sys.executable, "-m", "unittest", "-v", *duration_args, *shard]
            output_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=root,
                    env=env,
                    text=True,
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                )
            except BaseException:
                output_file.close()
                raise
            processes.append((index, proc, output_file))

        failed = False
        total = len(processes)
        for index, proc, output_file in processes:
            proc.wait()
            output_file.seek(0)
            output = output_file.read()
            print(f"=== {label} {index}/{total} ===")
            if output:
                print(output, end="" if output.endswith("\n") else "\n")
            if proc.returncode != 0:
                failed = True
        return failed
    except KeyboardInterrupt:
        for _index, proc, _output_file in processes:
            if proc.poll() is None:
                proc.terminate()
        for _index, proc, _output_file in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise
    finally:
        for _index, _proc, output_file in processes:
            output_file.close()


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
    shards, serial_tests = _partition_test_ids(test_ids, jobs)

    env = os.environ.copy()
    pythonpath = [str(root / "src"), str(tests_dir)]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    duration_args = ["--durations", "20"] if sys.version_info >= (3, 12) else []
    failed = _run_shards(
        shards, root=root, env=env, duration_args=duration_args, label="test shard"
    )
    if serial_tests:
        failed = (
            _run_shards(
                [serial_tests],
                root=root,
                env=env,
                duration_args=duration_args,
                label="serial timing-sensitive tests",
            )
            or failed
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
