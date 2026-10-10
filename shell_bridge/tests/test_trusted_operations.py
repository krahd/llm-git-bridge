import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from trusted_operations import TrustedOperation, TrustedOperationPolicyError, authorize_known_operation


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
