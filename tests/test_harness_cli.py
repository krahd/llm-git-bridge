from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from llm_git_bridge.harness.browser_api import BrowserAPIServer


class WrongHealthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        data = b'{"ok":true}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class HarnessCLITests(unittest.TestCase):
    def test_module_entrypoint_exposes_cli(self):
        proc = subprocess.run(
            [sys.executable, "-m", "llm_git_bridge.harness.cli", "--help"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("llm-git-harness", proc.stdout)
        self.assertIn("browser-status", proc.stdout)

    def test_pyproject_installs_harness_command(self):
        text = Path("pyproject.toml").read_text()
        self.assertIn("llm-git-harness = \"llm_git_bridge.harness.cli:main\"", text)

    def test_browser_status_checks_loopback_and_staged_extension_without_pairing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extension = root / "safari-extension"
            extension.mkdir()
            (extension / "manifest.json").write_text("{}\n")
            digest = hashlib.sha256((extension / "manifest.json").read_bytes()).hexdigest()
            (root / "install-manifest.json").write_text(json.dumps({
                "schema": 2,
                "safari_files": {"manifest.json": digest},
            }) + "\n")
            server = BrowserAPIServer(("127.0.0.1", 0), root / "h.sqlite3")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env["HARNESS_INSTALL_DIR"] = str(root)
                proc = subprocess.run(
                    [
                        sys.executable, "-m", "llm_git_bridge.harness.cli", "browser-status",
                        "--http-port", str(server.server_address[1]),
                    ],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, env=env,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertTrue(payload["ready"])
                self.assertTrue(payload["browser_api"]["ok"])
                self.assertTrue(payload["extension"]["staged"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_browser_status_rejects_unrelated_loopback_health_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extension = root / "safari-extension"
            extension.mkdir()
            (extension / "manifest.json").write_text("{}\n")
            digest = hashlib.sha256((extension / "manifest.json").read_bytes()).hexdigest()
            (root / "install-manifest.json").write_text(json.dumps({
                "schema": 2,
                "safari_files": {"manifest.json": digest},
            }) + "\n")
            server = ThreadingHTTPServer(("127.0.0.1", 0), WrongHealthHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env["HARNESS_INSTALL_DIR"] = str(root)
                proc = subprocess.run(
                    [sys.executable, "-m", "llm_git_bridge.harness.cli", "browser-status",
                     "--http-port", str(server.server_address[1])],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, env=env,
                )
                self.assertEqual(proc.returncode, 1, proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertFalse(payload["ready"])
                self.assertFalse(payload["browser_api"]["ok"])
                self.assertEqual(payload["browser_api"]["error"], "unexpected health response")
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_browser_status_fails_closed_on_staged_extension_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            extension = root / "safari-extension"
            extension.mkdir()
            (extension / "manifest.json").write_text("{}\n")
            digest = hashlib.sha256((extension / "manifest.json").read_bytes()).hexdigest()
            (root / "install-manifest.json").write_text(json.dumps({
                "schema": 2,
                "safari_files": {"manifest.json": digest},
            }) + "\n")
            (extension / "manifest.json").write_text('{"changed": true}\n')
            server = BrowserAPIServer(("127.0.0.1", 0), root / "h.sqlite3")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                env = os.environ.copy()
                env["HARNESS_INSTALL_DIR"] = str(root)
                proc = subprocess.run(
                    [sys.executable, "-m", "llm_git_bridge.harness.cli", "browser-status",
                     "--http-port", str(server.server_address[1])],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, env=env,
                )
                self.assertEqual(proc.returncode, 1, proc.stderr)
                payload = json.loads(proc.stdout)
                self.assertFalse(payload["ready"])
                self.assertFalse(payload["extension"]["integrity_ok"])
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
