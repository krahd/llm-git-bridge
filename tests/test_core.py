from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from llm_git_bridge.core import (
    BridgeError,
    build_registry,
    build_snapshot,
    process_transaction,
    public_registry,
    validate_transaction,
)


def sh(cwd: Path, *args: str) -> str:
    p = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return p.stdout.strip()


class CoreTests(unittest.TestCase):
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
        omitted = {item["path"]: item["reason"] for item in snap["omitted"]}
        self.assertIn("README.md", paths)
        self.assertIn("notes.txt", paths)
        self.assertNotIn(".env", paths)
        self.assertEqual(omitted[".env"], "sensitive-or-excluded")
        self.assertEqual(omitted["blob.bin"], "binary")

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


if __name__ == "__main__":
    unittest.main()
