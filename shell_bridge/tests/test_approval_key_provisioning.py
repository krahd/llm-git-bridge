import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]

class ApprovalKeyProvisioningTests(unittest.TestCase):
    def test_native_helper_has_explicit_key_provisioning_mode(self):
        src = (ROOT / "shell_bridge" / "approval_gui.swift").read_text()
        self.assertIn("func provision(context: LAContext) throws -> String", src)
        self.assertIn("APPROVAL_PROVISION_ROOT", src)
        self.assertIn('CommandLine.arguments.contains("--provision-key")', src)
        self.assertIn("return sha256Hex(pemData)", src)

    def test_installer_provisions_and_pins_key_before_manifest(self):
        src = (ROOT / "shell_bridge" / "install.sh").read_text()
        provision = src.index('APPROVAL_PROVISION_ROOT="$APPROVAL_ROOT"')
        pin = src.index("cfg['operator_approval_public_key_sha256']=pin")
        manifest = src.rindex('python3 - "$INSTALL_DIR/install-manifest.json"')
        self.assertLess(provision, pin)
        self.assertLess(pin, manifest)
        self.assertIn('[ "${#APPROVAL_KEY_SHA256}" -eq 64 ]', src)
        self.assertIn("os.replace(tmp,path)", src)

if __name__ == "__main__":
    unittest.main()
