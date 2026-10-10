"""Fail-closed trusted operation descriptor validation.

Descriptors are data, not executable shell commands. No agent may register an
operation, choose an executable, or expand allowed roots at request time.
Registration and execution require a separately privileged local installer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

_NAME = re.compile(r"[a-z][a-z0-9-]{2,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class TrustedOperationPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class TrustedOperation:
    name: str
    executable: Path
    executable_sha256: str
    permitted_roots: tuple[Path, ...]
    permitted_actions: tuple[str, ...]
    requires_confirmation: bool = True

    def validate(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise TrustedOperationPolicyError("invalid operation name")
        if not self.executable.is_absolute() or self.executable.is_symlink():
            raise TrustedOperationPolicyError("helper must be an absolute non-symlink path")
        if not _SHA256.fullmatch(self.executable_sha256):
            raise TrustedOperationPolicyError("helper must pin an exact SHA-256 digest")
        if not self.permitted_roots:
            raise TrustedOperationPolicyError("operation needs at least one explicit resource root")
        for root in self.permitted_roots:
            if not root.is_absolute() or root.is_symlink() or ".." in root.parts:
                raise TrustedOperationPolicyError("resource roots must be absolute and non-symlink paths")
        if not self.permitted_actions or len(set(self.permitted_actions)) != len(self.permitted_actions):
            raise TrustedOperationPolicyError("operation must declare unique actions")
        for action in self.permitted_actions:
            if not _NAME.fullmatch(action):
                raise TrustedOperationPolicyError("invalid action")
        if not self.requires_confirmation:
            raise TrustedOperationPolicyError("privileged operations require independent human confirmation")


def authorize_known_operation(
    registered: dict[str, TrustedOperation], operation_name: str, action: str
) -> TrustedOperation:
    """Only select a preinstalled operation; never execute it from this module."""
    operation = registered.get(operation_name)
    if operation is None:
        raise TrustedOperationPolicyError("unknown privileged operation")
    operation.validate()
    if action not in operation.permitted_actions:
        raise TrustedOperationPolicyError("action not permitted")
    return operation
