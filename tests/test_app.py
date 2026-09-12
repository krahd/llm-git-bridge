from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llm_git_bridge import app
from llm_git_bridge.core import BridgeError, build_registry, save_json
from llm_git_bridge.transport import RcloneTransport


def sh(cwd: Path, *args: str) -> str:
    p = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return p.stdout.strip()


class FakeTransport:
    def __init__(self):
        self.files: dict[str, str] = {}
        self.list_calls: list[str] = []
        self.ensure_dir_calls: list[str] = []

    def ensure_dir(self, rel: str) -> None:
        self.ensure_dir_calls.append(rel)

    def list_files(self, rel: str) -> list[str]:
        self.list_calls.append(rel)
        prefix = rel.rstrip("/") + "/"
        out = []
        for path in self.files:
            if path.startswith(prefix):
                rest = path[len(prefix):]
                if "/" not in rest:
                    out.append(rest)
        return sorted(out)

    def download_text(self, rel: str, local: Path) -> str:
        text = self.files[rel]
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(text, encoding="utf-8")
        return text

    def upload_json(self, rel: str, obj, local_tmp: Path) -> None:
        local_tmp.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(obj, sort_keys=True) + "\n"
        local_tmp.write_text(text, encoding="utf-8")
        self.files[rel] = text


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="llmgb-app-"))
        self.state = self.tmp / "state"
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q")
        sh(self.repo, "git", "config", "user.email", "test@example.invalid")
        sh(self.repo, "git", "config", "user.name", "Test User")
        (self.repo / "README.md").write_text("hello\n", encoding="utf-8")
        sh(self.repo, "git", "add", "README.md")
        sh(self.repo, "git", "commit", "-qm", "initial")

        self.fake = FakeTransport()
        self.old = (app.STATE_DIR, app.REGISTRY_FILE, app.PUBLISHED_DIR)
        app.STATE_DIR = self.state
        app.REGISTRY_FILE = self.state / "registry.json"
        app.PUBLISHED_DIR = self.state / "published-results"
        save_json(app.REGISTRY_FILE, build_registry([self.tmp]))
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
        app.STATE_DIR, app.REGISTRY_FILE, app.PUBLISHED_DIR = self.old


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
        self.fake.list_calls.clear()
        self.assertEqual(app.process_pending_once(self.cfg), 0)
        self.assertEqual(self.fake.list_calls, ["v2/transactions"])

    def test_existing_remote_result_prevents_replay_after_local_state_loss(self):
        txid = "tx-already"
        self.fake.files[f"v2/transactions/{txid}.json"] = json.dumps({
            "protocol": 2, "kind": "materialize", "transaction_id": txid, "repo": "does-not-matter"
        })
        self.fake.files[f"v2/results/{txid}.json"] = json.dumps({"status": "success"})
        self.assertEqual(app.process_pending_once(self.cfg), 0)
        self.assertTrue((app.PUBLISHED_DIR / f"{txid}.json").exists())


class TransportTests(unittest.TestCase):
    def test_list_failure_is_not_silently_treated_as_empty(self):
        transport = RcloneTransport("fake")
        proc = subprocess.CompletedProcess(["rclone"], 1, stdout="", stderr="auth failed")
        with patch("llm_git_bridge.transport.run", return_value=proc):
            with self.assertRaises(BridgeError):
                transport.list_files("v2/transactions")

    def test_list_uses_short_poll_timeout(self):
        transport = RcloneTransport("fake", timeout=60, list_timeout=7)
        proc = subprocess.CompletedProcess(["rclone"], 0, stdout="", stderr="")
        with patch("llm_git_bridge.transport.run", return_value=proc) as mocked:
            self.assertEqual(transport.list_files("v2/transactions"), [])
        self.assertEqual(mocked.call_args.kwargs["timeout"], 7)

    def test_list_timeout_is_capped_by_general_timeout(self):
        transport = RcloneTransport("fake", timeout=5, list_timeout=12)
        self.assertEqual(transport.list_timeout, 5)


    def test_mkdir_uses_short_timeout(self):
        transport = RcloneTransport("fake", timeout=60, mkdir_timeout=8)
        proc = subprocess.CompletedProcess(["rclone"], 0, stdout="", stderr="")
        with patch("llm_git_bridge.transport.run", return_value=proc) as mocked:
            transport.ensure_dir("v2/meta")
        self.assertEqual(mocked.call_args.kwargs["timeout"], 8)

    def test_mkdir_timeout_is_capped_by_general_timeout(self):
        transport = RcloneTransport("fake", timeout=5, mkdir_timeout=12)
        self.assertEqual(transport.mkdir_timeout, 5)


if __name__ == "__main__":
    unittest.main()
