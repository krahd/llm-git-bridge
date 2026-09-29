"""Durable conversation-work harness.

The harness is deliberately independent from the live shell bridge runtime.  It
coordinates durable job state; it does not execute repository mutations itself.
"""

from .errors import Conflict, HarnessError, NotFound, ValidationError
from .service import HarnessService
from .sqlite_store import SQLiteHarnessStore

__all__ = [
    "Conflict",
    "HarnessError",
    "HarnessService",
    "NotFound",
    "SQLiteHarnessStore",
    "ValidationError",
]
