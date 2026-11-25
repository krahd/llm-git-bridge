#!/usr/bin/env python3
"""Operator-enrolled Local Executor Bridge v6 trusted Ursula study action.

The mailbox may select an action, never a command, model, port or pathname.
The bridge pins this helper outside the agent-writable repository and requires
native approval for every invocation. Real inference is additionally sandboxed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

MODEL = "qwen3:30b-a3b-instruct-2507-q4_K_M"
STUDIO_PORT = 41731
ACTIONS = {
    "ollama-status": None,
    "core-c3": ("core", "C", 3),
    "core-abc2": ("core", "A,B,C", 2),
    "social-c3": ("social", "C", 3),
}
EXPECTED_ORIGINS = {
    "https://github.com/krahd/ursula.git",
    "https://github.com/krahd/ursula",
    "git@github.com:krahd/ursula.git",
}
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def fail(reason: str) -> None:
    raise RuntimeError(reason)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["/usr/bin/git", "-C", str(repo), *args],
                            check=True, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=12, env={"PATH": PATH})
    return result.stdout.strip()


def check_repo(directory: str) -> Path:
    root = Path(directory)
    if not root.is_absolute() or root.is_symlink() or root.resolve() != root:
        fail("repository path must be canonical, absolute and not a symlink")
    if root.name != "prototype-0" or not (root / "package.json").is_file():
        fail("trusted action requires Ursula prototype-0")
    if git(root, "remote", "get-url", "origin") not in EXPECTED_ORIGINS:
        fail("local repository does not have the expected canonical origin")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        fail("tracked changes present: reconcile checkout before evaluating")
    head = git(root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        fail("invalid Git HEAD")
    print("Ursula repo head:", head, flush=True)
    for rel in ("dist", "data", "data/local", "data/local/evals"):
        p = root / rel
        if p.is_symlink():
            fail("refusing symlink in writable subtree: " + rel)
    return root


def sandbox_profile(repo: Path, scratch: Path) -> str:
    home = Path.home().resolve()
    quote = lambda p: json.dumps(str(p))
    if not str(repo).startswith(str(home) + "/"):
        fail("repository must be inside the operator home for read isolation")
    return "\n".join([
        "(version 1)", "(allow default)",
        "(deny network*)",
        f'(allow network-outbound (remote ip "localhost:11434"))',
        f'(allow network-outbound (remote ip "localhost:{STUDIO_PORT}"))',
        f'(allow network-bind (local ip "localhost:{STUDIO_PORT}"))',
        f'(allow network-inbound (local ip "localhost:{STUDIO_PORT}"))',
        "(deny file-write*)",
        f'(allow file-write* (literal "/dev/null") (subpath {quote(repo / "dist")}) (subpath {quote(repo / "data/local/evals")}) (subpath {quote(scratch)}))',
        f'(deny file-read* (subpath {quote(home)}))',
        f'(allow file-read-metadata (literal {quote(home)}) (literal {quote(home.parent)}))',
        f'(allow file-read* (subpath {quote(repo)}) (subpath {quote(scratch)}))',
    ])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def local_model_status() -> int:
    req = urllib.request.Request("http://127.0.0.1:11434/api/tags", method="GET")
    # An agent cannot redirect this privileged preflight to another host and
    # inherited HTTP proxy environment is never consulted.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=8) as res:
        if res.status != 200:
            fail(f"Ollama returned HTTP {res.status}")
        content = res.read(256 * 1024 + 1)
        if len(content) > 256 * 1024:
            fail("Ollama response exceeds limit")
    tags = json.loads(content)
    models = tags.get("models") if isinstance(tags, dict) else None
    if not isinstance(models, list):
        fail("Ollama returned invalid model list")
    model_names = {model.get("name") for model in models if isinstance(model, dict)}
    if MODEL not in model_names:
        fail("required Qwen model is not installed: " + MODEL)
    print("Local Ollama reachable; expected model installed:", MODEL)
    return 0


def run_eval(repo: Path, spec: tuple[str, str, int]) -> int:
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        fail("macOS sandbox-exec is mandatory; refusing unsandboxed inference")
    npm = shutil.which("npm", path=PATH)
    if not npm:
        fail("npm missing from approved toolchain PATH")
    scenario, arms, limit = spec
    scratch_parent = repo / "data" / "local" / "evals"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bridge-scratch-", dir=str(scratch_parent)) as temp:
        scratch = Path(temp)
        env = {
            "PATH": PATH, "HOME": str(scratch), "TMPDIR": str(scratch),
            "TMP": str(scratch), "TEMP": str(scratch),
            "npm_config_cache": str(scratch / "npm-cache"),
            "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
            "LANG": "en_US.UTF-8",
            "URSULA_STUDY_WEB_PORT": str(STUDIO_PORT),
        }
        cmd = ["/usr/bin/sandbox-exec", "-p", sandbox_profile(repo, scratch),
               npm, "run", "eval:blue-hotel:live", "--",
               "--model", MODEL, "--scenario", scenario,
               "--modes", arms, "--limit", str(limit), "--context", "16384"]
        print("Approved study:", scenario, arms, limit, "port", STUDIO_PORT, flush=True)
        # The bridge's request timeout is at most 300 seconds; finish before it.
        child = subprocess.Popen(cmd, cwd=repo, env=env, start_new_session=True)
        try:
            result = child.wait(timeout=250)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            fail("bounded study exceeded 250s; inspect preserved partial report")
        return result


def main(args: list[str]) -> int:
    if len(args) != 3 or args[1] not in ACTIONS:
        fail("expected registered action and one pinned repository root")
    repo = check_repo(args[2])
    action = args[1]
    if action == "ollama-status":
        return local_model_status()
    return run_eval(repo, ACTIONS[action])


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except (RuntimeError, OSError, subprocess.SubprocessError, urllib.error.URLError, ValueError) as error:
        print("Ursula trusted action refused:", error, file=sys.stderr)
        sys.exit(2)
