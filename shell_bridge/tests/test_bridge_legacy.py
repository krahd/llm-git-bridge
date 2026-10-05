import base64
import json
import subprocess
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge as b


class ValidationTests(unittest.TestCase):
    def test_accepts_nested_repo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "projects" / "repo"
            repo.mkdir(parents=True)
            req = {"protocol": 1, "id": "abc-123", "cwd": str(repo), "explanation": "Test request.", "command": "git status", "timeout_seconds": 10}
            out = b.validate_request(req, "abc-123.json", root, 300)
            self.assertEqual(out["cwd"], repo.resolve())

    def test_rejects_outside_root_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as other:
            root = Path(td)
            req = {"protocol": 1, "id": "abc", "cwd": other, "explanation": "Test request.", "command": "pwd"}
            with self.assertRaisesRegex(ValueError, "allowed_root"):
                b.validate_request(req, "abc.json", root, 300)
            link = root / "escape"
            try:
                link.symlink_to(other, target_is_directory=True)
            except OSError:
                return
            req["cwd"] = str(link)
            with self.assertRaisesRegex(ValueError, "allowed_root"):
                b.validate_request(req, "abc.json", root, 300)

    def test_id_and_filename_are_strict_and_traversal_safe(self):
        good = ["a.json", "abc-123.json", "x.y_z.json", "0.json"]
        for name in good:
            self.assertEqual(b.validate_request_name(name), name[:-5])
        bad = ["...json", "..json", ".json", "A.json", "a b.json", "../x.json", "x/.json", "é.json", "a" * 129 + ".json"]
        for name in bad:
            with self.assertRaises(ValueError, msg=name):
                b.validate_request_name(name)

    def test_filename_must_match_id(self):
        with tempfile.TemporaryDirectory() as td:
            req = {"protocol": 1, "id": "abc", "cwd": td, "explanation": "Test request.", "command": "pwd"}
            with self.assertRaisesRegex(ValueError, "filename"):
                b.validate_request(req, "xyz.json", Path(td), 300)

    def test_timeout_bool_and_payload_limits_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = {"protocol": 1, "id": "abc", "cwd": td, "explanation": "Test request.", "command": "pwd"}
            req = dict(base, timeout_seconds=True)
            with self.assertRaisesRegex(ValueError, "timeout_seconds"):
                b.validate_request(req, "abc.json", root, 300)
            req = dict(base, command="x" * 11)
            with self.assertRaisesRegex(ValueError, "command is too large"):
                b.validate_request(req, "abc.json", root, 300, max_command_bytes=10)
            req = dict(base, stdin_b64=base64.b64encode(b"12345").decode())
            with self.assertRaisesRegex(ValueError, "stdin is too large"):
                b.validate_request(req, "abc.json", root, 300, max_stdin_bytes=4)
            req = dict(base, write_scope="anything")
            with self.assertRaisesRegex(ValueError, "write_scope"):
                b.validate_request(req, "abc.json", root, 300)
            req = dict(base, write_scope="read_only")
            self.assertEqual(b.validate_request(req, "abc.json", root, 300)["write_scope"], "read_only")


class ConfirmationTests(unittest.TestCase):
    def test_high_impact_classifier_covers_control_plane_and_destructive_forms(self):
        cases = {
            "gh repo create krahd/example --private": "github_repository_control_plane",
            "gh repo delete krahd/example --yes": "github_repository_control_plane",
            "gh api repos/krahd/example -X PATCH -f private=true": "github_api_mutation",
            "gh issue create --title test --body body": "github_content_control_plane",
            "curl -X POST https://example.invalid -d x=y": "http_mutation",
            "ssh example.invalid 'touch /tmp/x'": "remote_shell_or_copy",
            "git push --force-with-lease origin main": "git_destructive_push",
            "git reset --hard HEAD~1": "git_destructive_local",
            "git clean -fdx": "git_destructive_local",
            "rm -rf generated": "filesystem_recursive_delete",
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(b.high_impact_command_category(command), expected)
        self.assertIsNone(b.high_impact_command_category("git push origin feature"))
        self.assertIsNone(b.high_impact_command_category("printf safe"))
        self.assertIsNone(b.high_impact_command_category("curl -fsS https://example.invalid"))
        for command in ["touch notes.txt", "git add notes.txt", "git commit -m update", "git push origin feature", "git switch feature", "git merge feature", "git rebase origin/main", "make build"]:
            self.assertIsNone(b.high_impact_command_category(command), command)

    def test_non_repository_mutation_classifier_covers_common_writes(self):
        self.assertEqual(b.non_repository_write_category("touch marker"), "filesystem_mutation")
        self.assertEqual(b.non_repository_write_category("defaults write example flag yes"), "system_configuration")
        self.assertEqual(b.non_repository_write_category("brew install example"), "package_management")
        self.assertIsNone(b.non_repository_write_category("git status --short"))
        self.assertIsNone(b.non_repository_write_category("cat README.md"))

    def test_write_plan_defaults_to_read_only_outside_git_and_repository_inside_git(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"; state.mkdir()
            cfg = {"allowed_root": str(root), "state_dir": str(state)}
            outside = root / "plain"; outside.mkdir()
            request = {"cwd": outside, "explanation": "Test request.", "command": "cat file", "write_scope": "auto"}
            plan = b.resolve_write_plan(request, cfg)
            self.assertEqual(plan["effective"], "read_only")
            self.assertEqual(plan["write_roots"], [])
            self.assertFalse(plan["allow_network"])

            repo = root / "repo"; repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            request = {"cwd": repo, "explanation": "Test request.", "command": "touch marker", "write_scope": "auto"}
            plan = b.resolve_write_plan(request, cfg)
            self.assertEqual(plan["effective"], "repository")
            self.assertIn(repo.resolve(), plan["write_roots"])
            self.assertFalse(plan["allow_network"])

    def test_write_plan_prompts_for_non_repository_mutation_and_explicit_system_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"; state.mkdir()
            cfg = {"allowed_root": str(root), "state_dir": str(state)}
            request = {"cwd": root, "explanation": "Test request.", "command": "touch marker", "write_scope": "auto"}
            plan = b.resolve_write_plan(request, cfg)
            self.assertEqual(plan["effective"], "system")
            self.assertEqual(plan["confirmation_category"], "non_repository_filesystem_mutation")
            self.assertTrue(plan["allow_network"])
            request["write_scope"] = "system"
            plan = b.resolve_write_plan(request, cfg)
            self.assertEqual(plan["confirmation_category"], "non_repository_filesystem_mutation")

    def test_validate_request_preserves_required_explanation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            req = {
                "protocol": 1,
                "id": "explain1",
                "cwd": str(root),
                "command": "printf ok",
                "explanation": "Check the repository state before editing.",
            }
            validated = b.validate_request(req, "explain1.json", root, 60)
            self.assertEqual(validated["explanation"], "Check the repository state before editing.")

    def test_validate_request_rejects_missing_explanation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            req = {
                "protocol": 1,
                "id": "explain0",
                "cwd": str(root),
                "command": "printf ok",
            }
            with self.assertRaisesRegex(ValueError, "explanation is required"):
                b.validate_request(req, "explain0.json", root, 60)

    def test_validate_request_rejects_blank_explanation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            req = {
                "protocol": 1,
                "id": "explain2",
                "cwd": str(root),
                "command": "printf ok",
                "explanation": "   ",
            }
            with self.assertRaisesRegex(ValueError, "explanation"):
                b.validate_request(req, "explain2.json", root, 60)

    def test_trusted_workspace_coordinator_gets_repository_scope_without_git_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"; state.mkdir()
            cfg = {"allowed_root": str(root), "state_dir": str(state)}
            request = {
                "cwd": root,
                "explanation": "Test request.", "command": f"python3 {b.WORKSPACE_COORDINATOR} list",
                "write_scope": "auto",
            }
            plan = b.resolve_write_plan(request, cfg)
            self.assertEqual(plan["effective"], "repository")
            self.assertTrue(plan["trusted_coordinator"])
            self.assertIsNone(plan["write_roots"])
            self.assertTrue(plan["allow_network"])

    def test_dialog_mode_requires_explicit_allow(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state"
            app_path = Path(td) / "Local Executor Approval.app"; app_path.mkdir()
            cfg = {"operator_confirmation_mode": "dialog", "operator_confirmation_timeout_seconds": 10,
                   "state_dir": str(state), "operator_approval_app": str(app_path)}
            def launch(app, pending, decision):
                req = json.loads(pending.read_text())
                b._atomic_json(decision, {"protocol":1,"kind":"operator_approval_decision",
                    "request_id":req["request_id"],"nonce":req["nonce"],
                    "payload_sha256":req["payload_sha256"],"decision":"allow","decided_at":time.time()})
                return True, ""
            with patch.object(b, "_launch_operator_approval_helper", side_effect=launch):
                result = b.request_operator_confirmation(
                    request_id="job", cwd=Path("/tmp"), command="gh repo create x/y",
                    category="github_repository_control_plane", cfg=cfg,
                    explanation="Create the requested GitHub repository.",
                )
            self.assertTrue(result["approved"])
            pending=json.loads((state/"approvals/pending/job.json").read_text())
            decision=json.loads((state/"approvals/decisions/job.json").read_text())
            self.assertEqual(pending["payload_sha256"], decision["payload_sha256"])
            self.assertEqual(pending["nonce"], decision["nonce"])

    def test_dialog_reuses_durable_bound_decision_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td)/"state"; app_path=Path(td)/"Local Executor Approval.app"; app_path.mkdir()
            cfg={"operator_confirmation_mode":"dialog","operator_confirmation_timeout_seconds":10,
                 "state_dir":str(state),"operator_approval_app":str(app_path)}
            calls=[]
            def launch(app,pending,decision):
                calls.append(1); req=json.loads(pending.read_text())
                b._atomic_json(decision,{"protocol":1,"kind":"operator_approval_decision","request_id":"job",
                  "nonce":req["nonce"],"payload_sha256":req["payload_sha256"],"decision":"allow","decided_at":time.time()})
                return True,""
            with patch.object(b,"_launch_operator_approval_helper",side_effect=launch):
                first=b.request_operator_confirmation(request_id="job",cwd=Path("/tmp"),command="ssh host true",category="remote_shell",cfg=cfg,explanation="Run a remote check.")
            with patch.object(b,"_launch_operator_approval_helper",side_effect=AssertionError("must not relaunch")):
                second=b.request_operator_confirmation(request_id="job",cwd=Path("/tmp"),command="ssh host true",category="remote_shell",cfg=cfg,explanation="Run a remote check.")
            self.assertTrue(first["approved"]); self.assertTrue(second["approved"]); self.assertEqual(len(calls),1)

    def test_dialog_rejects_decision_bound_to_other_payload(self):
        with tempfile.TemporaryDirectory() as td:
            state=Path(td)/"state"; app_path=Path(td)/"Local Executor Approval.app"; app_path.mkdir()
            cfg={"operator_confirmation_mode":"dialog","operator_confirmation_timeout_seconds":10,
                 "state_dir":str(state),"operator_approval_app":str(app_path)}
            def launch(app,pending,decision):
                req=json.loads(pending.read_text())
                b._atomic_json(decision,{"protocol":1,"kind":"operator_approval_decision","request_id":"job",
                  "nonce":req["nonce"],"payload_sha256":"0"*64,"decision":"allow","decided_at":time.time()})
                return True,""
            with patch.object(b,"_launch_operator_approval_helper",side_effect=launch):
                result=b.request_operator_confirmation(request_id="job",cwd=Path("/tmp"),command="ssh host true",category="remote_shell",cfg=cfg,explanation="Run a remote check.")
            self.assertFalse(result["approved"]); self.assertIn("failed request binding",result["message"])

    def test_binary_stdout_stderr_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = 'python3 -c "import sys; sys.stdout.buffer.write(bytes([97,0,98])); sys.stderr.buffer.write(bytes([101,114,114,255])); raise SystemExit(7)"'
            out = b.run_shell(Path(td), cmd, b"", 10)
            self.assertEqual(out["exit_code"], 7)
            self.assertEqual(base64.b64decode(out["stdout_b64"]), b"a\x00b")
            self.assertEqual(base64.b64decode(out["stderr_b64"]), b"err\xff")
            self.assertEqual(out["stdout_sha256"], b.sha256_bytes(b"a\x00b"))
            self.assertFalse(out["timed_out"])
            self.assertFalse(out["output_limited"])

    def test_stdin_exact(self):
        with tempfile.TemporaryDirectory() as td:
            out = b.run_shell(Path(td), "cat", b"hello\x00world", 10)
            self.assertEqual(base64.b64decode(out["stdout_b64"]), b"hello\x00world")

    def test_timeout_does_not_block_on_unread_stdin(self):
        with tempfile.TemporaryDirectory() as td:
            started = time.monotonic()
            out = b.run_shell(Path(td), "sleep 10", b"x" * (1024 * 1024), 1)
            elapsed = time.monotonic() - started
            self.assertTrue(out["timed_out"])
            self.assertLess(elapsed, 4.0)

    def test_timeout_kills_descendant_that_ignores_sigterm(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            marker = td / "SURVIVED"
            child = td / "child.py"
            child.write_text(
                "import signal,time,pathlib\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "time.sleep(2)\n"
                f"pathlib.Path({str(marker)!r}).write_text('bad')\n"
            )
            parent = td / "parent.py"
            parent.write_text(
                "import subprocess,time\n"
                f"subprocess.Popen(['python3',{str(child)!r}])\n"
                "time.sleep(20)\n"
            )
            out = b.run_shell(td, f"python3 {parent.name}", b"", 1)
            self.assertTrue(out["timed_out"])
            time.sleep(2.5)
            self.assertFalse(marker.exists())

    def test_output_limit_bounds_and_terminates(self):
        with tempfile.TemporaryDirectory() as td:
            cmd = 'python3 -c "import sys; sys.stdout.write(\'x\'*1000000); sys.stdout.flush()"'
            out = b.run_shell(Path(td), cmd, b"", 10, max_output_bytes=4096)
            self.assertTrue(out["output_limited"])
            self.assertLessEqual(out["stdout_bytes"], 4096)

    @unittest.skipUnless(sys.platform == "darwin" and b.SANDBOX_EXEC.exists() and os.environ.get("_LOCAL_EXECUTOR_NESTED_VALIDATION") != "1", "direct macOS sandbox execution required")
    def test_read_only_sandbox_blocks_write_and_repository_scope_allows_only_repo(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "repo"; repo.mkdir()
            sibling = root / "sibling"; sibling.mkdir()
            blocked = repo / "blocked"
            out = b.run_shell(repo, "touch blocked", b"", 10, sandbox_write_roots=[])
            self.assertNotEqual(out["exit_code"], 0)
            self.assertTrue(out["filesystem_sandboxed"])
            self.assertFalse(blocked.exists())

            allowed = repo / "allowed"
            out = b.run_shell(repo, "touch allowed", b"", 10, sandbox_write_roots=[repo])
            self.assertEqual(out["exit_code"], 0)
            self.assertTrue(allowed.exists())

            outside = sibling / "outside"
            out = b.run_shell(repo, f"touch {outside}", b"", 10, sandbox_write_roots=[repo])
            self.assertNotEqual(out["exit_code"], 0)
            self.assertFalse(outside.exists())

    @unittest.skipUnless(sys.platform == "darwin" and b.SANDBOX_EXEC.exists() and os.environ.get("_LOCAL_EXECUTOR_NESTED_VALIDATION") != "1", "direct macOS sandbox execution required")
    def test_read_only_sandbox_allows_zsh_heredoc_runtime_temp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = b.run_shell(root, "cat <<'EOF'\nhello\nEOF", b"", 10, sandbox_write_roots=[])
            self.assertEqual(out["exit_code"], 0, out["stderr_text"])
            self.assertEqual(out["stdout_text"], "hello\n")

    @unittest.skipUnless(sys.platform == "darwin" and b.SANDBOX_EXEC.exists() and os.environ.get("_LOCAL_EXECUTOR_NESTED_VALIDATION") != "1", "direct macOS sandbox execution required")
    def test_sandbox_denies_network_unless_explicitly_enabled_by_trusted_path(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cmd = "python3 -c \"import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1])\""
            blocked = b.run_shell(td, cmd, b"", 10, sandbox_write_roots=[], sandbox_allow_network=False)
            self.assertNotEqual(blocked["exit_code"], 0)
            self.assertTrue(blocked["network_sandboxed"])

            allowed = b.run_shell(td, cmd, b"", 10, sandbox_write_roots=[], sandbox_allow_network=True)
            self.assertEqual(allowed["exit_code"], 0)
            self.assertFalse(allowed["network_sandboxed"])

    def test_nonexistent_shell_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, "configured shell"):
                b.run_shell(Path(td), "true", b"", 1, shell="/definitely/not/a/shell")


class ProcessTests(unittest.TestCase):
    def make_cfg(self, root, state, **extra):
        cfg = {
            "remote": "x:", "base_path": "Bridge", "state_dir": str(state),
            "allowed_root": str(root), "max_timeout_seconds": 30,
            "shell": b.DEFAULT_SHELL,
        }
        cfg.update(extra)
        return cfg

    def run_with_remote(self, name, raw, cfg, fail_upload_once=False):
        uploaded, deleted = {}, []
        calls = {"shell": 0, "upload": 0}
        original_run = b.run_shell
        def fake_from(remote, base, leaf, local): Path(local).write_bytes(raw)
        def fake_to(local, remote, base, leaf):
            calls["upload"] += 1
            if fail_upload_once and calls["upload"] == 1:
                raise RuntimeError("simulated upload ambiguity")
            uploaded[leaf] = Path(local).read_bytes()
        def fake_delete(remote, base, leaf): deleted.append(leaf)
        def counted_run(*args, **kwargs):
            calls["shell"] += 1
            if os.environ.get("_LOCAL_EXECUTOR_NESTED_VALIDATION") == "1":
                kwargs["sandbox_write_roots"] = None
                kwargs["sandbox_read_roots"] = None
                kwargs["sandbox_deny_home_reads"] = False
            return original_run(*args, **kwargs)
        with patch.object(b, "copy_from_remote", fake_from), patch.object(b, "copy_to_remote", fake_to), \
             patch.object(b, "delete_remote", fake_delete), patch.object(b, "run_shell", counted_run):
            try:
                b.process_one(name, cfg)
                error = None
            except Exception as exc:
                error = exc
        return uploaded, deleted, calls, error

    def test_declined_high_impact_request_never_starts_shell(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state, operator_confirmation_mode="reject")
            raw = json.dumps({
                "protocol": 1, "id": "danger1", "cwd": str(root),
                "explanation": "Test request.", "command": "gh repo create krahd/should-not-exist --private",
                "timeout_seconds": 10,
            }).encode()
            up, _, calls, err = self.run_with_remote("danger1.json", raw, cfg)
            self.assertIsNone(err)
            self.assertEqual(calls["shell"], 0)
            result = json.loads(up["results/danger1.json"])
            self.assertEqual(result["status"], "rejected")
            self.assertFalse((state / "requests" / "danger1" / "started.json").exists())
            self.assertTrue((state / "requests" / "danger1" / "finished.json").exists())
            self.assertEqual(result["operator_confirmation"]["category"], "github_repository_control_plane")

    def test_non_repository_write_requires_confirmation_before_started(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state, operator_confirmation_mode="reject")
            raw = json.dumps({
                "protocol": 1, "id": "write1", "cwd": str(root),
                "explanation": "Test request.", "command": "touch SHOULD_NOT_EXIST", "timeout_seconds": 10,
            }).encode()
            up, _, calls, err = self.run_with_remote("write1.json", raw, cfg)
            self.assertIsNone(err)
            self.assertEqual(calls["shell"], 0)
            result = json.loads(up["results/write1.json"])
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["operator_confirmation"]["category"], "non_repository_filesystem_mutation")
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())
            self.assertFalse((state / "requests" / "write1" / "started.json").exists())

    @unittest.skipUnless(sys.platform == "darwin" and b.SANDBOX_EXEC.exists() and os.environ.get("_LOCAL_EXECUTOR_NESTED_VALIDATION") != "1", "direct macOS sandbox execution required")
    def test_repository_cwd_gets_repo_scoped_write_without_dialogue(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            repo = root / "repo"; repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state, operator_confirmation_mode="reject")
            raw = json.dumps({
                "protocol": 1, "id": "repowrite1", "cwd": str(repo),
                "explanation": "Test request.", "command": "touch marker", "timeout_seconds": 10,
            }).encode()
            up, _, calls, err = self.run_with_remote("repowrite1.json", raw, cfg)
            self.assertIsNone(err)
            self.assertEqual(calls["shell"], 1)
            result = json.loads(up["results/repowrite1.json"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["write_scope"]["effective"], "repository")
            self.assertFalse(result["write_scope"]["allow_network"])
            self.assertTrue((repo / "marker").exists())

    def test_repository_network_mutation_requires_confirmation_before_started(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            repo = root / "repo"; repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state, operator_confirmation_mode="reject")
            raw = json.dumps({
                "protocol": 1, "id": "netwrite1", "cwd": str(repo),
                "explanation": "Test request.", "command": "curl -X POST https://example.invalid -d x=y", "timeout_seconds": 10,
            }).encode()
            up, _, calls, err = self.run_with_remote("netwrite1.json", raw, cfg)
            self.assertIsNone(err)
            self.assertEqual(calls["shell"], 0)
            result = json.loads(up["results/netwrite1.json"])
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["operator_confirmation"]["category"], "http_mutation")
            self.assertFalse((state / "requests" / "netwrite1" / "started.json").exists())

    def test_completed_replay_is_hash_bound_and_no_reexecution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state)
            raw = json.dumps({"protocol":1,"id":"job1","cwd":str(root),"explanation": "Test request.", "command":"printf hello","timeout_seconds":10}).encode()
            up, deleted, calls, err = self.run_with_remote("job1.json", raw, cfg)
            self.assertIsNone(err); self.assertEqual(calls["shell"], 1)
            first = json.loads(up["results/job1.json"])
            self.assertEqual(first["status"], "completed")
            up2, _, calls2, err2 = self.run_with_remote("job1.json", raw, cfg)
            self.assertIsNone(err2); self.assertEqual(calls2["shell"], 0)
            self.assertEqual(json.loads(up2["results/job1.json"])["request_sha256"], first["request_sha256"])
            changed = json.dumps({"protocol":1,"id":"job1","cwd":str(root),"explanation": "Test request.", "command":"printf CHANGED","timeout_seconds":10}).encode()
            up3, _, calls3, err3 = self.run_with_remote("job1.json", changed, cfg)
            self.assertIsNone(err3); self.assertEqual(calls3["shell"], 0)
            conflict = json.loads(up3["results/job1.json"])
            self.assertEqual(conflict["status"], "rejected")
            self.assertIn("reused", conflict["message"])

    def test_started_without_finished_is_indeterminate_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            rdir = state / "requests" / "job2"; rdir.mkdir(parents=True)
            raw = json.dumps({"protocol":1,"id":"job2","cwd":str(root),"explanation": "Test request.", "command":"touch SHOULD_NOT_EXIST"}).encode()
            (rdir / "request.json").write_bytes(raw)
            (rdir / "started.json").write_text(json.dumps({"request_sha256": b.sha256_bytes(raw)}))
            cfg = self.make_cfg(root, state)
            up, _, calls, err = self.run_with_remote("job2.json", raw, cfg)
            self.assertIsNone(err); self.assertEqual(calls["shell"], 0)
            self.assertEqual(json.loads(up["results/job2.json"])["status"], "indeterminate")
            self.assertFalse((root / "SHOULD_NOT_EXIST").exists())

    def test_upload_failure_after_execution_replays_without_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state)
            raw = json.dumps({"protocol":1,"id":"job3","cwd":str(root),"explanation": "Test request.", "command":"printf once","timeout_seconds":10}).encode()
            _, _, calls, err = self.run_with_remote("job3.json", raw, cfg, fail_upload_once=True)
            self.assertIsInstance(err, RuntimeError)
            self.assertEqual(calls["shell"], 1)
            self.assertTrue((state / "requests" / "job3" / "finished.json").exists())
            up2, _, calls2, err2 = self.run_with_remote("job3.json", raw, cfg)
            self.assertIsNone(err2); self.assertEqual(calls2["shell"], 0)
            self.assertEqual(json.loads(up2["results/job3.json"])["stdout_text"], "once")

    def test_malformed_json_is_terminal_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state)
            up, _, calls, err = self.run_with_remote("bad1.json", b"{not-json", cfg)
            self.assertIsNone(err); self.assertEqual(calls["shell"], 0)
            result = json.loads(up["results/bad1.json"])
            self.assertEqual(result["status"], "rejected")
            self.assertTrue((state / "requests" / "bad1" / "finished.json").exists())

    def test_oversize_request_rejected_without_loading_or_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg = self.make_cfg(root, state, max_request_bytes=10)
            raw = b"x" * 100
            up, _, calls, err = self.run_with_remote("big1.json", raw, cfg)
            self.assertIsNone(err); self.assertEqual(calls["shell"], 0)
            result = json.loads(up["results/big1.json"])
            self.assertEqual(result["status"], "rejected")
            self.assertEqual(result["request_sha256"], b.sha256_bytes(raw))


class ConfigAndLockTests(unittest.TestCase):
    def test_config_validation_and_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"; root.mkdir()
            state = Path(td) / "state"
            cfg_path = Path(td) / "cfg.json"
            cfg = {"remote":"x:","base_path":"Bridge","allowed_root":str(root),"state_dir":str(state),"shell":b.DEFAULT_SHELL}
            cfg_path.write_text(json.dumps(cfg))
            loaded = b.load_config(cfg_path)
            lock1 = b.acquire_instance_lock(loaded)
            try:
                with self.assertRaisesRegex(RuntimeError, "another"):
                    b.acquire_instance_lock(loaded)
            finally:
                lock1.close()
            cfg["poll_seconds"] = 0
            cfg_path.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(ValueError, "poll_seconds"):
                b.load_config(cfg_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
