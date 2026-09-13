from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

import llm_git_bridge.core as core_mod

import llm_git_bridge.core as core

from llm_git_bridge.core import (
    BridgeError,
    build_registry,
    build_snapshot,
    process_transaction,
    public_registry,
    run_configured_command,
    validate_transaction,
)


PRIVATE_KEY_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
PRIVATE_KEY_END = "-----END " + "PRIVATE KEY-----"
SERVICE_ACCOUNT_TYPE = "service" + "_account"
PRIVATE_KEY_FIELD = "private" + "_key"
CLIENT_EMAIL_FIELD = "client" + "_email"


def sh(cwd: Path, *args: str) -> str:
    p = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return p.stdout.strip()


class CoreTests(unittest.TestCase):
    def test_suite_imports_bridge_from_current_checkout(self):
        project_root = Path(__file__).resolve().parents[1]
        imported_core = Path(core.__file__).resolve()
        self.assertTrue(
            imported_core.is_relative_to(project_root),
            f"tests imported bridge code outside current checkout: {imported_core}",
        )

    def make_repo(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="llmgb-test-"))
        sh(root, "git", "init", "-q")
        sh(root, "git", "config", "user.email", "test@example.invalid")
        sh(root, "git", "config", "user.name", "Test User")
        (root / "README.md").write_text("hello\n", encoding="utf-8")
        sh(root, "git", "add", "README.md")
        sh(root, "git", "commit", "-qm", "initial")
        return root

    def test_registry_does_not_publish_paths(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-root-"))
        repo = parent / "alpha"
        repo.mkdir()
        sh(repo, "git", "init", "-q")
        sh(repo, "git", "config", "user.email", "test@example.invalid")
        sh(repo, "git", "config", "user.name", "Test User")
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        sh(repo, "git", "add", "a.txt")
        sh(repo, "git", "commit", "-qm", "initial")

        local = build_registry([parent])
        public = public_registry(local)
        blob = json.dumps(public)
        self.assertNotIn(str(parent), blob)
        self.assertEqual(public["repos"][0]["name"], "alpha")

    def test_snapshot_excludes_sensitive_and_binary(self):
        repo = self.make_repo()
        (repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        (repo / "notes.txt").write_text("notes\n", encoding="utf-8")
        (repo / "blob.bin").write_bytes(b"\x00\x01")
        sh(repo, "git", "add", ".env", "notes.txt", "blob.bin")
        snap = build_snapshot(repo, "demo")
        paths = {item["path"] for item in snap["files"]}
        omitted_paths = {item.get("path"): item["reason"] for item in snap["omitted"] if "path" in item}
        self.assertIn("README.md", paths)
        self.assertIn("notes.txt", paths)
        self.assertNotIn(".env", paths)
        self.assertNotIn(".env", json.dumps(snap))
        self.assertTrue(any(item["reason"] == "sensitive-or-excluded" for item in snap["omitted"]))
        self.assertEqual(omitted_paths["blob.bin"], "binary")

    def test_snapshot_excludes_private_key_content_under_innocuous_name(self):
        repo = self.make_repo()
        (repo / "notes.txt").write_text(
            PRIVATE_KEY_BEGIN + "\nsecret\n" + PRIVATE_KEY_END + "\n",
            encoding="utf-8",
        )
        sh(repo, "git", "add", "notes.txt")
        snap = build_snapshot(repo, "demo")
        self.assertNotIn("notes.txt", {item["path"] for item in snap["files"]})
        self.assertIn("sensitive-content", {item["reason"] for item in snap["omitted"]})
        self.assertNotIn("notes.txt", json.dumps(snap["omitted"]))

    def test_secret_detector_accepts_structured_service_account_but_not_source_literals(self):
        credential = json.dumps({
            "type": SERVICE_ACCOUNT_TYPE,
            PRIVATE_KEY_FIELD: PRIVATE_KEY_BEGIN + "\nsecret\n" + PRIVATE_KEY_END + "\n",
            CLIENT_EMAIL_FIELD: "service@example.invalid",
        }).encode("utf-8")
        source_json = json.dumps({
            "type": SERVICE_ACCOUNT_TYPE,
            PRIVATE_KEY_FIELD: "example",
            CLIENT_EMAIL_FIELD: "x",
        }, separators=(",", ":"))
        source = (f'MARKER = "{PRIVATE_KEY_BEGIN}"\nSAMPLE = {source_json!r}\n').encode("utf-8")
        self.assertTrue(core_mod._contains_obvious_secret(credential))
        self.assertFalse(core_mod._contains_obvious_secret(source))

    def test_bridge_source_does_not_trip_legacy_secret_marker_filter(self):
        legacy_pem_markers = (
            ("-----BEGIN " + "PRIVATE KEY-----").encode("utf-8"),
            ("-----BEGIN RSA " + "PRIVATE KEY-----").encode("utf-8"),
            ("-----BEGIN OPENSSH " + "PRIVATE KEY-----").encode("utf-8"),
            ("-----BEGIN EC " + "PRIVATE KEY-----").encode("utf-8"),
            ("-----BEGIN DSA " + "PRIVATE KEY-----").encode("utf-8"),
        )
        legacy_service_fields = (
            b'"type"',
            ('"service' + '_account"').encode("utf-8"),
            ('"private' + '_key"').encode("utf-8"),
            ('"client' + '_email"').encode("utf-8"),
        )
        for source_path in (Path(core_mod.__file__).resolve(), Path(__file__).resolve()):
            data = source_path.read_bytes()
            self.assertFalse(any(marker in data for marker in legacy_pem_markers), source_path)
            self.assertFalse(all(marker in data.lower() for marker in legacy_service_fields), source_path)

    def test_snapshot_excludes_common_secret_paths(self):
        repo = self.make_repo()
        (repo / ".kube").mkdir()
        (repo / ".kube" / "config").write_text("token: secret\n", encoding="utf-8")
        (repo / "terraform.tfstate").write_text("{}\n", encoding="utf-8")
        sh(repo, "git", "add", ".kube/config", "terraform.tfstate")
        snap = build_snapshot(repo, "demo")
        paths = {item["path"] for item in snap["files"]}
        self.assertNotIn(".kube/config", paths)
        self.assertNotIn("terraform.tfstate", paths)

    def test_configured_command_environment_scrubs_common_credentials(self):
        repo = self.make_repo()
        with __import__("unittest.mock").mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "secret",
                "GITHUB_TOKEN": "secret",
                "AWS_SECRET_ACCESS_KEY": "secret",
                "SSH_AUTH_SOCK": "/tmp/agent.sock",
                "RCLONE_CONFIG_PASS": "secret",
                "KUBECONFIG": "/tmp/kubeconfig",
                "LLMGB_SAFE_TEST_VAR": "visible",
            },
            clear=False,
        ):
            env = core_mod._command_environment(repo, repo / ".private-home")
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertNotIn("RCLONE_CONFIG_PASS", env)
        self.assertNotIn("KUBECONFIG", env)
        self.assertEqual(env["LLMGB_SAFE_TEST_VAR"], "visible")
        self.assertEqual(env["HOME"], str(repo / ".private-home"))
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")

    def test_configured_command_output_is_bounded(self):
        repo = self.make_repo()
        result = run_configured_command(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 200000); sys.stderr.write('y' * 200000)"],
            cwd=repo,
            env=os.environ.copy(),
            timeout=5,
            output_limit=4096,
        )
        self.assertEqual(result.returncode, 0)
        self.assertLessEqual(len(result.stdout.encode()), 4096)
        self.assertLessEqual(len(result.stderr.encode()), 4096)
        self.assertTrue(result.stdout.endswith("x" * 100))
        self.assertTrue(result.stderr.endswith("y" * 100))

    def test_configured_command_timeout_kills_process_group(self):
        repo = self.make_repo()
        started = time.monotonic()
        result = run_configured_command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=repo,
            env=os.environ.copy(),
            timeout=0.2,
        )
        self.assertTrue(result.timed_out)
        self.assertLess(time.monotonic() - started, 3)

    def test_transaction_validation_rejects_bad_branch(self):
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-1",
            "repo": "demo",
            "base_sha": "a" * 40,
            "branch": "main",
            "patch": "x",
            "run": [],
        }
        with self.assertRaises(BridgeError):
            validate_transaction(tx, safe_branch_prefix="ai/")

    def test_process_transaction_creates_commit_and_cleans_worktree(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-apply-1",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/test-change",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+world\n"
            ),
            "run": [],
            "commit_message": "bridge change",
            "publish_snapshot": True,
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        outcome = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={},
            allow_commit=True,
        )
        self.assertEqual(outcome.result["status"], "success")
        commit = outcome.result["commit"]
        self.assertEqual(sh(repo, "git", "rev-parse", "ai/test-change"), commit)
        self.assertFalse((state / "worktrees" / "tx-apply-1").exists())
        self.assertIsNotNone(outcome.snapshot)
        readme = next(x for x in outcome.snapshot["files"] if x["path"] == "README.md")
        self.assertEqual(readme["content"], "hello\nworld\n")

    def test_existing_branch_requires_exact_base(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        sh(repo, "git", "branch", "ai/existing", head)
        (repo / "x.txt").write_text("x\n", encoding="utf-8")
        sh(repo, "git", "add", "x.txt")
        sh(repo, "git", "commit", "-qm", "advance main")
        wrong = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-stale",
            "repo": "demo",
            "base_sha": wrong,
            "branch": "ai/existing",
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
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )

    def test_snapshot_does_not_upload_untracked_content(self):
        repo = self.make_repo()
        (repo / "scratch.txt").write_text("private scratch\n", encoding="utf-8")
        snap = build_snapshot(repo, "demo")
        paths = {item["path"] for item in snap["files"]}
        self.assertNotIn("scratch.txt", paths)
        self.assertTrue(snap["untracked"])

    def test_failed_new_branch_is_removed(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-bad-patch",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/bad-patch",
            "patch": "diff --git a/nope b/nope\n--- a/nope\n+++ b/nope\n@@ -1 +1 @@\n-x\n+y\n",
            "run": [],
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )
        proc = subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "refs/heads/ai/bad-patch"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_transaction_rejects_weird_branch_path(self):
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-2",
            "repo": "demo",
            "base_sha": "a" * 40,
            "branch": "ai/../../evil",
            "patch": "x",
            "run": [],
        }
        with self.assertRaises(BridgeError):
            validate_transaction(tx, safe_branch_prefix="ai/")


    def test_transaction_rejects_symlink_creation(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        patch = (
            "diff --git a/leak b/leak\n"
            "new file mode 120000\n"
            "index 0000000..3b18e51\n"
            "--- /dev/null\n"
            "+++ b/leak\n"
            "@@ -0,0 +1 @@\n"
            "+/etc/passwd\n"
            "\\ No newline at end of file\n"
        )
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-symlink",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/symlink",
            "patch": patch,
            "run": [],
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )



    def test_snapshot_enforces_total_budget(self):
        repo = self.make_repo()
        for i in range(5):
            (repo / f"f{i}.txt").write_text("x" * 100 + "\n", encoding="utf-8")
        sh(repo, "git", "add", ".")
        snap = build_snapshot(repo, "demo", max_file_bytes=1000, max_total_bytes=180, max_files=100)
        self.assertLessEqual(snap["included_bytes"], 180)
        self.assertTrue(any(item["reason"] == "snapshot-budget" for item in snap["omitted"]))

    def test_transaction_rejects_sensitive_path_change(self):
        repo = self.make_repo()
        (repo / ".env").write_text("TOKEN=old\n", encoding="utf-8")
        sh(repo, "git", "add", ".env")
        sh(repo, "git", "commit", "-qm", "add env")
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-env",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/env-change",
            "patch": (
                "diff --git a/.env b/.env\n"
                "--- a/.env\n"
                "+++ b/.env\n"
                "@@ -1 +1 @@\n"
                "-TOKEN=old\n"
                "+TOKEN=new\n"
            ),
            "run": [],
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )

    def test_commit_hooks_and_signing_are_disabled(self):
        repo = self.make_repo()
        marker = repo / "HOOK_RAN"
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
        hook.chmod(0o755)
        sh(repo, "git", "config", "commit.gpgSign", "true")
        sh(repo, "git", "config", "gpg.program", "/usr/bin/false")
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-no-hooks",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/no-hooks",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+safe\n"
            ),
            "run": [],
        }
        outcome = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
            safe_branch_prefix="ai/",
            commands={},
        )
        self.assertEqual(outcome.result["status"], "success")
        self.assertFalse(marker.exists())

    def test_command_generated_untracked_file_is_not_committed_or_snapshotted(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-generated",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/generated",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+safe\n"
            ),
            "run": ["generate"],
            "publish_snapshot": True,
        }
        outcome = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
            safe_branch_prefix="ai/",
            commands={"generate": [sys.executable, "-c", "open('generated.tmp','w').write('x')"]},
        )
        commit = outcome.result["commit"]
        tree = sh(repo, "git", "ls-tree", "-r", "--name-only", commit).splitlines()
        self.assertNotIn("generated.tmp", tree)
        self.assertFalse(outcome.snapshot["untracked"])

    def test_registry_ids_remain_stable_when_collision_appears_later(self):
        # Use names that slugify to the same ID without depending on a
        # case-sensitive filesystem. macOS commonly treats Foo/foo as one path.
        parent = Path(tempfile.mkdtemp(prefix="llmgb-stable-"))
        first = parent / "Foo Bar"
        first.mkdir()
        sh(first, "git", "init", "-q")
        sh(first, "git", "config", "user.email", "test@example.invalid")
        sh(first, "git", "config", "user.name", "Test User")
        (first / "a").write_text("a\n", encoding="utf-8")
        sh(first, "git", "add", "a")
        sh(first, "git", "commit", "-qm", "initial")
        one = build_registry([parent])
        first_id = next(iter(one["repos"]))

        second = parent / "foo-bar"
        second.mkdir()
        sh(second, "git", "init", "-q")
        sh(second, "git", "config", "user.email", "test@example.invalid")
        sh(second, "git", "config", "user.name", "Test User")
        (second / "b").write_text("b\n", encoding="utf-8")
        sh(second, "git", "add", "b")
        sh(second, "git", "commit", "-qm", "initial")
        two = build_registry([parent], previous=one)
        by_path = {entry["path"]: rid for rid, entry in two["repos"].items()}
        self.assertEqual(by_path[str(first.resolve())], first_id)
        self.assertNotEqual(by_path[str(second.resolve())], first_id)


    def test_push_requires_local_opt_in(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-push-disabled",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/push-disabled",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+push\n"
            ),
            "run": [],
            "push": True,
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(repo), "show-ref", "--verify", "refs/heads/ai/push-disabled"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).returncode,
            0,
        )

    def test_push_uses_origin_safe_refspec_and_disables_hooks(self):
        repo = self.make_repo()
        remote = Path(tempfile.mkdtemp(prefix="llmgb-remote-")) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        sh(repo, "git", "remote", "add", "origin", str(remote))
        head = sh(repo, "git", "rev-parse", "HEAD")
        marker = repo / "PUSH_HOOK_RAN"
        hook = repo / ".git" / "hooks" / "pre-push"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
        hook.chmod(0o755)
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-push-enabled",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/push-enabled",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+push\n"
            ),
            "run": [],
            "push": True,
        }
        outcome = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
            safe_branch_prefix="ai/",
            commands={},
            allow_push=True,
        )
        self.assertFalse(marker.exists())
        self.assertEqual(outcome.result["status"], "success")
        self.assertEqual(outcome.result["push"]["status"], "success")
        remote_sha = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/ai/push-enabled"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout.strip()
        self.assertEqual(remote_sha, outcome.result["commit"])

    def test_push_failure_preserves_local_commit(self):
        repo = self.make_repo()
        secret_remote = Path(tempfile.mkdtemp(prefix="llmgb-secret-")) / "TOKEN_SHOULD_NOT_LEAK" / "remote.git"
        sh(repo, "git", "remote", "add", "origin", str(secret_remote))
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-push-fails",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/push-fails",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+push\n"
            ),
            "run": [],
            "push": True,
        }
        outcome = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
            safe_branch_prefix="ai/",
            commands={},
            allow_push=True,
        )
        self.assertEqual(outcome.result["status"], "success")
        self.assertEqual(outcome.result["push"]["status"], "error")
        self.assertNotIn("TOKEN_SHOULD_NOT_LEAK", outcome.result["push"]["error"])
        self.assertEqual(sh(repo, "git", "rev-parse", "ai/push-fails"), outcome.result["commit"])

    def test_push_field_must_be_boolean(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-push-invalid",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/push-invalid",
            "patch": "x",
            "run": [],
            "push": "yes",
        }
        with self.assertRaises(BridgeError):
            validate_transaction(tx, safe_branch_prefix="ai/")

    def test_publish_snapshot_field_must_be_boolean(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-snapshot-invalid",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/snapshot-invalid",
            "patch": "x",
            "run": [],
            "publish_snapshot": "yes",
        }
        with self.assertRaises(BridgeError):
            validate_transaction(tx, safe_branch_prefix="ai/")


    def test_transaction_id_rejects_dot_and_dotdot(self):
        base = {
            "protocol": 2,
            "kind": "transaction",
            "repo": "demo",
            "base_sha": "a" * 40,
            "branch": "ai/test",
            "patch": "x",
            "run": [],
        }
        for txid in (".", "..", "tx.with.dot"):
            tx = {**base, "transaction_id": txid}
            with self.assertRaises(BridgeError):
                validate_transaction(tx, safe_branch_prefix="ai/")

    def test_run_command_count_is_bounded(self):
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-too-many-commands",
            "repo": "repo",
            "base_sha": "0" * 40,
            "branch": "ai/too-many-commands",
            "patch": "diff --git a/a b/a\n",
            "run": ["test"] * 17,
        }
        with self.assertRaisesRegex(BridgeError, "at most 16 commands"):
            validate_transaction(tx, safe_branch_prefix="ai/")

    def test_run_command_names_are_safe_tokens(self):
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-run-name",
            "repo": "demo",
            "base_sha": "a" * 40,
            "branch": "ai/test",
            "patch": "x",
            "run": ["../test"],
        }
        with self.assertRaises(BridgeError):
            validate_transaction(tx, safe_branch_prefix="ai/")

    def test_sensitive_source_cannot_be_renamed_to_public_path(self):
        repo = self.make_repo()
        (repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        sh(repo, "git", "add", ".env")
        sh(repo, "git", "commit", "-qm", "add secret")
        head = sh(repo, "git", "rev-parse", "HEAD")
        patch_text = (
            "diff --git a/.env b/.env\n"
            "deleted file mode 100644\n"
            "--- a/.env\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-TOKEN=secret\n"
            "diff --git a/public.txt b/public.txt\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/public.txt\n"
            "@@ -0,0 +1 @@\n"
            "+TOKEN=secret\n"
        )
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-secret-rename",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/secret-rename",
            "patch": patch_text,
            "run": [],
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )

    def test_transaction_rejects_symlink_deletion(self):
        repo = self.make_repo()
        os.symlink("README.md", repo / "link")
        sh(repo, "git", "add", "link")
        sh(repo, "git", "commit", "-qm", "add link")
        head = sh(repo, "git", "rev-parse", "HEAD")
        patch_text = (
            "diff --git a/link b/link\n"
            "deleted file mode 120000\n"
            "--- a/link\n"
            "+++ /dev/null\n"
            "@@ -1 +0,0 @@\n"
            "-README.md\n"
            "\\ No newline at end of file\n"
        )
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-delete-link",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/delete-link",
            "patch": patch_text,
            "run": [],
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )

    def test_transaction_rejects_gitmodules_changes(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-gitmodules",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/gitmodules",
            "patch": (
                "diff --git a/.gitmodules b/.gitmodules\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/.gitmodules\n"
                "@@ -0,0 +1 @@\n"
                "+[submodule \"evil\"]\n"
            ),
            "run": [],
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )

    def test_transaction_rejects_ci_automation_paths(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        patch_text = (
            "diff --git a/.github/workflows/pwn.yml b/.github/workflows/pwn.yml\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/.github/workflows/pwn.yml\n"
            "@@ -0,0 +1 @@\n"
            "+name: unsafe\n"
        )
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-ci",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/ci",
            "patch": patch_text,
            "run": [],
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )

    def test_command_cannot_change_staged_tree(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-index-mutation",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/index-mutation",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n hello\n+safe\n"
            ),
            "run": ["mutate"],
        }
        script = "import subprocess; open('extra.txt','w').write('x'); subprocess.run(['git','add','extra.txt'],check=True)"
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={"mutate": [sys.executable, "-c", script]},
            )

    def test_command_cannot_commit_or_move_refs(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-command-commit",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/command-commit",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n hello\n+safe\n"
            ),
            "run": ["commit"],
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={"commit": ["git", "commit", "-m", "unexpected"]},
            )

    def test_command_output_stays_local(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-output-local",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/output-local",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n hello\n+safe\n"
            ),
            "run": ["show"],
        }
        outcome = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={"show": [sys.executable, "-c", "print('TOKEN_SHOULD_NOT_LEAK')"]},
        )
        self.assertNotIn("TOKEN_SHOULD_NOT_LEAK", json.dumps(outcome.result))
        log = (state / "command-logs" / "tx-output-local" / "01.log").read_text(encoding="utf-8")
        self.assertIn("TOKEN_SHOULD_NOT_LEAK", log)


    def test_configured_commands_run_without_git_metadata_or_sensitive_paths(self):
        repo = self.make_repo()
        (repo / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
        sh(repo, "git", "add", ".env")
        sh(repo, "git", "commit", "-qm", "add sensitive tracked file")
        head = sh(repo, "git", "rev-parse", "HEAD")
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        txid = "tx-command-isolation"
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/command-isolation",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n hello\n+safe\n"
            ),
            "run": ["inspect"],
        }
        script = (
            "from pathlib import Path; "
            "print('git=' + str(Path('.git').exists())); "
            "print('env=' + str(Path('.env').exists())); "
            "print('home=' + str(Path.home()))"
        )
        process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={"inspect": [sys.executable, "-c", script]},
        )
        log = (state / "command-logs" / txid / "01.log").read_text(encoding="utf-8")
        self.assertIn("git=False", log)
        self.assertIn("env=False", log)
        self.assertIn("home=" + str(state / "command-homes" / txid), log)
        self.assertFalse((state / "command-runs" / txid).exists())
        self.assertFalse((state / "command-homes" / txid).exists())

    def test_configured_command_workspace_omits_symlinks_and_obvious_secret_content(self):
        repo = self.make_repo()
        (repo / "innocent.txt").write_text(
            PRIVATE_KEY_BEGIN + "\nsecret\n" + PRIVATE_KEY_END + "\n",
            encoding="utf-8",
        )
        detector_json = json.dumps({
            "type": SERVICE_ACCOUNT_TYPE,
            PRIVATE_KEY_FIELD: "example",
            CLIENT_EMAIL_FIELD: "x",
        }, separators=(",", ":"))
        (repo / "detector_source.py").write_text(
            f'MARKER = "{PRIVATE_KEY_BEGIN}"\nSAMPLE = {detector_json!r}\n',
            encoding="utf-8",
        )
        os.symlink("README.md", repo / "readme-link")
        sh(repo, "git", "add", "innocent.txt", "detector_source.py", "readme-link")
        sh(repo, "git", "commit", "-qm", "add command workspace hazards")
        head = sh(repo, "git", "rev-parse", "HEAD")
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        txid = "tx-command-workspace-filter"
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/command-workspace-filter",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n hello\n+safe\n"
            ),
            "run": ["inspect"],
        }
        script = (
            "from pathlib import Path; "
            "print('secret=' + str(Path('innocent.txt').exists())); "
            "print('source=' + str(Path('detector_source.py').exists())); "
            "print('symlink=' + str(Path('readme-link').exists()))"
        )
        process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={"inspect": [sys.executable, "-c", script]},
        )
        log = (state / "command-logs" / txid / "01.log").read_text(encoding="utf-8")
        self.assertIn("secret=False", log)
        self.assertIn("source=True", log)
        self.assertIn("symlink=False", log)

    def test_command_environment_uses_worktree_pythonpath(self):
        repo = self.make_repo()
        (repo / "src").mkdir()
        (repo / "src" / "placeholder.txt").write_text("x\n", encoding="utf-8")
        sh(repo, "git", "add", "src/placeholder.txt")
        sh(repo, "git", "commit", "-qm", "add src")
        head = sh(repo, "git", "rev-parse", "HEAD")
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        txid = "tx-pythonpath"
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/pythonpath",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n hello\n+safe\n"
            ),
            "run": ["env"],
        }
        process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={"env": [sys.executable, "-c", "import os; print(os.environ.get('PYTHONPATH',''))"]},
        )
        log = (state / "command-logs" / txid / "01.log").read_text(encoding="utf-8")
        self.assertIn(str(state / "command-runs" / txid / "src"), log)
        self.assertNotIn(str(repo / "src"), log)

    def test_absolute_repo_command_is_remapped_to_worktree(self):
        repo = self.make_repo()
        script = repo / "check.sh"
        script.write_text("#!/bin/sh\necho original\n", encoding="utf-8")
        script.chmod(0o755)
        sh(repo, "git", "add", "check.sh")
        sh(repo, "git", "commit", "-qm", "add check")
        head = sh(repo, "git", "rev-parse", "HEAD")
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        txid = "tx-remap-command"
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/remap-command",
            "patch": (
                "diff --git a/check.sh b/check.sh\n"
                "--- a/check.sh\n"
                "+++ b/check.sh\n"
                "@@ -1,2 +1,2 @@\n #!/bin/sh\n-echo original\n+echo patched\n"
            ),
            "run": ["check"],
        }
        process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={"check": [str(script)]},
        )
        log = (state / "command-logs" / txid / "01.log").read_text(encoding="utf-8")
        self.assertIn("patched", log)
        self.assertNotIn("\noriginal\n", log)

    def test_push_timeout_is_secondary_to_local_commit(self):
        repo = self.make_repo()
        remote = Path(tempfile.mkdtemp(prefix="llmgb-remote-")) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        sh(repo, "git", "remote", "add", "origin", str(remote))
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-push-timeout",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/push-timeout",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n hello\n+safe\n"
            ),
            "run": [],
            "push": True,
        }
        original_run = core.run

        def maybe_timeout(argv, **kwargs):
            if "push" in argv:
                raise BridgeError("command timed out after 120s")
            return original_run(argv, **kwargs)

        with patch("llm_git_bridge.core.run", side_effect=maybe_timeout):
            outcome = process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
                allow_push=True,
            )
        self.assertEqual(outcome.result["status"], "success")
        self.assertEqual(outcome.result["push"]["status"], "error")
        self.assertEqual(sh(repo, "git", "rev-parse", "ai/push-timeout"), outcome.result["commit"])

    def test_deferred_snapshot_is_not_built(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-no-snapshot-build",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/no-snapshot-build",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n hello\n+safe\n"
            ),
            "run": [],
        }
        with patch("llm_git_bridge.core.build_snapshot", side_effect=AssertionError("must not run")):
            outcome = process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )
        self.assertTrue(outcome.result["snapshot_deferred"])
        self.assertIsNone(outcome.snapshot)

    def test_requested_snapshot_failure_is_secondary(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-snapshot-secondary",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/snapshot-secondary",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n hello\n+safe\n"
            ),
            "run": [],
            "publish_snapshot": True,
        }
        with patch("llm_git_bridge.core.build_snapshot", side_effect=OSError("/private/path")):
            outcome = process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )
        self.assertEqual(outcome.result["status"], "success")
        self.assertEqual(outcome.result["snapshot_error"], "snapshot generation failed")
        self.assertNotIn("/private/path", json.dumps(outcome.result))

    def test_commits_disabled_fails_before_branch_creation(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-commit-disabled",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/commit-disabled",
            "patch": "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n hello\n+safe\n",
            "run": [],
        }
        with self.assertRaises(BridgeError):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
                allow_commit=False,
            )
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(repo), "show-ref", "--verify", "refs/heads/ai/commit-disabled"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).returncode,
            0,
        )


    def test_identical_retry_recovers_committed_transaction_without_second_commit(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-crash-recovery",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/crash-recovery",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+recovered\n"
            ),
            "run": [],
            "commit_message": "crash recovery test",
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        first = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={},
        )
        commit = first.result["commit"]
        second = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={},
            allow_commit=False,
        )
        self.assertEqual(second.result["commit"], commit)
        self.assertTrue(second.result["recovered_after_crash"])
        self.assertEqual(sh(repo, "git", "rev-list", "--count", f"{head}..ai/crash-recovery"), "1")

    def test_recovered_commit_stays_successful_if_push_is_later_disabled(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-recover-push-policy",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/recover-push-policy",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+durable\n"
            ),
            "run": [],
            "push": True,
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        first = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={},
            allow_push=True,
        )
        recovered = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={},
            allow_commit=False,
            allow_push=False,
        )
        self.assertEqual(recovered.result["status"], "success")
        self.assertEqual(recovered.result["commit"], first.result["commit"])
        self.assertTrue(recovered.result["recovered_after_crash"])
        self.assertEqual(recovered.result["push"]["status"], "error")
        self.assertIn("no longer enabled", recovered.result["push"]["error"])

    def test_retry_with_same_transaction_id_but_changed_request_is_rejected(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-hash-binding",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/hash-binding",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+one\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={},
        )
        changed = dict(tx)
        changed["commit_message"] = "different request bytes"
        with self.assertRaisesRegex(BridgeError, "already committed with a different request"):
            process_transaction(
                repo,
                "demo",
                changed,
                state_dir=state,
                safe_branch_prefix="ai/",
                commands={},
            )

    def test_commit_message_cannot_spoof_reserved_bridge_trailers(self):
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-trailer-spoof",
            "repo": "demo",
            "base_sha": "a" * 40,
            "branch": "ai/trailer-spoof",
            "patch": "x",
            "run": [],
            "commit_message": "hello\n\nLLM-Git-Bridge-Transaction: forged",
        }
        with self.assertRaisesRegex(BridgeError, "reserved bridge trailer"):
            validate_transaction(tx, safe_branch_prefix="ai/")


if __name__ == "__main__":
    unittest.main()
