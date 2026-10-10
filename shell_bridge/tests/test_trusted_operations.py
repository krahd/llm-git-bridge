import pathlib
import hashlib
import os
import tempfile
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from trusted_operations import TrustedOperation, TrustedOperationPolicyError, authorize_known_operation, verify_installed_helper


def descriptor(**overrides):
    fields = dict(
        name="bounded-deploy",
        executable=pathlib.Path("/opt/local-executor/helpers/deploy"),
        executable_sha256="a" * 64,
        permitted_roots=(pathlib.Path("/srv/example"),),
        permitted_actions=("inspect", "prepare", "apply", "rollback"),
        requires_confirmation=True,
    )
    fields.update(overrides)
    return TrustedOperation(**fields)


class TrustedOperationContractTests(unittest.TestCase):
    def test_known_explicit_action(self):
        value = descriptor()
        self.assertIs(authorize_known_operation({"bounded-deploy": value}, "bounded-deploy", "inspect"), value)

    def test_unknown_helper_and_unlisted_action_fail_closed(self):
        with self.assertRaises(TrustedOperationPolicyError):
            authorize_known_operation({}, "bounded-deploy", "inspect")
        with self.assertRaises(TrustedOperationPolicyError):
            authorize_known_operation({"bounded-deploy": descriptor()}, "bounded-deploy", "shell")

    def test_missing_digest_and_dynamic_scope_are_rejected(self):
        for changes in (
            {"executable_sha256": ""},
            {"executable_sha256": "A" * 64},
            {"permitted_roots": ()},
            {"permitted_roots": (pathlib.Path("relative"),)},
            {"permitted_roots": (pathlib.Path("/srv/../etc"),)},
            {"permitted_actions": ("inspect", "inspect")},
            {"requires_confirmation": False},
        ):
            with self.subTest(changes=changes), self.assertRaises(TrustedOperationPolicyError):
                descriptor(**changes).validate()

    def test_contract_never_exposes_shell_execution(self):
        import trusted_operations
        self.assertFalse(hasattr(trusted_operations, "run"))
        self.assertFalse(hasattr(trusted_operations, "execute"))

    def test_helper_integrity_and_modified_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = pathlib.Path(directory) / "helper"
            helper.write_bytes(b"safe helper")
            helper.chmod(0o700)
            operation = descriptor(
                executable=helper,
                executable_sha256=hashlib.sha256(b"safe helper").hexdigest(),
            )
            verify_installed_helper(operation)
            helper.write_bytes(b"modified")
            with self.assertRaises(TrustedOperationPolicyError):
                verify_installed_helper(operation)

    def test_symlink_and_group_writable_helper_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            original = pathlib.Path(directory) / "original"
            original.write_bytes(b"safe")
            original.chmod(0o700)
            linked = pathlib.Path(directory) / "linked"
            linked.symlink_to(original)
            digest = hashlib.sha256(b"safe").hexdigest()
            with self.assertRaises(TrustedOperationPolicyError):
                verify_installed_helper(descriptor(executable=linked, executable_sha256=digest))
            original.chmod(0o770)
            with self.assertRaises(TrustedOperationPolicyError):
                verify_installed_helper(descriptor(executable=original, executable_sha256=digest))
