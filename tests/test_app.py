from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--local",
                "--origin",
                "__seed__",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "user.name=Test User",
                str(self._seed_repo),
                str(self.repo),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

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
        head = sh(self.repo, "git", "rev-parse", "HEAD")
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

    def test_transaction_snapshot_is_deferred_by_default(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        head = sh(self.repo, "git", "rev-parse", "HEAD")
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
        head = sh(self.repo, "git", "rev-parse", "HEAD")
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

    def test_processed_marker_avoids_result_listing_in_steady_state(self):
        repo_id = next(iter(json.loads(app.REGISTRY_FILE.read_text())["repos"]))
        head = sh(self.repo, "git", "rev-parse", "HEAD")
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
        self.assertNotIn("path", json.dumps(event))
        self.fake.list_calls.clear()
        self.assertEqual(app.process_pending_once(self.cfg), 0)
        self.assertEqual(self.fake.list_calls, ["v2/transactions"])

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
        save_json(local_result, {"status": "success", "transaction_id": txid})
        self.assertEqual(app.process_pending_once(self.cfg), 0)
        self.assertFalse((app.PUBLISHED_DIR / filename).exists())
        self.assertIn(f"v2/transactions/{filename}", self.fake.files)

    def test_future_dated_local_result_does_not_block_recovery_forever(self):
        txid = "tx-recovery-clock-skew"
        filename = f"{txid}.json"
        self.fake.files[f"v2/transactions/{filename}"] = "{}"
        local_result = app.STATE_DIR / "results" / filename
        save_json(local_result, {"status": "success", "transaction_id": txid})
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
        save_json(app.PUBLISHED_DIR / filename, {"transaction_id": "tx-marked"})
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


if __name__ == "__main__":
    unittest.main()
