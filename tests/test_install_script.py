from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
WRAPPER = ROOT / "bin" / "llm-git-bridge"


class InstallerTests(unittest.TestCase):
    def test_installer_is_posix_shell_syntax_clean(self) -> None:
        proc = subprocess.run(["sh", "-n", str(INSTALLER)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_installer_has_no_personal_repository_paths(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertNotIn("/Users/", text)
        self.assertNotIn("tom-repos", text)
        self.assertNotIn("projects/", text)

    def test_installer_uses_clean_canonical_sources_and_safe_update(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("https://github.com/krahd/llm-git-bridge.git", text)
        self.assertNotIn("utm_", text.lower())
        self.assertNotIn("reset --hard", text)
        self.assertIn("merge --ff-only", text)
        self.assertIn('"$BRIDGE" setup', text)

    def test_daemon_install_is_macos_only(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('[ "$OS" = Darwin ]', text)
        self.assertIn('"$BRIDGE" daemon install --command "$BRIDGE"', text)

    def test_installer_non_tty_does_not_auto_confirm_optional_actions(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        confirm_start = text.index("confirm() {")
        confirm_end = text.index("\n}\n", confirm_start)
        confirm_body = text[confirm_start:confirm_end]
        self.assertIn('if [ ! -t 0 ]; then', confirm_body)
        non_tty = confirm_body.split('if [ ! -t 0 ]; then', 1)[1].split('fi', 1)[0]
        self.assertIn('return 1', non_tty)
        self.assertNotIn('[ "$default" = yes ]', non_tty)

    def test_installer_preserves_non_symlink_command_and_cleans_interrupted_clone(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('exists and is not a symlink; refusing to overwrite it', text)
        self.assertIn('trap cleanup_incomplete_install EXIT', text)
        self.assertIn("trap 'exit 130' INT", text)
        self.assertIn("trap 'exit 143' TERM", text)

    def test_installer_refuses_setup_without_a_configured_rclone_remote(self) -> None:
        text = INSTALLER.read_text(encoding="utf-8")
        self.assertIn(
            "fail \"no rclone remote is configured; run 'rclone config' in a terminal, then rerun this installer\"",
            text,
        )

    def test_installer_first_run_and_rerun_are_idempotent_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed"
            origin = root / "origin.git"
            install_dir = root / "installed"
            bin_dir = root / "commands"
            fake_bin = root / "fake-bin"
            home = root / "home"
            config_dir = root / "config"
            fake_bin.mkdir()
            home.mkdir()
            config_dir.mkdir()

            subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
            (seed / "bin").mkdir()
            bridge = seed / "bin" / "llm-git-bridge"
            bridge.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            bridge.chmod(0o755)
            subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(seed),
                    "-c", "user.name=Installer Test",
                    "-c", "user.email=installer@example.invalid",
                    "commit", "-qm", "initial",
                ],
                check=True,
            )
            subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)

            rclone = fake_bin / "rclone"
            rclone.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            rclone.chmod(0o755)

            config_file = config_dir / "llm-git-bridge" / "config.json"
            config_file.parent.mkdir()
            config_file.write_text('{"keep": true}\n', encoding="utf-8")

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "XDG_CONFIG_HOME": str(config_dir),
                    "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                    "LLM_GIT_BRIDGE_REPO_URL": str(origin),
                    "LLM_GIT_BRIDGE_REF": "main",
                    "LLM_GIT_BRIDGE_INSTALL_DIR": str(install_dir),
                    "LLM_GIT_BRIDGE_BIN_DIR": str(bin_dir),
                }
            )

            first = subprocess.run(
                ["sh", str(INSTALLER), "--no-setup", "--no-daemon"],
                env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue((install_dir / ".git").is_dir())
            self.assertTrue((bin_dir / "llm-git-bridge").is_symlink())
            first_head = subprocess.check_output(
                ["git", "-C", str(install_dir), "rev-parse", "HEAD"], text=True
            ).strip()

            marker = seed / "updated.txt"
            marker.write_text("second\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(seed), "add", "updated.txt"], check=True)
            subprocess.run(
                [
                    "git", "-C", str(seed),
                    "-c", "user.name=Installer Test",
                    "-c", "user.email=installer@example.invalid",
                    "commit", "-qm", "update",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(seed), "push", "-q", str(origin), "main"], check=True
            )

            second = subprocess.run(
                ["sh", str(INSTALLER), "--no-setup", "--no-daemon"],
                env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            second_head = subprocess.check_output(
                ["git", "-C", str(install_dir), "rev-parse", "HEAD"], text=True
            ).strip()
            self.assertNotEqual(first_head, second_head)
            self.assertTrue((install_dir / "updated.txt").is_file())
            self.assertEqual(config_file.read_text(encoding="utf-8"), '{"keep": true}\n')

    def test_installer_tag_install_is_rerunnable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed"
            origin = root / "origin.git"
            install_dir = root / "installed"
            bin_dir = root / "commands"
            fake_bin = root / "fake-bin"
            home = root / "home"
            fake_bin.mkdir()
            home.mkdir()

            subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
            (seed / "bin").mkdir()
            bridge = seed / "bin" / "llm-git-bridge"
            bridge.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            bridge.chmod(0o755)
            subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(seed),
                    "-c", "user.name=Installer Test",
                    "-c", "user.email=installer@example.invalid",
                    "commit", "-qm", "initial",
                ],
                check=True,
            )
            tagged_head = subprocess.check_output(
                ["git", "-C", str(seed), "rev-parse", "HEAD"], text=True
            ).strip()
            subprocess.run(["git", "-C", str(seed), "tag", "v-test"], check=True)
            subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)

            rclone = fake_bin / "rclone"
            rclone.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            rclone.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                    "LLM_GIT_BRIDGE_REPO_URL": str(origin),
                    "LLM_GIT_BRIDGE_REF": "v-test",
                    "LLM_GIT_BRIDGE_INSTALL_DIR": str(install_dir),
                    "LLM_GIT_BRIDGE_BIN_DIR": str(bin_dir),
                }
            )

            for _ in range(2):
                proc = subprocess.run(
                    ["sh", str(INSTALLER), "--no-setup", "--no-daemon"],
                    env=env, capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                head = subprocess.check_output(
                    ["git", "-C", str(install_dir), "rev-parse", "HEAD"], text=True
                ).strip()
                self.assertEqual(head, tagged_head)

    def test_installer_refuses_local_branch_commits_not_on_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seed = root / "seed"
            origin = root / "origin.git"
            install_dir = root / "installed"
            bin_dir = root / "commands"
            fake_bin = root / "fake-bin"
            home = root / "home"
            fake_bin.mkdir()
            home.mkdir()

            subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
            (seed / "bin").mkdir()
            bridge = seed / "bin" / "llm-git-bridge"
            bridge.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            bridge.chmod(0o755)
            subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
            subprocess.run(
                [
                    "git", "-C", str(seed),
                    "-c", "user.name=Installer Test",
                    "-c", "user.email=installer@example.invalid",
                    "commit", "-qm", "initial",
                ],
                check=True,
            )
            subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)

            rclone = fake_bin / "rclone"
            rclone.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            rclone.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                    "LLM_GIT_BRIDGE_REPO_URL": str(origin),
                    "LLM_GIT_BRIDGE_REF": "main",
                    "LLM_GIT_BRIDGE_INSTALL_DIR": str(install_dir),
                    "LLM_GIT_BRIDGE_BIN_DIR": str(bin_dir),
                }
            )

            first = subprocess.run(
                ["sh", str(INSTALLER), "--no-setup", "--no-daemon"],
                env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            (install_dir / "local-only.txt").write_text("local\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(install_dir), "add", "local-only.txt"], check=True)
            subprocess.run(
                [
                    "git", "-C", str(install_dir),
                    "-c", "user.name=Installer Test",
                    "-c", "user.email=installer@example.invalid",
                    "commit", "-qm", "local-only",
                ],
                check=True,
            )
            local_head = subprocess.check_output(
                ["git", "-C", str(install_dir), "rev-parse", "HEAD"], text=True
            ).strip()

            second = subprocess.run(
                ["sh", str(INSTALLER), "--no-setup", "--no-daemon"],
                env=env, capture_output=True, text=True, timeout=20,
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("contains commits not present", second.stderr)
            self.assertEqual(
                subprocess.check_output(
                    ["git", "-C", str(install_dir), "rev-parse", "HEAD"], text=True
                ).strip(),
                local_head,
            )

    def test_wrapper_works_through_installer_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bin_dir = tmp_path / "bin"
            bin_dir.mkdir()
            link = bin_dir / "llm-git-bridge"
            link.symlink_to(WRAPPER)
            env = os.environ.copy()
            env["HOME"] = str(tmp_path / "home")
            env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
            env["XDG_STATE_HOME"] = str(tmp_path / "state")
            proc = subprocess.run(
                [str(link), "--help"],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("llm-git-bridge", proc.stdout)
            self.assertIn("setup", proc.stdout)


if __name__ == "__main__":
    unittest.main()
