"""Fail-closed trusted operation descriptor validation.

Descriptors are data, not executable shell commands. No agent may register an
operation, choose an executable, or expand allowed roots at request time.
Registration and execution require a separately privileged local installer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import re
import stat

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


def verify_installed_helper(operation: TrustedOperation) -> None:
    """Preflight a locally provisioned helper, without executing it.

    The invocation layer must independently prevent replacement between
    verification and exec (e.g. with a trusted immutable install directory).
    This method alone is not an execution security boundary.
    """
    operation.validate()
    helper = operation.executable
    if not helper.exists():
        raise TrustedOperationPolicyError("installed helper missing")
    # Reject any symlink component, not just a symlink at the final path.
    for node in (helper, *helper.parents):
        if node.is_symlink():
            raise TrustedOperationPolicyError("helper path traverses a symlink")
    try:
        fd = os.open(helper, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise TrustedOperationPolicyError("helper is not a regular file")
            if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise TrustedOperationPolicyError("helper is writable by others")
            digest = hashlib.sha256()
            while True:
                block = os.read(fd, 1048576)
                if not block:
                    break
                digest.update(block)
        finally:
            os.close(fd)
    except OSError as exc:
        raise TrustedOperationPolicyError("cannot verify installed helper") from exc
    if digest.hexdigest() != operation.executable_sha256:
        raise TrustedOperationPolicyError("installed helper does not match pinned SHA-256")


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
