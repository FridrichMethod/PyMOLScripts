"""Tests for install.sh and install.ps1: sources, replacing, failures, usage.

The local-edit guard (records, exit 3, --force) is covered by
test_local_edits.py; the fixtures both use live in support.py.

install.sh runs under /bin/sh everywhere but Windows. install.ps1 runs under
every PowerShell on PATH: pwsh, plus Windows PowerShell 5.1 on Windows.
Missing shells skip their tests.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""

import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    NAMES,
    OLD_MTIME,
    ON_WINDOWS,
    PWSH,
    REPO,
    WINDOWS_POWERSHELL,
    InstallerBase,
    PowerShellRunner,
    ShRunner,
    configs,
)


class InstallerContract(InstallerBase):
    """Behaviour both installers share; a runner mixin says how to run one."""

    # --- sources ------------------------------------------------------------

    def test_checkout_mode_installs_into_home_without_downloading(self):
        files = configs("checkout")
        result = self.install(self.checkout(files))
        self.assert_installed(self.home, files)
        self.assert_recorded(self.home, files)
        self.assertEqual(self.server.requests, [])
        out = result.stdout.decode()
        for name in NAMES:
            self.assertIn(f"installed {self.home / name}", out)

    @unittest.skipUnless((REPO / ".git").exists(), "not a git checkout")
    def test_repository_checkout_installs_its_configs_byte_for_byte(self):
        self.install(REPO / self.script_name, self.prefix_flag, str(self.prefix))
        self.assert_installed(
            self.prefix, {name: (REPO / "configs" / name).read_bytes() for name in NAMES}
        )
        self.assertEqual(self.server.requests, [])

    def test_incomplete_checkout_downloads_instead(self):
        files = configs("served")
        self.server.publish("testref", files)
        script = self.checkout({".pymolrc": b"local only\n"})
        self.install(script, self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)

    def test_configs_without_git_beside_the_installer_download_instead(self):
        files = configs("served")
        self.server.publish("testref", files)
        script = self.checkout(configs("unversioned"), git=False)
        self.install(script, self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)

    def test_temp_copy_ignores_configs_planted_beside_it(self):
        # dotfiles used to stage the downloaded installer directly in $TMPDIR,
        # which other users can write to on a shared /tmp.
        files = configs("served")
        self.server.publish("testref", files)
        planted = configs("planted")
        self.seed(self.tmpdir / "configs", planted)
        (self.tmpdir / ".git").mkdir()
        script = self.tmpdir / self.temp_copy_name
        shutil.copy2(REPO / self.script_name, script)
        self.install(script, self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        self.assert_recorded(self.prefix, files)
        self.assertEqual(self.server.config_requests("testref"), sorted(
            f"/base/testref/configs/{name}" for name in NAMES
        ))

    def test_installer_ignores_a_checkout_that_others_planted_beside_it(self):
        # Saved under its own name (curl -O) into a shared directory such as
        # /tmp or C:\Temp, where anyone can create .git and configs/ first.
        files = configs("served")
        self.server.publish("testref", files)
        planted = self.tmpdir / "configs"
        self.seed(planted, configs("planted"))
        (self.tmpdir / ".git").mkdir()
        script = self.tmpdir / self.script_name
        shutil.copy2(REPO / self.script_name, script)
        env = self.foreign_env(
            self.tmpdir / ".git", planted, *(planted / name for name in NAMES)
        )
        result = self.install(script, self.prefix_flag, str(self.prefix), env=env)
        self.assert_installed(self.prefix, files)
        self.assert_recorded(self.prefix, files)
        self.assertEqual(self.server.config_requests("testref"), sorted(
            f"/base/testref/configs/{name}" for name in NAMES
        ))
        self.assertIn(str(planted), result.stderr.decode())

    def test_symlinked_configs_beside_the_installer_download_instead(self):
        # A link someone else owns could be pointed elsewhere after the check.
        files = configs("served")
        self.server.publish("testref", files)
        real = self.root / "elsewhere"
        self.seed(real, configs("linked"))
        top = self.root / "linked"
        (top / ".git").mkdir(parents=True)
        shutil.copy2(REPO / self.script_name, top / self.script_name)
        self.symlink(str(real), str(top / "configs"), target_is_directory=True)
        self.install(top / self.script_name, self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)

    def test_download_mode_fetches_the_ref_from_the_base_url(self):
        files = configs("served")
        self.server.publish("testref", files)
        self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        self.assertEqual(
            sorted(self.server.requests),
            sorted(f"/base/testref/configs/{name}" for name in NAMES),
        )
        self.assert_no_temp_left(self.prefix)

    def test_download_mode_defaults_to_main(self):
        files = configs("main")
        self.server.publish("main", files)
        env = dict(self.env)
        del env["PYMOLSCRIPTS_REF"]
        self.install(self.lone(), self.prefix_flag, str(self.prefix), env=env)
        self.assert_installed(self.prefix, files)

    # --- replacing ----------------------------------------------------------

    def test_replaces_files_it_installed_before(self):
        self.install(self.checkout(configs("old"), "old"), self.prefix_flag, str(self.prefix))
        files = configs("new")
        result = self.install(self.checkout(files), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        self.assert_recorded(self.prefix, files)
        self.assertIn(f"updated {self.prefix / '.pymolrc'}", result.stdout.decode())
        self.assert_no_temp_left(self.prefix)

    def test_replaces_symlinks_without_writing_through_them(self):
        old = configs("old")
        self.install(self.checkout(old, "old"), self.prefix_flag, str(self.prefix))
        targets = self.root / "targets"
        self.seed(targets, old)
        for name in NAMES:
            os.remove(self.prefix / name)
            self.symlink(str(targets / name), str(self.prefix / name))
        files = configs("new")
        self.install(self.checkout(files), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        for name, body in old.items():
            self.assertEqual((targets / name).read_bytes(), body, "wrote through a symlink")

    def test_leaves_symlinks_whose_content_is_current(self):
        files = configs("same")
        self.prefix.mkdir()
        targets = self.root / "targets"
        self.seed(targets, files)
        for name in NAMES:
            self.symlink(str(targets / name), str(self.prefix / name))
        result = self.install(self.checkout(files), self.prefix_flag, str(self.prefix))
        self.assertIn("already current", result.stdout.decode())
        for name in NAMES:
            self.assertTrue((self.prefix / name).is_symlink(), f"{name} was replaced")
        self.assert_recorded(self.prefix, files)

    def test_replaces_dangling_symlinks(self):
        self.prefix.mkdir()
        for name in NAMES:
            self.symlink(str(self.root / "gone" / name), str(self.prefix / name))
        files = configs("new")
        self.server.publish("testref", files)
        self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        self.assert_no_temp_left(self.prefix)

    # --- idempotence --------------------------------------------------------

    def check_second_run_writes_nothing(self, script):
        files = configs("current")
        self.install(script, self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        self.assert_recorded(self.prefix, files)
        record_dir = self.record_path(self.prefix, NAMES[0]).parent
        self.age(*(self.prefix / name for name in NAMES), self.prefix)
        self.age(*record_dir.iterdir(), record_dir)
        before = self.snapshot(self.prefix)
        records = self.tree(self.state_root)
        result = self.install(script, self.prefix_flag, str(self.prefix))
        self.assertEqual(self.snapshot(self.prefix), before)
        self.assertEqual(os.stat(self.prefix).st_mtime_ns, OLD_MTIME * 10**9, "wrote into DIR")
        self.assertEqual(self.tree(self.state_root), records, "rewrote a record")
        lines = result.stdout.decode().strip().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("already current", lines[0])
        self.assert_no_temp_left(self.prefix)

    def test_current_files_are_left_untouched_from_a_checkout(self):
        self.check_second_run_writes_nothing(self.checkout(configs("current")))

    def test_current_files_are_left_untouched_after_download(self):
        self.server.publish("testref", configs("current"))
        self.check_second_run_writes_nothing(self.lone())

    def test_rewrites_only_the_changed_file(self):
        old = configs("old")
        files = dict(old, **{".pymolrc.py": configs("new")[".pymolrc.py"]})
        self.install(self.checkout(old, "old"), self.prefix_flag, str(self.prefix))
        self.age(self.prefix / ".pymolrc")
        result = self.install(self.checkout(files), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        self.assertEqual(os.stat(self.prefix / ".pymolrc").st_mtime_ns, OLD_MTIME * 10**9)
        lines = result.stdout.decode().strip().splitlines()
        self.assertEqual(lines, [f"PyMOLScripts: updated {self.prefix / '.pymolrc.py'}"])
        self.assert_recorded(self.prefix, files)

    # --- failures -----------------------------------------------------------

    def test_failed_download_changes_nothing(self):
        files = configs("new")
        self.server.publish("testref", {".pymolrc": files[".pymolrc"]})  # .py: 404
        self.prefix.mkdir()
        (self.prefix / ".pymolrc").write_bytes(b"old rc\n")
        self.age(self.prefix / ".pymolrc")
        result = self.run_installer(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertEqual((self.prefix / ".pymolrc").read_bytes(), b"old rc\n")
        self.assertEqual(os.stat(self.prefix / ".pymolrc").st_mtime_ns, OLD_MTIME * 10**9)
        self.assertFalse((self.prefix / ".pymolrc.py").exists())
        self.assertIn("/base/testref/configs/.pymolrc.py", result.stderr.decode())
        self.assertEqual(sorted(p.name for p in self.prefix.iterdir()), [".pymolrc"])
        self.assertFalse(self.state_root.exists(), "wrote a record")
        self.assert_no_temp_left(self.prefix)

    def test_empty_download_is_an_error(self):
        self.server.publish("testref", {".pymolrc": b"rc\n", ".pymolrc.py": b""})
        result = self.run_installer(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn("empty", result.stderr.decode())
        self.assertFalse(self.prefix.exists())
        self.assertFalse(self.state_root.exists(), "wrote a record")
        self.assert_no_temp_left()

    def test_destination_that_is_a_directory_is_an_error(self):
        self.prefix.mkdir()
        (self.prefix / ".pymolrc").write_bytes(b"old rc\n")
        (self.prefix / ".pymolrc.py").mkdir()
        result = self.run_installer(
            self.checkout(configs("new")), self.prefix_flag, str(self.prefix)
        )
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn("directory", result.stderr.decode())
        self.assertEqual((self.prefix / ".pymolrc").read_bytes(), b"old rc\n")

    # --- prefix and usage ---------------------------------------------------

    def test_creates_a_missing_prefix(self):
        target = self.prefix / "nested" / "dir"
        files = configs("new")
        self.install(self.checkout(files), self.prefix_flag, str(target))
        self.assert_installed(target, files)
        self.assert_recorded(target, files)
        self.assertFalse((self.home / ".pymolrc").exists())

    def test_prefix_that_is_a_file_is_an_error(self):
        self.prefix.write_bytes(b"not a directory\n")
        result = self.run_installer(
            self.checkout(configs("new")), self.prefix_flag, str(self.prefix)
        )
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn("not a directory", result.stderr.decode())

    def test_usage_errors_exit_2_and_write_nothing(self):
        script = self.checkout(configs("new"))
        for args in self.usage_cases:
            with self.subTest(args=args):
                result = self.run_installer(script, *args, cwd=self.root)
                self.assertEqual(result.returncode, 2, self.describe(result))
                self.assertNotEqual(result.stderr.strip(), b"")
                self.assertFalse((self.home / ".pymolrc").exists())
                self.assertFalse((self.root / "relative").exists())
                self.assertFalse(self.prefix.exists())
                self.assertFalse(self.state_root.exists())

    def test_help_exits_0(self):
        result = self.run_installer(self.checkout(configs("new")), self.help_flag)
        self.assertEqual(result.returncode, 0, self.describe(result))
        self.assertIn(b"Usage:", result.stdout)
        for word in (self.force_flag, ".pymolrc.local.pml", "3 stopped"):
            self.assertIn(word.encode(), result.stdout)
        self.assertFalse((self.home / ".pymolrc").exists())
        self.assertFalse(self.state_root.exists())


@unittest.skipIf(ON_WINDOWS, "install.sh is the POSIX installer")
class ShInstallerTests(ShRunner, InstallerContract, unittest.TestCase):
    def restricted_path(self, *tools):
        """A PATH holding only what install.sh needs, plus `tools`."""
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        for tool in ("cat", "id", "ls", "mkdir", "mktemp", "mv", "od", "rm", "tr", *tools):
            found = shutil.which(tool, path=self.env.get("PATH"))
            if found is None:
                self.skipTest(f"{tool} not found")
            os.symlink(found, bindir / tool)
        return str(bindir)

    def test_piped_script_downloads_even_from_inside_a_checkout(self):
        files = configs("served")
        self.server.publish("testref", files)
        # $0 is "sh" when piped; a file of that name next to configs/ in the
        # working directory must not make it look like a checkout.
        cwd = self.checkout(configs("local")).parent
        (cwd / "sh").write_bytes(b"")
        with open(REPO / "install.sh", "rb") as stdin:
            result = subprocess.run(
                ["/bin/sh", "-s", "--", "--prefix", str(self.prefix)],
                env=self.env,
                stdin=stdin,
                cwd=cwd,
                capture_output=True,
                timeout=180,
            )
        self.assertEqual(result.returncode, 0, self.describe(result))
        self.assert_installed(self.prefix, files)
        self.assertEqual(len(self.server.requests), 2)

    def test_compares_without_cmp(self):
        # Minimal images (Fedora, Rocky Linux) ship no diffutils, so no cmp
        # and no diff; a local edit still stops and --force still works.
        files = configs("current")
        script = self.checkout(files)
        env = dict(self.env, PATH=self.restricted_path("cp", "date"))
        self.install(script, "--prefix", str(self.prefix), env=env)
        self.assert_installed(self.prefix, files)
        lines = self.install(script, "--prefix", str(self.prefix), env=env).stdout.decode().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("already current", lines[0])
        edited = files[".pymolrc"] + b"# edited\n"
        (self.prefix / ".pymolrc").write_bytes(edited)
        result = self.stopped(script, "--prefix", str(self.prefix), env=env)
        self.assertNotIn("diff -u", result.stderr.decode())
        lines = self.install(script, "--prefix", str(self.prefix), "--force", env=env).stdout.decode().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(f"PyMOLScripts: updated {self.prefix / '.pymolrc'} "), lines)
        self.assert_installed(self.prefix, files)
        backups = sorted(self.prefix.glob(".pymolrc.local-*"))
        self.assertEqual(len(backups), 1, backups)
        self.assertEqual(backups[0].read_bytes(), edited)

    def test_missing_download_tool_is_named(self):
        env = dict(self.env, PATH=self.restricted_path())
        result = self.run_installer(self.lone(), "--prefix", str(self.prefix), env=env)
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn("curl not found", result.stderr.decode())
        self.assertFalse(self.prefix.exists())

    def test_wget_is_used_without_curl(self):
        if shutil.which("wget") is None:
            self.skipTest("wget not found")
        files = configs("served")
        self.server.publish("testref", files)
        env = dict(self.env, PATH=self.restricted_path("wget"))
        self.install(self.lone(), "--prefix", str(self.prefix), env=env)
        self.assert_installed(self.prefix, files)

    def test_unset_home_is_an_error(self):
        env = {k: v for k, v in self.env.items() if k != "HOME"}
        result = self.run_installer(self.checkout(configs("new")), env=env)
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn("HOME is not set", result.stderr.decode())

    def test_new_files_follow_the_umask(self):
        self.install(self.checkout(configs("new")), "--prefix", str(self.prefix), umask=0o027)
        for name in NAMES:
            mode = stat.S_IMODE(os.stat(self.prefix / name).st_mode)
            self.assertEqual(mode, 0o640, f"{name}: {oct(mode)}")


class PowerShellContract(PowerShellRunner, InstallerContract):
    @unittest.skipUnless(ON_WINDOWS, "a dangling link has no type outside Windows")
    def test_dangling_directory_link_is_an_error(self):
        self.prefix.mkdir()
        (self.prefix / ".pymolrc").write_bytes(b"old rc\n")
        try:
            os.symlink(str(self.root / "gone"), str(self.prefix / ".pymolrc.py"), True)
        except OSError as exc:
            self.skipTest(f"cannot create symlinks here: {exc}")
        result = self.run_installer(
            self.checkout(configs("new")), self.prefix_flag, str(self.prefix)
        )
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn("directory", result.stderr.decode())
        self.assertEqual((self.prefix / ".pymolrc").read_bytes(), b"old rc\n")

    def test_unset_home_variable_is_an_error(self):
        var = "USERPROFILE" if ON_WINDOWS else "HOME"
        env = {k: v for k, v in self.env.items() if k.upper() != var}
        result = self.run_installer(self.checkout(configs("new")), env=env)
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn(f"{var} is not set", result.stderr.decode())


@unittest.skipUnless(PWSH, "pwsh not found")
class PwshInstallerTests(PowerShellContract, unittest.TestCase):
    exe = PWSH or ""


@unittest.skipUnless(WINDOWS_POWERSHELL, "Windows PowerShell 5.1 not found")
class WindowsPowerShellInstallerTests(PowerShellContract, unittest.TestCase):
    exe = WINDOWS_POWERSHELL or ""

    def test_checkout_mode_with_pwsh_modules_on_the_module_path(self):
        # Windows PowerShell started by a program that pwsh started (python
        # under a pwsh CI step, say) inherits pwsh's PSModulePath, so a cmdlet
        # it autoloads, such as Get-Acl, comes from PowerShell 7's module and
        # fails to load. The ownership check must not depend on one.
        modules = Path(PWSH).resolve().parent / "Modules" if PWSH else None
        if modules is None or not (modules / "Microsoft.PowerShell.Security").is_dir():
            self.skipTest("no PowerShell 7 Microsoft.PowerShell.Security module")
        env = {k: v for k, v in self.env.items() if k.upper() != "PSMODULEPATH"}
        inherited = [v for k, v in self.env.items() if k.upper() == "PSMODULEPATH"]
        env["PSModulePath"] = os.pathsep.join([str(modules), *inherited])
        files = configs("checkout")
        self.install(self.checkout(files), self.prefix_flag, str(self.prefix), env=env)
        self.assert_installed(self.prefix, files)
        self.assertEqual(self.server.requests, [])


if __name__ == "__main__":
    unittest.main()
