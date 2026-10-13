#!/usr/bin/env python3
"""Fail-closed, read-only v5->v6 durable state identity gate.

The same path must retain the old request deduplication and journal receipts
even when no workspaces/ directory exists. Externally mounted journals need
separate acceptance; do not silently start v6 with an empty new state.
"""
from __future__ import annotations

from pathlib import Path
import sys


class V5StateHold(ValueError):
    pass


def verify(candidate: Path, source: Path, *, home: Path | None = None) -> None:
    home = Path.home() if home is None else home
    candidate = candidate.expanduser()
    source = source.expanduser()
    if (not candidate.is_absolute() or not source.is_absolute()
            or candidate != source):
        raise V5StateHold("source and candidate do not retain the exact journal path")
    if home not in source.parents:
        raise V5StateHold("durable journal is outside the private home subtree")
    current = source
    while current != home:
        if current.is_symlink():
            raise V5StateHold("durable journal contains linked path components")
        current = current.parent


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: verify_v5_state_continuity.py TARGET_STATE SOURCE_STATE",
              file=sys.stderr)
        return 2
    try:
        verify(Path(argv[1]), Path(argv[2]))
    except V5StateHold as exc:
        print("V5_DURABLE_STATE_HOLD: " + str(exc), file=sys.stderr)
        return 1
    print("V5_DURABLE_STATE_REUSE_VERIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
