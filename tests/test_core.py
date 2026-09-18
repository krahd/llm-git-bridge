from __future__ import annotations

import json
import os
import shutil
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
    reconcile_registry_membership,
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
    @classmethod
    def setUpClass(cls):
        cls._seed_root = Path(tempfile.mkdtemp(prefix="llmgb-seed-"))
        cls._seed_repo = cls._seed_root / "repo"
        cls._seed_repo.mkdir()
        sh(cls._seed_repo, "git", "init", "-q")
        sh(cls._seed_repo, "git", "config", "user.email", "test@example.invalid")
        sh(cls._seed_repo, "git", "config", "user.name", "Test User")
        (cls._seed_repo / "README.md").write_text("hello\n", encoding="utf-8")
        sh(cls._seed_repo, "git", "add", "README.md")
        sh(cls._seed_repo, "git", "commit", "-qm", "initial")
        cls._seed_head = sh(cls._seed_repo, "git", "rev-parse", "HEAD")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._seed_root, ignore_errors=True)

    def test_suite_imports_bridge_from_current_checkout(self):
        project_root = Path(__file__).resolve().parents[1]
        imported_core = Path(core.__file__).resolve()
        self.assertTrue(
            imported_core.is_relative_to(project_root),
            f"tests imported bridge code outside current checkout: {imported_core}",
        )

    def clone_seed_repo(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self._seed_repo, destination, symlinks=True)
        return destination

    def make_repo(self) -> Path:
        parent = Path(tempfile.mkdtemp(prefix="llmgb-test-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        return self.clone_seed_repo(parent / "repo")

    def test_registry_does_not_publish_paths(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-root-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        repo = self.clone_seed_repo(parent / "alpha")

        local = build_registry([parent])
        repo_id = next(iter(local["repos"]))
        public = public_registry(
            local,
            {repo_id: {"read": True, "edit": True, "push": False}},
        )
        blob = json.dumps(public)
        self.assertNotIn(str(parent), blob)
        self.assertNotIn("git_marker_id", blob)
        self.assertNotIn("repo_identity", blob)
        self.assertNotIn("retired_repo_ids", blob)
        self.assertEqual(public["repos"][0]["name"], "alpha")
        self.assertEqual(
            public["repos"][0]["capabilities"],
            {"read": True, "edit": True, "push": False},
        )

    def test_public_registry_requires_complete_boolean_capabilities(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-root-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        self.clone_seed_repo(parent / "alpha")
        local = build_registry([parent])
        repo_id = next(iter(local["repos"]))

        with self.assertRaisesRegex(BridgeError, "public capabilities"):
            public_registry(local, {})
        with self.assertRaisesRegex(BridgeError, "public capabilities"):
            public_registry(local, {repo_id: {"read": True, "edit": True, "push": "yes"}})

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

    def test_internal_git_ignores_repository_routing_environment(self):
        repo = self.make_repo()
        poison = self.make_repo()
        (poison / "poison.txt").write_text("poison\n", encoding="utf-8")
        sh(poison, "git", "add", "poison.txt")
        sh(poison, "git", "commit", "-qm", "poison")
        expected_head = sh(repo, "git", "rev-parse", "HEAD")
        with patch.dict(
            os.environ,
            {
                "GIT_DIR": str(poison / ".git"),
                "GIT_WORK_TREE": str(poison),
                "GIT_INDEX_FILE": str(poison / ".git" / "index"),
                "GIT_OBJECT_DIRECTORY": str(poison / ".git" / "objects"),
                "GIT_COMMON_DIR": str(poison / ".git"),
                "GIT_NAMESPACE": "poison",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.bare",
                "GIT_CONFIG_VALUE_0": "true",
            },
            clear=False,
        ):
            state = core_mod.repo_state(repo)
            direct_head = core_mod.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=10
            ).stdout.strip()
        self.assertEqual(state["head"], expected_head)
        self.assertEqual(direct_head, expected_head)
        self.assertFalse(state["dirty"])

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
                "GIT_DIR": "/tmp/poison.git",
                "GIT_WORK_TREE": "/tmp/poison-worktree",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.bare",
                "GIT_CONFIG_VALUE_0": "true",
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
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("GIT_WORK_TREE", env)
        self.assertNotIn("GIT_CONFIG_COUNT", env)
        self.assertNotIn("GIT_CONFIG_KEY_0", env)
        self.assertNotIn("GIT_CONFIG_VALUE_0", env)
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

    def test_configured_command_cancellation_kills_process_group(self):
        repo = self.make_repo()
        started = time.monotonic()
        result = run_configured_command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=repo,
            env=os.environ.copy(),
            timeout=10,
            cancel_check=lambda: time.monotonic() - started >= 0.2,
        )
        self.assertTrue(result.cancelled)
        self.assertFalse(result.timed_out)
        self.assertLess(time.monotonic() - started, 3)

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

    def test_cancelled_configured_command_leaves_no_commit_or_worktree(self):
        repo = self.make_repo()
        head = self._seed_head
        txid = "tx-command-cancel"
        branch = "ai/command-cancel"
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "demo",
            "base_sha": head,
            "branch": branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+cancelled\n"
            ),
            "run": ["slow"],
        }
        with self.assertRaisesRegex(BridgeError, "configured command cancelled"):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=state,
                safe_branch_prefix="ai/",
                commands={"slow": [sys.executable, "-c", "import time; time.sleep(10)"]},
                cancel_check=lambda: True,
            )
        self.assertFalse((state / "worktrees" / txid).exists())
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(repo), "show-ref", "--verify", f"refs/heads/{branch}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).returncode,
            0,
        )
        self.assertEqual(sh(repo, "git", "rev-parse", "HEAD"), head)

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

    def test_transaction_validation_accepts_sha256_object_id(self):
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-sha256",
            "repo": "demo",
            "base_sha": "a" * 64,
            "branch": "ai/sha256",
            "patch": "x",
            "run": [],
        }
        self.assertIs(validate_transaction(tx, safe_branch_prefix="ai/"), tx)

    def test_sha256_repository_transaction_commits_successfully(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-sha256-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        repo = parent / "sha256-repo"
        init = subprocess.run(
            ["git", "init", "-q", "--object-format=sha256", str(repo)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if init.returncode != 0:
            self.skipTest("Git build does not support SHA-256 repositories")
        sh(repo, "git", "config", "user.email", "test@example.invalid")
        sh(repo, "git", "config", "user.name", "Test User")
        (repo / "README.md").write_text("hello\n", encoding="utf-8")
        sh(repo, "git", "add", "README.md")
        sh(repo, "git", "commit", "-qm", "initial")
        head = sh(repo, "git", "rev-parse", "HEAD")
        self.assertEqual(len(head), 64)
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-sha256-e2e",
            "repo": "sha256-repo",
            "base_sha": head,
            "branch": "ai/sha256-edit",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1 @@\n"
                "-hello\n"
                "+hello sha256\n"
            ),
            "run": [],
        }
        outcome = process_transaction(
            repo,
            "sha256-repo",
            tx,
            state_dir=parent / "sha256-state",
            safe_branch_prefix="ai/",
        )
        self.assertEqual(outcome.result["status"], "success")
        self.assertEqual(len(outcome.result["commit"]), 64)
        self.assertEqual(sh(repo, "git", "show", "ai/sha256-edit:README.md"), "hello sha256")

    def test_safe_branch_validation_matches_documented_ref_component_rules_without_git(self):
        with patch.object(core_mod, "run", side_effect=AssertionError("must not spawn Git")):
            self.assertEqual(core_mod.validate_safe_branch_name("ai/good-name", "ai/"), "ai/good-name")
            for branch in ("ai/.hidden", "ai/middle.lock/leaf", "ai/double..dot", "ai/trailing."):
                with self.subTest(branch=branch):
                    with self.assertRaises(BridgeError):
                        core_mod.validate_safe_branch_name(branch, "ai/")

    def test_changed_file_mode_validation_uses_one_raw_diff_query(self):
        repo = self.make_repo()
        for index in range(12):
            (repo / f"file-{index}.txt").write_text(f"{index}\n", encoding="utf-8")
        sh(repo, "git", "add", ".")
        original_git = core_mod.git
        diff_queries: list[tuple[str, ...]] = []

        def counted_git(worktree, *args, **kwargs):
            if args and args[0] == "diff":
                diff_queries.append(tuple(args))
            return original_git(worktree, *args, **kwargs)

        with patch.object(core_mod, "git", side_effect=counted_git):
            core_mod.ensure_changed_files_are_regular(repo)
        self.assertEqual(len(diff_queries), 1)
        self.assertIn("--raw", diff_queries[0])
        self.assertIn("-z", diff_queries[0])
        self.assertIn("--no-renames", diff_queries[0])

    def test_process_transaction_creates_commit_and_cleans_worktree(self):
        repo = self.make_repo()
        head = self._seed_head
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
        assert outcome.snapshot is not None
        self.assertEqual(outcome.snapshot["head"], commit)
        self.assertEqual(outcome.snapshot["branch"], "ai/test-change")
        self.assertFalse(outcome.snapshot["dirty"])
        self.assertFalse(outcome.snapshot["tracked_dirty"])
        readme = next(x for x in outcome.snapshot["files"] if x["path"] == "README.md")
        self.assertEqual(readme["content"], "hello\nworld\n")

    def test_transaction_rejects_tracked_dirty_authoritative_checkout_before_claim(self):
        for staged in (False, True):
            with self.subTest(staged=staged):
                repo = self.make_repo()
                head = sh(repo, "git", "rev-parse", "HEAD")
                (repo / "README.md").write_text("dirty\n", encoding="utf-8")
                if staged:
                    sh(repo, "git", "add", "README.md")
                txid = f"tx-dirty-{'staged' if staged else 'unstaged'}"
                tx = {
                    "protocol": 2,
                    "kind": "transaction",
                    "transaction_id": txid,
                    "repo": "demo",
                    "base_sha": head,
                    "branch": f"ai/{txid}",
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
                with self.assertRaisesRegex(BridgeError, "tracked working tree|index has staged"):
                    process_transaction(
                        repo,
                        "demo",
                        tx,
                        state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                        safe_branch_prefix="ai/",
                        commands={},
                    )
                claim = subprocess.run(
                    [
                        "git", "-C", str(repo), "show-ref", "--verify",
                        f"refs/llm-git-bridge/transactions/{txid}",
                    ],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                self.assertNotEqual(claim.returncode, 0)

    def test_transaction_allows_and_preserves_untracked_authoritative_files(self):
        repo = self.make_repo()
        head = self._seed_head
        untracked = repo / "local-only.txt"
        untracked.write_text("local\n", encoding="utf-8")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-untracked-authoritative",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/untracked-authoritative",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+bridge\n"
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
        self.assertEqual(untracked.read_text(encoding="utf-8"), "local\n")
        committed = sh(repo, "git", "ls-tree", "-r", "--name-only", outcome.result["commit"])
        self.assertNotIn("local-only.txt", committed.splitlines())

    def test_binary_patch_is_committed_but_omitted_from_snapshot(self):
        repo = self.make_repo()
        blob = repo / "blob.bin"
        blob.write_bytes(b"\x00one\xff")
        sh(repo, "git", "add", "blob.bin")
        sh(repo, "git", "commit", "-qm", "add binary")
        head = sh(repo, "git", "rev-parse", "HEAD")
        blob.write_bytes(b"\x00two\xfe")
        patch_proc = subprocess.run(
            ["git", "-C", str(repo), "diff", "--binary", "HEAD", "--", "blob.bin"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        patch_text = patch_proc.stdout
        sh(repo, "git", "checkout", "--", "blob.bin")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-binary-patch",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/binary-patch",
            "patch": patch_text,
            "run": [],
            "publish_snapshot": True,
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
        self.assertIsNotNone(outcome.snapshot)
        assert outcome.snapshot is not None
        self.assertNotIn("blob.bin", {item["path"] for item in outcome.snapshot["files"]})
        omitted = {item.get("path"): item.get("reason") for item in outcome.snapshot["omitted"]}
        self.assertEqual(omitted.get("blob.bin"), "binary")

    def test_patch_with_space_and_unicode_path_is_applied_exactly(self):
        repo = self.make_repo()
        weird = repo / "space cafe.txt"
        weird.write_text("one\n", encoding="utf-8")
        sh(repo, "git", "add", weird.name)
        sh(repo, "git", "commit", "-qm", "add weird path")
        head = sh(repo, "git", "rev-parse", "HEAD")
        weird.write_text("one\ntwo\n", encoding="utf-8")
        patch_proc = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotePath=true", "diff", "HEAD", "--", weird.name],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        patch_text = patch_proc.stdout
        sh(repo, "git", "checkout", "--", weird.name)
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-weird-path",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/weird-path-file",
            "patch": patch_text,
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
        data = subprocess.run(
            ["git", "-C", str(repo), "show", f"{outcome.result['commit']}:{weird.name}"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout
        self.assertEqual(data, "one\ntwo\n")

    def test_commit_object_is_verified_before_branch_publication(self):
        repo = self.make_repo()
        head = self._seed_head
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-verify-commit-object",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/verify-commit-object",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+verified\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        original_run = core_mod.run

        def corrupt_commit_tree(argv, **kwargs):
            if argv and Path(argv[0]).name == "git" and "commit-tree" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout=head + "\n", stderr="")
            return original_run(argv, **kwargs)

        with patch.object(core_mod, "run", side_effect=corrupt_commit_tree):
            with self.assertRaisesRegex(BridgeError, "commit object verification failed"):
                process_transaction(
                    repo,
                    "demo",
                    tx,
                    state_dir=state,
                    safe_branch_prefix="ai/",
                    commands={},
                )
        branch = subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "refs/heads/ai/verify-commit-object"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertNotEqual(branch.returncode, 0)

    def test_simple_no_command_transaction_has_bounded_git_process_budget(self):
        repo = self.make_repo()
        head = self._seed_head
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-process-budget",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/process-budget",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+budget\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        original_run = core_mod.run
        git_calls: list[tuple[str, ...]] = []

        def counted_run(argv, *args, **kwargs):
            if argv and Path(argv[0]).name == "git":
                git_calls.append(tuple(str(item) for item in argv))
            return original_run(argv, *args, **kwargs)

        with patch.object(core_mod, "run", side_effect=counted_run):
            outcome = process_transaction(
                repo,
                "demo",
                tx,
                state_dir=state,
                safe_branch_prefix="ai/",
                commands={},
            )
        self.assertEqual(outcome.result["status"], "success")
        # One post-commit read verifies tree, parent, transaction trailer, and
        # request hash before the target ref is published. Keep that correctness
        # check in the explicit subprocess budget.
        self.assertLessEqual(len(git_calls), 13, git_calls)

    def test_failed_patch_application_is_atomic_and_leaves_no_branch(self):
        repo = self.make_repo()
        head = self._seed_head
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-bad-apply",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/bad-apply",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " not-the-current-line\n"
                "+should-not-apply\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        with self.assertRaisesRegex(BridgeError, "git apply failed"):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=state,
                safe_branch_prefix="ai/",
                commands={},
            )
        self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "hello\n")
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(repo), "show-ref", "--verify", "refs/heads/ai/bad-apply"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).returncode,
            0,
        )

    def test_existing_branch_requires_exact_base(self):
        repo = self.make_repo()
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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


    def test_incremental_registry_discovers_new_repo_without_resampling_known_repo(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-discovery-"))
        try:
            first = parent / "first"
            shutil.copytree(self._seed_repo, first, symlinks=True)
            previous = build_registry([parent])
            first_id = next(iter(previous["repos"]))

            second = parent / "second"
            shutil.copytree(self._seed_repo, second, symlinks=True)
            with patch.object(core_mod, "repo_state", wraps=core_mod.repo_state) as state, patch.object(
                core_mod, "is_git_repo", wraps=core_mod.is_git_repo
            ) as is_repo:
                current, added, removed, updated = reconcile_registry_membership([parent], previous)

            by_path = {entry["path"]: rid for rid, entry in current["repos"].items()}
            self.assertEqual(by_path[str(first.resolve())], first_id)
            self.assertEqual(len(added), 1)
            self.assertEqual(removed, ())
            self.assertEqual(updated, ())
            self.assertEqual(state.call_count, 1)
            self.assertEqual(is_repo.call_count, 1)
            self.assertEqual(state.call_args.args[0], second.resolve())
            self.assertEqual(is_repo.call_args.args[0], second.resolve())
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_incremental_registry_removes_missing_repo_under_available_root(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-discovery-remove-"))
        try:
            repo = parent / "gone"
            shutil.copytree(self._seed_repo, repo, symlinks=True)
            previous = build_registry([parent])
            repo_id = next(iter(previous["repos"]))
            shutil.rmtree(repo)

            current, added, removed, updated = reconcile_registry_membership([parent], previous)

            self.assertEqual(added, ())
            self.assertEqual(removed, (repo_id,))
            self.assertEqual(updated, ())
            self.assertEqual(current["repos"], {})
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_incremental_registry_preserves_entries_when_approved_root_is_unavailable(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-discovery-offline-"))
        repo = parent / "repo"
        shutil.copytree(self._seed_repo, repo, symlinks=True)
        previous = build_registry([parent])
        repo_id = next(iter(previous["repos"]))
        shutil.rmtree(parent)

        current, added, removed, updated = reconcile_registry_membership([parent], previous)

        self.assertEqual(added, ())
        self.assertEqual(removed, ())
        self.assertEqual(updated, ())
        self.assertIn(repo_id, current["repos"])

    def test_full_registry_preserves_entries_when_approved_root_is_unavailable(self):
        base = Path(tempfile.mkdtemp(prefix="llmgb-full-offline-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        available = base / "available"
        unavailable = base / "unavailable"
        available.mkdir()
        unavailable.mkdir()
        self.clone_seed_repo(available / "one")
        self.clone_seed_repo(unavailable / "two")
        previous = build_registry([available, unavailable])
        old_by_name = {entry["name"]: rid for rid, entry in previous["repos"].items()}
        shutil.rmtree(unavailable)

        current = build_registry([available, unavailable], previous=previous)

        self.assertIn(old_by_name["two"], current["repos"])
        self.assertEqual(current["repos"][old_by_name["two"]]["name"], "two")
        self.assertNotIn(old_by_name["two"], current.get("retired_repo_ids", []))

    def test_full_registry_move_uses_unique_history_identity_when_marker_changes(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-full-move-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        old_path = self.clone_seed_repo(parent / "old-name")
        previous = build_registry([parent])
        old_id = next(iter(previous["repos"]))
        previous["repos"][old_id]["git_marker_id"] = "forced-marker-mismatch"
        new_path = parent / "new-name"
        old_path.rename(new_path)

        current = build_registry([parent], previous=previous)

        by_path = {entry["path"]: rid for rid, entry in current["repos"].items()}
        self.assertEqual(by_path[str(new_path.resolve())], old_id)
        self.assertNotIn(old_id, current.get("retired_repo_ids", []))

    def test_incremental_same_history_clone_cannot_steal_id_from_unavailable_root(self):
        base = Path(tempfile.mkdtemp(prefix="llmgb-offline-clone-"))
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        offline_root = base / "offline-root"
        live_root = base / "live-root"
        offline_root.mkdir()
        live_root.mkdir()
        self.clone_seed_repo(offline_root / "policy-repo")
        previous = build_registry([offline_root, live_root])
        old_id = next(iter(previous["repos"]))
        shutil.rmtree(offline_root)
        clone = self.clone_seed_repo(live_root / "clone")

        current, added, removed, updated = reconcile_registry_membership(
            [offline_root, live_root], previous
        )

        self.assertIn(old_id, current["repos"])
        self.assertEqual(current["repos"][old_id]["path"], previous["repos"][old_id]["path"])
        clone_id = next(
            rid for rid, entry in current["repos"].items()
            if entry["path"] == str(clone.resolve())
        )
        self.assertNotEqual(clone_id, old_id)
        self.assertEqual(added, (clone_id,))
        self.assertEqual(removed, ())
        self.assertEqual(updated, ())

    def test_incremental_registry_ignores_fake_git_marker(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-discovery-fake-"))
        try:
            fake = parent / "fake"
            fake.mkdir()
            (fake / ".git").write_text("not a gitdir\n", encoding="utf-8")
            previous = {"version": 1, "generated_at": "test", "repos": {}}

            current, added, removed, updated = reconcile_registry_membership([parent], previous)

            self.assertEqual(current["repos"], {})
            self.assertEqual(added, ())
            self.assertEqual(removed, ())
            self.assertEqual(updated, ())
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_full_registry_ignores_unborn_new_repo_without_poisoning_scan(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-full-unborn-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        known = self.clone_seed_repo(parent / "known")
        unborn = parent / "unborn"
        unborn.mkdir()
        sh(unborn, "git", "init", "-q")
        registry = build_registry([parent])
        self.assertEqual({entry["name"] for entry in registry["repos"].values()}, {known.name})

    def test_full_registry_identity_failure_for_known_repo_remains_fail_closed(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-full-known-identity-"))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        self.clone_seed_repo(parent / "known")
        previous = build_registry([parent])
        with patch.object(core_mod, "repo_identity", side_effect=BridgeError("identity unavailable")):
            with self.assertRaisesRegex(BridgeError, "identity unavailable"):
                build_registry([parent], previous=previous)

    def test_incremental_registry_ignores_unborn_new_repo_until_it_has_history(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-discovery-unborn-"))
        try:
            known = parent / "known"
            shutil.copytree(self._seed_repo, known, symlinks=True)
            previous = build_registry([parent])
            known_id = next(iter(previous["repos"]))

            unborn = parent / "unborn"
            unborn.mkdir()
            sh(unborn, "git", "init", "-q")

            current, added, removed, updated = reconcile_registry_membership([parent], previous)

            self.assertEqual(set(current["repos"]), {known_id})
            self.assertEqual(added, ())
            self.assertEqual(removed, ())
            self.assertEqual(updated, ())

            sh(unborn, "git", "config", "user.email", "unborn@example.invalid")
            sh(unborn, "git", "config", "user.name", "Unborn")
            (unborn / "README.md").write_text("ready\n", encoding="utf-8")
            sh(unborn, "git", "add", "README.md")
            sh(unborn, "git", "commit", "-qm", "initial")

            admitted, added, removed, updated = reconcile_registry_membership([parent], current)
            self.assertEqual(len(added), 1)
            self.assertEqual(removed, ())
            self.assertEqual(updated, ())
            self.assertIn(known_id, admitted["repos"])
            self.assertEqual(admitted["repos"][added[0]]["path"], str(unborn.resolve()))
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_incremental_registry_keeps_known_identity_validation_fail_closed(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-discovery-known-fail-"))
        try:
            repo = parent / "known"
            shutil.copytree(self._seed_repo, repo, symlinks=True)
            previous = build_registry([parent])
            # Force the cheap marker to look changed so the policy-bearing history
            # identity must be revalidated rather than reused.
            previous["repos"][next(iter(previous["repos"]))]["git_marker_id"] = "changed-marker"

            with patch.object(core_mod, "repo_identity", side_effect=BridgeError("identity unavailable")):
                with self.assertRaisesRegex(BridgeError, "identity unavailable"):
                    reconcile_registry_membership([parent], previous)
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_replaced_repo_at_same_path_gets_new_id_and_retires_old_policy_id(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-discovery-replace-"))
        try:
            repo = parent / "demo"
            shutil.copytree(self._seed_repo, repo, symlinks=True)
            previous = build_registry([parent])
            old_id = next(iter(previous["repos"]))
            old_marker = previous["repos"][old_id]["git_marker_id"]
            shutil.rmtree(repo)
            repo.mkdir()
            sh(repo, "git", "init", "-q")
            sh(repo, "git", "config", "user.email", "replacement@example.invalid")
            sh(repo, "git", "config", "user.name", "Replacement")
            (repo / "NEW.md").write_text("replacement\n", encoding="utf-8")
            sh(repo, "git", "add", "NEW.md")
            sh(repo, "git", "commit", "-qm", "replacement")

            current, added, removed, updated = reconcile_registry_membership([parent], previous)

            self.assertEqual(removed, (old_id,))
            self.assertEqual(updated, ())
            self.assertEqual(len(added), 1)
            new_id = added[0]
            self.assertNotEqual(new_id, old_id)
            self.assertIn(old_id, current["retired_repo_ids"])
            self.assertNotEqual(current["repos"][new_id]["git_marker_id"], old_marker)
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_moved_repo_keeps_id_when_history_identity_matches(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-discovery-move-"))
        try:
            first = parent / "before"
            shutil.copytree(self._seed_repo, first, symlinks=True)
            previous = build_registry([parent])
            repo_id = next(iter(previous["repos"]))
            old_identity = previous["repos"][repo_id]["repo_identity"]
            second = parent / "after"
            first.rename(second)

            current, added, removed, updated = reconcile_registry_membership([parent], previous)

            self.assertEqual(added, ())
            self.assertEqual(removed, ())
            self.assertEqual(updated, (repo_id,))
            self.assertEqual(current["repos"][repo_id]["path"], str(second.resolve()))
            self.assertEqual(current["repos"][repo_id]["name"], "after")
            self.assertEqual(current["repos"][repo_id]["repo_identity"], old_identity)
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_additional_clone_of_same_history_does_not_steal_existing_id(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-discovery-clone-"))
        try:
            original = parent / "z-original"
            shutil.copytree(self._seed_repo, original, symlinks=True)
            previous = build_registry([parent])
            original_id = next(iter(previous["repos"]))
            clone = parent / "a-clone"
            shutil.copytree(self._seed_repo, clone, symlinks=True)

            current, added, removed, updated = reconcile_registry_membership([parent], previous)

            self.assertEqual(removed, ())
            self.assertEqual(updated, ())
            self.assertEqual(current["repos"][original_id]["path"], str(original.resolve()))
            self.assertEqual(len(added), 1)
            clone_id = added[0]
            self.assertNotEqual(clone_id, original_id)
            self.assertEqual(current["repos"][clone_id]["path"], str(clone.resolve()))
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_full_scan_does_not_reuse_retired_repository_id(self):
        parent = Path(tempfile.mkdtemp(prefix="llmgb-retired-id-"))
        try:
            repo = parent / "demo"
            shutil.copytree(self._seed_repo, repo, symlinks=True)
            previous = build_registry([parent])
            old_id = next(iter(previous["repos"]))
            shutil.rmtree(repo)
            removed = build_registry([parent], previous=previous)
            self.assertIn(old_id, removed["retired_repo_ids"])

            shutil.copytree(self._seed_repo, repo, symlinks=True)
            current = build_registry([parent], previous=removed)
            new_id = next(iter(current["repos"]))
            self.assertNotEqual(new_id, old_id)
            self.assertIn(old_id, current["retired_repo_ids"])
        finally:
            shutil.rmtree(parent, ignore_errors=True)

    def test_push_requires_local_opt_in(self):
        repo = self.make_repo()
        head = self._seed_head
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

    def test_current_branch_write_requires_explicit_opt_in(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        branch = sh(repo, "git", "symbolic-ref", "--short", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-current-disabled",
            "repo": "demo",
            "base_sha": head,
            "branch": branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+current\n"
            ),
            "run": [],
        }
        with self.assertRaisesRegex(BridgeError, "branch must start"):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
            )
        self.assertEqual(sh(repo, "git", "rev-parse", "HEAD"), head)
        self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "hello\n")

    def test_current_branch_write_fast_forwards_checkout_and_pushes_origin(self):
        repo = self.make_repo()
        remote = Path(tempfile.mkdtemp(prefix="llmgb-remote-")) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        sh(repo, "git", "remote", "add", "origin", str(remote))
        branch = sh(repo, "git", "symbolic-ref", "--short", "HEAD")
        head = sh(repo, "git", "rev-parse", "HEAD")
        sh(repo, "git", "push", "-q", "origin", f"{head}:refs/heads/{branch}")
        marker = repo / "PUSH_HOOK_RAN"
        hook = repo / ".git" / "hooks" / "pre-push"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 99\n", encoding="utf-8")
        hook.chmod(0o755)
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-current-enabled",
            "repo": "demo",
            "base_sha": head,
            "branch": branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+current\n"
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
            allow_current_branch_write=True,
        )
        commit = outcome.result["commit"]
        self.assertEqual(outcome.result["status"], "success")
        self.assertEqual(outcome.result["push"]["status"], "success")
        self.assertFalse(marker.exists())
        self.assertEqual(sh(repo, "git", "rev-parse", "HEAD"), commit)
        self.assertEqual(sh(repo, "git", "symbolic-ref", "--short", "HEAD"), branch)
        self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "hello\ncurrent\n")
        self.assertEqual(sh(repo, "git", "status", "--porcelain"), "")
        remote_sha = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout.strip()
        self.assertEqual(remote_sha, commit)

    def test_current_branch_authority_does_not_allow_other_non_safe_branch(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        current = sh(repo, "git", "symbolic-ref", "--short", "HEAD")
        other = "main" if current != "main" else "master"
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-current-wrong-branch",
            "repo": "demo",
            "base_sha": head,
            "branch": other,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+wrong\n"
            ),
            "run": [],
        }
        with self.assertRaisesRegex(BridgeError, "branch must start"):
            process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
                allow_current_branch_write=True,
            )

    def test_current_branch_retry_recovers_without_second_commit(self):
        repo = self.make_repo()
        head = sh(repo, "git", "rev-parse", "HEAD")
        branch = sh(repo, "git", "symbolic-ref", "--short", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-current-recovery",
            "repo": "demo",
            "base_sha": head,
            "branch": branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+recovered\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        first = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={},
            allow_current_branch_write=True,
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
            allow_current_branch_write=True,
        )
        self.assertEqual(second.result["commit"], commit)
        self.assertTrue(second.result["recovered_after_crash"])
        self.assertEqual(sh(repo, "git", "rev-list", "--count", f"{head}..{branch}"), "1")

    def test_current_branch_race_does_not_leave_wrong_branch_advanced(self):
        repo = self.make_repo()
        base = sh(repo, "git", "rev-parse", "HEAD")
        target = sh(repo, "git", "symbolic-ref", "--short", "HEAD")
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-current-branch-race",
            "repo": "demo",
            "base_sha": base,
            "branch": target,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+race\n"
            ),
            "run": [],
        }
        original_run = core_mod.run
        switched = False

        def race_before_merge(args, **kwargs):
            nonlocal switched
            if not switched and args[:3] == ["git", "-C", str(repo)] and "merge" in args:
                switched = True
                subprocess.run(
                    ["git", "-C", str(repo), "switch", "-q", "-c", "human-race", base],
                    check=True,
                )
            return original_run(args, **kwargs)

        with patch.object(core_mod, "run", side_effect=race_before_merge):
            with self.assertRaisesRegex(BridgeError, "current branch changed during commit publication"):
                process_transaction(
                    repo,
                    "demo",
                    tx,
                    state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                    safe_branch_prefix="ai/",
                    commands={},
                    allow_current_branch_write=True,
                )

        self.assertTrue(switched)
        self.assertEqual(sh(repo, "git", "rev-parse", target), base)
        self.assertEqual(sh(repo, "git", "rev-parse", "human-race"), base)
        self.assertEqual(sh(repo, "git", "symbolic-ref", "--short", "HEAD"), "human-race")
        self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "hello\n")
        self.assertEqual(sh(repo, "git", "status", "--porcelain"), "")

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
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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
        head = self._seed_head
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


    def test_retry_recovers_after_crash_leaves_new_branch_worktree_at_base(self):
        repo = self.make_repo()
        head = self._seed_head
        txid = "tx-crash-after-worktree"
        branch = "ai/crash-after-worktree"
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        wt = state / "worktrees" / txid
        wt.parent.mkdir(parents=True, exist_ok=True)
        core_mod.add_disposable_worktree(repo, "-b", branch, str(wt), head)
        self.assertEqual(sh(repo, "git", "rev-parse", branch), head)
        self.assertTrue(wt.exists())

        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "demo",
            "base_sha": head,
            "branch": branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+after-crash\n"
            ),
            "run": [],
        }
        outcome = process_transaction(
            repo, "demo", tx, state_dir=state, safe_branch_prefix="ai/", commands={}
        )
        self.assertEqual(outcome.result["status"], "success")
        self.assertEqual(sh(repo, "git", "rev-list", "--count", f"{head}..{branch}"), "1")
        self.assertFalse(wt.exists())

    def test_retry_discards_staged_state_left_by_crashed_worktree_before_reapplying(self):
        repo = self.make_repo()
        head = self._seed_head
        txid = "tx-crash-after-patch"
        branch = "ai/crash-after-patch"
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        wt = state / "worktrees" / txid
        wt.parent.mkdir(parents=True, exist_ok=True)
        core_mod.add_disposable_worktree(repo, "-b", branch, str(wt), head)
        (wt / "README.md").write_text("hello\nstale-staged\n", encoding="utf-8")
        sh(wt, "git", "add", "README.md")
        self.assertTrue(sh(wt, "git", "diff", "--cached", "--name-only"))

        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "demo",
            "base_sha": head,
            "branch": branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+fresh-retry\n"
            ),
            "run": [],
        }
        outcome = process_transaction(
            repo, "demo", tx, state_dir=state, safe_branch_prefix="ai/", commands={}
        )
        committed = sh(repo, "git", "show", f"{outcome.result['commit']}:README.md")
        self.assertEqual(committed, "hello\nfresh-retry")
        self.assertEqual(sh(repo, "git", "rev-list", "--count", f"{head}..{branch}"), "1")
        self.assertFalse(wt.exists())

    def test_external_branch_move_before_commit_cannot_be_overwritten(self):
        repo = self.make_repo()
        head = self._seed_head
        branch = "ai/stale-branch-race"
        sh(repo, "git", "branch", branch, head)
        tree = sh(repo, "git", "write-tree")
        external_commit = sh(
            repo, "git", "commit-tree", tree, "-p", head, "-m", "external branch movement"
        )
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-stale-branch-race",
            "repo": "demo",
            "base_sha": head,
            "branch": branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+bridge-change\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        original_run = core_mod.run
        moved = False

        def run_with_branch_race(argv, **kwargs):
            nonlocal moved
            if not moved and argv and argv[0] == "git" and "commit-tree" in argv:
                moved = True
                original_run(
                    [
                        "git", "-C", str(repo), "update-ref",
                        f"refs/heads/{branch}", external_commit, head,
                    ]
                )
            return original_run(argv, **kwargs)

        with patch.object(core_mod, "run", side_effect=run_with_branch_race):
            with self.assertRaises(BridgeError):
                process_transaction(
                    repo, "demo", tx, state_dir=state, safe_branch_prefix="ai/", commands={}
                )

        self.assertTrue(moved)
        self.assertEqual(sh(repo, "git", "rev-parse", branch), external_commit)
        log = sh(repo, "git", "log", "-1", "--format=%B", branch)
        self.assertNotIn("LLM-Git-Bridge-Transaction", log)

    def test_symbolic_safe_branch_cannot_redirect_publication_to_protected_branch(self):
        repo = self.make_repo()
        protected_branch = sh(repo, "git", "branch", "--show-current")
        base = sh(repo, "git", "rev-parse", "HEAD")
        sh(repo, "git", "switch", "--detach", "-q", base)
        target_branch = "ai/symbolic-alias"
        sh(
            repo,
            "git",
            "symbolic-ref",
            f"refs/heads/{target_branch}",
            f"refs/heads/{protected_branch}",
        )
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-symbolic-safe-branch",
            "repo": "demo",
            "base_sha": base,
            "branch": target_branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+must-not-reach-protected\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        with self.assertRaisesRegex(BridgeError, "symbolic local branch ref is not allowed"):
            process_transaction(
                repo, "demo", tx, state_dir=state,
                safe_branch_prefix="ai/", commands={},
            )
        self.assertEqual(sh(repo, "git", "rev-parse", f"refs/heads/{protected_branch}"), base)
        self.assertEqual(
            sh(repo, "git", "symbolic-ref", f"refs/heads/{target_branch}"),
            f"refs/heads/{protected_branch}",
        )

    def test_symbolic_ref_race_cannot_redirect_publication_to_protected_branch(self):
        repo = self.make_repo()
        protected_branch = sh(repo, "git", "branch", "--show-current")
        base = sh(repo, "git", "rev-parse", "HEAD")
        target_branch = "ai/symbolic-race"
        sh(repo, "git", "branch", target_branch, base)
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-symbolic-ref-race",
            "repo": "demo",
            "base_sha": base,
            "branch": target_branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+bridge-change\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        original_run = core_mod.run
        raced = False
        target_ref = f"refs/heads/{target_branch}"

        def run_with_symbolic_race(argv, **kwargs):
            nonlocal raced
            if (
                not raced
                and argv
                and argv[0] == "git"
                and "update-ref" in argv
                and target_ref in argv
                and "commit-tree" not in argv
            ):
                raced = True
                original_run(
                    [
                        "git", "-C", str(repo), "symbolic-ref",
                        target_ref, f"refs/heads/{protected_branch}",
                    ]
                )
            return original_run(argv, **kwargs)

        with patch.object(core_mod, "run", side_effect=run_with_symbolic_race):
            outcome = process_transaction(
                repo, "demo", tx, state_dir=state,
                safe_branch_prefix="ai/", commands={},
            )

        self.assertTrue(raced)
        self.assertEqual(sh(repo, "git", "rev-parse", f"refs/heads/{protected_branch}"), base)
        self.assertEqual(sh(repo, "git", "rev-parse", target_ref), outcome.result["commit"])
        symbolic = subprocess.run(
            ["git", "-C", str(repo), "symbolic-ref", "-q", target_ref],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertNotEqual(symbolic.returncode, 0)

    def test_symbolic_transaction_claim_ref_is_rejected(self):
        repo = self.make_repo()
        base = self._seed_head
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-symbolic-claim",
            "repo": "demo",
            "base_sha": base,
            "branch": "ai/symbolic-claim",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+bridge-change\n"
            ),
            "run": [],
        }
        request_hash = core_mod.transaction_request_sha256(tx)
        payload = Path(tempfile.mkdtemp(prefix="llmgb-claim-")) / "claim"
        self.addCleanup(shutil.rmtree, payload.parent, ignore_errors=True)
        payload.write_text(request_hash + "\n", encoding="utf-8")
        oid = sh(repo, "git", "hash-object", "-w", str(payload))
        backing_ref = "refs/llm-git-bridge/test-claim-backing"
        claim_ref = core_mod._transaction_claim_ref(tx["transaction_id"])
        sh(repo, "git", "update-ref", backing_ref, oid)
        sh(repo, "git", "symbolic-ref", claim_ref, backing_ref)

        with self.assertRaisesRegex(
            BridgeError, "symbolic transaction identity claim ref is not allowed"
        ):
            process_transaction(
                repo, "demo", tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/", commands={},
            )
        self.assertEqual(sh(repo, "git", "rev-parse", "HEAD"), base)
        missing = subprocess.run(
            ["git", "-C", str(repo), "show-ref", "--verify", "refs/heads/ai/symbolic-claim"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertNotEqual(missing.returncode, 0)

    def test_existing_target_branch_checked_out_elsewhere_is_not_moved(self):
        repo = self.make_repo()
        head = self._seed_head
        branch = "ai/checked-out-target"
        sh(repo, "git", "branch", branch, head)
        sh(repo, "git", "switch", branch)
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-checked-out-target",
            "repo": "demo",
            "base_sha": head,
            "branch": branch,
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+bridge-change\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        with self.assertRaises(BridgeError):
            process_transaction(
                repo, "demo", tx, state_dir=state,
                safe_branch_prefix="ai/", commands={},
            )
        self.assertEqual(sh(repo, "git", "rev-parse", branch), head)
        self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "hello\n")
        self.assertEqual(sh(repo, "git", "status", "--porcelain"), "")

    def test_identical_retry_recovers_committed_transaction_without_second_commit(self):
        repo = self.make_repo()
        head = self._seed_head
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
            "publish_snapshot": True,
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
        self.assertIsNotNone(second.snapshot)
        assert second.snapshot is not None
        self.assertEqual(second.snapshot["head"], commit)
        self.assertEqual(second.snapshot["branch"], "ai/crash-recovery")
        self.assertFalse(second.snapshot["dirty"])
        self.assertEqual(sh(repo, "git", "rev-list", "--count", f"{head}..ai/crash-recovery"), "1")

    def test_recovery_ignores_git_replace_refs_when_verifying_bridge_commit(self):
        repo = self.make_repo()
        head = self._seed_head
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-replace-ref-recovery",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/replace-ref-recovery",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+durable\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        first = process_transaction(
            repo, "demo", tx, state_dir=state, safe_branch_prefix="ai/", commands={}
        )
        commit = first.result["commit"]
        tree = sh(repo, "git", "show", "-s", "--format=%T", head)
        replacement = sh(repo, "git", "commit-tree", tree, "-p", head, "-m", "replacement")
        sh(repo, "git", "replace", commit, replacement)

        recovered = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={},
            allow_commit=False,
        )
        self.assertEqual(recovered.result["commit"], commit)
        self.assertTrue(recovered.result["recovered_after_crash"])

    def test_recovery_ignores_legacy_git_grafts_when_verifying_bridge_commit(self):
        repo = self.make_repo()
        head = self._seed_head
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-graft-recovery",
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/graft-recovery",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+durable\n"
            ),
            "run": [],
        }
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        first = process_transaction(
            repo, "demo", tx, state_dir=state, safe_branch_prefix="ai/", commands={}
        )
        commit = first.result["commit"]
        grafts = repo / ".git" / "info" / "grafts"
        grafts.write_text(commit + "\n", encoding="ascii")

        recovered = process_transaction(
            repo,
            "demo",
            tx,
            state_dir=state,
            safe_branch_prefix="ai/",
            commands={},
            allow_commit=False,
        )
        self.assertEqual(recovered.result["commit"], commit)
        self.assertTrue(recovered.result["recovered_after_crash"])

    def test_recovered_commit_stays_successful_if_push_is_later_disabled(self):
        repo = self.make_repo()
        head = self._seed_head
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

    def test_changed_request_cannot_reuse_committed_transaction_id_on_different_branch(self):
        repo = self.make_repo()
        head = self._seed_head
        txid = "tx-global-hash-binding"
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        first = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": txid,
            "repo": "demo",
            "base_sha": head,
            "branch": "ai/hash-binding-original",
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
        process_transaction(
            repo, "demo", first, state_dir=state, safe_branch_prefix="ai/", commands={}
        )
        changed = dict(first)
        changed["branch"] = "ai/hash-binding-different"
        changed["patch"] = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1 +1,2 @@\n"
            " hello\n"
            "+two\n"
        )
        with self.assertRaisesRegex(BridgeError, "transaction_id.*different request"):
            process_transaction(
                repo, "demo", changed, state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-lost-")),
                safe_branch_prefix="ai/", commands={},
            )
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(repo), "show-ref", "--verify", "refs/heads/ai/hash-binding-different"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ).returncode,
            0,
        )

    def test_retry_with_same_transaction_id_but_changed_request_is_rejected(self):
        repo = self.make_repo()
        head = self._seed_head
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
        with self.assertRaisesRegex(BridgeError, "already (?:committed|claimed).*different request"):
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


    def test_repo_state_reports_clean_tracked_untracked_and_detached_states(self):
        repo = self.make_repo()
        clean = core.repo_state(repo)
        self.assertFalse(clean["dirty"])
        self.assertEqual(clean["branch"], sh(repo, "git", "branch", "--show-current"))

        (repo / "README.md").write_text("changed\n", encoding="utf-8")
        tracked = core.repo_state(repo)
        self.assertTrue(tracked["tracked_dirty"])
        self.assertFalse(tracked["untracked"])

        sh(repo, "git", "checkout", "--", "README.md")
        (repo / "untracked.txt").write_text("x\n", encoding="utf-8")
        untracked = core.repo_state(repo)
        self.assertFalse(untracked["tracked_dirty"])
        self.assertTrue(untracked["untracked"])

        (repo / "untracked.txt").unlink()
        sh(repo, "git", "checkout", "--detach", "HEAD")
        detached = core.repo_state(repo)
        self.assertIsNone(detached["branch"])
        self.assertEqual(detached["head"], sh(repo, "git", "rev-parse", "HEAD"))

    def test_command_workspace_contains_tracked_files_but_not_untracked_tree(self):
        repo = self.make_repo()
        (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        sh(repo, "git", "add", "tracked.txt")
        sh(repo, "git", "commit", "-qm", "tracked")
        (repo / "tracked.txt").write_text("staged version\n", encoding="utf-8")
        sh(repo, "git", "add", "tracked.txt")
        untracked = repo / "node_modules" / "pkg"
        untracked.mkdir(parents=True)
        (untracked / "large.js").write_text("untracked\n" * 1000, encoding="utf-8")

        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        workspace, _home = core._prepare_command_workspace(repo, state, "tx-workspace")
        self.assertEqual((workspace / "tracked.txt").read_text(encoding="utf-8"), "staged version\n")
        self.assertFalse((workspace / "node_modules").exists())
        self.assertFalse((workspace / ".git").exists())

    def test_add_disposable_worktree_recovers_stale_registration(self):
        repo = self.make_repo()
        worktree = Path(tempfile.mkdtemp(prefix="llmgb-wt-parent-")) / "worktree"
        sh(repo, "git", "worktree", "add", "--detach", str(worktree), "HEAD")
        __import__("shutil").rmtree(worktree)
        core.add_disposable_worktree(repo, "--detach", str(worktree), "HEAD")
        try:
            self.assertTrue((worktree / "README.md").exists())
        finally:
            core.retire_worktree(repo, worktree)


    def test_push_pins_exact_transaction_commit_if_local_branch_moves_before_push(self):
        repo = self.make_repo()
        remote = Path(tempfile.mkdtemp(prefix="llmgb-remote-")) / "remote.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        sh(repo, "git", "remote", "add", "origin", str(remote))
        base = sh(repo, "git", "rev-parse", "HEAD")

        # Create an unrelated commit object that an external actor will move the
        # local target branch to after bridge publication but before push.
        (repo / "external.txt").write_text("external\n", encoding="utf-8")
        sh(repo, "git", "add", "external.txt")
        tree = sh(repo, "git", "write-tree")
        external = sh(repo, "git", "commit-tree", tree, "-p", base, "-m", "external")
        sh(repo, "git", "reset", "--hard", base)

        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-push-race",
            "repo": "demo",
            "base_sha": base,
            "branch": "ai/push-race",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+bridge\n"
            ),
            "run": [],
            "push": True,
        }

        original_run = core_mod.run
        moved = False

        def racing_run(argv, **kwargs):
            nonlocal moved
            if not moved and "push" in argv:
                moved = True
                subprocess.run(
                    ["git", "-C", str(repo), "update-ref", "refs/heads/ai/push-race", external],
                    check=True,
                )
            return original_run(argv, **kwargs)

        with patch("llm_git_bridge.core.run", side_effect=racing_run):
            outcome = process_transaction(
                repo,
                "demo",
                tx,
                state_dir=Path(tempfile.mkdtemp(prefix="llmgb-state-")),
                safe_branch_prefix="ai/",
                commands={},
                allow_push=True,
            )

        self.assertTrue(moved)
        self.assertEqual(outcome.result["push"]["status"], "success")
        self.assertEqual(sh(repo, "git", "rev-parse", "ai/push-race"), external)
        remote_sha = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", "refs/heads/ai/push-race"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout.strip()
        self.assertEqual(remote_sha, outcome.result["commit"])
        self.assertNotEqual(remote_sha, external)

    def test_transaction_from_detached_authoritative_head_can_create_safe_branch(self):
        repo = self.make_repo()
        base = self._seed_head
        sh(repo, "git", "checkout", "--detach", "-q", base)
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-detached-head",
            "repo": "demo",
            "base_sha": base,
            "branch": "ai/from-detached",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+detached\n"
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
        self.assertEqual(sh(repo, "git", "rev-parse", "ai/from-detached"), outcome.result["commit"])
        self.assertEqual(sh(repo, "git", "rev-parse", "HEAD"), base)

    def test_push_without_origin_is_secondary_to_durable_local_commit(self):
        repo = self.make_repo()
        base = self._seed_head
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-no-origin",
            "repo": "demo",
            "base_sha": base,
            "branch": "ai/no-origin",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+local\n"
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
        self.assertEqual(sh(repo, "git", "rev-parse", "ai/no-origin"), outcome.result["commit"])


    def test_retry_after_crash_immediately_after_transaction_claim_succeeds_once(self):
        repo = self.make_repo()
        base = self._seed_head
        state = Path(tempfile.mkdtemp(prefix="llmgb-state-"))
        tx = {
            "protocol": 2,
            "kind": "transaction",
            "transaction_id": "tx-crash-after-claim",
            "repo": "demo",
            "base_sha": base,
            "branch": "ai/crash-after-claim",
            "patch": (
                "diff --git a/README.md b/README.md\n"
                "--- a/README.md\n"
                "+++ b/README.md\n"
                "@@ -1 +1,2 @@\n"
                " hello\n"
                "+claimed\n"
            ),
            "run": [],
        }
        request_hash = core_mod.transaction_request_sha256(tx)
        core_mod._create_transaction_claim(
            repo, tx["transaction_id"], request_hash, state_dir=state
        )
        self.assertEqual(
            core_mod._read_transaction_claim(repo, tx["transaction_id"]), request_hash
        )
        self.assertIsNone(core_mod.branch_tip(repo, tx["branch"]))

        outcome = process_transaction(
            repo, "demo", tx, state_dir=state, safe_branch_prefix="ai/", commands={}
        )
        self.assertEqual(outcome.result["status"], "success")
        self.assertEqual(sh(repo, "git", "rev-list", "--count", f"{base}..{tx['branch']}"), "1")

    def test_configured_command_timeout_kills_same_group_descendant(self):
        repo = self.make_repo()
        marker_path = repo / "descendant-survived"
        child_code = (
            "import pathlib,time; time.sleep(0.8); "
            f"pathlib.Path({str(marker_path)!r}).write_text('survived')"
        )
        parent_code = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child_code!r}]); "
            "time.sleep(10)"
        )
        result = run_configured_command(
            [sys.executable, "-c", parent_code],
            cwd=repo,
            env=os.environ.copy(),
            timeout=0.2,
        )
        self.assertTrue(result.timed_out)
        time.sleep(1.0)
        self.assertFalse(marker_path.exists())


if __name__ == "__main__":
    unittest.main()
