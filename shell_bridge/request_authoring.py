"""Deterministic helpers for authoring transport-safe production-v5 requests.

Production v5 accepts the legacy ``command`` field. This module lets clients
supply literal argv locally, validates it, renders it with ``shlex.join``, and
emits that existing command schema. It does not change bridge authority or
submission transport.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import shlex
from pathlib import Path
from typing import Any, Iterable

WRITE_SCOPES = {"auto", "read_only", "repository", "system"}
MAX_TIMEOUT_SECONDS = 300
MAX_COMMAND_BYTES = 256 * 1024
MAX_ARGV_ITEMS = 1024
MAX_STDIN_BYTES = 1024 * 1024
MAX_EXPLANATION_BYTES = 4096
_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")

_SHELL_FEATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("multiline", re.compile(r"[\r\n]")),
    ("command_chain", re.compile(r"&&|\|\||;")),
    ("pipeline", re.compile(r"(?<!\|)\|(?!\|)")),
    ("redirection", re.compile(r"[<>]")),
    ("command_substitution", re.compile(r"`|\$\(")),
)
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish"}
_CODE_INTERPRETERS = {"python", "python3", "ruby", "perl", "node"}


def _basename(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def _reject_inline_code_wrapper(values: list[str]) -> None:
    """Reject argv forms that hide a program inside an interpreter argument."""
    exe = _basename(values[0])
    if exe == "env":
        raise ValueError("argv beginning with env obscures the executable; invoke it directly")
    if exe in _SHELLS:
        for arg in values[1:]:
            if arg == "--":
                break
            if not arg.startswith("-"):
                break
            if "c" in arg[1:]:
                raise ValueError("shell -c/-lc belongs in an explicit shell command or reviewed helper")
    if exe in _CODE_INTERPRETERS:
        inline_flags = {"-c"} if exe.startswith("python") else {"-e"}
        if any(arg in inline_flags for arg in values[1:]):
            raise ValueError("inline interpreter code belongs in a reviewed repository helper")


def validate_argv(argv: Iterable[str]) -> list[str]:
    if isinstance(argv, (str, bytes)):
        raise ValueError("argv must be an iterable of strings, not a string")
    values = list(argv)
    if not values:
        raise ValueError("argv must not be empty")
    if len(values) > MAX_ARGV_ITEMS:
        raise ValueError(f"argv has too many items; maximum is {MAX_ARGV_ITEMS}")
    if not all(isinstance(arg, str) for arg in values):
        raise ValueError("argv must contain only strings")
    if not values[0]:
        raise ValueError("argv[0] must be non-empty")
    if any("\0" in arg for arg in values):
        raise ValueError("argv may not contain NUL bytes")
    _reject_inline_code_wrapper(values)
    rendered = shlex.join(values)
    if len(rendered.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ValueError("rendered argv is too large")
    return values


def render_argv(argv: Iterable[str]) -> str:
    return shlex.join(validate_argv(argv))


def shell_complexity_reasons(command: str) -> list[str]:
    if not isinstance(command, str) or not command:
        raise ValueError("command must be a non-empty string")
    return [name for name, pattern in _SHELL_FEATURES if pattern.search(command)]


def request_policy(req: dict[str, Any]) -> str:
    if req.get("argv") is not None:
        return "invalid_for_production_v5"
    command = req.get("command")
    if not isinstance(command, str) or not command:
        return "invalid"
    return "move_to_reviewed_helper" if shell_complexity_reasons(command) else "legacy_command_ok"


def lint_request(req: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    rid = req.get("id")
    if not isinstance(rid, str) or rid in {"", ".", ".."} or not _ID_RE.fullmatch(rid):
        errors.append("id must be lowercase safe ASCII and at most 128 characters")
    if req.get("protocol") != 1:
        errors.append("protocol must be 1")
    if not isinstance(req.get("cwd"), str) or not req.get("cwd"):
        errors.append("cwd must be a non-empty string")
    explanation = req.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        errors.append("explanation must be a non-empty string")
    elif len(explanation.strip().encode("utf-8")) > MAX_EXPLANATION_BYTES:
        errors.append("explanation is too large")
    if req.get("write_scope", "auto") not in WRITE_SCOPES:
        errors.append("write_scope is invalid")
    timeout = req.get("timeout_seconds", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        errors.append(f"timeout_seconds must be an integer in 1..{MAX_TIMEOUT_SECONDS}")
    stdin_b64 = req.get("stdin_b64")
    if stdin_b64 is not None:
        if not isinstance(stdin_b64, str):
            errors.append("stdin_b64 must be a base64 string")
        else:
            try:
                stdin = base64.b64decode(stdin_b64, validate=True)
            except (binascii.Error, ValueError):
                errors.append("stdin_b64 is not valid base64")
            else:
                if len(stdin) > MAX_STDIN_BYTES:
                    errors.append("stdin is too large")
    if req.get("argv") is not None:
        errors.append("production v5 requests must use command; use build_request(argv=...) to render it")
    command = req.get("command")
    if not isinstance(command, str) or not command:
        errors.append("command must be a non-empty string")
    elif len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
        errors.append("command is too large")
    else:
        reasons = shell_complexity_reasons(command)
        if reasons:
            warnings.append(
                "compound shell semantics detected (" + ", ".join(reasons)
                + "); prefer a reviewed repository helper"
            )
    return {
        "errors": errors,
        "warnings": warnings,
        "policy": request_policy(req) if not errors else "invalid",
    }


def build_request(
    *,
    request_id: str,
    cwd: str,
    explanation: str,
    argv: Iterable[str] | None = None,
    command: str | None = None,
    allow_shell: bool = False,
    write_scope: str = "auto",
    timeout_seconds: int = 60,
    stdin: bytes | None = None,
) -> dict[str, Any]:
    if (argv is None) == (command is None):
        raise ValueError("exactly one of argv or command is required")
    if not isinstance(request_id, str) or request_id in {"", ".", ".."} or not _ID_RE.fullmatch(request_id):
        raise ValueError("invalid request id")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("cwd must be a non-empty string")
    if not isinstance(explanation, str) or not explanation.strip():
        raise ValueError("explanation must be a non-empty string")
    explanation = explanation.strip()
    if len(explanation.encode("utf-8")) > MAX_EXPLANATION_BYTES:
        raise ValueError("explanation is too large")
    if write_scope not in WRITE_SCOPES:
        raise ValueError("invalid write_scope")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be an integer in 1..{MAX_TIMEOUT_SECONDS}")
    if argv is not None:
        rendered = render_argv(argv)
    else:
        if not allow_shell:
            raise ValueError("shell command requires allow_shell=True")
        if not isinstance(command, str) or not command:
            raise ValueError("command must be a non-empty string")
        if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
            raise ValueError("command is too large")
        rendered = command
    req: dict[str, Any] = {
        "protocol": 1,
        "id": request_id,
        "cwd": cwd,
        "command": rendered,
        "explanation": explanation,
        "write_scope": write_scope,
        "timeout_seconds": timeout_seconds,
    }
    if stdin is not None:
        if not isinstance(stdin, bytes):
            raise ValueError("stdin must be bytes")
        if len(stdin) > MAX_STDIN_BYTES:
            raise ValueError("stdin is too large")
        req["stdin_b64"] = base64.b64encode(stdin).decode("ascii")
    return req


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    lint = sub.add_parser("lint", help="lint an existing production-v5 request JSON file")
    lint.add_argument("path", type=Path)
    build = sub.add_parser("build", help="build a production-v5 request from literal argv")
    build.add_argument("--id", required=True)
    build.add_argument("--cwd", required=True)
    build.add_argument("--explanation", required=True)
    build.add_argument("--write-scope", choices=sorted(WRITE_SCOPES), default="auto")
    build.add_argument("--timeout", type=int, default=60)
    build.add_argument("argv", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "lint":
        req = json.loads(args.path.read_text(encoding="utf-8"))
        report = lint_request(req)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if not report["errors"] else 2
    if not args.argv:
        raise SystemExit("build requires argv after --")
    values = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
    req = build_request(
        request_id=args.id,
        cwd=args.cwd,
        explanation=args.explanation,
        argv=values,
        write_scope=args.write_scope,
        timeout_seconds=args.timeout,
    )
    print(json.dumps(req, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
