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
                       state_dir=str(state))
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
        return dict(home=self.home,now=1000,launch=launch,ps=ps)

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
        pending.write_text(json.dumps({"kind":"operator_approval_request", "request_id":"x","expires_at":999}))
        r=recovery.inventory(**self.inputs())
        self.assertTrue(r["containment_ready"])
        self.assertEqual(r["states"][str(self.states[self.labels[1]])]["approvals"]["expired"],1)

    def test_live_approval_blocks_containment(self):
        pending = self.states[self.labels[1]] / "approvals/pending/x.json"
        pending.write_text(json.dumps({"kind":"operator_approval_request", "request_id":"x","expires_at":1001}))
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
        with patch.object(recovery,"command",side_effect=fake_command):
            recovery.containment(r,self.home,r["fingerprint"])
        self.assertTrue((self.agents/(recovery.KEEP+".plist")).exists())
        self.assertFalse((self.agents/(self.labels[1]+".plist")).exists())
        self.assertTrue(any(x[:2]==("/bin/launchctl","disable") for x in calls))
        archives=list((self.home/"Library/Application Support/llm-git-bridge/retired-launchagents").glob("*/"+self.labels[1]+".plist"))
        self.assertEqual(len(archives),1)

    def test_containment_never_runs_without_matching_fingerprint(self):
        r=recovery.inventory(**self.inputs())
        with self.assertRaises(recovery.Hold):
            recovery.containment(r,self.home,"0"*64)
        self.assertTrue((self.agents / (self.labels[1]+".plist")).exists())

if __name__=="__main__":
    unittest.main()
