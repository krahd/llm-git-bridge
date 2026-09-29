"""Transport-independent harness model helpers."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .errors import ValidationError

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
LIFECYCLES = frozenset({"active", "waiting", "blocked", "completed", "failed", "cancelled"})
HANDOFF_STATES = frozenset({"pending", "starting", "claimed", "expired", "cancelled"})
OPERATION_STATES = frozenset({"started", "completed", "failed", "indeterminate"})


def validate_token(value: str, field: str) -> str:
    if not isinstance(value, str) or not TOKEN_RE.fullmatch(value):
        raise ValidationError(f"invalid {field}")
    return value


def validate_lifecycle(value: str) -> str:
    if value not in LIFECYCLES:
        raise ValidationError("invalid lifecycle")
    return value


def validate_ttl(value: float, *, minimum: float = 1.0, maximum: float = 86_400.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("invalid ttl")
    ttl = float(value)
    if not minimum <= ttl <= maximum:
        raise ValidationError("ttl out of range")
    return ttl


@dataclass(frozen=True)
class LeaseToken:
    job_id: str
    lease_id: str
    holder_id: str
    generation: int
    expires_at: float


@dataclass(frozen=True)
class HandoffToken:
    job_id: str
    handoff_id: str
    nonce: str
    expires_at: float


@dataclass(frozen=True)
class OperationStart:
    operation_id: str
    created: bool
    status: str
    record: dict[str, Any]
