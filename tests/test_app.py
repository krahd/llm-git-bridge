from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from llm_git_bridge import app
from llm_git_bridge.core import BridgeError, git_marker_identity, repo_identity, save_json
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


    def test_auto_discovery_adds_new_repo_and_publishes_path_free_registry_while_idle(self):
        new_repo = self.tmp / "new-repo"
        shutil.copytree(self._seed_repo, new_repo, symlinks=True)
        cfg = dict(self.cfg)
        cfg["registry_scan_interval"] = 5.0

        self.assertEqual(app.process_pending_once(cfg), 0)

        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        by_name = {entry["name"]: entry for entry in registry["repos"].values()}
        self.assertIn("new-repo", by_name)
        self.assertEqual(by_name["new-repo"]["path"], str(new_repo.resolve()))
        published = json.loads(self.fake.files["v2/meta/repos.json"])
        self.assertIn("new-repo", {entry["name"] for entry in published["repos"]})
        self.assertNotIn(str(self.tmp), json.dumps(published))
        self.assertEqual(registry["discovery"]["last_added"], 1)
        self.assertEqual(registry["discovery"]["last_removed"], 0)

    def test_auto_discovery_publish_failure_does_not_replace_local_registry(self):
        new_repo = self.tmp / "publish-failure-repo"
        shutil.copytree(self._seed_repo, new_repo, symlinks=True)
        cfg = dict(self.cfg)
        cfg["registry_scan_interval"] = 5.0
        before = app.REGISTRY_FILE.read_bytes()

        with patch.object(app, "publish_registry", side_effect=BridgeError("publish failed")):
            with self.assertRaisesRegex(BridgeError, "publish failed"):
                app.refresh_registry_membership(cfg, publish=True)

        self.assertEqual(app.REGISTRY_FILE.read_bytes(), before)

    def test_full_refresh_publish_failure_does_not_replace_local_registry(self):
        new_repo = self.tmp / "full-refresh-failure"
        shutil.copytree(self._seed_repo, new_repo, symlinks=True)
        cfg = dict(self.cfg)
        before = app.REGISTRY_FILE.read_bytes()

        with patch.object(app, "publish_registry", side_effect=BridgeError("publish failed")):
            with self.assertRaisesRegex(BridgeError, "publish failed"):
                app.refresh_registry(cfg, publish=True)

        self.assertEqual(app.REGISTRY_FILE.read_bytes(), before)

    def test_repo_refresh_publish_failure_does_not_replace_local_registry(self):
        cfg = dict(self.cfg)
        before = app.REGISTRY_FILE.read_bytes()
        (self.repo / "README.md").write_text("advanced\n", encoding="utf-8")
        sh(self.repo, "git", "add", "README.md")
        sh(self.repo, "git", "commit", "-qm", "advance")

        with patch.object(app, "publish_registry", side_effect=BridgeError("publish failed")):
            with self.assertRaisesRegex(BridgeError, "publish failed"):
                app.refresh_repo_entry(cfg, "repo", publish=True)

        self.assertEqual(app.REGISTRY_FILE.read_bytes(), before)

    def test_first_capability_hash_publishes_unchanged_registry_on_upgrade(self):
        cfg = app.default_config()
        cfg["transport"]["remote"] = "fake"
        cfg["roots"] = [{"path": str(self.tmp), "push": False}]
        cfg["registry_scan_interval"] = 5.0
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry.pop("discovery", None)
        save_json(app.REGISTRY_FILE, registry)
        self.fake.files.pop("v2/meta/repos.json", None)

        refreshed = app.refresh_registry_membership(cfg, publish=True)

        self.assertIn("v2/meta/repos.json", self.fake.files)
        public = json.loads(self.fake.files["v2/meta/repos.json"])
        entry = next(item for item in public["repos"] if item["id"] == "repo")
        self.assertEqual(entry["capabilities"], {"read": True, "edit": True, "push": False})
        self.assertTrue(refreshed["discovery"]["capabilities_hash"])

    def test_auto_discovery_continues_while_long_worker_is_active(self):
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["discovery"] = {
            "last_scan_at": "test",
            "last_scan_epoch": time.time(),
            "last_scan_duration_s": 0.0,
            "last_added": 0,
            "last_removed": 0,
            "last_updated": 0,
        }
        save_json(app.REGISTRY_FILE, registry)
        cfg = dict(self.cfg)
        cfg.update({"registry_scan_interval": 5.0, "max_workers": 2, "max_pending_jobs": 8, "poll_interval": 0.5})
        txid = "tx-discovery-active-worker"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "repo",
            "base_sha": self._seed_head,
            "branch": "ai/discovery-active-worker",
            "patch": "",
            "run": [],
        }) + "\n"
        new_repo = self.tmp / "created-while-active"
        worker_started = threading.Event()
        discovered = threading.Event()

        def worker(task, _cancel):
            worker_started.set()
            self.assertTrue(discovered.wait(4.0), "registry discovery stalled behind active worker")
            return self._b2_dummy_worker_outcome(task.filename[:-5], repo_id=task.repo_id)

        def create_repo():
            self.assertTrue(worker_started.wait(2.0))
            shutil.copytree(self._seed_repo, new_repo, symlinks=True)

        def discovery_due(_cfg, _registry):
            return worker_started.is_set() and (new_repo / ".git").exists() and not discovered.is_set()

        real_refresh = app.refresh_registry_membership

        def refresh_and_signal(*args, **kwargs):
            refreshed = real_refresh(*args, **kwargs)
            if "created-while-active" in {entry["name"] for entry in refreshed["repos"].values()}:
                discovered.set()
            return refreshed

        creator = threading.Thread(target=create_repo)
        creator.start()
        scheduler = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2, worker_fn=worker)
        scheduler.start()
        try:
            with patch.object(app, "_registry_discovery_due", side_effect=discovery_due), patch.object(
                app, "refresh_registry_membership", side_effect=refresh_and_signal
            ):
                self.assertEqual(app.process_pending_once(cfg, scheduler=scheduler), 1)
        finally:
            creator.join(2.0)
            scheduler.shutdown(cancel_running=True)

        self.assertTrue(discovered.is_set())
        published = json.loads(self.fake.files["v2/meta/repos.json"])
        self.assertIn("created-while-active", {entry["name"] for entry in published["repos"]})

    def test_auto_discovery_does_not_publish_when_membership_is_unchanged(self):
        cfg = dict(self.cfg)
        cfg["registry_scan_interval"] = 5.0
        registry = app.refresh_registry_membership(cfg, publish=False)
        registry["discovery"]["last_scan_epoch"] = 0.0
        save_json(app.REGISTRY_FILE, registry)

        self.assertEqual(app.process_pending_once(cfg), 0)

        self.assertNotIn("v2/meta/repos.json", self.fake.files)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        self.assertIn("discovery", registry)
        self.assertEqual(registry["discovery"]["last_added"], 0)
        self.assertEqual(registry["discovery"]["last_removed"], 0)

    def test_auto_discovery_republishes_when_effective_capabilities_change(self):
        cfg = app.default_config()
        cfg["transport"]["remote"] = "fake"
        cfg["roots"] = [{"path": str(self.tmp), "push": False}]
        cfg["registry_scan_interval"] = 5.0

        app.refresh_registry_membership(cfg, publish=True)
        self.fake.files.pop("v2/meta/repos.json", None)

        cfg["roots"] = [{"path": str(self.tmp), "push": True}]
        refreshed = app.refresh_registry_membership(cfg, publish=True)

        published = json.loads(self.fake.files["v2/meta/repos.json"])
        entry = next(item for item in published["repos"] if item["id"] == "repo")
        self.assertTrue(entry["capabilities"]["push"])
        self.assertTrue(refreshed["discovery"]["capabilities_hash"])
        self.assertEqual(refreshed["discovery"]["last_added"], 0)
        self.assertEqual(refreshed["discovery"]["last_removed"], 0)
        self.assertEqual(refreshed["discovery"]["last_updated"], 0)

    def test_auto_discovery_does_not_publish_for_equivalent_canonical_path(self):
        cfg = dict(self.cfg)
        cfg["registry_scan_interval"] = 5.0
        registry = app.refresh_registry_membership(cfg, publish=False)
        registry["repos"]["repo"]["path"] = str(
            self.tmp / ".." / self.tmp.name / "repo"
        )
        registry["discovery"]["last_scan_epoch"] = 0.0
        save_json(app.REGISTRY_FILE, registry)

        self.assertEqual(app.process_pending_once(cfg), 0)

        self.assertNotIn("v2/meta/repos.json", self.fake.files)
        refreshed = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        self.assertEqual(refreshed["repos"]["repo"]["path"], str(self.repo.resolve()))
        self.assertEqual(refreshed["discovery"]["last_added"], 0)
        self.assertEqual(refreshed["discovery"]["last_removed"], 0)
        self.assertEqual(refreshed["discovery"]["last_updated"], 0)

    def test_unknown_repo_retries_against_due_auto_discovery_once(self):
        new_repo = self.tmp / "arrived-later"
        shutil.copytree(self._seed_repo, new_repo, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["discovery"] = {
            "last_scan_at": "test",
            "last_scan_epoch": 0.0,
            "last_scan_duration_s": 0.0,
            "last_added": 0,
            "last_removed": 0,
        }
        save_json(app.REGISTRY_FILE, registry)
        cfg = dict(self.cfg)
        cfg["registry_scan_interval"] = 5.0
        txid = "tx-auto-discovery-late"
        filename = f"{txid}.json"
        raw = json.dumps({
            "protocol": 2,
            "kind": "materialize",
            "transaction_id": txid,
            "repo": "arrived-later",
        }) + "\n"
        downloaded = app._DownloadedRequest(
            filename=filename,
            raw=raw,
            request_bytes_sha256="0" * 64,
            download_s=0.0,
            download_transport="test",
        )
        request = app._ValidatedRequest(downloaded=downloaded, obj=json.loads(raw))

        job, refreshed = app._plan_local_job_with_discovery(cfg, registry, request)

        self.assertEqual(job.kind, "materialize")
        self.assertIsNotNone(job.scheduling_key)
        self.assertIn("arrived-later", {entry["name"] for entry in refreshed["repos"].values()})
        self.assertIn("v2/meta/repos.json", self.fake.files)

    def test_auto_discovery_scan_interval_rate_limits_unknown_repo_rescans(self):
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["discovery"] = {
            "last_scan_at": "test",
            "last_scan_epoch": time.time(),
            "last_scan_duration_s": 0.0,
            "last_added": 0,
            "last_removed": 0,
        }
        save_json(app.REGISTRY_FILE, registry)
        cfg = dict(self.cfg)
        cfg["registry_scan_interval"] = 60.0
        txid = "tx-auto-discovery-rate-limit"
        filename = f"{txid}.json"
        raw = json.dumps({
            "protocol": 2,
            "kind": "materialize",
            "transaction_id": txid,
            "repo": "not-there",
        }) + "\n"
        request = app._ValidatedRequest(
            downloaded=app._DownloadedRequest(
                filename=filename,
                raw=raw,
                request_bytes_sha256="0" * 64,
                download_s=0.0,
                download_transport="test",
            ),
            obj=json.loads(raw),
        )

        with patch.object(app, "refresh_registry_membership") as refresh:
            with self.assertRaisesRegex(BridgeError, "unknown repository"):
                app._plan_local_job_with_discovery(cfg, registry, request)
        refresh.assert_not_called()

    def test_diagnostics_exposes_path_free_registry_discovery_status(self):
        cfg = dict(self.cfg)
        cfg["registry_scan_interval"] = 5.0
        txid = "tx-registry-diagnostics"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "diagnostics",
            "transaction_id": txid,
        }) + "\n"

        self.assertEqual(app.process_pending_once(cfg), 1)

        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        status = result["registry"]
        self.assertTrue(status["auto_discovery_enabled"])
        self.assertEqual(status["scan_interval_s"], 5.0)
        self.assertEqual(status["repo_count"], 1)
        self.assertIsInstance(status["last_scan_at"], str)
        self.assertNotIn(str(self.tmp), json.dumps(status))

    def test_repository_replacement_at_same_path_is_rejected_before_policy_can_apply(self):
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo"]["git_marker_id"] = git_marker_identity(self.repo)
        registry["repos"]["repo"]["repo_identity"] = repo_identity(self.repo)
        save_json(app.REGISTRY_FILE, registry)
        old_marker_id = registry["repos"]["repo"]["git_marker_id"]
        old_repo_identity = registry["repos"]["repo"]["repo_identity"]
        shutil.rmtree(self.repo)
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q")
        sh(self.repo, "git", "config", "user.email", "replacement@example.invalid")
        sh(self.repo, "git", "config", "user.name", "Replacement User")
        (self.repo / "README.md").write_text("replacement history\n", encoding="utf-8")
        sh(self.repo, "git", "add", "README.md")
        sh(self.repo, "git", "commit", "-qm", "replacement initial")
        self.assertNotEqual(repo_identity(self.repo), old_repo_identity)
        # Filesystems may immediately reuse the same .git inode. The security
        # boundary therefore must not depend on marker identity alone.
        _ = old_marker_id == git_marker_identity(self.repo)
        txid = "tx-replaced-repo-policy"
        replacement_head = sh(self.repo, "git", "rev-parse", "HEAD")
        patch_text = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-replacement history
+replacement changed
"""
        cfg = {
            **self.cfg,
            "commands": {"repo": {"privileged": ["python3", "-c", "print('old policy')"]}},
            "push_enabled_repos": ["repo"],
        }
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "repo",
            "base_sha": replacement_head,
            "branch": "ai/replaced-repo-policy",
            "patch": patch_text,
            "run": ["privileged"],
            "push": False,
        }) + "\n"

        self.assertEqual(app.process_pending_once(cfg), 1)

        result = json.loads(self.fake.files[f"v2/results/{txid}.json"])
        self.assertEqual(result["status"], "error")
        self.assertIn("requested command is not configured locally: privileged", result["error"])
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(self.repo), "show-ref", "--verify", "refs/heads/ai/replaced-repo-policy"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).returncode,
            0,
        )

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
            "allow_current_branch_write": True,
            "push_enabled_repos": ["repo"],
        }

        task = app._prepare_transaction_worker_task(cfg, registry, request)
        cfg["commands"]["repo"]["test"][2] = "print('mutated')"
        cfg["safe_branch_prefix"] = "changed/"
        cfg["allow_commit"] = False
        cfg["allow_current_branch_write"] = False
        cfg["push_enabled_repos"].clear()

        self.assertEqual(
            json.loads(task.commands_json),
            {"test": ["python3", "-c", "print('frozen')"]},
        )
        self.assertEqual(task.safe_branch_prefix, "ai/")
        self.assertTrue(task.allow_commit)
        self.assertTrue(task.allow_push)
        self.assertTrue(task.allow_current_branch_write)

    def test_b3_worker_task_rejects_repo_removed_from_latest_roots(self):
        txid = "tx-b3-root-revoked"
        downloaded = app._DownloadedRequest(
            filename=f"{txid}.json",
            raw=json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": "repo",
                "base_sha": self._seed_head,
                "branch": "ai/b3-root-revoked",
                "patch": "",
                "run": [],
            }),
            request_bytes_sha256="00" * 32,
            download_s=0.0,
            download_transport="fake",
        )
        request = app._ValidatedRequest(downloaded=downloaded, obj=json.loads(downloaded.raw))
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        revoked = {**self.cfg, "roots": []}
        with self.assertRaisesRegex(app.BridgeError, "no longer under a configured root"):
            app._prepare_transaction_worker_task(revoked, registry, request)

    def test_b3_worker_task_uses_latest_command_and_push_policy(self):
        txid = "tx-b3-latest-policy"
        downloaded = app._DownloadedRequest(
            filename=f"{txid}.json",
            raw=json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": "repo",
                "base_sha": self._seed_head,
                "branch": "ai/b3-latest-policy",
                "patch": "",
                "run": [],
            }),
            request_bytes_sha256="00" * 32,
            download_s=0.0,
            download_transport="fake",
        )
        request = app._ValidatedRequest(downloaded=downloaded, obj=json.loads(downloaded.raw))
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        latest = {
            **self.cfg,
            "commands": {"repo": {"test": ["python3", "-c", "print('latest')"]}},
            "roots": [{"path": str(self.tmp), "push": True}],
            "version": 2,
            "repo_overrides": {},
        }
        task = app._prepare_transaction_worker_task(latest, registry, request)
        self.assertEqual(json.loads(task.commands_json)["test"][-1], "print('latest')")
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

    def test_c2_same_repo_burst_does_not_block_independent_repository(self):
        repo_b = self.tmp / "repo-b-fair"
        shutil.copytree(self._seed_repo, repo_b, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo-b"] = {
            **registry["repos"]["repo"],
            "id": "repo-b",
            "name": "repo-b",
            "path": str(repo_b),
        }
        save_json(app.REGISTRY_FILE, registry)

        specs = (
            ("tx-c2-01-a1", "repo"),
            ("tx-c2-02-a2", "repo"),
            ("tx-c2-03-b1", "repo-b"),
        )
        for txid, repo_id in specs:
            self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": repo_id,
                "base_sha": self._seed_head,
                "branch": f"ai/{txid}",
                "patch": "",
                "run": [],
            }) + "\n"

        b_started = threading.Event()
        release_a1 = threading.Event()
        a2_started_early = threading.Event()
        observed: list[str] = []
        lock = threading.Lock()

        def fake_process(_repo_path, repo_id, obj, **_kwargs):
            txid = obj["transaction_id"]
            with lock:
                observed.append(txid)
            if txid == "tx-c2-01-a1":
                self.assertTrue(b_started.wait(2.0), "independent repo B was head-of-line blocked")
                release_a1.wait(2.0)
            elif txid == "tx-c2-02-a2":
                if not release_a1.is_set():
                    a2_started_early.set()
            elif txid == "tx-c2-03-b1":
                b_started.set()
                release_a1.set()
            return Mock(
                result={
                    "protocol": 2,
                    "kind": "result",
                    "transaction_id": txid,
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
                self.assertEqual(app.process_pending_once(self.cfg, scheduler=scheduler), 3)
        finally:
            release_a1.set()
            scheduler.shutdown(cancel_running=True)

        self.assertFalse(a2_started_early.is_set())
        self.assertLess(observed.index("tx-c2-03-b1"), observed.index("tx-c2-02-a2"))
        for txid, _repo_id in specs:
            self.assertEqual(json.loads(self.fake.files[f"v2/results/{txid}.json"])["status"], "success")

    def test_c2_live_arrival_during_active_job_uses_idle_worker_without_same_repo_overlap(self):
        """A later mailbox poll must admit repo B while repo A1 is still running."""
        repo_b = self.tmp / "repo-b-live-arrival"
        shutil.copytree(self._seed_repo, repo_b, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo-b"] = {
            **registry["repos"]["repo"],
            "id": "repo-b",
            "name": "repo-b",
            "path": str(repo_b),
        }
        save_json(app.REGISTRY_FILE, registry)

        def request(txid: str, repo_id: str) -> str:
            return json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": repo_id,
                "base_sha": self._seed_head,
                "branch": f"ai/{txid}",
                "patch": "",
                "run": [],
            }) + "\n"

        # Only A1 exists in the initial mailbox snapshot. A2/B1 appear after A1
        # starts, matching the production defect found by the post-RC canary.
        self.fake.files["v2/transactions/tx-live-a1.json"] = request("tx-live-a1", "repo")
        a1_started = threading.Event()
        b1_started = threading.Event()
        release_a1 = threading.Event()
        a2_started_early = threading.Event()
        observed: list[str] = []
        lock = threading.Lock()

        def fake_process(_repo_path, repo_id, obj, **_kwargs):
            txid = obj["transaction_id"]
            with lock:
                observed.append(txid)
            if txid == "tx-live-a1":
                a1_started.set()
                self.assertTrue(b1_started.wait(2.0), "watcher did not discover live repo-B arrival")
                release_a1.wait(2.0)
            elif txid == "tx-live-a2":
                if not release_a1.is_set():
                    a2_started_early.set()
            elif txid == "tx-live-b1":
                b1_started.set()
                release_a1.set()
            return Mock(
                result={
                    "protocol": 2,
                    "kind": "result",
                    "transaction_id": txid,
                    "repo": repo_id,
                    "branch": obj["branch"],
                    "status": "success",
                    "processed_at": "test",
                },
                snapshot=None,
            )

        def inject_live_arrivals():
            self.assertTrue(a1_started.wait(2.0))
            self.fake.files["v2/transactions/tx-live-a2.json"] = request("tx-live-a2", "repo")
            self.fake.files["v2/transactions/tx-live-b1.json"] = request("tx-live-b1", "repo-b")

        cfg = dict(self.cfg)
        cfg["max_workers"] = 2
        cfg["max_pending_jobs"] = 8
        cfg["poll_interval"] = 0.5
        injector = threading.Thread(target=inject_live_arrivals)
        injector.start()
        scheduler = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2)
        scheduler.start()
        try:
            with patch("llm_git_bridge.app.process_transaction", side_effect=fake_process):
                self.assertEqual(app.process_pending_once(cfg, scheduler=scheduler), 3)
        finally:
            release_a1.set()
            injector.join(2.0)
            scheduler.shutdown(cancel_running=True)

        self.assertFalse(a2_started_early.is_set())
        self.assertLess(observed.index("tx-live-b1"), observed.index("tx-live-a2"))
        for txid in ("tx-live-a1", "tx-live-a2", "tx-live-b1"):
            self.assertEqual(json.loads(self.fake.files[f"v2/results/{txid}.json"])["status"], "success")

    def test_c2_transient_live_poll_failure_preserves_pending_and_recovers_discovery(self):
        """A live-list failure must not discard queued work or drain active workers."""
        repo_b = self.tmp / "repo-b-live-poll-retry"
        shutil.copytree(self._seed_repo, repo_b, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo-b"] = {
            **registry["repos"]["repo"],
            "id": "repo-b",
            "name": "repo-b",
            "path": str(repo_b),
        }
        save_json(app.REGISTRY_FILE, registry)

        def request(txid: str, repo_id: str) -> str:
            return json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": repo_id,
                "base_sha": self._seed_head,
                "branch": f"ai/{txid}",
                "patch": "",
                "run": [],
            }) + "\n"

        self.fake.files["v2/transactions/tx-live-retry-a1.json"] = request(
            "tx-live-retry-a1", "repo"
        )
        a1_started = threading.Event()
        b1_started = threading.Event()
        release_a1 = threading.Event()
        a2_started_early = threading.Event()
        observed: list[str] = []
        lock = threading.Lock()

        def fake_process(_repo_path, repo_id, obj, **_kwargs):
            txid = obj["transaction_id"]
            with lock:
                observed.append(txid)
            if txid == "tx-live-retry-a1":
                a1_started.set()
                self.assertTrue(b1_started.wait(5.0), "live repoll did not recover after list failure")
                release_a1.wait(2.0)
            elif txid == "tx-live-retry-a2":
                if not release_a1.is_set():
                    a2_started_early.set()
            elif txid == "tx-live-retry-b1":
                b1_started.set()
                release_a1.set()
            return Mock(
                result={
                    "protocol": 2,
                    "kind": "result",
                    "transaction_id": txid,
                    "repo": repo_id,
                    "branch": obj["branch"],
                    "status": "success",
                    "processed_at": "test",
                },
                snapshot=None,
            )

        real_list_entries = self.fake.list_entries
        list_calls = 0

        def flaky_live_list(rel: str):
            nonlocal list_calls
            if rel != "v2/transactions":
                return real_list_entries(rel)
            list_calls += 1
            if list_calls == 2:
                self.fake.files["v2/transactions/tx-live-retry-a2.json"] = request(
                    "tx-live-retry-a2", "repo"
                )
            elif list_calls == 3:
                self.fake.files["v2/transactions/tx-live-retry-b1.json"] = request(
                    "tx-live-retry-b1", "repo-b"
                )
                raise BridgeError("temporary live mailbox list failure")
            return real_list_entries(rel)

        cfg = dict(self.cfg)
        cfg["max_workers"] = 2
        cfg["max_pending_jobs"] = 8
        cfg["poll_interval"] = 0.5
        scheduler = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2)
        scheduler.start()
        try:
            with patch.object(self.fake, "list_entries", side_effect=flaky_live_list):
                with patch("llm_git_bridge.app.process_transaction", side_effect=fake_process):
                    self.assertEqual(app.process_pending_once(cfg, scheduler=scheduler), 3)
        finally:
            release_a1.set()
            scheduler.shutdown(cancel_running=True)

        self.assertGreaterEqual(list_calls, 4)
        self.assertFalse(a2_started_early.is_set())
        self.assertLess(observed.index("tx-live-retry-b1"), observed.index("tx-live-retry-a2"))
        scheduler_events = [
            item for item in app._recent_metrics(50)
            if item.get("event", "").startswith("scheduler-")
        ]
        self.assertTrue(any(item.get("event") == "scheduler-live-poll-error" for item in scheduler_events))
        tx_events = [item for item in scheduler_events if item.get("event") in {
            "scheduler-enqueue", "scheduler-dispatch", "scheduler-finish"
        }]
        self.assertTrue(tx_events)
        self.assertTrue(all(item.get("transaction_id") for item in tx_events))

    def test_c2_result_publication_failure_preserves_pending_and_does_not_drain_workers(self):
        """A durable result publication failure must not discard queued repo work."""
        repo_b = self.tmp / "repo-b-publication-retry"
        shutil.copytree(self._seed_repo, repo_b, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo-b"] = {
            **registry["repos"]["repo"],
            "id": "repo-b",
            "name": "repo-b",
            "path": str(repo_b),
        }
        save_json(app.REGISTRY_FILE, registry)

        def request(txid: str, repo_id: str) -> str:
            return json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": repo_id,
                "base_sha": self._seed_head,
                "branch": f"ai/{txid}",
                "patch": "",
                "run": [],
            }) + "\n"

        self.fake.files["v2/transactions/tx-publish-a1.json"] = request(
            "tx-publish-a1", "repo"
        )
        a1_started = threading.Event()
        a1_finished = threading.Event()
        b1_started = threading.Event()
        publication_failed = threading.Event()
        a2_started_early = threading.Event()
        observed: list[str] = []
        lock = threading.Lock()

        def fake_process(_repo_path, repo_id, obj, **_kwargs):
            txid = obj["transaction_id"]
            with lock:
                observed.append(txid)
            if txid == "tx-publish-a1":
                a1_started.set()
                self.assertTrue(
                    publication_failed.wait(5.0),
                    "independent B1 did not reach its publication failure while A1 was active",
                )
                a1_finished.set()
            elif txid == "tx-publish-a2":
                if not a1_finished.is_set():
                    a2_started_early.set()
            elif txid == "tx-publish-b1":
                b1_started.set()
            return Mock(
                result={
                    "protocol": 2,
                    "kind": "result",
                    "transaction_id": txid,
                    "repo": repo_id,
                    "branch": obj["branch"],
                    "status": "success",
                    "processed_at": "test",
                },
                snapshot=None,
            )

        def inject_live_arrivals():
            self.assertTrue(a1_started.wait(2.0))
            self.fake.files["v2/transactions/tx-publish-a2.json"] = request(
                "tx-publish-a2", "repo"
            )
            self.fake.files["v2/transactions/tx-publish-b1.json"] = request(
                "tx-publish-b1", "repo-b"
            )

        real_publish = app._publish_and_cleanup_result
        failed_once = False

        def fail_b1_publication(transport, filename, result, **kwargs):
            nonlocal failed_once
            if filename == "tx-publish-b1.json" and not failed_once:
                failed_once = True
                publication_failed.set()
                raise BridgeError("simulated durable result publication failure")
            return real_publish(transport, filename, result, **kwargs)

        cfg = dict(self.cfg)
        cfg["max_workers"] = 2
        cfg["max_pending_jobs"] = 8
        cfg["poll_interval"] = 0.5
        injector = threading.Thread(target=inject_live_arrivals)
        injector.start()
        scheduler = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2)
        scheduler.start()
        try:
            with patch("llm_git_bridge.app.process_transaction", side_effect=fake_process), patch(
                "llm_git_bridge.app._publish_and_cleanup_result",
                side_effect=fail_b1_publication,
            ):
                # A1 and A2 publish normally; B1 remains a durable local result for retry.
                self.assertEqual(app.process_pending_once(cfg, scheduler=scheduler), 2)
        finally:
            injector.join(2.0)
            scheduler.shutdown(cancel_running=True)

        self.assertTrue(failed_once)
        self.assertTrue(b1_started.is_set())
        self.assertFalse(a2_started_early.is_set())
        self.assertLess(observed.index("tx-publish-b1"), observed.index("tx-publish-a2"))
        local_b1 = app.STATE_DIR / "results" / "tx-publish-b1.json"
        self.assertTrue(local_b1.exists())
        self.assertIn("v2/transactions/tx-publish-b1.json", self.fake.files)
        scheduler_events = [
            item for item in app._recent_metrics(100)
            if item.get("event", "").startswith("scheduler-")
        ]
        self.assertTrue(any(
            item.get("event") == "scheduler-publication-error"
            and item.get("transaction_id") == "tx-publish-b1"
            for item in scheduler_events
        ))

        # Recovery must publish the durable result without re-executing B1.
        old = time.time() - app.RESULT_RETRY_GRACE_S - 1.0
        os.utime(local_b1, (old, old))

        def must_not_execute(_task, _cancel):
            raise AssertionError("durable publication recovery re-executed a mutation")

        retry = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2, worker_fn=must_not_execute)
        retry.start()
        try:
            self.assertEqual(app.process_pending_once(cfg, scheduler=retry), 1)
        finally:
            retry.shutdown(cancel_running=True)
        self.assertIn("v2/results/tx-publish-b1.json", self.fake.files)
        self.assertNotIn("v2/transactions/tx-publish-b1.json", self.fake.files)

    def test_g_scheduler_metrics_expose_bounded_public_operational_fields(self):
        self.cfg["max_workers"] = 2
        txid = "tx-g-metrics"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "repo",
            "base_sha": self._seed_head,
            "branch": "ai/g-metrics",
            "patch": "",
            "run": [],
        }) + "\n"
        scheduler = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2)
        scheduler.start()
        try:
            with patch("llm_git_bridge.app.process_transaction") as process:
                process.return_value = Mock(
                    result={"protocol": 2, "kind": "result", "transaction_id": txid,
                            "repo": "repo", "branch": "ai/g-metrics", "status": "success",
                            "processed_at": "test"},
                    snapshot=None,
                )
                self.assertEqual(app.process_pending_once(self.cfg, scheduler=scheduler), 1)
        finally:
            scheduler.shutdown(cancel_running=True)
        metrics = app._recent_metrics(20)
        scheduler_events = [m for m in metrics if m.get("event", "").startswith("scheduler-")]
        self.assertTrue(scheduler_events)
        allowed = app._METRIC_PUBLIC_KEYS
        self.assertTrue(all(set(m) <= allowed for m in scheduler_events))
        self.assertTrue(any("queue_wait_s" in m for m in scheduler_events))
        self.assertTrue(any("execution_s" in m for m in scheduler_events))

    def test_e_worker_failure_isolated_from_concurrent_repository(self):
        repo_b = self.tmp / "repo-b-error-isolation"
        shutil.copytree(self._seed_repo, repo_b, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo-b"] = {
            **registry["repos"]["repo"], "id": "repo-b", "name": "repo-b", "path": str(repo_b)
        }
        save_json(app.REGISTRY_FILE, registry)
        for suffix, repo_id in (("a", "repo"), ("b", "repo-b")):
            txid = f"tx-e-isolation-{suffix}"
            self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
                "protocol": 2, "kind": "transaction", "transaction_id": txid,
                "repo": repo_id, "base_sha": self._seed_head,
                "branch": f"ai/e-isolation-{suffix}", "patch": "", "run": [],
            }) + "\n"
        barrier = threading.Barrier(2)

        def fake_process(_repo_path, repo_id, obj, **_kwargs):
            barrier.wait(2.0)
            if repo_id == "repo":
                raise BridgeError("isolated worker failure")
            return Mock(result={
                "protocol": 2, "kind": "result", "transaction_id": obj["transaction_id"],
                "repo": repo_id, "branch": obj["branch"], "status": "success", "processed_at": "test",
            }, snapshot=None)

        scheduler = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2)
        scheduler.start()
        try:
            with patch("llm_git_bridge.app.process_transaction", side_effect=fake_process):
                self.assertEqual(app.process_pending_once(self.cfg, scheduler=scheduler), 2)
        finally:
            scheduler.shutdown(cancel_running=True)
        failed = json.loads(self.fake.files["v2/results/tx-e-isolation-a.json"])
        succeeded = json.loads(self.fake.files["v2/results/tx-e-isolation-b.json"])
        self.assertEqual(failed["status"], "error")
        self.assertIn("isolated worker failure", failed["error"])
        self.assertEqual(succeeded["status"], "success")

    def test_e_concurrent_publication_failure_recovers_from_durable_results_without_reexecution(self):
        repo_b = self.tmp / "repo-b-durable-recovery"
        shutil.copytree(self._seed_repo, repo_b, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo-b"] = {
            **registry["repos"]["repo"], "id": "repo-b", "name": "repo-b", "path": str(repo_b)
        }
        save_json(app.REGISTRY_FILE, registry)
        txids = ("tx-e-recover-a", "tx-e-recover-b")
        for txid, repo_id in zip(txids, ("repo", "repo-b")):
            self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
                "protocol": 2, "kind": "transaction", "transaction_id": txid,
                "repo": repo_id, "base_sha": self._seed_head,
                "branch": f"ai/{txid}", "patch": "", "run": [],
            }) + "\n"
        barrier = threading.Barrier(2)
        executed: list[str] = []

        def worker(task, _cancel):
            executed.append(task.filename[:-5])
            barrier.wait(2.0)
            return self._b2_dummy_worker_outcome(task.filename[:-5], repo_id=task.repo_id)

        first = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2, worker_fn=worker)
        first.start()
        try:
            with patch("llm_git_bridge.app._publish_and_cleanup_result", side_effect=BridgeError("transport down")):
                self.assertEqual(app.process_pending_once(self.cfg, scheduler=first), 0)
        finally:
            first.shutdown(cancel_running=True)
        self.assertCountEqual(executed, list(txids))
        publication_errors = [
            item for item in app._recent_metrics(50)
            if item.get("event") == "scheduler-publication-error"
        ]
        self.assertCountEqual(
            [item.get("transaction_id") for item in publication_errors],
            list(txids),
        )
        for txid in txids:
            local_result = app.STATE_DIR / "results" / f"{txid}.json"
            self.assertTrue(local_result.exists())
            old = time.time() - app.RESULT_RETRY_GRACE_S - 1.0
            os.utime(local_result, (old, old))

        def must_not_execute(_task, _cancel):
            raise AssertionError("durable recovery re-executed local mutation")

        second = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2, worker_fn=must_not_execute)
        second.start()
        try:
            self.assertEqual(app.process_pending_once(self.cfg, scheduler=second), 2)
        finally:
            second.shutdown(cancel_running=True)
        for txid in txids:
            self.assertIn(f"v2/results/{txid}.json", self.fake.files)
            self.assertNotIn(f"v2/transactions/{txid}.json", self.fake.files)

    def test_h_bounded_scale_soak_across_ten_repositories(self):
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        repo_ids = ["repo"]
        for index in range(1, 10):
            repo_id = f"repo-{index}"
            repo_path = self.tmp / repo_id
            shutil.copytree(self._seed_repo, repo_path, symlinks=True)
            registry["repos"][repo_id] = {
                **registry["repos"]["repo"], "id": repo_id, "name": repo_id, "path": str(repo_path)
            }
            repo_ids.append(repo_id)
        save_json(app.REGISTRY_FILE, registry)
        txids: list[str] = []
        for round_no in range(2):
            for index, repo_id in enumerate(repo_ids):
                txid = f"tx-h-{round_no}-{index:02d}"
                txids.append(txid)
                self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
                    "protocol": 2, "kind": "transaction", "transaction_id": txid,
                    "repo": repo_id, "base_sha": self._seed_head,
                    "branch": f"ai/{txid}", "patch": "", "run": [],
                }) + "\n"
        active_repos: set[str] = set()
        overlap_violation: list[str] = []
        peak = 0
        lock = threading.Lock()

        def fake_process(_repo_path, repo_id, obj, **_kwargs):
            nonlocal peak
            with lock:
                if repo_id in active_repos:
                    overlap_violation.append(repo_id)
                active_repos.add(repo_id)
                peak = max(peak, len(active_repos))
            time.sleep(0.005)
            with lock:
                active_repos.remove(repo_id)
            return Mock(result={
                "protocol": 2, "kind": "result", "transaction_id": obj["transaction_id"],
                "repo": repo_id, "branch": obj["branch"], "status": "success", "processed_at": "test",
            }, snapshot=None)

        cfg = dict(self.cfg)
        cfg["max_workers"] = 4
        cfg["max_pending_jobs"] = 8
        scheduler = app._LocalWorkerScheduler(max_workers=4, queue_capacity=4)
        scheduler.start()
        metrics: list[dict[str, object]] = []
        try:
            with patch("llm_git_bridge.app.process_transaction", side_effect=fake_process), patch(
                "llm_git_bridge.app._append_metric", side_effect=lambda event: metrics.append(dict(event))
            ):
                self.assertEqual(app.process_pending_once(cfg, scheduler=scheduler), len(txids))
        finally:
            scheduler.shutdown(cancel_running=True)
        self.assertFalse(overlap_violation)
        self.assertGreaterEqual(peak, 2)
        self.assertLessEqual(peak, 4)
        queue_depths = [int(m["queue_depth"]) for m in metrics if "queue_depth" in m]
        self.assertTrue(queue_depths)
        self.assertLessEqual(max(queue_depths), cfg["max_pending_jobs"])
        for txid in txids:
            self.assertEqual(json.loads(self.fake.files[f"v2/results/{txid}.json"])["status"], "success")

    def test_c3_two_worker_external_process_canary_reduces_makespan(self):
        def task(txid: str, key: str) -> app._TransactionWorkerTask:
            return app._TransactionWorkerTask(
                filename=f"{txid}.json", request_raw="{}", repo_id=key,
                repo_path=self.tmp / key, scheduling_key=key, commands_json="{}",
                safe_branch_prefix="ai/", allow_commit=True, allow_push=False,
            )

        def external_wait_worker(task_obj, _cancel):
            subprocess.run(
                [sys.executable, "-c", "import time; time.sleep(0.20)"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return self._b2_dummy_worker_outcome(task_obj.filename[:-5], repo_id=task_obj.repo_id)

        serial = app._LocalWorkerScheduler(max_workers=1, queue_capacity=2, worker_fn=external_wait_worker)
        serial.start()
        started = time.monotonic()
        try:
            serial.wait(serial.submit(task("tx-c3-serial-a", "repo-a")))
            serial.wait(serial.submit(task("tx-c3-serial-b", "repo-b")))
        finally:
            serial.shutdown(cancel_running=True)
        serial_s = time.monotonic() - started

        concurrent = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2, worker_fn=external_wait_worker)
        concurrent.start()
        started = time.monotonic()
        try:
            first = concurrent.submit(task("tx-c3-concurrent-a", "repo-a"))
            second = concurrent.submit(task("tx-c3-concurrent-b", "repo-b"))
            concurrent.wait(first)
            concurrent.wait(second)
        finally:
            concurrent.shutdown(cancel_running=True)
        concurrent_s = time.monotonic() - started

        self.assertLess(concurrent_s, serial_s * 0.80, (serial_s, concurrent_s))

    def test_c1_durable_result_persistence_failure_reaps_all_inflight_workers(self):
        """Failure before the durable-result boundary must still unwind fail-closed."""
        repo_b = self.tmp / "repo-b-persistence-failure"
        shutil.copytree(self._seed_repo, repo_b, symlinks=True)
        registry = json.loads(app.REGISTRY_FILE.read_text(encoding="utf-8"))
        registry["repos"]["repo-b"] = {
            **registry["repos"]["repo"],
            "id": "repo-b",
            "name": "repo-b",
            "path": str(repo_b),
        }
        save_json(app.REGISTRY_FILE, registry)

        txids = ("tx-c1-persist-fail-a", "tx-c1-persist-fail-b")
        for txid, repo_id in zip(txids, ("repo", "repo-b")):
            self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
                "protocol": 2,
                "kind": "transaction",
                "transaction_id": txid,
                "repo": repo_id,
                "base_sha": self._seed_head,
                "branch": f"ai/{txid}",
                "patch": "",
                "run": [],
            }) + "\n"

        barrier = threading.Barrier(2)

        def worker(task, _cancel):
            barrier.wait(2.0)
            return self._b2_dummy_worker_outcome(task.filename[:-5], repo_id=task.repo_id)

        real_persist = app._persist_signed_result
        persist_calls = 0

        def fail_first_persistence(filename, result):
            nonlocal persist_calls
            persist_calls += 1
            if persist_calls == 1:
                raise BridgeError("simulated durable result persistence failure")
            return real_persist(filename, result)

        scheduler = app._LocalWorkerScheduler(max_workers=2, queue_capacity=2, worker_fn=worker)
        scheduler.start()
        try:
            with patch(
                "llm_git_bridge.app._persist_signed_result",
                side_effect=fail_first_persistence,
            ):
                with self.assertRaisesRegex(BridgeError, "simulated durable result persistence failure"):
                    app.process_pending_once(self.cfg, scheduler=scheduler)
            self.assertEqual(persist_calls, 2)
            self.assertEqual(scheduler.active_count(), 0)
            self.assertEqual(scheduler.active_repo_keys(), frozenset())
            self.assertEqual(scheduler.active_transaction_ids(), frozenset())
            durable = [
                (app.STATE_DIR / "results" / f"{txid}.json").exists()
                for txid in txids
            ]
            self.assertEqual(sum(durable), 1)
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
        cfg = app.default_config()
        self.assertEqual(cfg["version"], 2)
        self.assertEqual(cfg["roots"], [])
        self.assertEqual(cfg["repo_overrides"], {})

    def test_v1_config_migration_preserves_push_without_broadening_root_policy(self):
        temp = Path(tempfile.mkdtemp(prefix="llmgb-config-migrate-"))
        old_config_file = app.CONFIG_FILE
        app.CONFIG_FILE = temp / "config.json"
        self.addCleanup(setattr, app, "CONFIG_FILE", old_config_file)
        save_json(app.CONFIG_FILE, {
            **app.default_config(),
            "version": 1,
            "transport": {"type": "rclone", "remote": "fake", "rc_enabled": True},
            "roots": [str(temp)],
            "push_enabled_repos": ["repo-b", "repo-a", "repo-a"],
            "repo_overrides": {},
        })

        loaded = app.load_config()

        self.assertEqual(loaded["version"], 2)
        self.assertEqual(loaded["roots"], [{"path": str(temp), "push": False}])
        self.assertEqual(
            loaded["repo_overrides"],
            {"repo-a": {"push": True}, "repo-b": {"push": True}},
        )
        self.assertTrue(app._repo_push_allowed(loaded, "repo-a", temp / "repo-a"))
        self.assertFalse(app._repo_push_allowed(loaded, "new-repo", temp / "new-repo"))

    def test_v2_config_rejects_unknown_top_level_and_transport_fields(self):
        cfg = app.default_config()
        cfg["typo"] = True
        with self.assertRaisesRegex(BridgeError, "unknown fields: typo"):
            app._validate_config(cfg)

        cfg = app.default_config()
        cfg["transport"]["typo"] = True
        with self.assertRaisesRegex(BridgeError, "transport has unknown fields: typo"):
            app._validate_config(cfg)

    def test_config_version_rejects_bool_and_float_aliases(self):
        for version in (True, False, 1.0, 2.0):
            with self.subTest(version=version):
                raw = app.default_config()
                raw["version"] = version
                with self.assertRaisesRegex(BridgeError, "unsupported config version"):
                    app._migrate_config(raw)

    def test_v1_migration_compacts_duplicate_and_redundant_nested_roots(self):
        temp = Path(tempfile.mkdtemp(prefix="llmgb-config-compact-"))
        outer = temp / "repos"
        nested = outer / "nested"
        raw = {
            "version": 1,
            "transport": {"type": "rclone", "remote": "fake", "rc_enabled": True},
            "roots": [str(outer), str(outer / "."), str(nested)],
            "push_enabled_repos": [],
        }
        migrated = app._migrate_config(raw)
        self.assertEqual(migrated["roots"], [{"path": str(outer.resolve()), "push": False}])

    def test_registry_publication_failure_persists_path_free_retry_payload(self):
        cfg = app.default_config()
        cfg["transport"]["remote"] = "fake"
        cfg["roots"] = [{"path": str(self.tmp), "push": False}]
        registry = app.load_registry()
        with patch.object(self.fake, "upload_json", side_effect=BridgeError("rclone rc write outcome is unknown")):
            with self.assertRaisesRegex(BridgeError, "outcome is unknown"):
                app.publish_registry(cfg, registry)
        pending = app._pending_registry_publication_path()
        self.assertTrue(pending.exists())
        payload = json.loads(pending.read_text(encoding="utf-8"))
        self.assertNotIn(str(self.tmp), json.dumps(payload))
        self.assertTrue(app._retry_pending_registry_publication(cfg))
        self.assertFalse(pending.exists())

    def test_policy_mutation_rolls_back_exact_local_config_when_publication_fails(self):
        old_cfg = app.default_config()
        old_cfg["transport"]["remote"] = "fake"
        old_cfg["roots"] = [{"path": str(self.tmp), "push": False}]
        app.save_config(old_cfg)
        before = app.CONFIG_FILE.read_text(encoding="utf-8")
        candidate = json.loads(json.dumps(old_cfg))
        candidate["roots"][0]["push"] = True
        with patch.object(self.fake, "upload_json", side_effect=BridgeError("definite publication failure")):
            with self.assertRaisesRegex(BridgeError, "definite publication failure"):
                app._save_config_and_refresh_registry(candidate)
        self.assertEqual(app.CONFIG_FILE.read_text(encoding="utf-8"), before)

    def test_effective_push_uses_repo_override_then_most_specific_root(self):
        temp = Path(tempfile.mkdtemp(prefix="llmgb-root-policy-"))
        outer = temp / "repos"
        nested = outer / "client"
        cfg = app.default_config()
        cfg["roots"] = [
            {"path": str(outer), "push": True},
            {"path": str(nested), "push": False},
        ]

        self.assertTrue(app._repo_push_allowed(cfg, "outer-repo", outer / "project"))
        self.assertFalse(app._repo_push_allowed(cfg, "nested-repo", nested / "project"))
        cfg["repo_overrides"]["nested-repo"] = {"push": True}
        self.assertTrue(app._repo_push_allowed(cfg, "nested-repo", nested / "project"))
        cfg["repo_overrides"]["outer-repo"] = {"push": False}
        self.assertFalse(app._repo_push_allowed(cfg, "outer-repo", outer / "project"))
        self.assertFalse(app._repo_push_allowed(cfg, "elsewhere", temp / "elsewhere"))

    def test_v2_config_rejects_and_never_honours_legacy_push_allowlist(self):
        cfg = app.default_config()
        cfg["push_enabled_repos"] = ["demo"]
        with self.assertRaisesRegex(BridgeError, "legacy push_enabled_repos"):
            app._validate_config(cfg)
        self.assertFalse(app._repo_push_allowed(cfg, "demo", self.tmp / "demo"))

    def test_legacy_in_memory_push_allowlist_remains_transitionally_supported(self):
        cfg = {"roots": [], "push_enabled_repos": ["demo"]}
        self.assertTrue(app._repo_push_allowed(cfg, "demo", self.tmp / "demo"))

    def test_v2_config_rejects_duplicate_canonical_roots_and_bad_policies(self):
        temp = Path(tempfile.mkdtemp(prefix="llmgb-root-policy-"))
        duplicate = app.default_config()
        duplicate["roots"] = [
            {"path": str(temp), "push": False},
            {"path": str(temp / "."), "push": True},
        ]
        with self.assertRaisesRegex(BridgeError, "duplicate canonical paths"):
            app._validate_config(duplicate)

        bad_root = app.default_config()
        bad_root["roots"] = [{"path": str(temp), "push": "yes"}]
        with self.assertRaisesRegex(BridgeError, "push policy"):
            app._validate_config(bad_root)

        bad_override = app.default_config()
        bad_override["repo_overrides"] = {"repo": {"push": True, "extra": False}}
        with self.assertRaisesRegex(BridgeError, "exactly one boolean push field"):
            app._validate_config(bad_override)

    def test_root_policy_compaction_preserves_meaningful_nested_override(self):
        temp = Path(tempfile.mkdtemp(prefix="llmgb-root-compact-"))
        outer = temp / "repos"
        middle = outer / "client"
        deep = middle / "private"
        roots = app._prune_redundant_roots([
            {"path": str(deep), "push": True},
            {"path": str(outer), "push": True},
            {"path": str(middle), "push": False},
            {"path": str(outer / "redundant"), "push": True},
        ])

        by_path = {root["path"]: root["push"] for root in roots}
        self.assertEqual(by_path[str(outer.resolve())], True)
        self.assertEqual(by_path[str(middle.resolve())], False)
        self.assertEqual(by_path[str(deep.resolve())], True)
        self.assertNotIn(str((outer / "redundant").resolve()), by_path)

    def test_roots_cli_manages_multiple_roots_and_avoids_redundant_nested_root(self):
        cfg = app.default_config()
        cfg["transport"]["remote"] = "fake"
        app.save_config(cfg)
        outer = self.tmp / "managed"
        nested = outer / "nested"
        other = self.tmp / "other"
        nested.mkdir(parents=True)
        other.mkdir()
        parser = app.build_parser()

        with patch("builtins.print"):
            self.assertEqual(
                app.cmd_roots_add(parser.parse_args(["roots", "add", str(outer), "--push", "enable"])),
                0,
            )
            self.assertEqual(
                app.cmd_roots_add(parser.parse_args(["roots", "add", str(nested)])),
                0,
            )
            self.assertEqual(
                app.cmd_roots_add(parser.parse_args(["roots", "add", str(other)])),
                0,
            )

        loaded = app.load_config()
        self.assertEqual(
            {root["path"]: root["push"] for root in loaded["roots"]},
            {str(outer.resolve()): True, str(other.resolve()): False},
        )

        with patch("builtins.print"):
            self.assertEqual(
                app.cmd_roots_add(parser.parse_args(["roots", "add", str(nested), "--push", "disable"])),
                0,
            )
        loaded = app.load_config()
        self.assertEqual(
            {root["path"]: root["push"] for root in loaded["roots"]}[str(nested.resolve())],
            False,
        )

        with patch("builtins.print"):
            self.assertEqual(
                app.cmd_roots_remove(parser.parse_args(["roots", "remove", str(nested)])),
                0,
            )
        self.assertNotIn(
            str(nested.resolve()),
            {root["path"] for root in app.load_config()["roots"]},
        )

    def test_configure_push_parser_supports_returning_to_root_inheritance(self):
        args = app.build_parser().parse_args(["configure-push", "demo", "inherit"])
        self.assertEqual(args.action, "inherit")

    def test_configure_push_inherit_removes_override_and_republishes_capability(self):
        cfg = app.default_config()
        cfg["transport"]["remote"] = "fake"
        cfg["roots"] = [{"path": str(self.tmp), "push": True}]
        cfg["repo_overrides"] = {"repo": {"push": False}}
        app.save_config(cfg)
        args = app.build_parser().parse_args(["configure-push", "repo", "inherit"])

        with patch("builtins.print"):
            self.assertEqual(app.cmd_configure_push(args), 0)

        loaded = app.load_config()
        self.assertNotIn("repo", loaded["repo_overrides"])
        public = json.loads(self.fake.files["v2/meta/repos.json"])
        entry = next(item for item in public["repos"] if item["id"] == "repo")
        self.assertEqual(
            entry["capabilities"],
            {"read": True, "edit": True, "push": True},
        )

    def test_configure_current_branch_write_is_default_off_and_republishes_capability(self):
        cfg = app.default_config()
        self.assertFalse(cfg["allow_current_branch_write"])
        cfg["transport"]["remote"] = "fake"
        cfg["roots"] = [{"path": str(self.tmp), "push": True}]
        app.save_config(cfg)

        args = app.build_parser().parse_args(["configure-current-branch-write", "enable"])
        with patch("builtins.print"):
            self.assertEqual(app.cmd_configure_current_branch_write(args), 0)

        loaded = app.load_config()
        self.assertTrue(loaded["allow_current_branch_write"])
        public = json.loads(self.fake.files["v2/meta/repos.json"])
        entry = next(item for item in public["repos"] if item["id"] == "repo")
        self.assertEqual(
            entry["capabilities"],
            {
                "read": True,
                "edit": True,
                "push": True,
                "write_current_branch": True,
            },
        )

        args = app.build_parser().parse_args(["configure-current-branch-write", "disable"])
        with patch("builtins.print"):
            self.assertEqual(app.cmd_configure_current_branch_write(args), 0)
        self.assertFalse(app.load_config()["allow_current_branch_write"])

    def test_setup_does_not_persist_new_remote_before_transport_is_reachable(self):
        cfg = app.default_config()
        cfg["transport"]["remote"] = "old-remote"
        cfg["roots"] = [{"path": str(self.tmp), "push": False}]
        app.save_config(cfg)
        before = app.CONFIG_FILE.read_bytes()
        args = app.build_parser().parse_args([
            "setup", "--non-interactive", "--remote", "new-remote"
        ])

        with patch.object(self.fake, "ensure_dir", side_effect=BridgeError("remote unavailable")):
            with self.assertRaisesRegex(BridgeError, "remote unavailable"):
                app.cmd_setup(args)

        self.assertEqual(app.CONFIG_FILE.read_bytes(), before)
        self.assertEqual(app.load_config()["transport"]["remote"], "old-remote")

    def test_interactive_setup_configures_multiple_roots_and_push_policies(self):
        other_root = self.tmp / "other-root"
        other_root.mkdir()
        shutil.copytree(self._seed_repo, other_root / "other-repo", symlinks=True)
        args = app.build_parser().parse_args(["setup", "--remote", "fake"])
        answers = iter([
            str(self.tmp),
            "yes",
            "yes",
            str(other_root),
            "no",
            "no",
        ])

        with patch("llm_git_bridge.app._setup_is_interactive", return_value=True), patch(
            "builtins.input", side_effect=lambda _prompt: next(answers)
        ), patch("builtins.print"):
            self.assertEqual(app.cmd_setup(args), 0)

        cfg = app.load_config()
        roots = {root["path"]: root["push"] for root in cfg["roots"]}
        self.assertEqual(roots[str(self.tmp.resolve())], True)
        # other_root is nested beneath self.tmp and deliberately changes policy,
        # so it remains as a meaningful most-specific override.
        self.assertEqual(roots[str(other_root.resolve())], False)
        registry = app.load_registry()
        names = {entry["name"] for entry in registry["repos"].values()}
        self.assertIn("repo", names)
        self.assertIn("other-repo", names)

    def test_interactive_setup_rerun_can_keep_existing_roots_without_reentry(self):
        cfg = app.default_config()
        cfg["transport"]["remote"] = "fake"
        cfg["roots"] = [{"path": str(self.tmp), "push": True}]
        app.save_config(cfg)
        args = app.build_parser().parse_args(["setup"])
        answers = iter(["yes", "no", "no"])

        with patch("llm_git_bridge.app._setup_is_interactive", return_value=True), patch(
            "builtins.input", side_effect=lambda _prompt: next(answers)
        ), patch("builtins.print"):
            self.assertEqual(app.cmd_setup(args), 0)

        self.assertEqual(
            app.load_config()["roots"],
            [{"path": str(self.tmp.resolve()), "push": True}],
        )

    def test_interactive_setup_rerun_can_change_existing_root_push_policy(self):
        cfg = app.default_config()
        cfg["transport"]["remote"] = "fake"
        cfg["roots"] = [{"path": str(self.tmp), "push": True}]
        app.save_config(cfg)
        args = app.build_parser().parse_args(["setup"])
        answers = iter(["yes", "yes", "no", "no"])

        with patch("llm_git_bridge.app._setup_is_interactive", return_value=True), patch(
            "builtins.input", side_effect=lambda _prompt: next(answers)
        ), patch("builtins.print"):
            self.assertEqual(app.cmd_setup(args), 0)

        self.assertEqual(
            app.load_config()["roots"],
            [{"path": str(self.tmp.resolve()), "push": False}],
        )
        public = json.loads(self.fake.files["v2/meta/repos.json"])
        entry = next(item for item in public["repos"] if item["name"] == "repo")
        self.assertFalse(entry["capabilities"]["push"])

    def test_interactive_setup_allows_zero_repository_roots(self):
        cfg = app.default_config()
        with patch("builtins.input", return_value=""), patch("builtins.print"):
            roots = app._setup_repository_roots(cfg)
        self.assertEqual(roots, [])

    def test_interactive_setup_reports_eof_as_actionable_error(self):
        with patch("builtins.input", side_effect=EOFError):
            with self.assertRaisesRegex(BridgeError, "rerun setup in a terminal"):
                app._setup_input("Repository folder: ")

    def test_setup_noninteractive_preserves_roots_and_does_not_prompt(self):
        cfg = app.default_config()
        cfg["transport"]["remote"] = "fake"
        cfg["roots"] = [{"path": str(self.tmp), "push": True}]
        app.save_config(cfg)
        args = app.build_parser().parse_args(["setup", "--non-interactive"])

        with patch("builtins.input") as mocked_input, patch("builtins.print"):
            self.assertEqual(app.cmd_setup(args), 0)
        mocked_input.assert_not_called()
        self.assertEqual(app.load_config()["roots"], cfg["roots"])

    def test_saving_migrated_v1_config_preserves_one_private_backup(self):
        legacy = {
            **app.default_config(),
            "version": 1,
            "roots": [str(self.tmp)],
            "push_enabled_repos": ["repo"],
            "repo_overrides": {},
        }
        save_json(app.CONFIG_FILE, legacy)

        migrated = app.load_config()
        app.save_config(migrated)

        backup = app.CONFIG_FILE.with_name("config.v1-backup.json")
        self.assertTrue(backup.exists())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8"))["version"], 1)
        self.assertEqual(json.loads(app.CONFIG_FILE.read_text(encoding="utf-8"))["version"], 2)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        original_backup = backup.read_text(encoding="utf-8")

        migrated["poll_interval"] = 2.0
        app.save_config(migrated)
        self.assertEqual(backup.read_text(encoding="utf-8"), original_backup)

    def test_setup_readiness_is_platform_specific_without_side_effects(self):
        with patch("llm_git_bridge.app.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"), patch(
            "llm_git_bridge.app.sys.platform", "darwin"
        ), patch("llm_git_bridge.app.PLIST_PATH", self.tmp / "missing.plist"), patch("builtins.print") as mocked:
            app._print_setup_readiness()
        output = "\n".join(str(call.args[0]) for call in mocked.call_args_list)
        self.assertIn("git: available", output)
        self.assertIn("rclone: available", output)
        self.assertIn("automatic watcher: not installed", output)

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
                raise OSError("simulated marker persistence failure")
            return original_mark(name, source=source, request_bytes_sha256=request_bytes_sha256)

        with patch("llm_git_bridge.app._process_doctor_request", return_value={
            "protocol": 2, "kind": "result", "transaction_id": txid,
            "status": "success", "processed_at": "test", "operation": "doctor", "doctor": {},
        }) as doctor:
            with patch.object(app, "_mark_published", side_effect=fail_once):
                with self.assertRaisesRegex(BridgeError, "post-durable result publication/cleanup failed"):
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

    def test_mailbox_scope_is_created_bound_and_reused(self):
        scope = app._ensure_mailbox_scope(self.fake)
        self.assertRegex(scope, r"^[0-9a-f]{64}$")
        self.assertEqual(app._bound_mailbox_scope(), scope)
        remote = json.loads(self.fake.files["v2/meta/mailbox-scope.json"])
        self.assertEqual(app._validated_mailbox_scope_document(remote), scope)
        self.assertEqual(app._ensure_mailbox_scope(self.fake), scope)

    def test_mailbox_retarget_is_rejected_after_scope_binding(self):
        scope = app._ensure_mailbox_scope(self.fake)
        self.assertEqual(app._bound_mailbox_scope(), scope)
        self.fake.files.pop("v2/meta/mailbox-scope.json")
        with self.assertRaisesRegex(BridgeError, "bound replay scope"):
            app._ensure_mailbox_scope(self.fake)

    def test_legacy_publication_marker_migrates_only_after_mailbox_binding(self):
        filename = "tx-legacy-scope.json"
        save_json(app._published_marker(filename), {
            "filename": filename,
            "published_at": "test",
            "source": "legacy",
        })
        legacy = app._load_published_marker(filename)
        self.assertNotIn("mailbox_scope", legacy)
        scope = app._ensure_mailbox_scope(self.fake)
        migrated = app._load_published_marker(filename)
        self.assertEqual(migrated["mailbox_scope"], scope)

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
        runtime_sha = "a" * 40
        with patch.dict(os.environ, {"LLM_GIT_BRIDGE_RUNTIME_SHA": runtime_sha}):
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
        self.assertEqual(result["doctor"]["runtime_sha"], runtime_sha)
        blob = json.dumps(result)
        self.assertNotIn("client_secret", blob)
        self.assertNotIn("token", blob)
        self.assertNotIn("/Users/", blob)

    def test_doctor_ignores_invalid_runtime_sha_environment(self):
        with patch.dict(os.environ, {"LLM_GIT_BRIDGE_RUNTIME_SHA": "/Users/tom/not-a-sha"}):
            with patch("llm_git_bridge.app._version_line", return_value="version"):
                with patch("llm_git_bridge.app._rclone_remote_summary", return_value={
                    "remote_type": "drive", "custom_drive_client_id_configured": False, "config_inspected": True
                }):
                    result = app._process_doctor_request(
                        self.cfg, {"protocol": 2, "kind": "doctor", "transaction_id": "tx-doctor-runtime"},
                        "tx-doctor-runtime.json",
                    )
        self.assertIsNone(result["doctor"]["runtime_sha"])
        self.assertNotIn("/Users/tom/not-a-sha", json.dumps(result))

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


    def test_daemon_install_failure_restores_stopped_previous_plist_without_starting_it(self):
        from argparse import Namespace
        old_plist = app.PLIST_PATH
        old_state = app.STATE_DIR
        try:
            app.PLIST_PATH = self.tmp / "daemon.plist"
            app.STATE_DIR = self.tmp / "state"
            previous = b"<?xml version=\"1.0\"?><plist version=\"1.0\"><dict/></plist>\n"
            app.PLIST_PATH.write_bytes(previous)
            wrapper = self.tmp / "llm-git-bridge"
            wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
            wrapper.chmod(0o755)
            calls = []
            def launchctl(*args, check=True):
                calls.append((args, check))
                if args[0] == "print":
                    return 1
                if args[0] == "bootstrap":
                    raise BridgeError("bootstrap failed")
                return 0
            with patch.object(app.sys, "platform", "darwin"):
                with patch.object(app.os, "getuid", return_value=501):
                    with patch.object(app.shutil, "which", return_value="/usr/bin/rclone"):
                        with patch.object(app, "_launchctl", side_effect=launchctl):
                            with self.assertRaisesRegex(BridgeError, "bootstrap failed"):
                                app.cmd_daemon(Namespace(action="install", command=str(wrapper)))
            self.assertEqual(app.PLIST_PATH.read_bytes(), previous)
            self.assertEqual(sum(1 for args, _ in calls if args[0] == "bootstrap"), 1)
        finally:
            app.PLIST_PATH = old_plist
            app.STATE_DIR = old_state

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

    def test_d_configure_concurrency_cli_persists_validated_limits(self):
        parser = app.build_parser()
        args = parser.parse_args([
            "configure-concurrency", "--workers", "2", "--max-pending-jobs", "8"
        ])
        with patch("builtins.print"):
            self.assertEqual(app.cmd_configure_concurrency(args), 0)
        cfg = app.load_config()
        self.assertEqual(cfg["max_workers"], 2)
        self.assertEqual(cfg["max_pending_jobs"], 8)

    def test_configure_discovery_cli_persists_validated_interval(self):
        parser = app.build_parser()
        args = parser.parse_args(["configure-discovery", "--interval", "45"])
        with patch("builtins.print"):
            self.assertEqual(app.cmd_configure_discovery(args), 0)
        cfg = app.load_config()
        self.assertEqual(cfg["registry_scan_interval"], 45.0)

    def test_registry_scan_interval_is_bounded_and_validated(self):
        self.assertEqual(app.default_config()["registry_scan_interval"], 30.0)
        for value in (5, 30, 3600):
            cfg = app.default_config()
            cfg["registry_scan_interval"] = value
            self.assertEqual(app._validate_config(cfg)["registry_scan_interval"], value)
        for value in (True, 0, 4.9, 3600.1, float("nan"), float("inf"), "30"):
            cfg = app.default_config()
            cfg["registry_scan_interval"] = value
            with self.subTest(value=value), self.assertRaisesRegex(BridgeError, "registry_scan_interval"):
                app._validate_config(cfg)

    def test_d_pending_limit_cannot_be_lower_than_worker_count(self):
        cfg = app.default_config()
        cfg["max_workers"] = 4
        cfg["max_pending_jobs"] = 2
        with self.assertRaisesRegex(BridgeError, "at least max_workers"):
            app._validate_config(cfg)

    def test_d_max_pending_jobs_is_bounded_and_validated(self):
        self.assertEqual(app.default_config()["max_pending_jobs"], 8)
        for value in (1, 8, 32):
            cfg = app.default_config()
            cfg["max_pending_jobs"] = value
            self.assertEqual(app._validate_config(cfg)["max_pending_jobs"], value)
        for value in (True, 0, 33, 1.5, "8"):
            cfg = app.default_config()
            cfg["max_pending_jobs"] = value
            with self.subTest(value=value), self.assertRaisesRegex(BridgeError, "max_pending_jobs"):
                app._validate_config(cfg)

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
        base["roots"] = [{"path": str(temp), "push": False}]
        cases = [
            {**base, "transport": "not-an-object"},
            {**base, "poll_interval": float("nan")},
            {**base, "poll_interval": 0.1},
            {**base, "registry_scan_interval": float("nan")},
            {**base, "registry_scan_interval": 4.0},
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
        cfg["roots"] = [{"path": str(temp), "push": False}]
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
