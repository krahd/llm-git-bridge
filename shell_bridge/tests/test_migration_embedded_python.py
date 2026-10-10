import ast
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"<<'(?P<name>PY[A-Z0-9]*)'\n(?P<source>.*?)\n(?P=name)(?=\n|$)", re.DOTALL)


class EmbeddedMigrationPythonSyntaxTests(unittest.TestCase):
    def test_every_inline_migration_python_program_compiles(self):
        for name in ("install.sh", "cutover.sh", "prepare_v6_on_mac.sh",
                     "v6_release_once.sh"):
            with self.subTest(script=name):
                text = (ROOT / name).read_text(encoding="utf-8")
                matches = list(PATTERN.finditer(text))
                self.assertGreater(len(matches), 1, "no inline programs found")
                for match in matches:
                    with self.subTest(script=name, heredoc=match.group("name")):
                        ast.parse(match.group("source"),
                                  filename=f"{name}:{match.group('name')}")


if __name__ == "__main__":
    unittest.main()
