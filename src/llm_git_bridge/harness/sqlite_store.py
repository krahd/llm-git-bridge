"""SQLite persistence for conversation harness v1."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from contextlib import contextmanager

SCHEMA_VERSION = 1


class SQLiteHarnessStore:
    """Small transactional store.

    One connection is owned by one service instance.  Callers that use the
    service from multiple threads should create one instance per thread/process;
    SQLite WAL and BEGIN IMMEDIATE provide the cross-process write boundary.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS harness_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs(
                job_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                lease_generation INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL,
                goal TEXT NOT NULL,
                lifecycle TEXT NOT NULL,
                waiting_on TEXT,
                repo_url TEXT,
                repo_path TEXT,
                resource_key TEXT,
                workspace_job_id TEXT,
                checkpoint_ref TEXT,
                checkpoint_sha TEXT,
                next_action TEXT,
                success_condition TEXT,
                blocked_on TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS leases(
                job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
                lease_id TEXT NOT NULL UNIQUE,
                holder_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                acquired_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                last_heartbeat_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS handoffs(
                handoff_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                nonce_hash TEXT NOT NULL UNIQUE,
                nonce_secret TEXT,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                claimed_at REAL,
                claimed_by TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_pending_handoff_per_job
                ON handoffs(job_id) WHERE state='pending';
            CREATE TABLE IF NOT EXISTS operations(
                operation_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                idempotency_key TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                target TEXT,
                intended_effect TEXT,
                result_json TEXT,
                created_at REAL NOT NULL,
                finished_at REAL,
                UNIQUE(job_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS pairing_codes(
                code_hash TEXT PRIMARY KEY,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL
            );
            CREATE TABLE IF NOT EXISTS browser_tokens(
                token_hash TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                revoked_at REAL
            );
            CREATE TABLE IF NOT EXISTS protocol_requests(
                request_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT,
                created_at REAL NOT NULL,
                finished_at REAL
            );
            CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_by_job ON events(job_id, seq);
            """
        )
        row = self.conn.execute("SELECT value FROM harness_meta WHERE key='schema_version'").fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO harness_meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),)
            )
        elif int(row["value"]) != SCHEMA_VERSION:
            raise RuntimeError("unsupported harness schema version")

    @staticmethod
    def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return None if row is None else dict(row)

    @staticmethod
    def json_dump(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
