"""Versioned local JSON protocol with at-most-once mutation semantics."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
from typing import Any

from .errors import Conflict, HarnessError, NotFound, ValidationError
from .model import LeaseToken, validate_token
from .service import HarnessService

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1_000_000
MUTATING_ACTIONS = frozenset({
    "create_job", "acquire_lease", "renew_lease", "release_lease", "update_job",
    "create_handoff", "claim_handoff", "start_operation", "finish_operation", "create_pairing_code",
})


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(action: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({"action": action, "args": args}).encode("utf-8")).hexdigest()


def _lease(args: dict[str, Any]) -> LeaseToken:
    raw = args.get("lease")
    if not isinstance(raw, dict):
        raise ValidationError("lease token is required")
    try:
        return LeaseToken(
            job_id=raw["job_id"],
            lease_id=raw["lease_id"],
            holder_id=raw["holder_id"],
            generation=int(raw["generation"]),
            expires_at=float(raw.get("expires_at", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError("invalid lease token") from exc


_REQUIRED_ARGS: dict[str, tuple[str, ...]] = {
    "create_job": ("job_id", "title", "goal"),
    "get_job": ("job_id",),
    "continuation": ("job_id",),
    "events": ("job_id",),
    "acquire_lease": ("job_id", "holder_id"),
    "renew_lease": ("lease",),
    "release_lease": ("lease",),
    "update_job": ("lease", "expected_version", "changes"),
    "create_handoff": ("lease",),
    "claim_handoff": ("job_id", "nonce", "holder_id"),
    "start_operation": ("lease", "idempotency_key", "kind"),
    "finish_operation": ("lease", "operation_id", "status"),
}


def _validate_action_args(action: str, args: dict[str, Any]) -> None:
    missing = [name for name in _REQUIRED_ARGS.get(action, ()) if name not in args]
    if missing:
        raise ValidationError(f"missing required args: {', '.join(missing)}")
    if action == "create_job":
        if not isinstance(args.get("title"), str) or not isinstance(args.get("goal"), str):
            raise ValidationError("title and goal must be strings")
        if "lifecycle" in args and not isinstance(args["lifecycle"], str):
            raise ValidationError("lifecycle must be a string")
    if action in {"renew_lease", "release_lease", "update_job", "create_handoff", "start_operation", "finish_operation"}:
        _lease(args)
    if action == "update_job":
        value = args.get("expected_version")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError("expected_version must be an integer")
        if not isinstance(args.get("changes"), dict):
            raise ValidationError("changes must be an object")
        if "lifecycle" in args["changes"] and not isinstance(args["changes"]["lifecycle"], str):
            raise ValidationError("lifecycle must be a string")


class LocalProtocol:
    def __init__(self, service: HarnessService):
        self.service = service
        self.store = service.store

    def _begin(self, request_id: str, action: str, args: dict[str, Any]) -> dict[str, Any] | None:
        validate_token(request_id, "request_id")
        fp = _fingerprint(action, args)
        now = self.service._now()
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM protocol_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is not None:
                if row["fingerprint"] != fp:
                    raise Conflict("request_id reused with different payload")
                if row["status"] in {"completed", "failed"}:
                    cached = json.loads(row["response_json"])
                    cached["replayed"] = True
                    return cached
                return {
                    "ok": False,
                    "protocol": PROTOCOL_VERSION,
                    "request_id": request_id,
                    "error": {
                        "type": "indeterminate_request",
                        "message": "request started previously; reconcile state before issuing a new mutation",
                    },
                }
            conn.execute(
                "INSERT INTO protocol_requests(request_id,fingerprint,action,status,created_at) VALUES(?,?,?,'started',?)",
                (request_id, fp, action, now),
            )
        return None

    def _finish(self, request_id: str, response: dict[str, Any], *, failed: bool = False) -> dict[str, Any]:
        now = self.service._now()
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE protocol_requests SET status=?,response_json=?,finished_at=? WHERE request_id=? AND status='started'",
                ("failed" if failed else "completed", _canonical(response), now, request_id),
            )
        return response

    def dispatch(self, message: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(message, dict):
            return self._error(None, "validation_error", "message must be an object")
        if message.get("protocol") != PROTOCOL_VERSION:
            return self._error(message.get("request_id"), "validation_error", "unsupported protocol version")
        action = message.get("action")
        args = message.get("args", {})
        request_id = message.get("request_id")
        if not isinstance(action, str) or not isinstance(args, dict):
            return self._error(request_id, "validation_error", "invalid action or args")
        mutating = action in MUTATING_ACTIONS
        try:
            _validate_action_args(action, args)
            if mutating:
                if not isinstance(request_id, str):
                    raise ValidationError("request_id is required for mutations")
                cached = self._begin(request_id, action, args)
                if cached is not None:
                    return cached
            result = self._execute(action, args)
            response = {"ok": True, "protocol": PROTOCOL_VERSION, "request_id": request_id, "result": _jsonable(result)}
            return self._finish(request_id, response) if mutating else response
        except HarnessError as exc:
            response = self._error(request_id, self._error_type(exc), str(exc))
            return self._finish(request_id, response, failed=True) if mutating and isinstance(request_id, str) else response
        except Exception:
            # Do not cache unexpected failures: a request journalled as started remains
            # indeterminate, which is safer than pretending the mutation did not happen.
            return self._error(request_id, "internal_error", "internal harness error")

    @staticmethod
    def _error_type(exc: HarnessError) -> str:
        if isinstance(exc, Conflict):
            return "conflict"
        if isinstance(exc, NotFound):
            return "not_found"
        if isinstance(exc, ValidationError):
            return "validation_error"
        return "harness_error"

    @staticmethod
    def _error(request_id: Any, kind: str, message: str) -> dict[str, Any]:
        return {"ok": False, "protocol": PROTOCOL_VERSION, "request_id": request_id, "error": {"type": kind, "message": message}}

    def _execute(self, action: str, args: dict[str, Any]) -> Any:
        s = self.service
        if action == "ping":
            return {"service": "chatgpt-conversation-harness-v1", "protocol": PROTOCOL_VERSION}
        if action == "create_pairing_code":
            return s.create_pairing_code(ttl=args.get("ttl", 300.0))
        if action == "create_job":
            return s.create_job(**args)
        if action == "get_job":
            return s.get_job(args["job_id"])
        if action == "continuation":
            return s.continuation(args["job_id"])
        if action == "events":
            return s.events(args["job_id"])
        if action == "acquire_lease":
            return s.acquire_lease(args["job_id"], args["holder_id"], ttl=args.get("ttl", 900.0))
        if action == "renew_lease":
            return s.renew_lease(_lease(args), ttl=args.get("ttl", 900.0))
        if action == "release_lease":
            s.release_lease(_lease(args)); return {"released": True}
        if action == "update_job":
            token = _lease(args)
            changes = args.get("changes")
            if not isinstance(changes, dict):
                raise ValidationError("changes must be an object")
            return s.update_job(token, expected_version=int(args["expected_version"]), **changes)
        if action == "create_handoff":
            return s.create_handoff(_lease(args), ttl=args.get("ttl", 900.0))
        if action == "claim_handoff":
            return s.claim_handoff(args["job_id"], args["nonce"], args["holder_id"], ttl=args.get("ttl", 900.0))
        if action == "start_operation":
            token = _lease(args)
            return s.start_operation(
                token,
                idempotency_key=args["idempotency_key"],
                kind=args["kind"],
                target=args.get("target"),
                intended_effect=args.get("intended_effect"),
            )
        if action == "finish_operation":
            return s.finish_operation(_lease(args), args["operation_id"], status=args["status"], result=args.get("result"))
        raise ValidationError("unknown action")
