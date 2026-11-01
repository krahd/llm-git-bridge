"""Regression contract for the native approval app's Rosetta-independent build."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")
CI = (ROOT.parent / ".github/workflows/bridge-v6-qualification.yml").read_text(encoding="utf-8")


class NativeArchitectureTests(unittest.TestCase):
    def test_installer_uses_physical_cpu_not_translated_shell(self):
        self.assertIn("/usr/sbin/sysctl -n hw.optional.arm64", INSTALL)
        self.assertIn('APPROVAL_TARGET_ARCH="arm64"', INSTALL)
        self.assertIn('APPROVAL_TARGET_ARCH="x86_64"', INSTALL)
        self.assertIn('-target "$APPROVAL_TARGET_ARCH-apple-macos11.0"', INSTALL)

    def test_installer_verifies_built_executable_before_manifest(self):
        required = (
            'APP_EXEC="$APP_BUNDLE/Contents/MacOS/local-executor-approval"',
            '/usr/bin/lipo -archs "$APP_EXEC"',
            'fail "approval app lacks required native architecture',
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, INSTALL)
        self.assertLess(INSTALL.index('APP_ARCHS="$(/usr/bin/lipo'), INSTALL.index("PYGENERATED"))
        self.assertLess(INSTALL.index('APP_ARCHS="$(/usr/bin/lipo'), INSTALL.index("chmod 700 \"$APP_EXEC\""))

    def test_macos_ci_cross_compiles_arm64_and_checks_slice(self):
        self.assertIn('Verify ARM64 approval app build for macOS 28', CI)
        self.assertIn('arm64-apple-macos11.0', CI)
        self.assertIn('lipo -archs', CI)


if __name__ == "__main__":
    unittest.main()
