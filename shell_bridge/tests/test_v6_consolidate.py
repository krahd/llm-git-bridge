"""Offline regressions for the single-owner consolidation controller."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import v6_consolidate as vc

ROOT, REQS, RESULTS = "prodRoot0001", "prodReqs0001", "prodResults01"
STAGING = "stagingRoot01"
KEEPER = "net.laurenzo.local-executor-bridge"
COMMIT = "a" * 40


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_private(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(0o600)


class FakeHost(vc.Host):
    uid = 501

    def __init__(self, home: Path):
        self.home = home
        self.loaded = {}
        self.procs = {os.getpid(): (1, os.getpid(), "python3 operator-terminal")}
        self.disabled = set()
        self.calls = []
        self.clock = time.time()
        self.next_pid = 7000
        self.locks = {}
        self.signals = []
        self.on_bootstrap = None
        self.queues = {}

    def launchctl_list(self):
        return dict(self.loaded)

    def disabled_labels(self):
        return {label: True for label in self.disabled}

    def processes(self):
        return dict(self.procs)

    def open_lock_files(self, pid):
        return list(self.locks.get(pid, []))

    def lock_holders(self, directory):
        return []

    def remote_queue(self, remote, requests_folder_id):
        return list(self.queues.get(requests_folder_id, []))

    def user_temp_dir(self):
        return self.home / "T"

    def spawn(self, label, cmd, children=()):
        pid = self.next_pid
        self.next_pid += 10
        self.loaded[label] = pid
        self.procs[pid] = (1, pid, cmd)
        for offset, (child_cmd, own_session) in enumerate(children, start=1):
            self.procs[pid + offset] = (pid, pid + offset if own_session else pid, child_cmd)
        return pid

    def launchctl(self, *args, check=True):
        self.calls.append(args)
        verb = args[0]
        if verb == "disable":
            self.disabled.add(args[1].rsplit("/", 1)[1])
        elif verb == "enable":
            self.disabled.discard(args[1].rsplit("/", 1)[1])
        elif verb == "bootout":
            label = args[1].rsplit("/", 1)[1]
            pid = self.loaded.pop(label, None)
            if pid:
                for p in [p for p, (ppid, pgid, _) in self.procs.items()
                          if p == pid or ppid == pid or pgid == pid]:
                    self.procs.pop(p, None)
        elif verb == "bootstrap":
            plist = plistlib.loads(Path(args[2]).read_bytes())
            pid = self.spawn(plist["Label"], " ".join(plist["ProgramArguments"]))
            if self.on_bootstrap:
                self.on_bootstrap(pid)
        return ""

    def signal(self, pid, sig):
        self.signals.append((pid, sig))
        self.procs.pop(pid, None)

    def sleep(self, seconds):
        self.clock += seconds

    def now(self):
        return self.clock


class ConsolidationFixture:
    def __init__(self, home: Path):
        self.home = home
        self.agents = home / "Library" / "LaunchAgents"
        self.agents.mkdir(parents=True)
        self.state = str(home / ".local/state/chatgpt-shell-bridge")
        (Path(self.state) / "requests").mkdir(parents=True)
        self.host = FakeHost(home)

    def config(self, name, root=ROOT, state=None, **extra):
        cfg = {"drive_root_folder_id": root, "requests_folder_id": REQS if root == ROOT else "stReqs000001",
               "results_folder_id": RESULTS, "remote": "drive:", "base_path": "Bridge",
               "bridge_instance_id": "inst-" + root, "state_dir": state or self.state, **extra}
        path = self.home / ".config" / name / "config.json"
        write_private(path, json.dumps(cfg).encode())
        return path

    def install_dir(self, name):
        d = self.home / ".local/share" / name
        d.mkdir(parents=True, exist_ok=True)
        write_private(d / "bridge.py", b"# bridge " + name.encode())
        return d

    def registration(self, label, name, *, filename=None, running=True, root=ROOT,
                     state=None, children=()):
        install = self.install_dir(name)
        config = self.config(name, root, state)
        args = ["/usr/bin/python3", str(install / "bridge.py"), "daemon", "--config", str(config)]
        raw = plistlib.dumps({"Label": label, "ProgramArguments": args,
                              "RunAtLoad": True, "KeepAlive": True})
        write_private(self.agents / (filename or label + ".plist"), raw)
        if running:
            self.host.spawn(label, " ".join(args), children)
        else:
            self.host.loaded[label] = None
        return config

    def release(self):
        release = self.home / ".local/share/local-executor-bridge-releases" / COMMIT
        files = {}
        for key, rel in vc.MANIFEST_FILES.items():
            raw = f"{key} release".encode()
            write_private(release / rel, raw)
            files[key] = sha(raw)
        config_path = self.home / ".config/local-executor-bridge-releases" / COMMIT / "config.json"
        cfg = {"drive_root_folder_id": ROOT, "requests_folder_id": REQS,
               "results_folder_id": RESULTS, "state_dir": self.state, "remote": "drive:",
               "base_path": "Bridge", "bridge_instance_id": "inst-" + ROOT,
               "operator_approval_app": str(release / "Local Executor Approval.app")}
        raw_cfg = json.dumps(cfg).encode()
        write_private(config_path, raw_cfg)
        raw_plist = plistlib.dumps({
            "Label": KEEPER, "RunAtLoad": True, "KeepAlive": True,
            "ProgramArguments": ["/usr/bin/python3", str(release / "bridge.py"), "daemon",
                                 "--config", str(config_path)],
            "EnvironmentVariables": {"LOCAL_EXECUTOR_BRIDGE_STATE_DIR": self.state,
                                     "CHATGPT_SHELL_BRIDGE_STATE_DIR": self.state}})
        write_private(release / "launchagent.plist", raw_plist)
        manifest = {"schema": 1, "source_commit": COMMIT, **files,
                    "config_sha256": sha(raw_cfg), "launchagent_plist_sha256": sha(raw_plist)}
        write_private(release / "install-manifest.json", json.dumps(manifest).encode())
        self.release_dir = release
        return release

    def plan(self):
        return {"keeper_label": KEEPER, "root_id": ROOT, "requests_id": REQS,
                "results_id": RESULTS, "state_dir": self.state,
                "release_dir": str(self.release_dir), "release_commit": COMMIT,
                "canonical_install_link": str(self.home / ".local/share/local-executor-bridge"),
                "legacy_coordinator_dirs": [str(self.home / ".local/share/chatgpt-shell-bridge")]}

    def healthy_on_bootstrap(self, pid):
        durable = self.home / ".local/state/local-executor-bridge-locks"
        self.host.locks[pid] = [str(durable / "x.lock")]
        write_private(Path(self.state) / "health.json", json.dumps({
            "publisher_pid": pid, "build_source_commit": COMMIT,
            "build_code_integrity": "matches_manifest"}).encode())

    def production_mess(self):
        """The observed Mac: v5 + v6 on production, two staging owners,
        a respawning candidate and two files declaring one label."""
        self.registration("com.tom.chatgpt-shell-bridge", "chatgpt-shell-bridge")
        self.registration("io.llm-git-bridge.daemon", "chatgpt-shell-bridge", running=False)
        self.registration(KEEPER, "local-executor-bridge")
        staging_state = str(self.home / ".local/state/staging")
        self.registration("net.laurenzo.local-executor-bridge-v6-candidate-b4ec5bd",
                          "cand-b4ec5bd", root=STAGING, state=staging_state,
                          children=[("rclone lsf drive:requests", False)])
        self.registration("net.laurenzo.local-executor-bridge-v6-candidate-004c272",
                          "cand-004c272", root=STAGING, running=False)
        self.registration("net.laurenzo.local-executor-bridge-v6-staging", "staging",
                          root=STAGING, running=False)
        self.registration("net.laurenzo.local-executor-bridge-v6-staging", "staging",
                          root=STAGING, running=False,
                          filename="net.laurenzo.local-executor-bridge-v6-staging.candidate-c90c97.plist")
        self.host.procs[9999] = (1, 9999, "/x/Local Executor Approval.app/Contents/MacOS/local-executor-approval")


class CensusTests(unittest.TestCase):
    def test_duplicate_declared_label_and_infrastructure_children(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ConsolidationFixture(Path(td))
            fx.production_mess()
            report = vc.census(fx.home, fx.host)
            staging = report["services"]["net.laurenzo.local-executor-bridge-v6-staging"]
            self.assertTrue(staging["duplicate_declarations"])
            self.assertEqual(len(staging["registrations"]), 2)
            cand = report["services"]["net.laurenzo.local-executor-bridge-v6-candidate-b4ec5bd"]
            self.assertEqual(cand["children"], [], "transport rclone is not request work")
            mismatched = [r for r in report["registrations"] if not r["filename_matches_label"]]
            self.assertEqual(len(mismatched), 1)
            self.assertEqual(sorted(report["approval_apps"]), [9999])
            production = [m for m in report["mailboxes"].values() if m["root"] == ROOT][0]
            self.assertEqual(len(production["labels"]), 3)

    def test_request_child_in_own_session_is_work(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ConsolidationFixture(Path(td))
            fx.registration(KEEPER, "local-executor-bridge",
                            children=[("rclone copy something", True)])
            report = vc.census(fx.home, fx.host)
            self.assertEqual(len(report["services"][KEEPER]["children"]), 1)

    def test_fingerprint_changes_when_registrations_change(self):
        with tempfile.TemporaryDirectory() as td:
            fx = ConsolidationFixture(Path(td))
            fx.registration(KEEPER, "local-executor-bridge")
            first = vc.census(fx.home, fx.host)["fingerprint"]
            self.assertEqual(first, vc.census(fx.home, fx.host)["fingerprint"])
            fx.registration("net.laurenzo.local-executor-bridge-v6-x", "x", running=False)
            self.assertNotEqual(first, vc.census(fx.home, fx.host)["fingerprint"])


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.fx = ConsolidationFixture(Path(self._td.name))
        self.fx.production_mess()
        self.fx.release()
        self.fx.host.on_bootstrap = self.fx.healthy_on_bootstrap

    def fingerprint(self):
        return vc.census(self.fx.home, self.fx.host)["fingerprint"]

    def assert_no_service_mutation(self):
        self.assertEqual([c for c in self.fx.host.calls if c[0] in
                          ("disable", "bootout", "bootstrap", "enable")], [])
        self.assertEqual(len(list(self.fx.agents.glob("*.plist"))), 7)

    def test_messy_mac_converges_to_one_release_owner(self):
        result = vc.migrate(self.fx.plan(), self.fingerprint(), self.fx.home, self.fx.host)
        host = self.fx.host
        self.assertEqual(list(host.loaded), [KEEPER])
        self.assertEqual(sorted(p.name for p in self.fx.agents.glob("*.plist")), [KEEPER + ".plist"])
        live = plistlib.loads((self.fx.agents / (KEEPER + ".plist")).read_bytes())
        self.assertEqual(live["ProgramArguments"][1], str(self.fx.release_dir / "bridge.py"))
        # Every obsolete label is disabled so no login can reload it; the keeper is enabled.
        self.assertIn("com.tom.chatgpt-shell-bridge", host.disabled)
        self.assertIn("net.laurenzo.local-executor-bridge-v6-staging", host.disabled)
        self.assertNotIn(KEEPER, host.disabled)
        run = Path(result["run_dir"])
        self.assertEqual(len(list((run / "retired-launchagents").iterdir())), 7)
        canonical = self.fx.home / ".local/share/local-executor-bridge"
        self.assertTrue(canonical.is_symlink())
        self.assertEqual(canonical.resolve(), self.fx.release_dir.resolve())
        shim = self.fx.home / ".local/share/chatgpt-shell-bridge/workspace.py"
        self.assertEqual(shim.resolve(), (self.fx.release_dir / "workspace.py").resolve())
        self.assertFalse((self.fx.home / ".local/share/chatgpt-shell-bridge/bridge.py").exists())
        self.assertEqual((host.next_pid - 10) in host.loaded.values(), True)
        self.assertIn((9999, 15), host.signals)
        for name in ("events.jsonl", "recovery-actions.jsonl", "indeterminate-ledger.json",
                     "census-before.json", "census-after.json"):
            self.assertTrue((run / name).is_file(), name)
        # Stop order: idle respawning labels before any running owner.
        boots = [c[1].rsplit("/", 1)[1] for c in host.calls if c[0] == "bootout"]
        self.assertLess(boots.index("io.llm-git-bridge.daemon"),
                        boots.index("com.tom.chatgpt-shell-bridge"))
        self.assertEqual(boots[-1], KEEPER)

    def test_changed_environment_after_review_mutates_nothing(self):
        stale = self.fingerprint()
        self.fx.registration("net.laurenzo.local-executor-bridge-v6-new", "new", running=False)
        with self.assertRaisesRegex(vc.Hold, "changed since operator review"):
            vc.migrate(self.fx.plan(), stale, self.fx.home, self.fx.host)
        self.assertEqual([c for c in self.fx.host.calls if c[0] == "disable"], [])

    def test_live_request_work_holds_before_any_stop(self):
        pid = self.fx.host.loaded["com.tom.chatgpt-shell-bridge"]
        self.fx.host.procs[pid + 5] = (pid, pid + 5, "/bin/zsh -c 'git push'")
        with self.assertRaisesRegex(vc.Hold, "live child processes"):
            vc.migrate(self.fx.plan(), self.fingerprint(), self.fx.home, self.fx.host)
        self.assert_no_service_mutation()

    def test_unexpired_approval_holds_before_any_stop(self):
        pending = Path(self.fx.state) / "approvals/pending/op-1.json"
        write_private(pending, json.dumps({"request_id": "op-1",
                                           "expires_at": self.fx.host.now() + 200}).encode())
        with self.assertRaisesRegex(vc.Hold, "actionable approvals"):
            vc.migrate(self.fx.plan(), self.fingerprint(), self.fx.home, self.fx.host)
        self.assert_no_service_mutation()

    def test_production_started_without_finished_holds_before_any_stop(self):
        folder = Path(self.fx.state) / "requests/in-flight-1"
        folder.mkdir()
        (folder / "started.json").write_text("{}")
        with self.assertRaisesRegex(vc.Hold, "in-flight or unresolved STARTED"):
            vc.migrate(self.fx.plan(), self.fingerprint(), self.fx.home, self.fx.host)
        self.assert_no_service_mutation()

    def test_historical_indeterminate_receipts_are_ledgered_not_blocking(self):
        folder = Path(self.fx.state) / "requests/old-ambiguous"
        folder.mkdir()
        (folder / "started.json").write_text("{}")
        (folder / "finished.json").write_text(json.dumps({"status": "indeterminate"}))
        result = vc.migrate(self.fx.plan(), self.fingerprint(), self.fx.home, self.fx.host)
        ledger = json.loads((Path(result["run_dir"]) / "indeterminate-ledger.json").read_text())
        self.assertEqual([r["request_id"] for r in ledger["receipts"]], ["old-ambiguous"])
        self.assertTrue((folder / "finished.json").exists(), "evidence is preserved")

    def test_tampered_release_is_refused_before_any_stop(self):
        (self.fx.release_dir / "bridge.py").write_bytes(b"# tampered")
        with self.assertRaisesRegex(vc.Hold, "does not match manifest"):
            vc.migrate(self.fx.plan(), self.fingerprint(), self.fx.home, self.fx.host)
        self.assert_no_service_mutation()

    def test_release_bound_to_another_mailbox_is_refused(self):
        plan = self.fx.plan()
        plan["root_id"] = STAGING
        with self.assertRaisesRegex(vc.Hold, "drive_root_folder_id differs"):
            vc.migrate(plan, self.fingerprint(), self.fx.home, self.fx.host)
        self.assert_no_service_mutation()

    def test_second_claimant_after_start_holds_without_restoring_old_owner(self):
        def respawn(pid):
            self.fx.healthy_on_bootstrap(pid)
            self.fx.host.spawn("net.laurenzo.local-executor-bridge-v6-ghost",
                               "/usr/bin/python3 /ghost/bridge.py daemon --config /g")
        self.fx.host.on_bootstrap = respawn
        with self.assertRaisesRegex(vc.Hold, "not exactly the keeper"):
            vc.migrate(self.fx.plan(), self.fingerprint(), self.fx.home, self.fx.host)
        restarted_old = [c for c in self.fx.host.calls if c[0] == "bootstrap"
                         and KEEPER not in c[-1]]
        self.assertEqual(restarted_old, [])

    def test_refuses_to_run_inside_a_bridge_request(self):
        host = self.fx.host
        host.procs[os.getpid()] = (4242, os.getpid(), "python3 v6_consolidate.py migrate")
        host.procs[4242] = (1, 4242, "/usr/bin/python3 /x/bridge.py daemon --config /c")
        with self.assertRaisesRegex(vc.Hold, "inside a bridge-executed request"):
            vc.migrate(self.fx.plan(), self.fingerprint(), self.fx.home, host)
        self.assert_no_service_mutation()

    def test_rollback_restores_recorded_filesystem_state(self):
        before = sorted(p.name for p in self.fx.agents.glob("*.plist"))
        v5_bridge = self.fx.home / ".local/share/chatgpt-shell-bridge/bridge.py"
        result = vc.migrate(self.fx.plan(), self.fingerprint(), self.fx.home, self.fx.host)
        with self.assertRaisesRegex(vc.Hold, "stop and disable every bridge service"):
            vc.rollback(Path(result["run_dir"]), self.fx.home, self.fx.host)
        self.fx.host.launchctl("bootout", f"gui/501/{KEEPER}")
        vc.rollback(Path(result["run_dir"]), self.fx.home, self.fx.host)
        self.assertEqual(sorted(p.name for p in self.fx.agents.glob("*.plist")), before)
        self.assertTrue(v5_bridge.is_file())
        self.assertFalse((self.fx.home / ".local/share/local-executor-bridge").is_symlink())


if __name__ == "__main__":
    unittest.main()
