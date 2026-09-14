from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from llm_git_bridge import app
from llm_git_bridge.core import BridgeError, save_json
from llm_git_bridge.transport import RcloneTransport, RemoteFileEntry, TransientTransportError


def sh(cwd: Path, *args: str) -> str:
    p = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return p.stdout.strip()


class FakeTransport:
    def __init__(self):
        self.files: dict[str, str] = {}
        self.list_calls: list[str] = []
        self.ensure_dir_calls: list[str] = []
        self.delete_calls: list[tuple[str, bool]] = []
        self.control_upload_calls: list[str] = []
        self.last_mode = "fake"

    def ensure_dir(self, rel: str) -> None:
        self.ensure_dir_calls.append(rel)

    def list_files(self, rel: str) -> list[str]:
        return [entry.name for entry in self.list_entries(rel)]

    def list_entries(self, rel: str) -> list[RemoteFileEntry]:
        self.list_calls.append(rel)
        prefix = rel.rstrip("/") + "/"
        out: list[RemoteFileEntry] = []
        for path in self.files:
            if path.startswith(prefix):
                rest = path[len(prefix):]
                if "/" not in rest:
                    out.append(RemoteFileEntry(rest, len(self.files[path].encode("utf-8"))))
        return sorted(out, key=lambda entry: entry.name)

    def delete_file(self, rel: str, *, allow_fallback: bool = True) -> bool:
        self.delete_calls.append((rel, allow_fallback))
        self.files.pop(rel, None)
        return True

    def download_text(self, rel: str, local: Path, *, max_bytes: int | None = None) -> str:
        text = self.files[rel]
        if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
            raise BridgeError("remote request exceeds maximum allowed size")
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(text, encoding="utf-8")
        return text

    def upload_json(self, rel: str, obj, local_tmp: Path) -> None:
        local_tmp.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(obj, sort_keys=True) + "\n"
        local_tmp.write_text(text, encoding="utf-8")
        self.files[rel] = text

    def upload_control_json(self, rel: str, obj, local_tmp: Path) -> None:
        self.control_upload_calls.append(rel)
        self.upload_json(rel, obj, local_tmp)


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._seed_root = Path(tempfile.mkdtemp(prefix="llmgb-app-seed-"))
        cls._seed_repo = cls._seed_root / "repo"
        cls._seed_repo.mkdir()
        sh(cls._seed_repo, "git", "init", "-q")
        sh(cls._seed_repo, "git", "config", "user.email", "test@example.invalid")
        sh(cls._seed_repo, "git", "config", "user.name", "Test User")
        (cls._seed_repo / "README.md").write_text("hello\n", encoding="utf-8")
        sh(cls._seed_repo, "git", "add", "README.md")
        sh(cls._seed_repo, "git", "commit", "-qm", "initial")
        cls._seed_head = sh(cls._seed_repo, "git", "rev-parse", "HEAD")
        cls._seed_branch = sh(cls._seed_repo, "git", "symbolic-ref", "--short", "HEAD")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._seed_root, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="llmgb-app-"))
        self.state = self.tmp / "state"
        self.repo = self.tmp / "repo"
        shutil.copytree(self._seed_repo, self.repo, symlinks=True)

        self.fake = FakeTransport()
        self.config = self.tmp / "config"
        self.old = (app.CONFIG_DIR, app.CONFIG_FILE, app.STATE_DIR, app.REGISTRY_FILE, app.PUBLISHED_DIR)
        app.CONFIG_DIR = self.config
        app.CONFIG_FILE = self.config / "config.json"
        app.STATE_DIR = self.state
        app.REGISTRY_FILE = self.state / "registry.json"
        app.PUBLISHED_DIR = self.state / "published-results"
        save_json(
            app.REGISTRY_FILE,
            {
                "version": 1,
                "generated_at": "test",
                "repos": {
                    "repo": {
                        "id": "repo",
                        "name": "repo",
                        "path": str(self.repo),
                        "head": self._seed_head,
                        "branch": self._seed_branch,
                        "dirty": False,
                        "tracked_dirty": False,
                        "untracked": False,
                        "last_seen": "test",
                    }
                },
            },
        )
        self.transport_patch = patch("llm_git_bridge.app.transport_from_config", return_value=self.fake)
        self.transport_patch.start()
        self.cfg = {
            "transport": {"type": "rclone", "remote": "fake"},
            "roots": [str(self.tmp)],
            "safe_branch_prefix": "ai/",
            "allow_commit": True,
            "push_enabled_repos": [],
            "commands": {},
        }

    def tearDown(self):
        self.transport_patch.stop()
        app.CONFIG_DIR, app.CONFIG_FILE, app.STATE_DIR, app.REGISTRY_FILE, app.PUBLISHED_DIR = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)


    def test_disappeared_registered_repo_yields_signed_generic_error_without_path_leak(self):
        txid = "tx-missing-repo"
        request = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "repo",
            "base_sha": self._seed_head,
            "branch": "ai/missing-repo",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+world\n"
            ),
            "run": [],
        }
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps(request) + "\n"
        shutil.rmtree(self.repo)

        processed = app.process_pending_once(self.cfg)

        self.assertEqual(processed, 1)
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["transaction_id"], txid)
        self.assertTrue(app._verify_result(filename, result))
        self.assertNotIn(str(self.tmp), json.dumps(result))
        self.assertNotIn(str(self.repo), json.dumps(result))
        self.assertNotIn(f"v2/transactions/{filename}", self.fake.files)

    def test_registry_change_between_pending_requests_is_observed_fail_closed(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        tx_first = "tx-a-registry-refresh"
        tx_second = "tx-b-registry-refresh"
        self.fake.files[f"v2/transactions/{tx_first}.json"] = json.dumps({
            "protocol": 2,
            "kind": "doctor",
            "transaction_id": tx_first,
        })
        self.fake.files[f"v2/transactions/{tx_second}.json"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": tx_second,
            "repo": repo_id,
            "base_sha": self._seed_head,
            "branch": "ai/registry-refresh-race",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+must-not-commit\n"
            ),
            "run": [],
        })

        def doctor_then_remove_registry_repo(_cfg, _obj, _filename):
            registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
            registry["repos"] = {}
            save_json(app.REGISTRY_FILE, registry)
            return {
                "protocol": 2,
                "kind": "result",
                "transaction_id": tx_first,
                "status": "success",
                "operation": "doctor",
                "processed_at": "test",
                "doctor": {},
            }

        with patch("llm_git_bridge.app._process_doctor_request", side_effect=doctor_then_remove_registry_repo):
            self.assertEqual(app.process_pending_once(self.cfg), 2)

        result = json.loads(self.fake.files[f"v2/results/{tx_second}.json"])
        self.assertEqual(result["status"], "error")
        self.assertTrue(app._verify_result(f"{tx_second}.json", result))
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(self.repo), "show-ref", "--verify", "refs/heads/ai/registry-refresh-race"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).returncode,
            0,
        )

    def test_b1_poll_stage_freezes_mailbox_entries_and_sizes(self):
        self.fake.files["v2/transactions/tx-b.json"] = "{}\n"
        self.fake.files["v2/transactions/tx-a.json"] = "{\"a\":1}\n"
        self.fake.files["v2/transactions/not a tx.json"] = "{}\n"

        poll = app._poll_transaction_mailbox(self.fake)

        self.assertEqual(poll.filenames, ("tx-a.json", "tx-b.json"))
        self.assertEqual(poll.size_for("tx-a.json"), len('{"a":1}\n'.encode("utf-8")))
        self.assertIsNone(poll.size_for("tx-missing.json"))
        self.assertEqual(poll.list_transport, "fake")

    def test_b1_download_and_validate_stages_preserve_exact_byte_binding(self):
        txid = "tx-b1-envelope"
        filename = f"{txid}.json"
        raw = '\ufeff{"protocol":2,"kind":"doctor","transaction_id":"tx-b1-envelope"}\r\n'
        self.fake.files[f"v2/transactions/{filename}"] = raw
        poll = app._poll_transaction_mailbox(self.fake)

        downloaded = app._download_remote_request(
            self.fake,
            filename,
            reported_size=poll.size_for(filename),
            poll=poll,
            poll_started=time.monotonic(),
        )

        self.assertIsNotNone(downloaded)
        assert downloaded is not None
        self.assertEqual(downloaded.raw, raw)
        self.assertEqual(
            downloaded.request_bytes_sha256,
            __import__("hashlib").sha256(raw.encode("utf-8")).hexdigest(),
        )
        validated = app._validate_downloaded_request(downloaded)
        self.assertEqual(validated.obj["transaction_id"], txid)

    def test_b1_durable_result_stage_precedes_remote_publication(self):
        txid = "tx-b1-durable-first"
        filename = f"{txid}.json"
        result = app._sign_result(filename, {
            "protocol": 2,
            "kind": "result",
            "transaction_id": txid,
            "status": "success",
            "processed_at": "test",
            "transport_timings": {
                "transaction_list_s": 0.0,
                "transaction_download_s": 0.0,
                "transaction_list_transport": "fake",
                "transaction_download_transport": "fake",
                "pre_result_upload_s": 0.0,
            },
        })

        local = app._persist_signed_result(filename, result)

        self.assertTrue(local.exists())
        self.assertTrue(app._verify_result(filename, json.loads(local.read_text())))
        self.assertNotIn(f"v2/results/{filename}", self.fake.files)

    def test_b1_transient_download_stage_returns_no_terminal_envelope(self):
        txid = "tx-b1-transient"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        poll = app._poll_transaction_mailbox(self.fake)

        with patch.object(
            self.fake,
            "download_text",
            side_effect=TransientTransportError("retry"),
        ):
            downloaded = app._download_remote_request(
                self.fake,
                filename,
                reported_size=poll.size_for(filename),
                poll=poll,
                poll_started=time.monotonic(),
            )

        self.assertIsNone(downloaded)
        self.assertIn(f"v2/transactions/{filename}", self.fake.files)
        self.assertFalse((app.STATE_DIR / "inbox" / filename).exists())

    def test_b1_classification_stage_keeps_unacknowledged_request_executable(self):
        txid = "tx-b1-classify"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        marker = app._published_marker(filename)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("not-json", encoding="utf-8")
        poll = app._poll_transaction_mailbox(self.fake)

        classified = app._classify_transaction_mailbox(self.fake, poll)

        self.assertEqual(classified.candidates, (filename,))
        self.assertEqual(classified.published, ())

    def test_b1_job_planning_resolves_canonical_repository_key(self):
        txid = "tx-b1-plan"
        filename = f"{txid}.json"
        downloaded = app._DownloadedRequest(
            filename=filename,
            raw="{}",
            request_bytes_sha256="00" * 32,
            download_s=0.0,
            download_transport="fake",
        )
        request = app._ValidatedRequest(downloaded=downloaded, obj={
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "repo",
        })
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))

        job = app._plan_local_job(registry, request)

        self.assertEqual(job.kind, "transaction")
        self.assertEqual(job.resource_class, "repository")
        self.assertEqual(job.scheduling_key, "repo")

    def test_b1_job_planning_marks_control_requests_unkeyed(self):
        downloaded = app._DownloadedRequest(
            filename="tx-b1-control.json",
            raw="{}",
            request_bytes_sha256="00" * 32,
            download_s=0.0,
            download_transport="fake",
        )
        request = app._ValidatedRequest(downloaded=downloaded, obj={
            "protocol": 2,
            "kind": "doctor",
            "transaction_id": "tx-b1-control",
        })

        job = app._plan_local_job({}, request)

        self.assertEqual(job.resource_class, "control")
        self.assertIsNone(job.scheduling_key)

    def _b2_dummy_worker_task(
        self,
        txid: str = "tx-b2-dummy",
        *,
        repo_id: str = "repo",
        repo_path: Path | None = None,
    ):
        return app._TransactionWorkerTask(
            filename=f"{txid}.json",
            request_raw=json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
            }),
            repo_id=repo_id,
            repo_path=repo_path or self.repo,
            scheduling_key=str((repo_path or self.repo).resolve()),
            commands_json="{}",
            safe_branch_prefix="ai/",
            allow_commit=True,
            allow_push=False,
        )

    def _b2_dummy_worker_outcome(
        self, txid: str = "tx-b2-dummy", *, repo_id: str = "repo"
    ):
        return app._TransactionWorkerOutcome(
            result={
                "protocol": 2,
                "kind": "result",
                "transaction_id": txid,
                "repo": repo_id,
                "branch": "ai/b2-dummy",
                "status": "success",
                "processed_at": "test",
            },
            snapshot=None,
        )

    def test_b3_worker_task_freezes_config_before_handoff(self):
        txid = "tx-b3-freeze-config"
        downloaded = app._DownloadedRequest(
            filename=f"{txid}.json",
            raw=json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": "repo",
                "base_sha": self._seed_head,
                "branch": "ai/b3-freeze-config",
                "patch": "",
                "run": [],
            }),
            request_bytes_sha256="00" * 32,
            download_s=0.0,
            download_transport="fake",
        )
        request = app._ValidatedRequest(downloaded=downloaded, obj=json.loads(downloaded.raw))
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        cfg = {
            **self.cfg,
            "commands": {"repo": {"test": ["python3", "-c", "print('frozen')"]}},
            "safe_branch_prefix": "ai/",
            "allow_commit": True,
            "push_enabled_repos": ["repo"],
        }

        task = app._prepare_transaction_worker_task(cfg, registry, request)
        cfg["commands"]["repo"]["test"][2] = "print('mutated')"
        cfg["safe_branch_prefix"] = "changed/"
        cfg["allow_commit"] = False
        cfg["push_enabled_repos"].clear()

        self.assertEqual(
            json.loads(task.commands_json),
            {"test": ["python3", "-c", "print('frozen')"]},
        )
        self.assertEqual(task.safe_branch_prefix, "ai/")
        self.assertTrue(task.allow_commit)
        self.assertTrue(task.allow_push)

    def test_b3_worker_task_freezes_registry_resolution_before_handoff(self):
        txid = "tx-b3-freeze-registry"
        downloaded = app._DownloadedRequest(
            filename=f"{txid}.json",
            raw=json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": "repo",
                "base_sha": self._seed_head,
                "branch": "ai/b3-freeze-registry",
                "patch": "",
                "run": [],
            }),
            request_bytes_sha256="00" * 32,
            download_s=0.0,
            download_transport="fake",
        )
        request = app._ValidatedRequest(downloaded=downloaded, obj=json.loads(downloaded.raw))
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        task = app._prepare_transaction_worker_task(self.cfg, registry, request)

        original_path = task.repo_path
        registry["repos"]["repo"]["path"] = str(self.repo / "replacement")
        registry["repos"]["repo"]["id"] = "replacement"

        self.assertEqual(task.repo_id, "repo")
        self.assertEqual(task.repo_path, original_path)
        self.assertEqual(task.repo_path, self.repo)

    def test_b2_scheduler_uses_non_daemon_threads_and_joins_cleanly(self):
        scheduler = app._LocalWorkerScheduler(
            max_workers=1,
            queue_capacity=1,
            worker_fn=lambda task, cancel: self._b2_dummy_worker_outcome(task.filename[:-5]),
        )
        scheduler.start()
        threads = tuple(scheduler._threads)

        self.assertEqual(len(threads), 1)
        self.assertTrue(threads[0].is_alive())
        self.assertFalse(threads[0].daemon)

        scheduler.shutdown()
        scheduler.shutdown()
        self.assertFalse(threads[0].is_alive())

    def test_b2_scheduler_queue_is_bounded_with_deterministic_backpressure(self):
        started = threading.Event()
        release = threading.Event()

        def worker(task, _cancel):
            started.set()
            self.assertTrue(release.wait(2.0))
            return self._b2_dummy_worker_outcome(task.filename[:-5])

        scheduler = app._LocalWorkerScheduler(max_workers=1, queue_capacity=1, worker_fn=worker)
        scheduler.start()
        try:
            first = scheduler.submit(self._b2_dummy_worker_task("tx-b2-first"))
            self.assertTrue(started.wait(2.0))
            second = scheduler.submit(
                self._b2_dummy_worker_task(
                    "tx-b2-second",
                    repo_id="repo-b",
                    repo_path=self.tmp / "repo-b-b2-queue",
                )
            )
            self.assertTrue(scheduler._jobs.full())
            release.set()
            self.assertEqual(scheduler.wait(first).result["transaction_id"], "tx-b2-first")
            self.assertEqual(scheduler.wait(second).result["transaction_id"], "tx-b2-second")
        finally:
            release.set()
            scheduler.shutdown(cancel_running=True)

    def test_b2_scheduler_rejects_non_owner_submit_calls(self):
        scheduler = app._LocalWorkerScheduler(
            worker_fn=lambda task, cancel: self._b2_dummy_worker_outcome(task.filename[:-5])
        )
        scheduler.start()
        errors = []

        def non_owner_submit():
            try:
                scheduler.submit(self._b2_dummy_worker_task("tx-b2-non-owner"))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=non_owner_submit)
        thread.start()
        thread.join(2.0)
        try:
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], BridgeError)
            self.assertIn("owner thread", str(errors[0]))
        finally:
            scheduler.shutdown(cancel_running=True)

    def test_b2_scheduler_cancel_signal_is_visible_to_running_worker(self):
        started = threading.Event()
        proceed = threading.Event()
        observed_cancel = threading.Event()

        def worker(task, cancel):
            started.set()
            self.assertTrue(proceed.wait(2.0))
            if cancel():
                observed_cancel.set()
            return self._b2_dummy_worker_outcome(task.filename[:-5])

        scheduler = app._LocalWorkerScheduler(worker_fn=worker)
        scheduler.start()
        try:
            handle = scheduler.submit(self._b2_dummy_worker_task("tx-b2-cancel"))
            self.assertTrue(started.wait(2.0))
            scheduler.request_cancel()
            proceed.set()
            scheduler.wait(handle)
            self.assertTrue(observed_cancel.is_set())
        finally:
            proceed.set()
            scheduler.shutdown(cancel_running=True)

    def test_b2_worker_exception_propagates_without_killing_worker(self):
        calls = []

        def worker(task, _cancel):
            calls.append(task.filename)
            if len(calls) == 1:
                raise BridgeError("worker boom")
            return self._b2_dummy_worker_outcome(task.filename[:-5])

        scheduler = app._LocalWorkerScheduler(worker_fn=worker)
        scheduler.start()
        try:
            with self.assertRaisesRegex(BridgeError, "worker boom"):
                scheduler.execute(self._b2_dummy_worker_task("tx-b2-error"))
            outcome = scheduler.execute(self._b2_dummy_worker_task("tx-b2-after-error"))
            self.assertEqual(outcome.result["transaction_id"], "tx-b2-after-error")
            self.assertTrue(scheduler._threads[0].is_alive())
        finally:
            scheduler.shutdown(cancel_running=True)

    def test_c1_scheduler_overlaps_different_repositories(self):
        barrier = threading.Barrier(2)
        entered: list[str] = []
        repo_b = self.tmp / "repo-b-key"

        def worker(task, _cancel):
            entered.append(task.repo_id)
            barrier.wait(2.0)
            return self._b2_dummy_worker_outcome(
                task.filename[:-5], repo_id=task.repo_id
            )

        scheduler = app._LocalWorkerScheduler(
            max_workers=2, queue_capacity=2, worker_fn=worker
        )
        scheduler.start()
        try:
            first = scheduler.submit(
                self._b2_dummy_worker_task("tx-c1-repo-a", repo_id="repo")
            )
            second = scheduler.submit(
                self._b2_dummy_worker_task(
                    "tx-c1-repo-b", repo_id="repo-b", repo_path=repo_b
                )
            )
            self.assertEqual(scheduler.active_count(), 2)
            self.assertEqual(
                scheduler.active_repo_keys(),
                frozenset({str(self.repo.resolve()), str(repo_b.resolve())}),
            )
            self.assertEqual(
                scheduler.wait(first).result["transaction_id"], "tx-c1-repo-a"
            )
            self.assertEqual(
                scheduler.wait(second).result["transaction_id"], "tx-c1-repo-b"
            )
            self.assertEqual(set(entered), {"repo", "repo-b"})
            self.assertEqual(scheduler.active_count(), 0)
        finally:
            scheduler.shutdown(cancel_running=True)

    def test_c1_scheduler_rejects_alias_repo_ids_for_same_canonical_path(self):
        started = threading.Event()
        release = threading.Event()

        def worker(task, _cancel):
            started.set()
            self.assertTrue(release.wait(2.0))
            return self._b2_dummy_worker_outcome(
                task.filename[:-5], repo_id=task.repo_id
            )

        scheduler = app._LocalWorkerScheduler(
            max_workers=2, queue_capacity=2, worker_fn=worker
        )
        scheduler.start()
        try:
            first = scheduler.submit(
                self._b2_dummy_worker_task("tx-c1-alias-a", repo_id="repo")
            )
            self.assertTrue(started.wait(2.0))
            with self.assertRaisesRegex(BridgeError, "repository already has"):
                scheduler.submit(
                    self._b2_dummy_worker_task(
                        "tx-c1-alias-b", repo_id="repo-alias", repo_path=self.repo
                    )
                )
            release.set()
            scheduler.wait(first)
        finally:
            release.set()
            scheduler.shutdown(cancel_running=True)

    def test_c1_scheduler_rejects_same_repo_until_prior_handle_is_reaped(self):
        started = threading.Event()
        release = threading.Event()

        def worker(task, _cancel):
            started.set()
            self.assertTrue(release.wait(2.0))
            return self._b2_dummy_worker_outcome(
                task.filename[:-5], repo_id=task.repo_id
            )

        scheduler = app._LocalWorkerScheduler(
            max_workers=2, queue_capacity=2, worker_fn=worker
        )
        scheduler.start()
        try:
            first = scheduler.submit(self._b2_dummy_worker_task("tx-c1-same-a"))
            self.assertTrue(started.wait(2.0))
            with self.assertRaisesRegex(BridgeError, "repository already has"):
                scheduler.submit(self._b2_dummy_worker_task("tx-c1-same-b"))
            self.assertTrue(scheduler.repo_in_flight(str(self.repo.resolve())))
            self.assertEqual(
                scheduler.active_transaction_ids(), frozenset({"tx-c1-same-a"})
            )
            release.set()
            scheduler.wait(first)
            self.assertFalse(scheduler.repo_in_flight(str(self.repo.resolve())))
            second = scheduler.submit(self._b2_dummy_worker_task("tx-c1-same-b"))
            scheduler.wait(second)
        finally:
            release.set()
            scheduler.shutdown(cancel_running=True)

    def test_c1_scheduler_releases_ownership_after_worker_error(self):
        def worker(_task, _cancel):
            raise BridgeError("c1 worker boom")

        scheduler = app._LocalWorkerScheduler(max_workers=2, worker_fn=worker)
        scheduler.start()
        try:
            handle = scheduler.submit(self._b2_dummy_worker_task("tx-c1-error"))
            with self.assertRaisesRegex(BridgeError, "c1 worker boom"):
                scheduler.wait(handle)
            self.assertEqual(scheduler.active_count(), 0)
            self.assertFalse(scheduler.repo_in_flight(str(self.repo.resolve())))
            self.assertEqual(scheduler.active_transaction_ids(), frozenset())
        finally:
            scheduler.shutdown(cancel_running=True)

    def test_c1_completion_handles_remain_exact_when_workers_finish_out_of_order(self):
        both_started = threading.Barrier(2)
        fast_done = threading.Event()
        release_slow = threading.Event()

        def worker(task, _cancel):
            both_started.wait(2.0)
            txid = task.filename[:-5]
            if txid == "tx-c1-slow":
                self.assertTrue(fast_done.wait(2.0))
                self.assertTrue(release_slow.wait(2.0))
            else:
                fast_done.set()
            return self._b2_dummy_worker_outcome(txid, repo_id=task.repo_id)

        scheduler = app._LocalWorkerScheduler(
            max_workers=2, queue_capacity=2, worker_fn=worker
        )
        scheduler.start()
        try:
            slow = scheduler.submit(
                self._b2_dummy_worker_task("tx-c1-slow", repo_id="repo")
            )
            fast = scheduler.submit(
                self._b2_dummy_worker_task(
                    "tx-c1-fast",
                    repo_id="repo-b",
                    repo_path=self.tmp / "repo-b-fast",
                )
            )
            fast_outcome = scheduler.wait(fast)
            self.assertEqual(fast_outcome.result["transaction_id"], "tx-c1-fast")
            self.assertTrue(scheduler.repo_in_flight(str(self.repo.resolve())))
            release_slow.set()
            slow_outcome = scheduler.wait(slow)
            self.assertEqual(slow_outcome.result["transaction_id"], "tx-c1-slow")
        finally:
            release_slow.set()
            scheduler.shutdown(cancel_running=True)

    def test_c1_cancel_signal_reaches_all_running_workers(self):
        started = threading.Barrier(3)
        proceed = threading.Event()
        observed: set[str] = set()

        def worker(task, cancel):
            started.wait(2.0)
            self.assertTrue(proceed.wait(2.0))
            if cancel():
                observed.add(task.filename[:-5])
            return self._b2_dummy_worker_outcome(
                task.filename[:-5], repo_id=task.repo_id
            )

        scheduler = app._LocalWorkerScheduler(
            max_workers=2, queue_capacity=2, worker_fn=worker
        )
        scheduler.start()
        try:
            first = scheduler.submit(
                self._b2_dummy_worker_task("tx-c1-cancel-a", repo_id="repo")
            )
            second = scheduler.submit(
                self._b2_dummy_worker_task(
                    "tx-c1-cancel-b",
                    repo_id="repo-b",
                    repo_path=self.tmp / "repo-b-cancel",
                )
            )
            started.wait(2.0)
            scheduler.request_cancel()
            proceed.set()
            scheduler.wait(first)
            scheduler.wait(second)
            self.assertEqual(observed, {"tx-c1-cancel-a", "tx-c1-cancel-b"})
            self.assertEqual(scheduler.active_count(), 0)
        finally:
            proceed.set()
            scheduler.shutdown(cancel_running=True)

    def test_c1_process_pending_overlaps_real_worker_handoffs_for_two_repos(self):
        repo_b = self.tmp / "repo-b"
        shutil.copytree(self._seed_repo, repo_b, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo-b"] = {
            **registry["repos"]["repo"],
            "id": "repo-b",
            "name": "repo-b",
            "path": str(repo_b),
        }
        save_json(app.REGISTRY_FILE, registry)

        for suffix, repo_id in (("a", "repo"), ("b", "repo-b")):
            txid = f"tx-c1-overlap-{suffix}"
            self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": repo_id,
                "base_sha": self._seed_head,
                "branch": f"ai/c1-overlap-{suffix}",
                "patch": "",
                "run": [],
            }) + "\n"

        barrier = threading.Barrier(2)
        worker_threads: set[int] = set()
        main_thread = threading.get_ident()

        def fake_process(_repo_path, repo_id, obj, **_kwargs):
            worker_threads.add(threading.get_ident())
            barrier.wait(2.0)
            return Mock(
                result={
                    "protocol": 2,
                    "kind": "result",
                    "transaction_id": obj["transaction_id"],
                    "repo": repo_id,
                    "branch": obj["branch"],
                    "status": "success",
                    "processed_at": "test",
                },
                snapshot=None,
            )

        scheduler = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2)
        scheduler.start()
        try:
            with patch("llm_git_bridge.app.process_transaction", side_effect=fake_process):
                self.assertEqual(app.process_pending_once(self.cfg, scheduler=scheduler), 2)
        finally:
            scheduler.shutdown(cancel_running=True)

        self.assertEqual(len(worker_threads), 2)
        self.assertNotIn(main_thread, worker_threads)
        for suffix in ("a", "b"):
            result = json.loads(
                self.fake.files[f"v2/results/tx-c1-overlap-{suffix}.json"]
            )
            self.assertEqual(result["status"], "success")

    def test_c1_publication_failure_reaps_all_inflight_workers(self):
        repo_b = self.tmp / "repo-b-publication-failure"
        shutil.copytree(self._seed_repo, repo_b, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo-b"] = {
            **registry["repos"]["repo"],
            "id": "repo-b",
            "name": "repo-b",
            "path": str(repo_b),
        }
        save_json(app.REGISTRY_FILE, registry)

        for suffix, repo_id in (("a", "repo"), ("b", "repo-b")):
            txid = f"tx-c1-publish-fail-{suffix}"
            self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": repo_id,
                "base_sha": self._seed_head,
                "branch": f"ai/c1-publish-fail-{suffix}",
                "patch": "",
                "run": [],
            }) + "\n"

        barrier = threading.Barrier(2)

        def worker(task, _cancel):
            barrier.wait(2.0)
            return self._b2_dummy_worker_outcome(
                task.filename[:-5], repo_id=task.repo_id
            )

        publish_calls = 0

        def fail_first_publication(*_args, **_kwargs):
            nonlocal publish_calls
            publish_calls += 1
            if publish_calls == 1:
                raise BridgeError("simulated publication failure")

        scheduler = app._LocalWorkerScheduler(
            max_workers=2, queue_capacity=2, worker_fn=worker
        )
        scheduler.start()
        try:
            with patch(
                "llm_git_bridge.app._publish_and_cleanup_result",
                side_effect=fail_first_publication,
            ):
                with self.assertRaisesRegex(BridgeError, "simulated publication failure"):
                    app.process_pending_once(self.cfg, scheduler=scheduler)

            self.assertEqual(publish_calls, 2)
            self.assertEqual(scheduler.active_count(), 0)
            self.assertEqual(scheduler.active_repo_keys(), frozenset())
            self.assertEqual(scheduler.active_transaction_ids(), frozenset())
            for suffix in ("a", "b"):
                self.assertTrue(
                    (app.STATE_DIR / "results" / f"tx-c1-publish-fail-{suffix}.json").exists()
                )
        finally:
            scheduler.shutdown(cancel_running=True)

    def test_c1_control_request_executes_while_transaction_worker_is_active(self):
        txid = "tx-c1-a-worker"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "repo",
            "base_sha": self._seed_head,
            "branch": "ai/c1-control-overlap",
            "patch": "",
            "run": [],
        }) + "\n"
        doctor_txid = "tx-c1-b-doctor"
        self.fake.files[f"v2/transactions/{doctor_txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "doctor",
            "transaction_id": doctor_txid,
        }) + "\n"

        worker_started = threading.Event()
        doctor_called = threading.Event()

        def worker(task, _cancel):
            worker_started.set()
            self.assertTrue(doctor_called.wait(2.0))
            return self._b2_dummy_worker_outcome(task.filename[:-5])

        def doctor(_cfg, obj, _filename):
            self.assertTrue(worker_started.is_set())
            doctor_called.set()
            return {
                "protocol": 2,
                "kind": "result",
                "transaction_id": obj["transaction_id"],
                "status": "success",
                "processed_at": "test",
            }

        scheduler = app._LocalWorkerScheduler(max_workers=2, worker_fn=worker)
        scheduler.start()
        try:
            with patch("llm_git_bridge.app._process_doctor_request", side_effect=doctor):
                self.assertEqual(app.process_pending_once(self.cfg, scheduler=scheduler), 2)
        finally:
            doctor_called.set()
            scheduler.shutdown(cancel_running=True)

    def test_c1_materialize_is_a_quiescent_barrier(self):
        txid = "tx-c1-a-worker"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "repo",
            "base_sha": self._seed_head,
            "branch": "ai/c1-materialize-barrier",
            "patch": "",
            "run": [],
        }) + "\n"
        materialize_txid = "tx-c1-b-materialize"
        self.fake.files[f"v2/transactions/{materialize_txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "materialize",
            "transaction_id": materialize_txid,
            "repo": "repo",
        }) + "\n"

        allow_worker_finish = threading.Event()
        worker_done = threading.Event()

        def worker(task, _cancel):
            self.assertTrue(allow_worker_finish.wait(2.0))
            worker_done.set()
            return self._b2_dummy_worker_outcome(task.filename[:-5])

        def materialize(_cfg, _registry, obj, _filename):
            self.assertTrue(worker_done.is_set())
            return {
                "protocol": 2,
                "kind": "result",
                "transaction_id": obj["transaction_id"],
                "status": "success",
                "processed_at": "test",
            }

        scheduler = app._LocalWorkerScheduler(max_workers=2, worker_fn=worker)
        scheduler.start()
        original_wait = scheduler.wait

        def wait_and_release(handle):
            allow_worker_finish.set()
            return original_wait(handle)

        try:
            with patch.object(scheduler, "wait", side_effect=wait_and_release), patch(
                "llm_git_bridge.app._process_materialize_request", side_effect=materialize
            ):
                self.assertEqual(app.process_pending_once(self.cfg, scheduler=scheduler), 2)
        finally:
            allow_worker_finish.set()
            scheduler.shutdown(cancel_running=True)

    def test_b2_transaction_executes_on_worker_but_result_publication_stays_owner_owned(self):
        txid = "tx-b2-worker-boundary"
        filename = f"{txid}.json"
        main_thread = threading.get_ident()
        worker_threads = []
        upload_threads = []
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "repo",
            "base_sha": self._seed_head,
            "branch": "ai/b2-worker-boundary",
            "patch": "",
            "run": [],
        }) + "\n"

        original_upload = self.fake.upload_control_json

        def upload_control_json(*args, **kwargs):
            upload_threads.append(threading.get_ident())
            return original_upload(*args, **kwargs)

        def fake_process_transaction(*_args, **_kwargs):
            worker_threads.append(threading.get_ident())
            return Mock(
                result={
                    "protocol": 2,
                    "kind": "result",
                    "transaction_id": txid,
                    "repo": "repo",
                    "branch": "ai/b2-worker-boundary",
                    "status": "success",
                    "processed_at": "test",
                },
                snapshot=None,
            )

        scheduler = app._LocalWorkerScheduler(max_workers=1, queue_capacity=1)
        scheduler.start()
        try:
            with patch.object(self.fake, "upload_control_json", side_effect=upload_control_json), patch(
                "llm_git_bridge.app.process_transaction", side_effect=fake_process_transaction
            ):
                self.assertEqual(app.process_pending_once(self.cfg, scheduler=scheduler), 1)
        finally:
            scheduler.shutdown(cancel_running=True)

        self.assertEqual(len(worker_threads), 1)
        self.assertNotEqual(worker_threads[0], main_thread)
        self.assertTrue(upload_threads)
        self.assertEqual(set(upload_threads), {main_thread})

    def test_b2_control_request_policy_bypasses_worker_queue(self):
        txid = "tx-b2-control-owner"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2,
            "kind": "diagnostics",
            "transaction_id": txid,
            "limit": 1,
        }) + "\n"

        def should_not_run(_task, _cancel):
            raise AssertionError("control request reached worker")

        scheduler = app._LocalWorkerScheduler(worker_fn=should_not_run)
        scheduler.start()
        try:
            self.assertEqual(app.process_pending_once(self.cfg, scheduler=scheduler), 1)
        finally:
            scheduler.shutdown(cancel_running=True)
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        self.assertEqual(result["status"], "success")

    def test_b2_materialize_policy_bypasses_worker_queue(self):
        txid = "tx-b2-materialize-owner"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2,
            "kind": "materialize",
            "transaction_id": txid,
            "repo": "repo",
        }) + "\n"

        def should_not_run(_task, _cancel):
            raise AssertionError("materialize request reached transaction worker")

        scheduler = app._LocalWorkerScheduler(worker_fn=should_not_run)
        scheduler.start()
        try:
            self.assertEqual(app.process_pending_once(self.cfg, scheduler=scheduler), 1)
        finally:
            scheduler.shutdown(cancel_running=True)
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        self.assertEqual(result["status"], "success")

    def test_result_auth_key_survives_runtime_state_deletion(self):
        first = app._result_auth_key()
        self.assertEqual(len(first), 32)
        key_path = app.CONFIG_DIR / "result-auth.key"
        self.assertTrue(key_path.exists())
        self.assertEqual(key_path.stat().st_mode & 0o777, 0o600)
        __import__("shutil").rmtree(app.STATE_DIR, ignore_errors=True)
        second = app._result_auth_key()
        self.assertEqual(second, first)

    def test_result_auth_key_migrates_legacy_state_key(self):
        legacy = app.STATE_DIR / "result-auth.key"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        expected = bytes.fromhex("42" * 32)
        legacy.write_text(expected.hex() + "\n", encoding="ascii")
        self.assertEqual(app._result_auth_key(), expected)
        migrated = app.CONFIG_DIR / "result-auth.key"
        self.assertTrue(migrated.exists())
        self.assertEqual(bytes.fromhex(migrated.read_text().strip()), expected)

    def test_push_is_disabled_by_default(self):
        self.assertEqual(app.default_config()["push_enabled_repos"], [])

    def test_materialize_request_is_remote_triggerable(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        txid = "tx-materialize"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "materialize",
            "transaction_id": txid,
            "repo": repo_id,
        })
        count = app.process_pending_once(self.cfg)
        self.assertEqual(count, 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["operation"], "materialize")
        self.assertIn(f"v2/repos/{repo_id}/snapshot.json", self.fake.files)
        self.assertEqual(self.fake.ensure_dir_calls, [])

    def test_materialize_branch_request_is_remote_triggerable(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        head = self._seed_head
        sh(self.repo, "git", "branch", "ai/branch-snapshot", head)
        txid = "tx-materialize-branch"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "materialize",
            "transaction_id": txid,
            "repo": repo_id,
            "branch": "ai/branch-snapshot",
        })
        count = app.process_pending_once(self.cfg)
        self.assertEqual(count, 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["branch"], "ai/branch-snapshot")
        self.assertEqual(result["head"], head)
        self.assertIn("/branches/", result["snapshot"])
        self.assertIn(result["snapshot"], self.fake.files)

    def test_materialize_branch_is_pinned_to_captured_tip(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        original = self._seed_head
        branch = "ai/materialize-race"
        sh(self.repo, "git", "branch", branch, original)
        (self.repo / "README.md").write_text("hello\nnewer\n", encoding="utf-8")
        sh(self.repo, "git", "add", "README.md")
        sh(self.repo, "git", "commit", "-m", "newer")
        newer = sh(self.repo, "git", "rev-parse", "HEAD")
        txid = "tx-materialize-race"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2,
            "kind": "materialize",
            "transaction_id": txid,
            "repo": repo_id,
            "branch": branch,
        })
        real_add = app.add_disposable_worktree
        moved = False

        def move_branch_then_add(repo, *args):
            nonlocal moved
            if not moved:
                moved = True
                sh(self.repo, "git", "branch", "-f", branch, newer)
            return real_add(repo, *args)

        with patch.object(app, "add_disposable_worktree", side_effect=move_branch_then_add):
            self.assertEqual(app.process_pending_once(self.cfg), 1)

        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["head"], original)
        snapshot = json.loads(self.fake.files[result["snapshot"]])
        self.assertEqual(snapshot["head"], original)
        self.assertEqual(sh(self.repo, "git", "rev-parse", branch), newer)

    def test_transaction_snapshot_is_deferred_by_default(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        head = self._seed_head
        txid = "tx-deferred-snapshot"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": repo_id,
            "base_sha": head,
            "branch": "ai/deferred-snapshot",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+world\n"
            ),
            "run": [],
        })
        self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertTrue(result["snapshot_deferred"])
        self.assertFalse(any("/branches/" in path for path in self.fake.files))

    def test_transaction_can_explicitly_publish_snapshot(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        head = self._seed_head
        txid = "tx-published-snapshot"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": repo_id,
            "base_sha": head,
            "branch": "ai/published-snapshot",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+world\n"
            ),
            "run": [],
            "publish_snapshot": True,
        })
        self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertIn("/branches/", result["snapshot"])
        self.assertIn(result["snapshot"], self.fake.files)

    def test_signed_result_and_marker_bind_to_exact_request_bytes(self):
        txid = "tx-result-request-hash"
        filename = f"{txid}.json"
        raw = json.dumps({"protocol": 2, "kind": "doctor", "transaction_id": txid}, separators=(",", ":"))
        self.fake.files[f"v2/transactions/{filename}"] = raw
        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }):
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        expected = __import__("hashlib").sha256(raw.encode("utf-8")).hexdigest()
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        marker = json.loads((app.PUBLISHED_DIR / filename).read_text(encoding="utf-8"))
        self.assertEqual(result["request_bytes_sha256"], expected)
        self.assertEqual(marker["request_bytes_sha256"], expected)
        self.assertTrue(app._verify_result(filename, result))

    def test_changed_bytes_reusing_published_transaction_id_are_not_executed(self):
        txid = "tx-published-reuse"
        filename = f"{txid}.json"
        original = json.dumps({"protocol": 2, "kind": "doctor", "transaction_id": txid}, separators=(",", ":"))
        self.fake.files[f"v2/transactions/{filename}"] = original
        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }):
            self.assertEqual(app.process_pending_once(self.cfg), 1)

        changed = json.dumps({"protocol": 2, "kind": "doctor", "transaction_id": txid, "changed": True}, separators=(",", ":"))
        self.fake.files[f"v2/transactions/{filename}"] = changed
        with patch("llm_git_bridge.app._process_doctor_request") as doctor:
            self.assertEqual(app.process_pending_once(self.cfg), 0)
        doctor.assert_not_called()
        self.assertNotIn(f"v2/transactions/{filename}", self.fake.files)
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        original_hash = __import__("hashlib").sha256(original.encode("utf-8")).hexdigest()
        changed_hash = __import__("hashlib").sha256(changed.encode("utf-8")).hexdigest()
        self.assertEqual(result["request_bytes_sha256"], original_hash)
        self.assertNotEqual(result["request_bytes_sha256"], changed_hash)

    def test_processed_marker_avoids_result_listing_in_steady_state(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        head = self._seed_head
        txid = "tx-change"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": repo_id,
            "base_sha": head,
            "branch": "ai/change",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+world\n"
            ),
            "run": [],
        })
        self.assertEqual(app.process_pending_once(self.cfg), 1)
        self.assertEqual(self.fake.ensure_dir_calls, [])
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertTrue(result["snapshot_deferred"])
        self.assertEqual(self.fake.list_calls, ["v2/transactions"])
        self.assertIn("transport_timings", result)
        metrics = (app.STATE_DIR / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        event = json.loads(metrics[-1])
        self.assertEqual(event["event"], "transaction")
        self.assertIn("result_upload_s", event)
        self.assertEqual(event["request_cleanup_status"], "success")
        self.assertNotIn(f"v2/transactions/{txid}.json", self.fake.files)
        self.assertIn(f"v2/results/{txid}.json", self.fake.control_upload_calls)
        self.assertFalse((app.STATE_DIR / "inbox" / f"{txid}.json").exists())
        self.assertFalse((app.STATE_DIR / "transactions" / txid).exists())
        self.assertFalse((app.STATE_DIR / "outbox" / f"result-{txid}.json").exists())
        self.assertNotIn("path", json.dumps(event))
        self.fake.list_calls.clear()
        self.assertEqual(app.process_pending_once(self.cfg), 0)
        self.assertEqual(self.fake.list_calls, ["v2/transactions"])

    def test_malformed_local_result_is_quarantined_and_request_reprocessed(self):
        txid = "tx-recovery-corrupt-local"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2,
            "kind": "doctor",
            "transaction_id": txid,
        })
        local_result = app.STATE_DIR / "results" / filename
        local_result.parent.mkdir(parents=True, exist_ok=True)
        local_result.write_text('{"status":', encoding="utf-8")

        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2,
            "kind": "result",
            "transaction_id": txid,
            "status": "success",
            "processed_at": "test",
            "operation": "doctor",
            "doctor": {},
        }) as doctor:
            self.assertEqual(app.process_pending_once(self.cfg), 1)

        doctor.assert_called_once()
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        self.assertEqual(result["status"], "success")
        self.assertTrue((app.PUBLISHED_DIR / filename).exists())
        self.assertNotIn(f"v2/transactions/{filename}", self.fake.files)

    def test_valid_json_local_result_with_wrong_transaction_id_is_reprocessed(self):
        txid = "tx-recovery-wrong-id"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        local_result = app.STATE_DIR / "results" / filename
        save_json(local_result, {"status": "success", "transaction_id": "tx-other"})
        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        doctor.assert_called_once()
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        self.assertEqual(result["transaction_id"], txid)

    def test_valid_json_local_result_with_bad_signature_is_reprocessed(self):
        txid = "tx-recovery-bad-local-signature"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        local_result = app.STATE_DIR / "results" / filename
        forged = app._sign_result(filename, {
            "protocol": 2,
            "kind": "result",
            "status": "success",
            "transaction_id": txid,
        })
        forged["bridge_auth"]["tag"] = "0" * 64
        save_json(local_result, forged)
        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        doctor.assert_called_once()
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        self.assertTrue(app._verify_result(filename, result))
        self.assertNotEqual(result["bridge_auth"]["tag"], "0" * 64)

    def test_commit_survives_local_result_persistence_failure_and_retry_recovers_once(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        head = self._seed_head
        txid = "tx-local-result-save-crash"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": repo_id,
            "base_sha": head,
            "branch": "ai/local-result-save-crash",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+durable\n"
            ),
            "run": [],
        })
        real_save = app.save_json
        local_result = app.STATE_DIR / "results" / filename
        failed = False

        def fail_result_save_once(path, obj):
            nonlocal failed
            if path == local_result and not failed:
                failed = True
                raise BridgeError("simulated local result persistence failure")
            return real_save(path, obj)

        with patch.object(app, "save_json", side_effect=fail_result_save_once):
            with self.assertRaisesRegex(BridgeError, "simulated local result persistence failure"):
                app.process_pending_once(self.cfg)

        commit = sh(self.repo, "git", "rev-parse", "ai/local-result-save-crash")
        self.assertEqual(sh(self.repo, "git", "rev-list", "--count", f"{head}..ai/local-result-save-crash"), "1")
        self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        self.assertEqual(result["commit"], commit)
        self.assertTrue(result["recovered_after_crash"])
        self.assertEqual(sh(self.repo, "git", "rev-list", "--count", f"{head}..ai/local-result-save-crash"), "1")

    def test_ambiguous_result_upload_accepted_remotely_recovers_without_reexecution(self):
        txid = "tx-result-upload-accepted"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        original_upload = self.fake.upload_control_json
        failed = False

        def accept_then_raise(rel, obj, local_tmp):
            nonlocal failed
            original_upload(rel, obj, local_tmp)
            if not failed:
                failed = True
                raise BridgeError("simulated ambiguous result upload")

        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            with patch.object(self.fake, "upload_control_json", side_effect=accept_then_raise):
                with self.assertRaisesRegex(BridgeError, "simulated ambiguous result upload"):
                    app.process_pending_once(self.cfg)
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        doctor.assert_called_once()
        self.assertTrue((app.PUBLISHED_DIR / filename).exists())
        self.assertNotIn(f"v2/transactions/{filename}", self.fake.files)

    def test_result_upload_failure_before_acceptance_recovers_from_local_result_without_reexecution(self):
        txid = "tx-result-upload-not-accepted"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            with patch.object(self.fake, "upload_control_json", side_effect=BridgeError("simulated upload failure")):
                with self.assertRaisesRegex(BridgeError, "simulated upload failure"):
                    app.process_pending_once(self.cfg)
            local_result = app.STATE_DIR / "results" / filename
            old = time.time() - app.RESULT_RETRY_GRACE_S - 1
            os.utime(local_result, (old, old))
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        doctor.assert_called_once()
        self.assertTrue((app.PUBLISHED_DIR / filename).exists())

    def test_marker_write_failure_after_remote_result_recovers_without_reexecution(self):
        txid = "tx-marker-write-crash"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        original_mark = app._mark_published
        failed = False

        def fail_once(name, *, source, request_bytes_sha256=None):
            nonlocal failed
            if source == "processed" and not failed:
                failed = True
                raise BridgeError("simulated marker persistence failure")
            return original_mark(name, source=source, request_bytes_sha256=request_bytes_sha256)

        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            with patch.object(app, "_mark_published", side_effect=fail_once):
                with self.assertRaisesRegex(BridgeError, "simulated marker persistence failure"):
                    app.process_pending_once(self.cfg)
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        doctor.assert_called_once()
        self.assertTrue((app.PUBLISHED_DIR / filename).exists())
        self.assertNotIn(f"v2/transactions/{filename}", self.fake.files)

    def test_crash_after_marker_before_request_cleanup_is_recovered_without_local_artifact_leak(self):
        txid = "tx-marker-before-cleanup-crash"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        original_cleanup = app._cleanup_remote_request
        crashed = False

        def crash_once(transport, name, *, allow_fallback=True):
            nonlocal crashed
            if name == filename and not crashed:
                crashed = True
                raise RuntimeError("simulated crash after marker")
            return original_cleanup(transport, name, allow_fallback=allow_fallback)

        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            with patch.object(app, "_cleanup_remote_request", side_effect=crash_once):
                with self.assertRaisesRegex(RuntimeError, "simulated crash after marker"):
                    app.process_pending_once(self.cfg)

            self.assertTrue((app.PUBLISHED_DIR / filename).exists())
            self.assertTrue((app.STATE_DIR / "inbox" / filename).exists())
            self.assertIn(f"v2/transactions/{filename}", self.fake.files)

            self.assertEqual(app.process_pending_once(self.cfg), 0)

        doctor.assert_called_once()
        self.assertNotIn(f"v2/transactions/{filename}", self.fake.files)
        self.assertFalse((app.STATE_DIR / "inbox" / filename).exists())
        self.assertFalse((app.STATE_DIR / "transactions" / txid).exists())
        self.assertFalse((app.STATE_DIR / "outbox" / f"result-{filename}").exists())

    def test_request_delete_failure_after_marker_is_retried_without_reexecution(self):
        txid = "tx-request-delete-retry"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        original_delete = self.fake.delete_file
        failed = False

        def defer_once(rel, *, allow_fallback=True):
            nonlocal failed
            if rel == f"v2/transactions/{filename}" and not failed:
                failed = True
                return False
            return original_delete(rel, allow_fallback=allow_fallback)

        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            with patch.object(self.fake, "delete_file", side_effect=defer_once):
                self.assertEqual(app.process_pending_once(self.cfg), 1)
                self.assertIn(f"v2/transactions/{filename}", self.fake.files)
                self.assertEqual(app.process_pending_once(self.cfg), 0)
        doctor.assert_called_once()
        self.assertNotIn(f"v2/transactions/{filename}", self.fake.files)

    def test_local_result_recovery_checks_remote_before_reupload(self):
        txid = "tx-recovery-existing"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = "{}"
        local_result = app.STATE_DIR / "results" / filename
        save_json(local_result, {"status": "success", "transaction_id": txid})
        remote_result = app._sign_result(filename, {"status": "success", "transaction_id": txid})
        self.fake.files[f"v2/results/{filename}"] = json.dumps(remote_result)
        with patch.object(self.fake, "upload_json", wraps=self.fake.upload_json) as upload:
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        upload.assert_not_called()
        self.assertTrue((app.PUBLISHED_DIR / filename).exists())
        self.assertNotIn(f"v2/transactions/{filename}", self.fake.files)

    def test_young_local_result_is_not_immediately_reuploaded_after_ambiguous_failure(self):
        txid = "tx-recovery-grace"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = "{}"
        local_result = app.STATE_DIR / "results" / filename
        save_json(local_result, app._sign_result(filename, {"status": "success", "transaction_id": txid}))
        self.assertEqual(app.process_pending_once(self.cfg), 0)
        self.assertFalse((app.PUBLISHED_DIR / filename).exists())
        self.assertIn(f"v2/transactions/{filename}", self.fake.files)

    def test_future_dated_local_result_does_not_block_recovery_forever(self):
        txid = "tx-recovery-clock-skew"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = "{}"
        local_result = app.STATE_DIR / "results" / filename
        save_json(local_result, app._sign_result(filename, {"status": "success", "transaction_id": txid}))
        future = __import__("time").time() + 3600
        __import__("os").utime(local_result, (future, future))
        self.assertEqual(app.process_pending_once(self.cfg), 1)
        self.assertTrue((app.PUBLISHED_DIR / filename).exists())

    def test_startup_reconciliation_prevents_replay_after_local_state_loss(self):
        txid = "tx-already"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2, "kind": "materialize", "transaction_id": txid, "repo": "does-not-matter"
        })
        filename = f"{txid}.json"
        remote_result = app._sign_result(filename, {"status": "success", "transaction_id": txid})
        self.fake.files[f"v2/results/{filename}"] = json.dumps(remote_result)
        self.assertEqual(app.reconcile_remote_results(self.cfg), 1)
        self.assertTrue((app.PUBLISHED_DIR / f"{txid}.json").exists())
        self.fake.list_calls.clear()
        self.assertEqual(app.process_pending_once(self.cfg), 0)
        self.assertEqual(self.fake.list_calls, ["v2/transactions"])

    def test_reconciliation_is_idempotent(self):
        txid = "tx-already"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        remote_result = app._sign_result(filename, {"status": "success", "transaction_id": txid})
        self.fake.files[f"v2/results/{filename}"] = json.dumps(remote_result)
        self.assertEqual(app.reconcile_remote_results(self.cfg), 1)
        self.assertEqual(app.reconcile_remote_results(self.cfg), 0)
        lines = (app.STATE_DIR / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[-1])["event"], "startup-reconcile")

    def test_startup_reconciliation_rejects_authenticated_result_with_wrong_transaction_id(self):
        txid = "tx-auth-wrong-id"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        remote_result = app._sign_result(filename, {
            "status": "success", "transaction_id": "tx-other"
        })
        self.fake.files[f"v2/results/{filename}"] = json.dumps(remote_result)
        self.assertEqual(app.reconcile_remote_results(self.cfg), 0)
        self.assertFalse((app.PUBLISHED_DIR / filename).exists())

    def test_startup_reconciliation_rejects_forged_remote_result(self):
        txid = "tx-forged-result"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        self.fake.files[f"v2/results/{filename}"] = json.dumps({
            "status": "success", "transaction_id": txid
        })
        self.assertEqual(app.reconcile_remote_results(self.cfg), 0)
        self.assertFalse((app.PUBLISHED_DIR / filename).exists())

    def test_local_result_recovery_refuses_forged_remote_conflict(self):
        txid = "tx-forged-conflict"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = "{}"
        local_result = app.STATE_DIR / "results" / filename
        save_json(local_result, {"status": "success", "transaction_id": txid})
        self.fake.files[f"v2/results/{filename}"] = json.dumps({
            "status": "success", "transaction_id": txid
        })
        with self.assertRaisesRegex(BridgeError, "unauthenticated remote result"):
            app.process_pending_once(self.cfg)
        self.assertFalse((app.PUBLISHED_DIR / filename).exists())

    def test_diagnostics_request_returns_only_sanitized_metrics(self):
        metrics = app.STATE_DIR / "metrics.jsonl"
        metrics.parent.mkdir(parents=True, exist_ok=True)
        metrics.write_text(json.dumps({
            "event": "transaction",
            "transaction_id": "tx-prior",
            "result_upload_s": 1.25,
            "path": "/private/repo",
            "secret": "nope",
        }) + "\n", encoding="utf-8")
        txid = "tx-diagnostics"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2, "kind": "diagnostics", "transaction_id": txid, "limit": 5
        })
        self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["operation"], "diagnostics")
        self.assertEqual(result["metrics"][0]["transaction_id"], "tx-prior")
        blob = json.dumps(result["metrics"])
        self.assertNotIn("/private/repo", blob)
        self.assertNotIn("secret", blob)

    def test_corrupt_publication_marker_cannot_suppress_pending_request(self):
        txid = "tx-corrupt-marker"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        marker = app.PUBLISHED_DIR / filename
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"filename":', encoding="utf-8")
        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        doctor.assert_called_once()
        result = json.loads(self.fake.files[f"v2/results/{filename}"])
        self.assertEqual(result["status"], "success")
        repaired = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(repaired["filename"], filename)

    def test_wrong_filename_publication_marker_cannot_suppress_pending_request(self):
        txid = "tx-wrong-marker"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        app.PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
        save_json(app.PUBLISHED_DIR / filename, {
            "filename": "tx-other.json", "published_at": "test", "source": "test"
        })
        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        doctor.assert_called_once()

    def test_idle_poll_reaps_acknowledged_request_without_subprocess_fallback(self):
        txid = "tx-old-request"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = "{}"
        app._mark_published(filename, source="test")
        self.assertEqual(app.process_pending_once(self.cfg), 0)
        self.assertNotIn(f"v2/transactions/{filename}", self.fake.files)
        self.assertEqual(self.fake.delete_calls[-1], (f"v2/transactions/{filename}", False))

    def test_doctor_request_is_sanitized(self):
        txid = "tx-doctor"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        with patch("llm_git_bridge.app._version_line", side_effect=["git version 2.51.0", "rclone v1.72.0"]):
            with patch("llm_git_bridge.app._rclone_remote_summary", return_value={
                "remote_type": "drive",
                "custom_drive_client_id_configured": True,
                "config_inspected": True,
            }):
                with patch("llm_git_bridge.app.RcloneRCProcess.healthy", return_value=True):
                    self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"] )
        self.assertEqual(result["operation"], "doctor")
        self.assertEqual(result["doctor"]["remote_type"], "drive")
        self.assertTrue(result["doctor"]["custom_drive_client_id_configured"])
        self.assertTrue(result["doctor"]["rc_socket_healthy"])
        blob = json.dumps(result)
        self.assertNotIn("client_secret", blob)
        self.assertNotIn("token", blob)
        self.assertNotIn("/Users/", blob)

    def test_diagnostics_limit_is_bounded(self):
        txid = "tx-diagnostics-bad"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2, "kind": "diagnostics", "transaction_id": txid, "limit": 500
        })
        self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["status"], "error")
        self.assertIn("diagnostics limit", result["error"])


    def test_duplicate_json_keys_are_rejected(self):
        txid = "tx-duplicate-json"
        self.fake.files[f"v2/transactions/{txid}.json"] = (
            '{"protocol":2,"kind":"diagnostics","transaction_id":"tx-duplicate-json",'
            '"transaction_id":"tx-other"}'
        )
        self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["status"], "error")
        self.assertIn("duplicate JSON key", result["error"])

    def test_nonfinite_json_constant_is_rejected(self):
        txid = "tx-nonfinite-json"
        self.fake.files[f"v2/transactions/{txid}.json"] = (
            '{"protocol":2,"kind":"diagnostics","transaction_id":"tx-nonfinite-json",'
            '"limit":NaN}'
        )
        self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["status"], "error")
        self.assertIn("invalid JSON constant", result["error"])

    def test_transient_transaction_download_failure_is_retried_without_terminal_result(self):
        txid = "tx-download-retry"
        request_path = f"v2/transactions/{txid}.json"
        result_path = f"v2/results/{txid}.json"
        self.fake.files[request_path] = json.dumps({
            "protocol": 2,
            "kind": "diagnostics",
            "transaction_id": txid,
            "limit": 1,
        })

        original_download = self.fake.download_text
        calls = 0

        def flaky_download(rel, local, *, max_bytes=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.fake.last_mode = "subprocess"
                raise TransientTransportError("transaction download failed; retry required")
            return original_download(rel, local, max_bytes=max_bytes)

        with patch.object(self.fake, "download_text", side_effect=flaky_download):
            self.assertEqual(app.process_pending_once(self.cfg), 0)
            self.assertIn(request_path, self.fake.files)
            self.assertNotIn(result_path, self.fake.files)
            self.assertFalse((app.PUBLISHED_DIR / f"{txid}.json").exists())

            self.assertEqual(app.process_pending_once(self.cfg), 1)

        result = json.loads(self.fake.files[result_path])
        self.assertEqual(result["status"], "success")
        self.assertNotIn(request_path, self.fake.files)

    def test_download_content_rejection_still_produces_terminal_result(self):
        txid = "tx-invalid-utf8-model"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        with patch.object(
            self.fake,
            "download_text",
            side_effect=BridgeError("remote request is not valid UTF-8 JSON text"),
        ):
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["status"], "error")
        self.assertIn("not valid UTF-8", result["error"])

    def test_non_bridge_exception_is_not_exposed_remotely(self):
        txid = "tx-internal-error"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2, "kind": "doctor", "transaction_id": txid
        })
        with patch("llm_git_bridge.app._process_doctor_request", side_effect=RuntimeError("/Users/tom/SECRET_TOKEN")):
            self.assertEqual(app.process_pending_once(self.cfg), 1)
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["error"], "internal bridge error")
        self.assertNotIn("SECRET_TOKEN", json.dumps(result))
        self.assertNotIn("/Users/", json.dumps(result))

    def test_oversized_request_is_rejected_before_json_parse(self):
        txid = "tx-oversized"
        self.fake.files[f"v2/transactions/{txid}.json"] = "x" * 100
        with patch.object(self.fake, "download_text", wraps=self.fake.download_text) as download:
            with patch.object(app, "MAX_REQUEST_BYTES", 16):
                self.assertEqual(app.process_pending_once(self.cfg), 1)
        download.assert_not_called()
        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["status"], "error")
        self.assertIn("maximum allowed size", result["error"])

    def test_metric_log_rotates_when_bounded_size_is_reached(self):
        path = app.STATE_DIR / "metrics.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x" * 100, encoding="utf-8")
        with patch.object(app, "MAX_METRICS_BYTES", 50):
            app._append_metric({"event": "test", "recorded_at": "now"})
        self.assertTrue(path.with_name("metrics.jsonl.1").exists())
        self.assertIn('"event": "test"', path.read_text(encoding="utf-8"))

    def test_command_logs_survive_request_cleanup_and_are_bounded(self):
        keep_txid = "tx-command-log-keep"
        keep = app.STATE_DIR / "command-logs" / keep_txid
        keep.mkdir(parents=True, exist_ok=True)
        (keep / "01.log").write_text("failure evidence\n", encoding="utf-8")
        app._cleanup_local_request_artifacts(f"{keep_txid}.json")
        self.assertTrue((keep / "01.log").exists())

        root = app.STATE_DIR / "command-logs"
        for index in range(4):
            path = root / f"tx-old-{index}"
            path.mkdir(parents=True, exist_ok=True)
            (path / "01.log").write_text(str(index), encoding="utf-8")
            stamp = time.time() - (20 - index)
            os.utime(path, (stamp, stamp))
        app._prune_command_logs(limit=2)
        self.assertLessEqual(len([p for p in root.iterdir() if p.is_dir()]), 2)

    def test_c1_command_log_pruning_preserves_active_transactions(self):
        root = app.STATE_DIR / "command-logs"
        active = root / "tx-c1-active"
        active.mkdir(parents=True, exist_ok=True)
        (active / "01.log").write_text("active\n", encoding="utf-8")
        os.utime(active, (1, 1))
        for index in range(3):
            path = root / f"tx-c1-history-{index}"
            path.mkdir(parents=True, exist_ok=True)
            (path / "01.log").write_text(str(index), encoding="utf-8")
            stamp = 10 + index
            os.utime(path, (stamp, stamp))

        app._prune_command_logs(limit=1, protected_txids={"tx-c1-active"})

        self.assertTrue(active.exists())
        history = [p for p in root.iterdir() if p.is_dir() and p.name != "tx-c1-active"]
        self.assertEqual(len(history), 1)

    def test_local_result_cache_is_bounded_without_removing_replay_markers(self):
        result_dir = app.STATE_DIR / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        for index in range(5):
            filename = f"tx-{index}.json"
            (result_dir / filename).write_text("{}\n", encoding="utf-8")
            app._mark_published(filename, source="test")
        app._prune_local_results(limit=2)
        self.assertEqual(len(list(result_dir.glob("*.json"))), 2)
        self.assertEqual(len(list(app.PUBLISHED_DIR.glob("*.json"))), 5)

    def test_local_result_pruning_skips_unacknowledged_oldest_result(self):
        result_dir = app.STATE_DIR / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        oldest = result_dir / "tx-unacknowledged.json"
        oldest.write_text("{}\n", encoding="utf-8")
        os.utime(oldest, (1, 1))
        for index in range(3):
            filename = f"tx-published-{index}.json"
            path = result_dir / filename
            path.write_text("{}\n", encoding="utf-8")
            os.utime(path, (10 + index, 10 + index))
            app._mark_published(filename, source="test")

        app._prune_local_results(limit=2)

        self.assertTrue(oldest.exists())
        remaining_published = [
            p for p in result_dir.glob("tx-published-*.json") if p.exists()
        ]
        self.assertEqual(len(remaining_published), 1)

    def test_local_request_cleanup_preserves_command_logs_but_removes_workspaces(self):
        filename = "tx-clean.json"
        txid = "tx-clean"
        (app.STATE_DIR / "inbox").mkdir(parents=True, exist_ok=True)
        (app.STATE_DIR / "inbox" / filename).write_text("{}\n", encoding="utf-8")
        for parent in ("transactions", "command-logs", "command-runs", "command-homes"):
            path = app.STATE_DIR / parent / txid
            path.mkdir(parents=True, exist_ok=True)
            (path / "artifact").write_text("x", encoding="utf-8")
        app._cleanup_local_request_artifacts(filename)
        self.assertFalse((app.STATE_DIR / "inbox" / filename).exists())
        for parent in ("transactions", "command-runs", "command-homes"):
            self.assertFalse((app.STATE_DIR / parent / txid).exists())
        self.assertTrue((app.STATE_DIR / "command-logs" / txid / "artifact").exists())

    def test_request_identity_rejects_dot_transaction_ids(self):
        with self.assertRaises(BridgeError):
            app._validate_request_identity({"protocol": 2, "transaction_id": ".."}, "...json")


    def test_strict_json_rejects_lone_surrogate_strings(self):
        with self.assertRaisesRegex(BridgeError, "invalid Unicode"):
            app.strict_json_loads(r'{"value":"\ud800"}')

    def test_startup_local_cleanup_recovers_after_remote_request_was_already_deleted(self):
        txid = "tx-cleanup-crash"
        filename = f"{txid}.json"
        app._mark_published(filename, source="test")
        (app.STATE_DIR / "inbox").mkdir(parents=True, exist_ok=True)
        (app.STATE_DIR / "inbox" / filename).write_text("{}", encoding="utf-8")
        for parent in ("transactions", "command-runs", "command-homes"):
            path = app.STATE_DIR / parent / txid
            path.mkdir(parents=True, exist_ok=True)
            (path / "leftover").write_text("x", encoding="utf-8")
        outbox = app.STATE_DIR / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / f"result-{filename}").write_text("x", encoding="utf-8")
        (outbox / f"snapshot-repo-{txid}.json").write_text("x", encoding="utf-8")

        self.assertEqual(app.reconcile_local_acknowledged_artifacts(), 1)
        self.assertFalse((app.STATE_DIR / "inbox" / filename).exists())
        for parent in ("transactions", "command-runs", "command-homes"):
            self.assertFalse((app.STATE_DIR / parent / txid).exists())
        self.assertFalse((outbox / f"result-{filename}").exists())
        self.assertFalse((outbox / f"snapshot-repo-{txid}.json").exists())


class WatchLockTests(unittest.TestCase):
    def test_watch_lock_rejects_second_consumer(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-lock-") as tmp:
            old_state = app.STATE_DIR
            app.STATE_DIR = Path(tmp)
            first = None
            try:
                first = app._acquire_watch_lock()
                with self.assertRaises(BridgeError):
                    app._acquire_watch_lock()
            finally:
                if first is not None:
                    app._release_watch_lock(first)
                app.STATE_DIR = old_state


class WatchRcdHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="llmgb-rc-health-"))
        self.sock = self.tmp / "rclone.sock"
        self.sock.touch()
        self.process = Mock()
        self.process.poll.return_value = None
        self.handle = app.RcloneRCProcess(self.sock, self.process)
        self.cfg = {"transport": {"type": "rclone", "remote": "fake", "rc_enabled": True}}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_rc_health_miss_does_not_restart_live_daemon(self):
        with patch.object(self.handle, "healthy", return_value=False):
            with patch.object(self.handle, "stop") as stop:
                with patch("llm_git_bridge.app.start_rclone_rcd") as start:
                    returned = app._ensure_watch_rcd(self.cfg, self.handle)
        self.assertIs(returned, self.handle)
        self.assertEqual(self.handle.health_failures, 1)
        stop.assert_not_called()
        start.assert_not_called()

    def test_second_consecutive_rc_health_miss_restarts_daemon(self):
        self.handle.health_failures = app.RCD_HEALTH_FAILURE_THRESHOLD - 1
        replacement = app.RcloneRCProcess(self.sock, None)
        with patch.object(self.handle, "healthy", return_value=False):
            with patch.object(self.handle, "stop") as stop:
                with patch("llm_git_bridge.app.start_rclone_rcd", return_value=replacement) as start:
                    returned = app._ensure_watch_rcd(self.cfg, self.handle)
        self.assertIs(returned, replacement)
        stop.assert_called_once_with()
        start.assert_called_once_with(app.RCLONE_RC_SOCKET)

    def test_successful_rc_health_probe_resets_failure_streak(self):
        self.handle.health_failures = 1
        with patch.object(self.handle, "healthy", return_value=True) as healthy:
            returned = app._ensure_watch_rcd(self.cfg, self.handle)
        self.assertIs(returned, self.handle)
        self.assertEqual(self.handle.health_failures, 0)
        healthy.assert_called_once_with(timeout=app.RCD_HEALTH_TIMEOUT_S)

    def test_dead_rc_process_restarts_immediately_without_probe(self):
        self.process.poll.return_value = 9
        replacement = app.RcloneRCProcess(self.sock, None)
        with patch.object(self.handle, "healthy") as healthy:
            with patch.object(self.handle, "stop") as stop:
                with patch("llm_git_bridge.app.start_rclone_rcd", return_value=replacement):
                    returned = app._ensure_watch_rcd(self.cfg, self.handle)
        self.assertIs(returned, replacement)
        healthy.assert_not_called()
        stop.assert_called_once_with()


    def test_daemon_restart_requests_sigterm_instead_of_force_kickstart(self):
        from argparse import Namespace
        old_plist = app.PLIST_PATH
        try:
            app.PLIST_PATH = self.tmp / "daemon.plist"
            app.PLIST_PATH.write_text("plist", encoding="utf-8")
            with patch.object(app.sys, "platform", "darwin"):
                with patch.object(app.os, "getuid", return_value=501):
                    with patch.object(app, "_launchctl", return_value=0) as launchctl:
                        self.assertEqual(app.cmd_daemon(Namespace(action="restart", command=None)), 0)
            launchctl.assert_called_once_with(
                "kill", "SIGTERM", f"gui/501/{app.LABEL}", check=False
            )
        finally:
            app.PLIST_PATH = old_plist

    def test_daemon_restart_bootstraps_when_service_is_not_loaded(self):
        from argparse import Namespace
        old_plist = app.PLIST_PATH
        try:
            app.PLIST_PATH = self.tmp / "daemon.plist"
            app.PLIST_PATH.write_text("plist", encoding="utf-8")
            with patch.object(app.sys, "platform", "darwin"):
                with patch.object(app.os, "getuid", return_value=501):
                    with patch.object(app, "_launchctl", side_effect=[1, 1, 0]) as launchctl:
                        self.assertEqual(app.cmd_daemon(Namespace(action="restart", command=None)), 0)
            self.assertEqual(
                launchctl.call_args_list,
                [
                    call("kill", "SIGTERM", f"gui/501/{app.LABEL}", check=False),
                    call("kickstart", f"gui/501/{app.LABEL}", check=False),
                    call("bootstrap", "gui/501", str(app.PLIST_PATH)),
                ],
            )
        finally:
            app.PLIST_PATH = old_plist



class TransportTests(unittest.TestCase):
    def test_list_uses_persistent_rc_when_socket_is_available(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            sock = Path(tmp) / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock)
            response = {"list": [
                {"Name": "b.json", "Path": "b.json", "IsDir": False, "Size": 2},
                {"Name": "a.json", "Path": "a.json", "IsDir": False, "Size": 1},
            ]}
            with patch("llm_git_bridge.transport._rc_request", return_value=response) as mocked:
                self.assertEqual(transport.list_files("v2/transactions"), ["a.json", "b.json"])
                self.assertEqual(
                    transport.list_entries("v2/transactions"),
                    [RemoteFileEntry("a.json", 1), RemoteFileEntry("b.json", 2)],
                )
            self.assertEqual(transport.last_mode, "rcd")
            self.assertEqual(mocked.call_args.args[1], "operations/list")
            self.assertEqual(mocked.call_args.args[2]["fs"], "fake:")
            self.assertEqual(mocked.call_args.args[2]["remote"], "v2/transactions")
            self.assertTrue(mocked.call_args.args[2]["opt"]["filesOnly"])
            self.assertEqual(mocked.call_args.kwargs["timeout"], transport.rc_list_timeout)

    def test_list_falls_back_when_rc_is_unhealthy(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            sock = Path(tmp) / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock)
            proc = subprocess.CompletedProcess(
                ["rclone"],
                0,
                stdout='[{"Name":"b.json","Path":"b.json","Size":2,"IsDir":false},'
                       '{"Name":"a.json","Path":"a.json","Size":1,"IsDir":false}]',
                stderr="",
            )
            with patch("llm_git_bridge.transport._rc_request", side_effect=BridgeError("rc down")):
                with patch("llm_git_bridge.transport.run", return_value=proc) as mocked:
                    self.assertEqual(transport.list_files("v2/transactions"), ["a.json", "b.json"])
            self.assertEqual(transport.last_mode, "subprocess")
            self.assertEqual(mocked.call_args.kwargs["timeout"], transport.list_timeout)

    def test_download_uses_bounded_rc_timeout(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            sock = root / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock, download_timeout=8, rc_download_timeout=4)
            local = root / "inbox" / "tx.json"

            def rc_side_effect(_socket, _command, payload, **_kwargs):
                dst = Path(payload["dstFs"]) / payload["dstRemote"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text("{}\n", encoding="utf-8")
                return {}

            with patch("llm_git_bridge.transport._rc_request", side_effect=rc_side_effect) as mocked:
                with patch("llm_git_bridge.transport.run") as fallback:
                    self.assertEqual(transport.download_text("v2/transactions/tx.json", local), "{}\n")
            fallback.assert_not_called()
            self.assertEqual(mocked.call_args.kwargs["timeout"], 4)
            self.assertEqual(transport.last_mode, "rcd")

    def test_download_fallback_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            sock = root / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock, timeout=60, download_timeout=7, rc_download_timeout=3)
            local = root / "inbox" / "tx.json"

            def fallback_side_effect(argv, **_kwargs):
                dst = Path(argv[-1])
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text("{}\n", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

            with patch("llm_git_bridge.transport._rc_request", side_effect=BridgeError("rc slow")):
                with patch("llm_git_bridge.transport.run", side_effect=fallback_side_effect) as mocked:
                    self.assertEqual(transport.download_text("v2/transactions/tx.json", local), "{}\n")
            self.assertEqual(mocked.call_args.kwargs["timeout"], 7)
            self.assertEqual(transport.last_mode, "subprocess")

    def test_download_subprocess_failure_is_classified_as_transient(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            transport = RcloneTransport("fake", download_timeout=7)
            local = root / "inbox" / "tx.json"
            with patch(
                "llm_git_bridge.transport.run",
                side_effect=BridgeError("command timed out after 7s"),
            ):
                with self.assertRaises(TransientTransportError):
                    transport.download_text("v2/transactions/tx.json", local)
            self.assertFalse(local.exists())

    def test_download_timeouts_are_capped(self):
        transport = RcloneTransport("fake", timeout=6, download_timeout=20, rc_download_timeout=9)
        self.assertEqual(transport.download_timeout, 6)
        self.assertEqual(transport.rc_download_timeout, 6)

    def test_upload_uses_rc_copyfile_without_spawning_rclone_copyto(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            sock = root / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock)
            local = root / "outbox" / "result.json"
            with patch("llm_git_bridge.transport._rc_request", return_value={}) as mocked:
                with patch("llm_git_bridge.transport.run") as fallback:
                    transport.upload_text("v2/results/result.json", "{}\n", local)
            fallback.assert_not_called()
            self.assertEqual(transport.last_mode, "rcd")
            self.assertEqual(mocked.call_args.args[1], "operations/copyfile")
            payload = mocked.call_args.args[2]
            self.assertEqual(payload["srcFs"], str(local.parent))
            self.assertEqual(payload["srcRemote"], local.name)
            self.assertEqual(payload["dstFs"], "fake:")
            self.assertEqual(payload["dstRemote"], "v2/results/result.json")

    def test_delete_uses_rc_without_subprocess_when_available(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            sock = Path(tmp) / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock)
            with patch("llm_git_bridge.transport._rc_request", return_value={}) as mocked:
                with patch("llm_git_bridge.transport.run") as fallback:
                    self.assertTrue(transport.delete_file("v2/transactions/tx.json"))
            fallback.assert_not_called()
            self.assertEqual(transport.last_mode, "rcd")
            self.assertEqual(mocked.call_args.args[1], "operations/deletefile")
            self.assertEqual(mocked.call_args.args[2]["remote"], "v2/transactions/tx.json")

    def test_delete_can_defer_instead_of_spawning_fallback(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            sock = Path(tmp) / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock)
            with patch("llm_git_bridge.transport._rc_request", side_effect=BridgeError("rc down")):
                with patch("llm_git_bridge.transport.run") as fallback:
                    self.assertFalse(transport.delete_file("v2/transactions/tx.json", allow_fallback=False))
            fallback.assert_not_called()

    def test_rc_is_enabled_by_default_but_can_be_disabled(self):
        self.assertTrue(app.default_config()["transport"]["rc_enabled"])
        cfg = app.default_config()
        cfg["transport"] = {"type": "rclone", "remote": "fake", "rc_enabled": False}
        transport = app.transport_from_config(cfg)
        self.assertIsNone(transport.rc_socket)

    def test_list_failure_is_not_silently_treated_as_empty(self):
        transport = RcloneTransport("fake")
        proc = subprocess.CompletedProcess(["rclone"], 1, stdout="", stderr="auth failed")
        with patch("llm_git_bridge.transport.run", return_value=proc):
            with self.assertRaises(BridgeError):
                transport.list_files("v2/transactions")

    def test_list_uses_short_poll_timeout(self):
        transport = RcloneTransport("fake", timeout=60, list_timeout=7)
        proc = subprocess.CompletedProcess(["rclone"], 0, stdout="[]", stderr="")
        with patch("llm_git_bridge.transport.run", return_value=proc) as mocked:
            self.assertEqual(transport.list_files("v2/transactions"), [])
        self.assertEqual(mocked.call_args.kwargs["timeout"], 7)

    def test_list_timeout_is_capped_by_general_timeout(self):
        transport = RcloneTransport("fake", timeout=5, list_timeout=12)
        self.assertEqual(transport.list_timeout, 5)

    def test_rc_list_timeout_is_shorter_than_fallback_and_capped(self):
        transport = RcloneTransport("fake", timeout=60, list_timeout=5, rc_list_timeout=9)
        self.assertEqual(transport.rc_list_timeout, 5)
        transport = RcloneTransport("fake", timeout=60, list_timeout=5, rc_list_timeout=2)
        self.assertEqual(transport.rc_list_timeout, 2)

    def test_retire_existing_rcd_requests_quit_and_removes_socket(self):
        from llm_git_bridge import transport as transport_mod

        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            sock = Path(tmp) / "rclone.sock"
            sock.touch()
            ready = [True, False]

            def fake_ready(_path, **_kwargs):
                return ready.pop(0) if ready else False

            with patch("llm_git_bridge.transport._rc_ready", side_effect=fake_ready):
                with patch("llm_git_bridge.transport._rc_request", return_value={}) as request:
                    transport_mod._retire_existing_rcd(sock, timeout=0.1)
            self.assertEqual(request.call_args.args[1], "core/quit")
            self.assertFalse(sock.exists())

    def test_rc_process_health_detects_dead_owned_process(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            sock = Path(tmp) / "rclone.sock"
            proc = type("Proc", (), {"poll": lambda self: 1})()
            from llm_git_bridge.transport import RcloneRCProcess
            handle = RcloneRCProcess(sock, proc)
            self.assertFalse(handle.healthy())


    def test_mkdir_uses_short_timeout(self):
        transport = RcloneTransport("fake", timeout=60, mkdir_timeout=8)
        proc = subprocess.CompletedProcess(["rclone"], 0, stdout="", stderr="")
        with patch("llm_git_bridge.transport.run", return_value=proc) as mocked:
            transport.ensure_dir("v2/meta")
        self.assertEqual(mocked.call_args.kwargs["timeout"], 8)

    def test_mkdir_timeout_is_capped_by_general_timeout(self):
        transport = RcloneTransport("fake", timeout=5, mkdir_timeout=12)
        self.assertEqual(transport.mkdir_timeout, 5)


    def test_list_rejects_duplicate_names_from_rc(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            sock = Path(tmp) / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock)
            response = {"list": [
                {"Name": "tx.json", "Path": "tx.json", "IsDir": False},
                {"Name": "tx.json", "Path": "tx.json", "IsDir": False},
            ]}
            with patch("llm_git_bridge.transport._rc_request", return_value=response):
                with self.assertRaises(BridgeError):
                    transport.list_files("v2/transactions")

    def test_list_rejects_duplicate_names_from_subprocess(self):
        transport = RcloneTransport("fake")
        proc = subprocess.CompletedProcess(
            ["rclone"],
            0,
            stdout='[{"Name":"tx.json","Path":"tx.json","Size":1,"IsDir":false},'
                   '{"Name":"tx.json","Path":"tx.json","Size":1,"IsDir":false}]',
            stderr="",
        )
        with patch("llm_git_bridge.transport.run", return_value=proc):
            with self.assertRaises(BridgeError):
                transport.list_files("v2/transactions")

    def test_list_entries_preserves_remote_size_metadata(self):
        transport = RcloneTransport("fake")
        proc = subprocess.CompletedProcess(
            ["rclone"],
            0,
            stdout='[{"Name":"tx.json","Path":"tx.json","Size":123,"IsDir":false}]',
            stderr="",
        )
        with patch("llm_git_bridge.transport.run", return_value=proc):
            self.assertEqual(
                transport.list_entries("v2/transactions"),
                [RemoteFileEntry("tx.json", 123)],
            )

    def test_download_rejects_oversized_file_and_removes_local_copy(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            sock = root / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock)
            local = root / "inbox" / "tx.json"

            def rc_side_effect(_socket, _command, payload, **_kwargs):
                dst = Path(payload["dstFs"]) / payload["dstRemote"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text("x" * 100, encoding="utf-8")
                return {}

            with patch("llm_git_bridge.transport._rc_request", side_effect=rc_side_effect):
                with self.assertRaises(BridgeError):
                    transport.download_text("v2/transactions/tx.json", local, max_bytes=16)
            self.assertFalse(local.exists())

    def test_control_upload_uses_bounded_rc_timeout(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            sock = root / "rclone.sock"
            sock.touch()
            transport = RcloneTransport(
                "fake", rc_socket=sock, timeout=60, control_upload_timeout=9, rc_control_upload_timeout=4
            )
            with patch("llm_git_bridge.transport._rc_request", return_value={}) as mocked:
                transport.upload_control_json("v2/results/tx.json", {}, root / "out.json")
            self.assertEqual(mocked.call_args.kwargs["timeout"], 4)

    def test_control_upload_does_not_race_ambiguous_rc_failure_with_fallback(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            sock = root / "rclone.sock"
            sock.touch()
            transport = RcloneTransport(
                "fake", rc_socket=sock, timeout=60, control_upload_timeout=7, rc_control_upload_timeout=3
            )
            with patch("llm_git_bridge.transport._rc_request", side_effect=BridgeError("rc timeout")):
                with patch("llm_git_bridge.transport.run") as fallback:
                    with self.assertRaises(BridgeError):
                        transport.upload_control_json("v2/results/tx.json", {}, root / "out.json")
            fallback.assert_not_called()

    def test_control_upload_can_fallback_when_rc_socket_never_connected(self):
        from llm_git_bridge import transport as transport_mod

        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            sock = root / "rclone.sock"
            sock.touch()
            transport = RcloneTransport(
                "fake", rc_socket=sock, timeout=60, control_upload_timeout=7, rc_control_upload_timeout=3
            )
            proc = subprocess.CompletedProcess(["rclone"], 0, stdout="", stderr="")
            with patch(
                "llm_git_bridge.transport._rc_request",
                side_effect=transport_mod._RcloneRCUnavailable("not connected"),
            ):
                with patch("llm_git_bridge.transport.run", return_value=proc) as fallback:
                    transport.upload_control_json("v2/results/tx.json", {}, root / "out.json")
            self.assertEqual(fallback.call_args.kwargs["timeout"], 7)

    def test_control_upload_subprocess_timeout_is_bounded_when_rc_is_absent(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            transport = RcloneTransport(
                "fake", timeout=60, control_upload_timeout=7, rc_control_upload_timeout=3
            )
            proc = subprocess.CompletedProcess(["rclone"], 0, stdout="", stderr="")
            with patch("llm_git_bridge.transport.run", return_value=proc) as mocked:
                transport.upload_control_json("v2/results/tx.json", {}, root / "out.json")
            self.assertEqual(mocked.call_args.kwargs["timeout"], 7)


    def test_materialize_parser_accepts_exactly_one_repo_argument(self):
        parser = app.build_parser()
        args = parser.parse_args(["materialize", "demo"] )
        self.assertEqual(args.repo, "demo")
        with patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                parser.parse_args(["materialize", "demo", "extra"])

    def test_c1_max_workers_defaults_to_one_and_is_bounded(self):
        self.assertEqual(app.default_config()["max_workers"], 1)
        valid = app.default_config()
        valid["max_workers"] = 8
        self.assertEqual(app._validate_config(valid)["max_workers"], 8)
        for value in (True, 0, 9, 1.5, "2"):
            invalid = app.default_config()
            invalid["max_workers"] = value
            with self.subTest(value=value), self.assertRaisesRegex(
                BridgeError, "max_workers"
            ):
                app._validate_config(invalid)

    def test_poll_interval_validation_rejects_nonfinite_and_out_of_range_values(self):
        for value in (float("nan"), float("inf"), -1.0, 0.1, 3600.1, True):
            with self.subTest(value=value):
                with self.assertRaises(BridgeError):
                    app._validate_poll_interval(value)
        self.assertEqual(app._validate_poll_interval(0.5), 0.5)
        self.assertEqual(app._validate_poll_interval(3600), 3600.0)

    def test_save_config_refuses_to_persist_invalid_config(self):
        temp = Path(tempfile.mkdtemp(prefix="llmgb-config-write-"))
        old_config_file = app.CONFIG_FILE
        app.CONFIG_FILE = temp / "config.json"
        self.addCleanup(setattr, app, "CONFIG_FILE", old_config_file)
        cfg = app.default_config()
        cfg["poll_interval"] = float("inf")
        with self.assertRaises(BridgeError):
            app.save_config(cfg)
        self.assertFalse(app.CONFIG_FILE.exists())

    def test_load_config_rejects_malformed_and_nonfinite_values(self):
        temp = Path(tempfile.mkdtemp(prefix="llmgb-config-"))
        old_config_file = app.CONFIG_FILE
        app.CONFIG_FILE = temp / "config.json"
        self.addCleanup(setattr, app, "CONFIG_FILE", old_config_file)
        base = app.default_config()
        base["transport"]["remote"] = "fake"
        base["roots"] = [str(temp)]
        cases = [
            {**base, "transport": "not-an-object"},
            {**base, "poll_interval": float("nan")},
            {**base, "poll_interval": 0.1},
            {**base, "safe_branch_prefix": "ai//"},
            {**base, "safe_branch_prefix": ".hidden/"},
            {**base, "commands": {"repo": {"bad name": ["true"]}}},
        ]
        for index, cfg in enumerate(cases):
            with self.subTest(index=index):
                save_json(app.CONFIG_FILE, cfg)
                with self.assertRaises(BridgeError):
                    app.load_config()

    def test_load_config_accepts_repo_scoped_commands(self):
        temp = Path(tempfile.mkdtemp(prefix="llmgb-config-"))
        old_config_file = app.CONFIG_FILE
        app.CONFIG_FILE = temp / "config.json"
        self.addCleanup(setattr, app, "CONFIG_FILE", old_config_file)
        cfg = app.default_config()
        cfg["transport"]["remote"] = "fake"
        cfg["roots"] = [str(temp)]
        cfg["commands"] = {"demo": {"test": ["python3", "-m", "unittest"]}}
        save_json(app.CONFIG_FILE, cfg)
        loaded = app.load_config()
        self.assertEqual(loaded["commands"], cfg["commands"])

    def test_startup_reconciliation_skips_result_archive_when_all_pending_are_marked(self):
        fake = FakeTransport()
        filename = "tx-marked.json"
        fake.files[f"v2/transactions/{filename}"] = "{}"
        fake.files[f"v2/results/{filename}"] = "{}"
        temp = Path(tempfile.mkdtemp(prefix="llmgb-reconcile-marked-"))
        old_state, old_published = app.STATE_DIR, app.PUBLISHED_DIR
        app.STATE_DIR = temp / "state"
        app.PUBLISHED_DIR = app.STATE_DIR / "published-results"
        self.addCleanup(setattr, app, "STATE_DIR", old_state)
        self.addCleanup(setattr, app, "PUBLISHED_DIR", old_published)
        app.PUBLISHED_DIR.mkdir(parents=True)
        save_json(app.PUBLISHED_DIR / filename, {"filename": filename, "published_at": "2026-09-13T00:00:00Z", "source": "test"})
        with patch("llm_git_bridge.app.transport_from_config", return_value=fake):
            self.assertEqual(app.reconcile_remote_results(app.default_config()), 0)
        self.assertEqual(fake.list_calls, ["v2/transactions"])

    def test_startup_reconciliation_skips_result_archive_when_no_requests_pending(self):
        fake = FakeTransport()
        for i in range(20):
            fake.files[f"v2/results/old-{i}.json"] = "{}"
        temp = Path(tempfile.mkdtemp(prefix="llmgb-reconcile-"))
        old_state, old_published = app.STATE_DIR, app.PUBLISHED_DIR
        app.STATE_DIR = temp / "state"
        app.PUBLISHED_DIR = app.STATE_DIR / "published-results"
        self.addCleanup(setattr, app, "STATE_DIR", old_state)
        self.addCleanup(setattr, app, "PUBLISHED_DIR", old_published)
        with patch("llm_git_bridge.app.transport_from_config", return_value=fake):
            self.assertEqual(app.reconcile_remote_results(app.default_config()), 0)
        self.assertEqual(fake.list_calls, ["v2/transactions"])


    def test_download_text_preserves_exact_utf8_bytes_including_bom_and_crlf(self):
        with tempfile.TemporaryDirectory(prefix="llmgb-rc-") as tmp:
            root = Path(tmp)
            sock = root / "rclone.sock"
            sock.touch()
            transport = RcloneTransport("fake", rc_socket=sock)
            local = root / "inbox" / "tx.json"
            payload_bytes = b'\xef\xbb\xbf{"protocol":2,"kind":"doctor","transaction_id":"tx-exact"}\r\n'

            def rc_side_effect(_socket, _command, payload, **_kwargs):
                dst = Path(payload["dstFs"]) / payload["dstRemote"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(payload_bytes)
                return {}

            with patch("llm_git_bridge.transport._rc_request", side_effect=rc_side_effect):
                raw = transport.download_text("v2/transactions/tx-exact.json", local, max_bytes=4096)
            self.assertEqual(raw.encode("utf-8"), payload_bytes)
            self.assertEqual(local.read_bytes(), payload_bytes)


if __name__ == "__main__":
    unittest.main()
