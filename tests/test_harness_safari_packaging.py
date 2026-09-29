from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/"safari"/"package.sh"
IGNORE=ROOT/"safari"/".gitignore"

class SafariPackagingTests(unittest.TestCase):
    def test_packaging_is_reproducible_and_noninteractive(self):
        text=PACKAGE.read_text()
        self.assertIn("safari-web-extension-packager",text)
        for flag in ("--swift","--macos-only","--copy-resources","--no-open","--no-prompt"):
            self.assertIn(flag,text)
        self.assertIn('net.laurenzo.chatgpt-conversation-harness',text)
    def test_generated_project_is_not_canonical_source(self):
        self.assertIn("build/",IGNORE.read_text())

if __name__=="__main__": unittest.main()
