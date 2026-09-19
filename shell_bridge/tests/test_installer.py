import subprocess, tarfile, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class InstallerPackageTests(unittest.TestCase):
    def test_installer_bash_syntax_and_no_personal_root(self):
        cp=subprocess.run(["bash","-n",str(ROOT/"install.sh")],capture_output=True,text=True)
        self.assertEqual(cp.returncode,0,cp.stderr)
        text=(ROOT/"install.sh").read_text()
        self.assertNotIn("/Users/tom",text)
        self.assertNotIn("com.tom",text)
        self.assertIn("drive_root_folder_id",text)
        self.assertIn("--stage-only",text)
        self.assertIn("SHELL_BRIDGE_STAGED=1",text)
    def test_package_excludes_python_cache(self):
        with tempfile.TemporaryDirectory() as td:
            out=Path(td)/"bridge.tar.gz"
            subprocess.run(["python3",str(ROOT/"build_package.py"),"--output",str(out)],check=True)
            with tarfile.open(out,"r:gz") as tf: names=tf.getnames()
            self.assertFalse(any("__pycache__" in n or n.endswith('.pyc') for n in names))
            self.assertTrue(any(n.endswith("MANIFEST.sha256") for n in names))
if __name__=='__main__': unittest.main(verbosity=2)
