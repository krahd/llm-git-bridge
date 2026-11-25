"""Enrollment tests never restart launchd or alter a real v6 installation."""
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "ursula_enrollment", Path(__file__).parent.parent / "enrol_ursula_ollama_eval.py"
)
enrol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enrol)


class EnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.install = self.home / ".local/share/local-executor-bridge"
        self.install.mkdir(parents=True)
        (self.install / "bridge.py").write_text("# fake installed bridge\n")
        self.config_dir = self.home / ".config/local-executor-bridge"
        self.config_dir.mkdir(parents=True)
        self.allowed = self.home / "tom-repos"
        self.repo = self.allowed / "projects/ursula/apps/storytelling/prototype-0"
        self.repo.mkdir(parents=True)
        self.state = self.home / ".local/state/local-executor-bridge"
        self.state.mkdir(parents=True)
        self.health = {
            "bridge_version": "6", "build_code_integrity": "matches_manifest",
            "build_source_commit": "a" * 40, "active_requests": [],
            "state": {"started_without_finished": 0},
        }
        (self.state / "health.json").write_text(json.dumps(self.health))
        self.config = self.config_dir / "config.json"
        self.config.write_text(json.dumps({
            "allowed_root": str(self.allowed), "state_dir": str(self.state),
            "operator_confirmation_mode": "dialog", "trusted_operations": {}
        }))
        plist = self.home / "Library/LaunchAgents/net.laurenzo.local-executor-bridge.plist"
        plist.parent.mkdir(parents=True)
        with plist.open("wb") as f:
            plistlib.dump({"Label": "net.laurenzo.local-executor-bridge"}, f)

    def invoke(self, apply=False):
        with (patch.object(enrol.sys, "platform", "darwin"),
              patch.object(enrol.Path, "home", return_value=self.home),
              patch.object(enrol.sys, "argv", ["enrol", *(["--apply"] if apply else [])]),
              patch.object(enrol.subprocess, "run") as runner):
            enrol.main()
        return runner

    def test_dry_run_does_not_modify_installation(self):
        original = self.config.read_bytes()
        runner = self.invoke()
        self.assertFalse(runner.called)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse((self.install / "ursula_ollama_eval_helper.py").exists())

    def test_apply_registers_digest_and_restart_once(self):
        runner = self.invoke(apply=True)
        self.assertEqual(runner.call_count, 1)
        helper = self.install / "ursula_ollama_eval_helper.py"
        self.assertTrue(helper.is_file())
        self.assertEqual(helper.stat().st_mode & 0o777, 0o700)
        registered = json.loads(self.config.read_text())["trusted_operations"]["ursula-ollama-eval"]
        self.assertEqual(registered["executable_sha256"], enrol.sha256(helper))
        self.assertEqual(registered["permitted_roots"], [str(self.repo)])
        self.assertEqual(set(registered["permitted_actions"]), set(enrol.ACTIONS))
        self.assertEqual(len(list(self.config_dir.glob("config.json.before-ursula-*"))), 1)
        second = self.invoke(apply=True)
        self.assertFalse(second.called)

    def test_active_requests_prevent_enrollment(self):
        self.health["active_requests"] = ["in-flight"]
        (self.state / "health.json").write_text(json.dumps(self.health))
        with self.assertRaisesRegex(SystemExit, "active or unfinished"):
            self.invoke(apply=True)
        self.assertFalse((self.install / "ursula_ollama_eval_helper.py").exists())


if __name__ == "__main__":
    unittest.main()
