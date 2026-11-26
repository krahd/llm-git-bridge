"""Offline validation. A real Mac/approval/Ollama acceptance run is separate."""
import importlib.util
import os
import io
import sys
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "ursula_eval_helper", Path(__file__).parent.parent / "ursula_ollama_eval_helper.py"
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class HelperTests(unittest.TestCase):
    def test_actions_are_exact_and_bounded(self):
        self.assertEqual(set(helper.ACTIONS),
                         {"ollama-status", "core-c3", "core-abc2", "social-c3"})
        self.assertEqual(helper.MODEL, "qwen3:30b-a3b-instruct-2507-q4_K_M")
        self.assertEqual(helper.STUDIO_PORT, 41731)
        self.assertTrue(all(spec is None or spec[2] <= 3
                            for spec in helper.ACTIONS.values()))

    def test_unknown_action_rejected_before_running(self):
        with self.assertRaisesRegex(RuntimeError, "registered action"):
            helper.main(["helper", "run-shell", "/tmp/any"])
        with self.assertRaisesRegex(RuntimeError, "registered action"):
            helper.main(["helper", "core-c3", "/tmp/repo", "--model", "anything"])

    def test_sandbox_scopes_network_and_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            repo = home / "tom-repos/projects/ursula/apps/storytelling/prototype-0"
            scratch = repo / "data/local/evals/run123"
            with patch.object(helper.Path, "home", return_value=home):
                profile = helper.sandbox_profile(repo, scratch)
            self.assertIn("(deny network*)", profile)
            self.assertIn('(remote ip "localhost:11434")', profile)
            self.assertIn('(remote ip "localhost:41731")', profile)
            self.assertNotIn('remote ip "*:', profile)
            self.assertIn("(deny file-write*)", profile)
            self.assertIn("(deny file-read*", profile)
            self.assertIn('(subpath "' + str(repo.parents[2]) + '")', profile)
            self.assertNotIn('(subpath "' + str(home / ".ssh") + '")', profile)
            self.assertIn(str(repo / "dist"), profile)
            self.assertIn(str(repo / "data/local/evals"), profile)
            self.assertNotIn('(subpath "' + str(repo / "data") + '")', profile)
            self.assertNotIn("(subpath \"/\")", profile)

    @unittest.skipUnless(helper.sys.platform == "darwin", "macOS Seatbelt qualification only")
    def test_profile_is_accepted_by_macos_sandbox_exec(self):
        import subprocess
        with tempfile.TemporaryDirectory(dir=str(Path.home())) as directory:
            home = Path(directory).resolve()
            repo = home / "tom-repos/projects/ursula/apps/storytelling/prototype-0"
            scratch = repo / "data/local/evals/test-scratch"
            scratch.mkdir(parents=True)
            profile = helper.sandbox_profile(repo, scratch)
            result = subprocess.run(["/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/true"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0,
                             "Seatbelt refused narrow evaluator profile: " + result.stderr)
            # The actual studio status API invokes git in the parent Ursula
            # repository; merely parsing the profile with /usr/bin/true
            # would not detect missing .git read permission.
            root = repo.parents[2]
            subprocess.run(["/usr/bin/git", "-C", str(root), "init", "-q"],
                           check=True, capture_output=True, text=True, timeout=10)
            (root / "README.md").write_text("outside nested prototype but inside Ursula\\n")
            env = {**os.environ, "HOME": str(scratch), "XDG_CONFIG_HOME": str(scratch)}
            status = subprocess.run([
                "/usr/bin/sandbox-exec", "-p", profile, "/usr/bin/git",
                "-C", str(root), "status", "--porcelain"
            ], capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(status.returncode, 0,
                             "Seatbelt blocked source provenance Git reads: " + status.stderr)
            self.assertIn("README.md", status.stdout)
            # This is a runtime network test, not merely a profile syntax check.
            # It exercises the trusted studio's fixed loopback bind/connect path.
            socket_script = (
                "import socket; "
                "server=socket.socket(); server.settimeout(3); "
                "server.bind(('127.0.0.1', 41731)); server.listen(1); "
                "client=socket.socket(); client.settimeout(3); "
                "client.connect(('127.0.0.1',41731)); "
                "peer,_=server.accept(); peer.sendall(b'OK'); "
                "assert client.recv(2)==b'OK'; "
                "peer.close(); client.close(); server.close()"
            )
            net = subprocess.run([
                "/usr/bin/sandbox-exec", "-p", profile, sys.executable,
                "-c", socket_script
            ], capture_output=True, text=True, env=env, timeout=10)
            self.assertEqual(net.returncode, 0,
                             "Seatbelt blocked pinned local studio TCP: " + net.stderr)

    def test_repo_must_match_canonical_origin_and_clean_tracked_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve() / "prototype-0"
            repo.mkdir()
            (repo / "package.json").write_text("{}")
            with patch.object(helper, "git", side_effect=[
                "https://github.com/krahd/ursula.git", "", "a"*40
            ]):
                self.assertEqual(helper.check_repo(str(repo)), repo)
            with patch.object(helper, "git", return_value="https://example.com/attacker"):
                with self.assertRaisesRegex(RuntimeError, "canonical origin"):
                    helper.check_repo(str(repo))
            with patch.object(helper, "git", side_effect=[
                "git@github.com:krahd/ursula.git", " M src/app/unsafe.ts"
            ]):
                with self.assertRaisesRegex(RuntimeError, "tracked changes"):
                    helper.check_repo(str(repo))

    def test_missing_macos_sandbox_refuses_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(helper.sys, "platform", "linux"):
                with self.assertRaisesRegex(RuntimeError, "sandbox-exec is mandatory"):
                    helper.run_eval(Path(directory), ("core", "C", 3))

    def test_ollama_status_does_not_accept_wrong_model(self):
        class FakeResponse(io.BytesIO):
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        class FakeOpener:
            def open(self, request, timeout=0):
                self.request = request
                self.timeout = timeout
                return FakeResponse(json.dumps(
                    {"models": [{"name": "other:latest"}]}).encode())
        instance = FakeOpener()
        with patch.object(helper.urllib.request, "build_opener", return_value=instance) as factory:
            with self.assertRaisesRegex(RuntimeError, "not installed"):
                helper.local_model_status()
        self.assertEqual(instance.request.full_url, "http://127.0.0.1:11434/api/tags")
        self.assertEqual(instance.timeout, 8)
        self.assertIsInstance(factory.call_args.args[0], helper.urllib.request.ProxyHandler)
        self.assertEqual(factory.call_args.args[0].proxies, {})
        self.assertIsInstance(factory.call_args.args[1], helper.NoRedirect)


if __name__ == "__main__":
    unittest.main()
