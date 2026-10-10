import pathlib
import secrets
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from inbound_ssh_witness import create, verify, parse_connection


class InboundSSHSessionWitnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=pathlib.Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.directory = pathlib.Path(self.temp.name)
        self.directory.chmod(0o700)
        self.nonce = secrets.token_hex(24)
        self.receipt = self.directory / "inbound.json"
        self.connection = "198.51.100.42 53421 192.0.2.55 22"

    def test_nonlocal_ssh_login_creates_verifiable_one_time_receipt(self):
        create(self.receipt, self.nonce, self.connection, timestamp=1000)
        result = verify(self.receipt, self.nonce, now=1001)
        self.assertTrue(result["inbound_ssh_nonlocal_session_witness"])
        self.assertFalse(result["external_internet_reachability_verified"])
        self.assertEqual(result["network_peer"], "198.51.100.42")
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)

    def test_no_replay_or_wrong_nonce(self):
        create(self.receipt, self.nonce, self.connection, timestamp=1000)
        with self.assertRaises(ValueError):
            verify(self.receipt, secrets.token_hex(24), now=1001)
        with self.assertRaises(FileExistsError):
            create(self.receipt, self.nonce, self.connection, timestamp=1000)

    def test_stale_future_or_ambiguous_time_rejected(self):
        create(self.receipt, self.nonce, self.connection, timestamp=1000)
        for now in (999, 1181):
            with self.subTest(now=now):
                with self.assertRaises(ValueError):
                    verify(self.receipt, self.nonce, now=now)

    def test_forged_loopback_or_invalid_ssh_connection_rejected(self):
        for connection in ("", "127.0.0.1 2123 127.0.0.1 22",
                           "not.an.ip 123 10.0.0.2 22",
                           "198.51.100.1 0 10.0.0.2 22",
                           "198.51.100.1 100 10.0.0.2 22 extra"):
            with self.subTest(connection=connection):
                with self.assertRaises(ValueError):
                    parse_connection(connection)

    def test_witness_does_not_create_in_group_writable_directory(self):
        self.directory.chmod(0o770)
        with self.assertRaises(ValueError):
            create(self.receipt, self.nonce, self.connection)
        self.assertFalse(self.receipt.exists())

    def test_symlinked_receipt_is_rejected(self):
        other = self.directory / "other"
        other.write_text("{}")
        self.receipt.symlink_to(other)
        with self.assertRaises(ValueError):
            verify(self.receipt, self.nonce, now=1000)


if __name__ == "__main__":
    unittest.main()
