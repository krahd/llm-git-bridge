"""Offline tests; never invoke launchctl or touch the operator's machine."""
import importlib.util
import json
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "shell_bridge" / "bridge_v6_recovery.py"
if not SOURCE.is_file():
    SOURCE = Path(__file__).with_name("bridge_v6_recovery.py")
spec = importlib.util.spec_from_file_location("recovery", SOURCE)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.home = Path(self.t.name) / "home"
        self.agents = self.home / "Library/LaunchAgents"
        self.agents.mkdir(parents=True)
        self.labels = [recovery.KEEP, "net.laurenzo.local-executor-bridge"]
        self.scripts = {}
        self.confs = {}
        self.states = {}
        for i, label in enumerate(self.labels):
            prefix = self.home / ".local/share" / f"bridge{i}"
            prefix.mkdir(parents=True)
            script = prefix / "bridge.py"
            script.write_text("# dummy\n")
            config = self.home / ".config" / f"bridge{i}" / "config.json"
            config.parent.mkdir(parents=True)
            state = self.home / ".local/state" / f"bridge{i}"
            (state / "requests").mkdir(parents=True)
            (state / "approvals/pending").mkdir(parents=True)
            cfg = dict(bridge_instance_id=f"instance-{i}",
                       drive_root_folder_id=recovery.PRODUCTION,
                       requests_folder_id="request-id",results_folder_id="result-id",
                       state_dir=str(state),remote="mock-remote:")
            config.write_text(json.dumps(cfg))
            (self.agents / f"{label}.plist").write_bytes(plistlib.dumps(dict(
                Label=label,ProgramArguments=["/usr/bin/python3",str(script),"daemon","--config",str(config)])))
            self.scripts[label]=script
            self.confs[label]=config
            self.states[label]=state

    def inputs(self, *, child=False):
        launch="PID Status Label\n100 0 " + self.labels[0] + "\n200 0 " + self.labels[1] + "\n"
        ps=f"100 1 100 /usr/bin/python3 {self.scripts[self.labels[0]]} daemon --config {self.confs[self.labels[0]]}\n"
        ps+=f"200 1 200 /usr/bin/python3 {self.scripts[self.labels[1]]} daemon --config {self.confs[self.labels[1]]}\n"
        if child:
            ps+="201 200 201 /bin/sh -c sleeper\n"
        return dict(home=self.home,now=1000,launch=launch,ps=ps,remote_listings={('mock-remote:', 'request-id'):[]})

    def test_clean_inventory_targets_only_v6(self):
        report=recovery.inventory(**self.inputs())
        self.assertTrue(report["containment_ready"])
        self.assertEqual(report["targets"],[self.labels[1]])
        self.assertEqual(len(report["fingerprint"]),64)

    def test_live_child_blocks_containment(self):
        report=recovery.inventory(**self.inputs(child=True))
        self.assertFalse(report["containment_ready"])
        self.assertTrue(any("live child" in reason for reason in report["holds"]))

    def test_expired_approval_is_historical_not_actionable(self):
        pending = self.states[self.labels[1]] / "approvals/pending/x.json"
        pending.write_text(json.dumps({"protocol":1,"kind":"operator_approval_request", "request_id":"x","expires_at":999}))
        r=recovery.inventory(**self.inputs())
        self.assertTrue(r["containment_ready"])
        self.assertEqual(r["states"][str(self.states[self.labels[1]])]["approvals"]["expired"],1)

    def test_live_approval_blocks_containment(self):
        pending = self.states[self.labels[1]] / "approvals/pending/x.json"
        pending.write_text(json.dumps({"protocol":1,"kind":"operator_approval_request", "request_id":"x","expires_at":1001}))
        r=recovery.inventory(**self.inputs())
        self.assertFalse(r["containment_ready"])
        self.assertTrue(any("actionable" in reason for reason in r["holds"]))

    def test_incomplete_journal_blocks_containment(self):
        folder=self.states[self.labels[0]] / "requests/x"
        folder.mkdir()
        (folder/"started.json").write_text("{}")
        r=recovery.inventory(**self.inputs())
        self.assertFalse(r["containment_ready"])

    def test_unregistered_bridge_process_is_held(self):
        kw=self.inputs()
        kw["ps"] += "300 1 300 /somewhere/bridge.py daemon --config /tmp/conf.json\n"
        with self.assertRaises(recovery.Hold):
            recovery.inventory(**kw)

    def test_config_mismatch_is_held(self):
        kw=self.inputs()
        kw["ps"] = kw["ps"].replace(str(self.confs[self.labels[1]]),"/tmp/other.json")
        with self.assertRaises(recovery.Hold):
            recovery.inventory(**kw)

    def test_mocked_containment_archives_only_obsolete_registration(self):
        kw=self.inputs()
        r=recovery.inventory(**kw)
        state={"loaded":True}
        calls=[]
        def fake_command(argv, **kwargs):
            calls.append(tuple(argv))
            if argv[:2]==["/bin/launchctl","disable"]:
                return ""
            if argv[:2]==["/bin/launchctl","bootout"]:
                state["loaded"]=False
                return ""
            if argv[:2]==["/bin/launchctl","list"]:
                if state["loaded"]:
                    return kw["launch"]
                return f"PID Status Label\n100 0 {self.labels[0]}\n"
            if argv[0]=="/bin/ps":
                if state["loaded"]:
                    return kw["ps"]
                return kw["ps"].splitlines()[0]+"\n"
            raise AssertionError(argv)
        with (patch.object(recovery,"command",side_effect=fake_command),
              patch.object(recovery,"verify_operator_context"),
              patch.object(recovery,"inventory",side_effect=lambda *a, **k: (
                  r if (self.agents/(self.labels[1]+".plist")).exists() else
                  {**r, "targets":[], "services":{self.labels[0]:r["services"][self.labels[0]]}}
              ))):
            recovery.containment(r,self.home,r["fingerprint"])
        self.assertTrue((self.agents/(recovery.KEEP+".plist")).exists())
        self.assertFalse((self.agents/(self.labels[1]+".plist")).exists())
        self.assertTrue(any(x[:2]==("/bin/launchctl","disable") for x in calls))
        archives=list((self.home/"Library/Application Support/llm-git-bridge/retired-launchagents").glob("*/"+self.labels[1]+".plist"))
        self.assertEqual(len(archives),1)

    def test_live_keeper_child_also_blocks_containment(self):
        kw=self.inputs()
        kw["ps"] += "101 100 101 /bin/sh -c sleeper\n"
        r=recovery.inventory(**kw)
        self.assertFalse(r["containment_ready"])
        self.assertIn(recovery.KEEP," ".join(r["holds"]))

    def test_remote_json_request_blocks_containment(self):
        kw=self.inputs()
        kw["remote_listings"]={('mock-remote:','request-id'):['new-command.json']}
        r=recovery.inventory(**kw)
        self.assertFalse(r["containment_ready"])
        self.assertTrue(any('remote requests' in reason for reason in r["holds"]))

    def test_remote_inventory_failure_blocks(self):
        kw=self.inputs()
        kw["remote_listings"]={}
        with self.assertRaises(recovery.Hold):
            recovery.inventory(**kw)

    def test_path_prefix_escape_is_rejected(self):
        label=self.labels[1]
        config=self.confs[label]
        data=json.loads(config.read_text())
        other=self.home/".local/state-other/fake"
        other.mkdir(parents=True)
        data["state_dir"]=str(other)
        config.write_text(json.dumps(data))
        with self.assertRaises(recovery.Hold):
            recovery.inventory(**self.inputs())

    def test_symlinked_config_ancestor_is_rejected(self):
        label=self.labels[1]
        conf=self.confs[label]
        target=self.home/"outside-config"
        conf.parent.rename(target)
        conf.parent.symlink_to(target)
        with self.assertRaises(recovery.Hold):
            recovery.inventory(**self.inputs())

    def test_group_writable_registration_is_rejected(self):
        label=self.labels[1]
        plist=self.agents/(label+".plist")
        plist.chmod(0o666)
        with self.assertRaises(recovery.Hold):
            recovery.inventory(**self.inputs())

    def test_nonfinite_approval_expiry_is_unverifiable(self):
        pending=self.states[self.labels[1]]/"approvals/pending/x.json"
        pending.write_text(json.dumps({"protocol":1,"kind":"operator_approval_request","request_id":"x","expires_at":float("nan")}))
        r=recovery.inventory(**self.inputs())
        self.assertFalse(r["containment_ready"])
        self.assertEqual(r["states"][str(self.states[self.labels[1]])]["approvals"]["unverifiable"],1)

    def test_journal_symlink_marker_is_rejected(self):
        folder=self.states[self.labels[1]]/"requests/x"
        folder.mkdir()
        (folder/"active.json").symlink_to("/tmp/nonexistent-approval")
        with self.assertRaises(recovery.Hold):
            recovery.inventory(**self.inputs())

    def test_stale_fingerprint_changes_after_ten_minutes(self):
        first=recovery.inventory(**self.inputs())
        kw=self.inputs()
        kw["now"]=1700
        second=recovery.inventory(**kw)
        self.assertNotEqual(first["fingerprint"],second["fingerprint"])

    def test_verified_remote_folder_id_validation(self):
        with self.assertRaises(recovery.Hold):
            recovery.remote_request_queue("mock-remote:","../unexpected")

    def test_malformed_approval_directory_symlink_is_rejected(self):
        pending=self.states[self.labels[1]]/"approvals/pending"
        pending.rmdir()
        pending.symlink_to("/tmp/not-present")
        with self.assertRaises(recovery.Hold):
            recovery.inventory(**self.inputs())

    def test_pinned_rclone_lookup_and_executable_names(self):
        calls=[]
        def fake(argv, **kw):
            calls.append(argv)
            return "history.txt\nold.disabled\nnew.json\n"
        with patch.object(recovery,"command",side_effect=fake):
            result=recovery.remote_request_queue("mock-remote:","A"*32)
        self.assertEqual(result,["new.json"])
        self.assertIn("--drive-root-folder-id",calls[0])
        self.assertIn("A"*32,calls[0])
        self.assertNotIn("requests",calls[0])

    def test_invocation_through_bridge_is_rejected(self):
        proc="120 121 120 python3 /tmp/bridge_v6_recovery.py --contain x\n"
        proc+="121 1 121 /usr/bin/python3 /Users/tom/.local/share/bridge/bridge.py daemon --config /tmp/conf\n"
        with (patch.object(recovery.os,"getpid",return_value=120),
              patch.object(recovery,"command",return_value=proc)):
            with self.assertRaises(recovery.Hold):
                recovery.verify_operator_context()

    def test_containment_archive_is_private(self):
        kw=self.inputs()
        r=recovery.inventory(**kw)
        state={"loaded":True}
        def fake(argv,**kwargs):
            if argv[:2]==["/bin/launchctl","disable"]: return ""
            if argv[:2]==["/bin/launchctl","bootout"]:
                state["loaded"]=False
                return ""
            if argv[:2]==["/bin/launchctl","list"]:
                return kw["launch"] if state["loaded"] else "PID Status Label\n100 0 "+recovery.KEEP+"\n"
            if argv[0]=="/bin/ps":
                return kw["ps"] if state["loaded"] else kw["ps"].splitlines()[0]+"\n"
            raise AssertionError(argv)
        def inv(*a,**k):
            if (self.agents/(self.labels[1]+".plist")).exists(): return r
            return {**r,"targets":[],"services":{recovery.KEEP:r["services"][recovery.KEEP]}}
        with (patch.object(recovery,"command",side_effect=fake),
              patch.object(recovery,"verify_operator_context"),
              patch.object(recovery,"inventory",side_effect=inv)):
            recovery.containment(r,self.home,r["fingerprint"])
        archive=self.home/"Library/Application Support/llm-git-bridge/retired-launchagents"
        self.assertEqual(archive.stat().st_mode & 0o077,0)

    def test_symlinked_archive_parent_refused_without_writes(self):
        kw=self.inputs()
        r=recovery.inventory(**kw)
        outside=Path(self.t.name)/"unrelated"
        outside.mkdir()
        parent=self.home/"Library/Application Support"
        parent.symlink_to(outside, target_is_directory=True)
        with (patch.object(recovery,"verify_operator_context"),
              patch.object(recovery,"inventory",return_value=r)):
            with self.assertRaises(recovery.Hold):
                recovery.containment(r,self.home,r["fingerprint"])
        self.assertFalse(any(outside.iterdir()))

    def test_containment_never_runs_without_matching_fingerprint(self):
        r=recovery.inventory(**self.inputs())
        with self.assertRaises(recovery.Hold):
            recovery.containment(r,self.home,"0"*64)
        self.assertTrue((self.agents / (self.labels[1]+".plist")).exists())

if __name__=="__main__":
    unittest.main()
