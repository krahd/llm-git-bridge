"""Harness state machine and transactional business rules."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any, Callable

from .errors import Conflict, NotFound, ValidationError
from .model import HandoffToken, LeaseToken, OperationStart, validate_lifecycle, validate_token, validate_ttl
from .sqlite_store import SQLiteHarnessStore


TERMINAL_LIFECYCLES = frozenset({"completed", "failed", "cancelled"})
LIFECYCLE_TRANSITIONS = {
    "active": frozenset({"waiting", "blocked", "completed", "failed", "cancelled"}),
    "waiting": frozenset({"active", "blocked", "completed", "failed", "cancelled"}),
    "blocked": frozenset({"active", "failed", "cancelled"}),
}


class HarnessService:
    def __init__(self, store: SQLiteHarnessStore, *, clock: Callable[[], float] = time.time):
        self.store = store
        self.clock = clock

    def _now(self) -> float:
        return float(self.clock())

    def _event(self, conn, job_id: str, event_type: str, payload: dict[str, Any], now: float) -> None:
        conn.execute(
            "INSERT INTO events(job_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (job_id, event_type, self.store.json_dump(payload), now),
        )

    def _job(self, conn, job_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFound("job not found")
        return dict(row)

    @staticmethod
    def _require_nonterminal(job: dict[str, Any]) -> None:
        if job["lifecycle"] in TERMINAL_LIFECYCLES:
            raise Conflict("job lifecycle is terminal")

    @staticmethod
    def _validate_lifecycle_transition(current: str, target: str) -> None:
        if current == target:
            return
        if current in TERMINAL_LIFECYCLES or target not in LIFECYCLE_TRANSITIONS.get(current, frozenset()):
            raise Conflict(f"illegal lifecycle transition: {current} -> {target}")

    @staticmethod
    def _require_no_unresolved_operations(conn, job_id: str) -> None:
        row = conn.execute(
            "SELECT operation_id,status FROM operations WHERE job_id=? AND status IN ('started','indeterminate') ORDER BY created_at LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is not None:
            raise Conflict(f"job has unresolved operation: {row['operation_id']} ({row['status']})")

    def _expire(self, conn, job_id: str, now: float) -> None:
        lease = conn.execute("SELECT * FROM leases WHERE job_id=?", (job_id,)).fetchone()
        if lease is not None and float(lease["expires_at"]) <= now:
            conn.execute("DELETE FROM leases WHERE job_id=?", (job_id,))
            self._event(
                conn,
                job_id,
                "lease_expired",
                {"lease_id": lease["lease_id"], "generation": lease["generation"]},
                now,
            )
        rows = conn.execute(
            "SELECT handoff_id FROM handoffs WHERE job_id=? AND state='pending' AND expires_at<=?",
            (job_id, now),
        ).fetchall()
        for row in rows:
            conn.execute("UPDATE handoffs SET state='expired',nonce_secret=NULL WHERE handoff_id=?", (row["handoff_id"],))
            self._event(conn, job_id, "handoff_expired", {"handoff_id": row["handoff_id"]}, now)

    def create_job(
        self,
        job_id: str,
        *,
        title: str,
        goal: str,
        lifecycle: str = "active",
        repo_url: str | None = None,
        repo_path: str | None = None,
        resource_key: str | None = None,
        workspace_job_id: str | None = None,
        next_action: str | None = None,
        success_condition: str | None = None,
    ) -> dict[str, Any]:
        job_id = validate_token(job_id, "job_id")
        validate_lifecycle(lifecycle)
        if not title.strip() or not goal.strip():
            raise ValidationError("title and goal are required")
        now = self._now()
        with self.store.transaction() as conn:
            try:
                conn.execute(
                    """INSERT INTO jobs(
                        job_id,version,lease_generation,title,goal,lifecycle,repo_url,repo_path,
                        resource_key,workspace_job_id,next_action,success_condition,created_at,updated_at
                    ) VALUES(?,1,0,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id, title.strip(), goal.strip(), lifecycle, repo_url, repo_path,
                        resource_key, workspace_job_id, next_action, success_condition, now, now,
                    ),
                )
            except Exception as exc:
                if "UNIQUE constraint failed: jobs.job_id" in str(exc):
                    raise Conflict("job already exists") from exc
                raise
            self._event(conn, job_id, "job_created", {"version": 1}, now)
            return self._job(conn, job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        validate_token(job_id, "job_id")
        now = self._now()
        with self.store.transaction() as conn:
            job = self._job(conn, job_id)
            self._expire(conn, job_id, now)
            return self._job(conn, job_id)

    def acquire_lease(self, job_id: str, holder_id: str, *, ttl: float = 900.0) -> LeaseToken:
        validate_token(job_id, "job_id")
        validate_token(holder_id, "holder_id")
        ttl = validate_ttl(ttl)
        now = self._now()
        with self.store.transaction() as conn:
            job = self._job(conn, job_id)
            self._expire(conn, job_id, now)
            self._require_nonterminal(job)
            pending = conn.execute(
                "SELECT handoff_id FROM handoffs WHERE job_id=? AND state='pending'", (job_id,)
            ).fetchone()
            if pending is not None:
                raise Conflict("job has a pending handoff")
            current = conn.execute("SELECT * FROM leases WHERE job_id=?", (job_id,)).fetchone()
            if current is not None:
                if current["holder_id"] != holder_id:
                    raise Conflict("job already leased")
                expires = now + ttl
                conn.execute(
                    "UPDATE leases SET expires_at=?,last_heartbeat_at=? WHERE job_id=?",
                    (expires, now, job_id),
                )
                self._event(conn, job_id, "lease_renewed", {"generation": current["generation"]}, now)
                return LeaseToken(job_id, current["lease_id"], holder_id, current["generation"], expires)
            generation = int(job["lease_generation"]) + 1
            lease_id = secrets.token_hex(16)
            expires = now + ttl
            conn.execute(
                "INSERT INTO leases(job_id,lease_id,holder_id,generation,acquired_at,expires_at,last_heartbeat_at) VALUES(?,?,?,?,?,?,?)",
                (job_id, lease_id, holder_id, generation, now, expires, now),
            )
            conn.execute(
                "UPDATE jobs SET lease_generation=?,version=version+1,updated_at=? WHERE job_id=?",
                (generation, now, job_id),
            )
            self._event(conn, job_id, "lease_acquired", {"lease_id": lease_id, "holder_id": holder_id, "generation": generation}, now)
            return LeaseToken(job_id, lease_id, holder_id, generation, expires)

    def _require_lease(self, conn, job_id: str, lease_id: str, generation: int, now: float):
        self._expire(conn, job_id, now)
        row = conn.execute("SELECT * FROM leases WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise Conflict("job is not leased")
        if row["lease_id"] != lease_id or int(row["generation"]) != int(generation):
            raise Conflict("stale lease token")
        return row

    def renew_lease(self, token: LeaseToken, *, ttl: float = 900.0) -> LeaseToken:
        ttl = validate_ttl(ttl)
        now = self._now()
        with self.store.transaction() as conn:
            row = self._require_lease(conn, token.job_id, token.lease_id, token.generation, now)
            expires = now + ttl
            conn.execute(
                "UPDATE leases SET expires_at=?,last_heartbeat_at=? WHERE job_id=?",
                (expires, now, token.job_id),
            )
            self._event(conn, token.job_id, "lease_renewed", {"generation": token.generation}, now)
            return LeaseToken(token.job_id, token.lease_id, row["holder_id"], token.generation, expires)

    def release_lease(self, token: LeaseToken) -> None:
        now = self._now()
        with self.store.transaction() as conn:
            self._job(conn, token.job_id)
            self._require_lease(conn, token.job_id, token.lease_id, token.generation, now)
            conn.execute("DELETE FROM leases WHERE job_id=?", (token.job_id,))
            self._event(conn, token.job_id, "lease_released", {"generation": token.generation}, now)

    def update_job(self, token: LeaseToken, *, expected_version: int, **changes: Any) -> dict[str, Any]:
        allowed = {
            "title", "goal", "lifecycle", "waiting_on", "repo_url", "repo_path", "resource_key",
            "workspace_job_id", "checkpoint_ref", "checkpoint_sha", "next_action", "success_condition", "blocked_on",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValidationError(f"unsupported job fields: {sorted(unknown)}")
        if "lifecycle" in changes:
            validate_lifecycle(changes["lifecycle"])
        if not changes:
            raise ValidationError("no changes")
        now = self._now()
        with self.store.transaction() as conn:
            self._require_lease(conn, token.job_id, token.lease_id, token.generation, now)
            job = self._job(conn, token.job_id)
            self._require_nonterminal(job)
            self._require_no_unresolved_operations(conn, token.job_id)
            if int(job["version"]) != int(expected_version):
                raise Conflict("job version changed")
            if "lifecycle" in changes:
                self._validate_lifecycle_transition(job["lifecycle"], changes["lifecycle"])
            fields = list(changes)
            values = [changes[k] for k in fields]
            sql = ",".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE jobs SET {sql},version=version+1,updated_at=? WHERE job_id=? AND version=?",
                (*values, now, token.job_id, expected_version),
            )
            self._event(conn, token.job_id, "job_updated", {"fields": sorted(fields), "from_version": expected_version}, now)
            return self._job(conn, token.job_id)

    def create_handoff(self, token: LeaseToken, *, ttl: float = 900.0) -> HandoffToken:
        ttl = validate_ttl(ttl)
        now = self._now()
        with self.store.transaction() as conn:
            self._require_lease(conn, token.job_id, token.lease_id, token.generation, now)
            job = self._job(conn, token.job_id)
            self._require_nonterminal(job)
            pending = conn.execute(
                "SELECT handoff_id FROM handoffs WHERE job_id=? AND state='pending'", (token.job_id,)
            ).fetchone()
            if pending is not None:
                raise Conflict("pending handoff already exists")
            handoff_id = secrets.token_hex(16)
            nonce = secrets.token_urlsafe(32)
            nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
            expires = now + ttl
            conn.execute(
                "INSERT INTO handoffs(handoff_id,job_id,nonce_hash,nonce_secret,state,created_at,expires_at) VALUES(?,?,?,?,'pending',?,?)",
                (handoff_id, token.job_id, nonce_hash, nonce, now, expires),
            )
            conn.execute("DELETE FROM leases WHERE job_id=?", (token.job_id,))
            conn.execute("UPDATE jobs SET version=version+1,updated_at=? WHERE job_id=?", (now, token.job_id))
            self._event(conn, token.job_id, "handoff_created", {"handoff_id": handoff_id, "from_generation": token.generation}, now)
            return HandoffToken(token.job_id, handoff_id, nonce, expires)

    def claim_handoff(self, job_id: str, nonce: str, holder_id: str, *, ttl: float = 900.0) -> LeaseToken:
        validate_token(job_id, "job_id")
        validate_token(holder_id, "holder_id")
        ttl = validate_ttl(ttl)
        if not isinstance(nonce, str) or len(nonce) < 32:
            raise ValidationError("invalid handoff nonce")
        nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        now = self._now()
        with self.store.transaction() as conn:
            job = self._job(conn, job_id)
            self._expire(conn, job_id, now)
            self._require_nonterminal(job)
            handoff = conn.execute(
                "SELECT * FROM handoffs WHERE job_id=? AND nonce_hash=?", (job_id, nonce_hash)
            ).fetchone()
            if handoff is None:
                raise Conflict("handoff not found")
            if handoff["state"] != "pending" or float(handoff["expires_at"]) <= now:
                raise Conflict("handoff is not claimable")
            if conn.execute("SELECT 1 FROM leases WHERE job_id=?", (job_id,)).fetchone() is not None:
                raise Conflict("job already leased")
            generation = int(job["lease_generation"]) + 1
            lease_id = secrets.token_hex(16)
            expires = now + ttl
            conn.execute(
                "UPDATE handoffs SET state='claimed',claimed_at=?,claimed_by=?,nonce_secret=NULL WHERE handoff_id=? AND state='pending'",
                (now, holder_id, handoff["handoff_id"]),
            )
            conn.execute(
                "INSERT INTO leases(job_id,lease_id,holder_id,generation,acquired_at,expires_at,last_heartbeat_at) VALUES(?,?,?,?,?,?,?)",
                (job_id, lease_id, holder_id, generation, now, expires, now),
            )
            conn.execute(
                "UPDATE jobs SET lease_generation=?,version=version+1,updated_at=? WHERE job_id=?",
                (generation, now, job_id),
            )
            self._event(conn, job_id, "handoff_claimed", {"handoff_id": handoff["handoff_id"], "holder_id": holder_id, "generation": generation}, now)
            return LeaseToken(job_id, lease_id, holder_id, generation, expires)

    def start_operation(
        self,
        token: LeaseToken,
        *,
        idempotency_key: str,
        kind: str,
        target: str | None = None,
        intended_effect: str | None = None,
    ) -> OperationStart:
        validate_token(idempotency_key, "idempotency_key")
        validate_token(kind, "operation kind")
        now = self._now()
        with self.store.transaction() as conn:
            self._require_lease(conn, token.job_id, token.lease_id, token.generation, now)
            job = self._job(conn, token.job_id)
            self._require_nonterminal(job)
            existing = conn.execute(
                "SELECT * FROM operations WHERE job_id=? AND idempotency_key=?",
                (token.job_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                record = dict(existing)
                return OperationStart(record["operation_id"], False, record["status"], record)
            self._require_no_unresolved_operations(conn, token.job_id)
            operation_id = secrets.token_hex(16)
            conn.execute(
                "INSERT INTO operations(operation_id,job_id,idempotency_key,kind,status,target,intended_effect,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (operation_id, token.job_id, idempotency_key, kind, "started", target, intended_effect, now),
            )
            self._event(conn, token.job_id, "operation_started", {"operation_id": operation_id, "idempotency_key": idempotency_key}, now)
            row = dict(conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone())
            return OperationStart(operation_id, True, "started", row)

    def finish_operation(self, token: LeaseToken, operation_id: str, *, status: str, result: Any = None) -> dict[str, Any]:
        if status not in {"completed", "failed", "indeterminate"}:
            raise ValidationError("invalid terminal operation status")
        now = self._now()
        with self.store.transaction() as conn:
            self._require_lease(conn, token.job_id, token.lease_id, token.generation, now)
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id=? AND job_id=?", (operation_id, token.job_id)
            ).fetchone()
            if row is None:
                raise NotFound("operation not found")
            previous = row["status"]
            if previous in {"completed", "failed"}:
                if previous == status:
                    return dict(row)
                raise Conflict("operation already terminal")
            if previous == "indeterminate" and status == "indeterminate":
                return dict(row)
            if previous == "indeterminate" and status not in {"completed", "failed"}:
                raise Conflict("indeterminate operation must resolve to completed or failed")
            result_json = None if result is None else self.store.json_dump(result)
            conn.execute(
                "UPDATE operations SET status=?,result_json=?,finished_at=? WHERE operation_id=? AND status=?",
                (status, result_json, now, operation_id, previous),
            )
            event_type = "operation_reconciled" if previous == "indeterminate" else "operation_finished"
            self._event(conn, token.job_id, event_type, {"operation_id": operation_id, "from_status": previous, "status": status}, now)
            return dict(conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone())


    def create_pairing_code(self, *, ttl: float = 300.0) -> dict[str, Any]:
        ttl = validate_ttl(ttl, minimum=30.0, maximum=1800.0)
        now = self._now()
        code = secrets.token_urlsafe(9)
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        with self.store.transaction() as conn:
            conn.execute("DELETE FROM pairing_codes WHERE expires_at<=? OR used_at IS NOT NULL", (now,))
            conn.execute(
                "INSERT INTO pairing_codes(code_hash,created_at,expires_at) VALUES(?,?,?)",
                (code_hash, now, now + ttl),
            )
        return {"code": code, "expires_at": now + ttl}

    def consume_pairing_code(self, code: str, client_id: str) -> dict[str, Any]:
        if not isinstance(code, str) or len(code) < 8 or len(code) > 64:
            raise ValidationError("invalid pairing code")
        validate_token(client_id, "client_id")
        now = self._now()
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (code_hash,)).fetchone()
            if row is None or row["used_at"] is not None or float(row["expires_at"]) <= now:
                raise Conflict("pairing code is invalid or expired")
            conn.execute("UPDATE pairing_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL", (now, code_hash))
            conn.execute(
                "INSERT INTO browser_tokens(token_hash,client_id,created_at,last_used_at) VALUES(?,?,?,?)",
                (token_hash, client_id, now, now),
            )
        return {"token": token, "client_id": client_id}

    def authenticate_browser(self, token: str) -> str:
        if not isinstance(token, str) or len(token) < 32:
            raise Conflict("invalid browser token")
        now = self._now()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM browser_tokens WHERE token_hash=? AND revoked_at IS NULL", (token_hash,)
            ).fetchone()
            if row is None:
                raise Conflict("invalid browser token")
            conn.execute("UPDATE browser_tokens SET last_used_at=? WHERE token_hash=?", (now, token_hash))
            return row["client_id"]

    def pending_reentries(self) -> list[dict[str, Any]]:
        now = self._now()
        with self.store.transaction() as conn:
            job_ids = [row[0] for row in conn.execute("SELECT DISTINCT job_id FROM handoffs WHERE state='pending'").fetchall()]
            for job_id in job_ids:
                self._expire(conn, job_id, now)
            rows = conn.execute(
                """SELECT h.handoff_id,h.job_id,h.nonce_secret,h.created_at,h.expires_at,
                          j.title,j.next_action,j.lifecycle
                   FROM handoffs h JOIN jobs j ON j.job_id=h.job_id
                   WHERE h.state='pending' AND h.expires_at>? AND h.nonce_secret IS NOT NULL
                   ORDER BY h.created_at""",
                (now,),
            ).fetchall()
            out=[]
            for row in rows:
                prompt=(
                    f"Resume harness job {row['job_id']} using handoff {row['handoff_id']} "
                    f"with token {row['nonce_secret']}. Use the existing ChatGPT Shell Bridge to reach "
                    "the Mac and the parallel Conversation Harness v1. Read the durable continuation "
                    "state first, claim the handoff with a new conversation holder id, reconcile any "
                    "in-flight operation before mutation, and continue from next_action. Do not replay "
                    "an ambiguous operation merely because the prior conversation ended."
                )
                out.append({
                    "handoff_id": row["handoff_id"], "job_id": row["job_id"], "title": row["title"],
                    "created_at": row["created_at"], "expires_at": row["expires_at"],
                    "next_action": row["next_action"], "lifecycle": row["lifecycle"], "prompt": prompt,
                })
            return out

    def continuation(self, job_id: str) -> dict[str, Any]:
        validate_token(job_id, "job_id")
        now = self._now()
        with self.store.transaction() as conn:
            job = self._job(conn, job_id)
            self._expire(conn, job_id, now)
            lease = conn.execute("SELECT * FROM leases WHERE job_id=?", (job_id,)).fetchone()
            handoff = conn.execute(
                "SELECT handoff_id,state,created_at,expires_at,claimed_at,claimed_by FROM handoffs WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            ops = conn.execute(
                "SELECT operation_id,idempotency_key,kind,status,target,intended_effect,created_at,finished_at FROM operations WHERE job_id=? ORDER BY created_at DESC LIMIT 20",
                (job_id,),
            ).fetchall()
            public_job = {k: v for k, v in job.items() if k != "repo_path"}
            return {
                "schema": 1,
                "job": public_job,
                "lease": None if lease is None else {
                    "holder_id": lease["holder_id"], "generation": lease["generation"], "expires_at": lease["expires_at"]
                },
                "handoff": None if handoff is None else dict(handoff),
                "operations": [dict(row) for row in ops],
            }

    def events(self, job_id: str) -> list[dict[str, Any]]:
        validate_token(job_id, "job_id")
        rows = self.store.conn.execute(
            "SELECT seq,event_type,payload_json,created_at FROM events WHERE job_id=? ORDER BY seq", (job_id,)
        ).fetchall()
        return [
            {"seq": row["seq"], "event_type": row["event_type"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
            for row in rows
        ]
