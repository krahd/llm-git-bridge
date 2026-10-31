#!/usr/bin/env python3
"""Offline Local Executor Bridge v6 client envelope checks; never submits commands."""
import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z", re.ASCII)
SCOPES = {"auto", "read_only", "repository", "system"}
STATUSES = {"completed", "rejected", "indeterminate"}


class CheckError(ValueError):
    pass


def _unique_pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise CheckError(f"duplicate JSON key: {key}")
        data[key] = value
    return data


def _load(path):
    raw = Path(path).read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise CheckError("file exceeds 2 MiB")
    try:
        parsed = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError) as exc:
        raise CheckError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CheckError("expected JSON object")
    return parsed, raw


def check_request(path):
    d, raw = _load(path)
    rid = d.get("id")
    if not isinstance(rid, str) or ID_RE.fullmatch(rid) is None:
        raise CheckError("invalid lowercase ASCII request id")
    if Path(path).name != f"{rid}.json":
        raise CheckError("request filename must match id")
    if type(d.get("protocol")) is not int or d["protocol"] != 1:
        raise CheckError("expected protocol 1")
    cwd = d.get("cwd")
    if not isinstance(cwd, str) or not cwd.startswith("/") or ".." in Path(cwd).parts:
        raise CheckError("cwd must be an absolute path without parent traversal")
    trusted = d.get("trusted_operation")
    command = d.get("command")
    if trusted is not None:
        if command is not None or not isinstance(trusted, dict) or set(trusted) != {"name", "action"} or any(not isinstance(v, str) for v in trusted.values()):
            raise CheckError("invalid trusted_operation (or combined shell command)")
        opid = d.get("operation_id")
        if not isinstance(opid, str) or not ID_RE.fullmatch(opid):
            raise CheckError("trusted_operation requires valid operation_id")
    elif not isinstance(command, str) or not command or len(command.encode("utf-8")) > 256 * 1024:
        raise CheckError("missing or oversized command")
    explanation = d.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip() or len(explanation.encode("utf-8")) > 4096:
        raise CheckError("missing or oversized explanation")
    timeout = d.get("timeout_seconds", 60)
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise CheckError("timeout_seconds must be an integer in 1..300")
    scope = d.get("write_scope", "auto")
    if not isinstance(scope, str) or scope not in SCOPES:
        raise CheckError("invalid write_scope")
    if trusted is not None and (scope != "auto" or d.get("stdin_b64") is not None):
        raise CheckError("trusted_operation requires auto scope and no stdin")
    opid = d.get("operation_id")
    if opid is not None and (not isinstance(opid, str) or not ID_RE.fullmatch(opid)):
        raise CheckError("invalid operation_id")
    if "stdin_b64" in d:
        if not isinstance(d["stdin_b64"], str):
            raise CheckError("stdin_b64 must be a string")
        try:
            raw_stdin = base64.b64decode(d["stdin_b64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CheckError("invalid stdin_b64") from exc
        if len(raw_stdin) > 1024 * 1024:
            raise CheckError("stdin exceeds 1 MiB")
    return {"id": rid, "sha256": hashlib.sha256(raw).hexdigest(), "write_scope": scope}


def check_result(path, request_path):
    expected = check_request(request_path)
    d, _ = _load(path)
    if d.get("protocol") != 1 or d.get("kind") != "result":
        raise CheckError("not a protocol-1 result")
    if d.get("id") != expected["id"] or Path(path).name != f"{expected['id']}.json":
        raise CheckError("result ID/filename does not match request")
    if d.get("request_sha256") != expected["sha256"]:
        raise CheckError("result request hash does not match exact request bytes")
    status = d.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        raise CheckError("unknown or missing terminal status")
    if status != "completed":
        raise CheckError(f"request did not complete: {status}")
    code = d.get("exit_code")
    if type(code) is not int or code != 0:
        raise CheckError(f"command failed (exit_code={code!r})")
    if d.get("timed_out") or d.get("output_limited") or d.get("streams_incomplete"):
        raise CheckError("command timed out or output was incomplete")
    return {"id": expected["id"], "status": "completed", "exit_code": 0, "request_hash_verified": True}


def check_health(path):
    d, _ = _load(path)
    if str(d.get("bridge_version")) != "6":
        raise CheckError("not a v6 daemon")
    if d.get("build_code_integrity") != "matches_manifest":
        raise CheckError("installed build manifest is not verified")
    for key in ("drive_root_folder_id", "requests_folder_id", "results_folder_id"):
        if not isinstance(d.get(key), str) or not d[key]:
            raise CheckError(f"missing pinned {key}")
    state = d.get("state", {})
    if not isinstance(state, dict):
        raise CheckError("invalid state object")
    return {"bridge_version": "6", "build_source_commit": d.get("build_source_commit"),
            "pinned_folders_present": True, "started_without_finished": state.get("started_without_finished")}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="mode", required=True)
    for mode in ("health", "request", "result"):
        q = sub.add_parser(mode)
        q.add_argument("path", help="local JSON file, never uploaded by this script")
        if mode == "result":
            q.add_argument("--request", required=True, help="exact raw request file used for upload")
    args = p.parse_args(argv)
    try:
        out = (check_health(args.path) if args.mode == "health" else
               check_request(args.path) if args.mode == "request" else
               check_result(args.path, args.request))
    except (OSError, CheckError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, **out}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
