from __future__ import annotations

import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from llm_git_bridge.core import BridgeError
from llm_git_bridge import app
from llm_git_bridge import self_update as su


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="llmgb-self-update-")
        self.tmp = Path(self.tmp_ctx.name)
        self.old = {
            "STATE_DIR": su.STATE_DIR,
            "CONFIG_DIR": su.CONFIG_DIR,
            "INSTALL_ROOT": su.INSTALL_ROOT,
            "RUNTIMES_DIR": su.RUNTIMES_DIR,
            "ACTIVE_RUNTIME": su.ACTIVE_RUNTIME,
            "UPDATER_CONFIG": su.UPDATER_CONFIG,
            "HANDOFF_DIR": su.HANDOFF_DIR,
            "HANDOFF_PATH": su.HANDOFF_PATH,
            "JOURNAL_PATH": su.JOURNAL_PATH,
            "LOCK_PATH": su.LOCK_PATH,
            "HEALTH_PATH": su.HEALTH_PATH,
            "RUNTIME_LAUNCHER": su.RUNTIME_LAUNCHER,
            "UPDATER_LAUNCHER": su.UPDATER_LAUNCHER,
            "PLIST_PATH": su.PLIST_PATH,
        }
        su.STATE_DIR = self.tmp / "state"
        su.CONFIG_DIR = self.tmp / "config"
        su.INSTALL_ROOT = self.tmp / "install"
        su.RUNTIMES_DIR = su.INSTALL_ROOT / "runtimes"
        su.ACTIVE_RUNTIME = su.INSTALL_ROOT / "active-runtime.json"
        su.UPDATER_CONFIG = su.CONFIG_DIR / "self-update.json"
        su.HANDOFF_DIR = su.STATE_DIR / "self-update"
        su.HANDOFF_PATH = su.HANDOFF_DIR / "handoff.json"
        su.JOURNAL_PATH = su.HANDOFF_DIR / "journal.json"
        su.LOCK_PATH = su.HANDOFF_DIR / "supervisor.lock"
        su.HEALTH_PATH = su.STATE_DIR / "runtime-health.json"
        su.RUNTIME_LAUNCHER = self.tmp / "bin" / "runtime"
        su.UPDATER_LAUNCHER = self.tmp / "bin" / "updater"
        su.PLIST_PATH = self.tmp / "daemon.plist"

    def tearDown(self):
        for key, value in self.old.items():
            setattr(su, key, value)
        self.tmp_ctx.cleanup()

    def test_qualification_receipt_is_exact_sha_bound(self):
        sha = "a" * 40
        su.record_qualification(sha=sha, transaction_id="tx-a", request_sha256="b" * 64, branch="ai/rc9")
        obj = su.require_qualification(sha)
        self.assertEqual(obj["commit"], sha)
        with self.assertRaisesRegex(BridgeError, "no local full-test qualification"):
            su.require_qualification("c" * 40)

    def test_handoff_requires_qualification_and_contains_no_remote_command(self):
        sha = "a" * 40
        with self.assertRaisesRegex(BridgeError, "no local full-test qualification"):
            su.write_handoff(transaction_id="tx-a", target_sha=sha, source_branch="ai/rc9", source_repo=self.tmp)
        su.record_qualification(sha=sha, transaction_id="tx-q", request_sha256=None, branch="ai/rc9")
        obj = su.write_handoff(transaction_id="tx-a", target_sha=sha, source_branch="ai/rc9", source_repo=self.tmp)
        self.assertEqual(set(obj), {"version", "transaction_id", "target_sha", "source_branch", "source_repo", "created_at"})

    def test_stable_updater_launcher_executes_module_from_active_runtime(self):
        text = su.stable_updater_launcher_text()
        self.assertIn('"-m", "llm_git_bridge.self_update", "--supervise"', text)
        self.assertNotIn('src/llm_git_bridge/self_update.py", "--supervise"', text)

    def test_supervisor_promotes_remote_then_local_and_verifies_health(self):
        previous_sha = "1" * 40
        target = "2" * 40
        repo = self.tmp / "repo"
        repo.mkdir()
        runtime = self.tmp / "runtime-target"
        runtime.mkdir()
        previous_runtime = self.tmp / "runtime-prev"
        previous_runtime.mkdir()
        su.record_qualification(sha=target, transaction_id="tx-q", request_sha256=None, branch="ai/rc9")
        su.HANDOFF_DIR.mkdir(parents=True)
        su.HANDOFF_PATH.write_text(json.dumps({"version":1,"transaction_id":"tx-u","target_sha":target,"source_branch":"ai/rc9","source_repo":str(repo)}))
        su.ACTIVE_RUNTIME.parent.mkdir(parents=True)
        su.ACTIVE_RUNTIME.write_text(json.dumps({"version":1,"sha":previous_sha,"path":str(previous_runtime)}))
        calls = []
        def fake_git(_repo, *args, **kwargs):
            calls.append(args)
            class P:
                returncode = 0
                stdout = ""
            p=P()
            if args[:2] == ("symbolic-ref", "--quiet"):
                p.stdout = "main\n"
            elif args[:2] == ("rev-parse", "HEAD"):
                p.stdout = previous_sha + "\n" if not any("merge" in c for c in calls) else target + "\n"
            elif args[:2] == ("rev-parse", "refs/heads/ai/rc9"):
                p.stdout = target + "\n"
            elif args[:2] == ("ls-remote", "--heads"):
                p.stdout = previous_sha + "\trefs/heads/main\n"
            return p
        with patch.object(su, "_git", side_effect=fake_git), patch.object(su, "_tracked_clean", return_value=True), patch.object(su, "_install_runtime", return_value=runtime), patch.object(su, "_restart_daemon") as restart, patch.object(su, "_wait_health", return_value=True):
            self.assertEqual(su.run_supervisor(), 0)
        self.assertTrue(any(c[:2] == ("push", "origin") for c in calls))
        self.assertTrue(any("merge" in c for c in calls))
        restart.assert_called_once_with()
        self.assertEqual(json.loads(su.ACTIVE_RUNTIME.read_text())["sha"], target)
        self.assertFalse(su.HANDOFF_PATH.exists())
        self.assertEqual(json.loads(su.JOURNAL_PATH.read_text())["status"], "success")

    def test_supervisor_rolls_runtime_pointer_back_when_target_health_fails(self):
        previous_sha = "1" * 40
        target = "2" * 40
        repo = self.tmp / "repo"; repo.mkdir()
        runtime = self.tmp / "runtime-target"; runtime.mkdir()
        previous_runtime = self.tmp / "runtime-prev"; previous_runtime.mkdir()
        su.record_qualification(sha=target, transaction_id="tx-q", request_sha256=None, branch="ai/rc9")
        su.HANDOFF_DIR.mkdir(parents=True)
        su.HANDOFF_PATH.write_text(json.dumps({"version":1,"transaction_id":"tx-u","target_sha":target,"source_branch":"ai/rc9","source_repo":str(repo)}))
        su.ACTIVE_RUNTIME.parent.mkdir(parents=True)
        su.ACTIVE_RUNTIME.write_text(json.dumps({"version":1,"sha":previous_sha,"path":str(previous_runtime)}))
        merged = {"done": False}
        def fake_git(_repo, *args, **kwargs):
            class P: returncode=0; stdout=""
            p=P()
            if args[:2] == ("symbolic-ref", "--quiet"): p.stdout="main\n"
            elif args[:2] == ("rev-parse", "HEAD"): p.stdout=(target if merged["done"] else previous_sha)+"\n"
            elif args[:2] == ("rev-parse", "refs/heads/ai/rc9"): p.stdout=target+"\n"
            elif args[:2] == ("ls-remote", "--heads"): p.stdout=target+"\trefs/heads/main\n"
            elif "merge" in args: merged["done"]=True
            return p
        with patch.object(su, "_git", side_effect=fake_git), patch.object(su, "_tracked_clean", return_value=True), patch.object(su, "_install_runtime", return_value=runtime), patch.object(su, "_restart_daemon"), patch.object(su, "_wait_health", side_effect=[False, True]):
            with self.assertRaisesRegex(BridgeError, "previous runtime restored"):
                su.run_supervisor()
        self.assertEqual(json.loads(su.ACTIVE_RUNTIME.read_text())["sha"], previous_sha)
        self.assertEqual(json.loads(su.JOURNAL_PATH.read_text())["status"], "rolled-back")


    def test_local_promotion_repairs_branch_switch_race_without_assuming_branch_base(self):
        target = "2" * 40
        main_base = "1" * 40
        wrong_base = "3" * 40
        repo = self.tmp / "repo"; repo.mkdir()
        state = {"branch": "main", "head": main_base, "rolled": False}
        def fake_git(_repo, *args, **kwargs):
            class P: returncode=0; stdout=""
            p=P()
            if args[:2] == ("symbolic-ref", "--quiet"):
                p.stdout=state["branch"]+"\n"
            elif args[:2] == ("rev-parse", "HEAD"):
                p.stdout=state["head"]+"\n"
            elif "merge" in args:
                state["branch"]="other"; state["head"]=target
            elif args[:2] == ("reflog", "show"):
                p.stdout=target+"\n"+wrong_base+"\n"
            elif args[:2] == ("update-ref", "--no-deref"):
                state["head"]=wrong_base; state["rolled"]=True
            elif args[:2] == ("reset", "--hard"):
                state["head"]=wrong_base
            return p
        with patch.object(su, "_git", side_effect=fake_git), patch.object(su, "_tracked_clean", return_value=True):
            with self.assertRaisesRegex(BridgeError, "current branch changed"):
                su._promote_local_main(repo, target, main_base, "tx-race")
        self.assertTrue(state["rolled"])
        self.assertEqual(state["head"], wrong_base)



class AppSelfUpdateTests(unittest.TestCase):
    def test_default_off_and_parser(self):
        self.assertFalse(app.default_config()["allow_self_update"])
        args = app.build_parser().parse_args(["configure-self-update", "enable"])
        self.assertEqual(args.action, "enable")

    def test_public_capability_is_only_bridge_repo_and_only_when_enabled(self):
        cfg = app.default_config()
        cfg["allow_self_update"] = True
        registry = {"repos": {
            "bridge": {"path": "/tmp/llm-git-bridge"},
            "other": {"path": "/tmp/other"},
        }}
        caps = app._public_capabilities(cfg, registry)
        self.assertTrue(caps["bridge"]["self_update"])
        self.assertNotIn("self_update", caps["other"])

    def test_self_update_public_capabilities_are_accepted_by_public_registry(self):
        cfg = app.default_config()
        cfg["allow_self_update"] = True
        local_registry = {
            "repos": {
                "bridge": {
                    "id": "bridge",
                    "name": "llm-git-bridge",
                    "path": "/tmp/llm-git-bridge",
                    "head": "a" * 40,
                    "branch": "main",
                    "dirty": False,
                    "tracked_dirty": False,
                    "untracked": False,
                    "last_seen": "2026-09-18T00:00:00Z",
                }
            }
        }
        public = app.public_registry(local_registry, app._public_capabilities(cfg, local_registry))
        self.assertTrue(public["repos"][0]["capabilities"]["self_update"])


if __name__ == "__main__":
    unittest.main()
