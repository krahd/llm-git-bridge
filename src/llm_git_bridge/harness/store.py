"""Persistence boundary for the harness."""
from __future__ import annotations

from typing import Protocol


class HarnessStore(Protocol):
    def close(self) -> None: ...
