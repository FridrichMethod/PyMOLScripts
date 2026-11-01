"""Tests for the local-edit guard of install.sh and install.ps1.

Each installer keeps a copy of what it last wrote (the record) and, before
writing anything, classifies both files: absent or a dangling link is
installed; equal to the new upstream is left alone and only recorded; equal,
ignoring CR, to the new upstream, to the record or (with no record, in
download mode) to the configs at the installer's legacy ref is updated;
anything else is a local edit, which stops the run with exit 3 and writes
nothing unless --force (-Force) is given.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import (  # noqa: E402
    CAN_CHMOD,
    NAMES,
    OLD_MTIME,
    ON_WINDOWS,
    PWSH,
    PYMOL_RUNS,
    REPO,
    WINDOWS_POWERSHELL,
    InstallerBase,
    PowerShellRunner,
    ShRunner,
    configs,
    legacy_ref,
)

BACKUP = re.compile(r"^(\.pymolrc(?:\.py)?)\.local-(\d{14})(?:-\d+)?$")


class LocalEditContract(InstallerBase):
    """The guard both installers share; a runner mixin says how to run one."""

    def setUp(self):
        super().setUp()
        self.v1 = configs("v1")
        self.v2 = configs("v2")
        self.edited = self.v1[".pymolrc"] + b"set ray_trace_mode, 1\r\n"

    def install_v1_then_publish_v2(self):
        """Install v1 by download (so it is recorded), then publish v2."""
        self.server.publish("testref", self.v1)
        self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.server.publish("testref", self.v2)
        self.server.requests.clear()

    def backups(self, directory):
        return sorted(p for p in directory.iterdir() if BACKUP.match(p.name))

    def override_line(self, result):
        lines = [line for line in result.stderr.decode().splitlines() if self.force_flag in line]
        self.assertTrue(lines, self.describe(result))
        return lines[-1].strip()

    def publish_installers(self):
        """The current installer at main and, at every ref the tests download
        configs from, one from before --force existed, which rejects it."""
        self.server.publish_script("main", self.script_name)
        for ref in ("testref", "oldref"):
            self.server.files[f"/base/{ref}/{self.script_name}"] = self.old_installer

    def override_env(self):
        """self.env with PYMOLSCRIPTS_REF and PYMOLSCRIPTS_BASE_URL pointing
        where the local server publishes nothing, so that a printed override
        line works only when it sets both itself. Unsetting them instead
        would let a broken line fall back to the real base URL."""
        return dict(
            self.env,
            PYMOLSCRIPTS_REF="wrongref",
            PYMOLSCRIPTS_BASE_URL=f"{self.server.base_url}/wrong",
        )

    # --- recording ----------------------------------------------------------

    def test_install_records_what_it_wrote(self):
        self.server.publish("testref", self.v1)
        self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, self.v1)
        self.assert_recorded(self.prefix, self.v1)
        self.assert_no_temp_left(self.prefix)
        if not ON_WINDOWS:
            for path in [self.state_root, *self.state_root.rglob("*")]:
                if path.is_dir():
                    mode = stat.S_IMODE(os.stat(path).st_mode)
                    self.assertEqual(mode, 0o700, f"{path}: {oct(mode)}")

    def test_update_replaces_files_matching_the_record(self):
        self.install_v1_then_publish_v2()
        result = self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, self.v2)
        self.assert_recorded(self.prefix, self.v2)
        out = result.stdout.decode()
        for name in NAMES:
            self.assertIn(f"updated {self.prefix / name}", out)
        self.assert_no_temp_left(self.prefix)

    def test_deleted_file_is_reinstalled(self):
        self.install_v1_then_publish_v2()
        self.server.publish("testref", self.v1)
        os.remove(self.prefix / ".pymolrc")
        result = self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(
            result.stdout.decode().splitlines(),
            [f"PyMOLScripts: installed {self.prefix / '.pymolrc'}"],
        )
        self.assert_installed(self.prefix, self.v1)
        self.assert_recorded(self.prefix, self.v1)

    def test_edit_equal_to_upstream_is_recorded_without_rewriting(self):
        self.install_v1_then_publish_v2()
        for name, body in self.v2.items():
            (self.prefix / name).write_bytes(body)
        self.age(*(self.prefix / name for name in NAMES), self.prefix)
        before = self.snapshot(self.prefix)
        result = self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertIn("already current", result.stdout.decode())
        self.assertEqual(self.snapshot(self.prefix), before)
        self.assertEqual(os.stat(self.prefix).st_mtime_ns, OLD_MTIME * 10**9, "wrote into DIR")
        self.assert_recorded(self.prefix, self.v2)

    def test_unrecorded_current_files_are_recorded_without_rewriting(self):
        # Every host the installer reached before the guard existed.
        self.seed(self.prefix, self.v1)
        self.server.publish("testref", self.v1)
        self.age(*(self.prefix / name for name in NAMES), self.prefix)
        before = self.tree(self.prefix)
        result = self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertIn("already current", result.stdout.decode())
        self.assertEqual(self.tree(self.prefix), before, "wrote into DIR")
        self.assert_recorded(self.prefix, self.v1)
        self.assertEqual(self.server.config_requests(legacy_ref(self.script_name)), [])
        records = self.tree(self.state_root)
        self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(self.tree(self.state_root), records, "rewrote a record")
        self.assertEqual(self.tree(self.prefix), before, "wrote into DIR")

    def test_line_ending_only_change_is_not_an_edit(self):
        # PyMOL's File > Edit pymolrc saves the CRLF configs with LF on a no-op save.
        self.install_v1_then_publish_v2()
        (self.prefix / ".pymolrc").write_bytes(self.v1[".pymolrc"].replace(b"\r", b""))
        self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, self.v2)
        self.assert_recorded(self.prefix, self.v2)

    def test_line_ending_only_difference_from_upstream_restores_the_file(self):
        self.server.publish("testref", self.v1)
        self.seed(self.prefix, {name: body.replace(b"\r", b"") for name, body in self.v1.items()})
        result = self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertIn(f"updated {self.prefix / '.pymolrc'}", result.stdout.decode())
        self.assert_installed(self.prefix, self.v1)
        self.assert_recorded(self.prefix, self.v1)

    # --- the legacy ref ---------------------------------------------------

    def test_unrecorded_legacy_version_is_updated(self):
        legacy = configs("legacy")
        ref = legacy_ref(self.script_name)
        self.server.publish(ref, legacy)
        self.server.publish("testref", self.v2)
        self.seed(self.prefix, {".pymolrc": legacy[".pymolrc"], ".pymolrc.py": self.v2[".pymolrc.py"]})
        result = self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(
            result.stdout.decode().splitlines(),
            [f"PyMOLScripts: updated {self.prefix / '.pymolrc'}"],
        )
        self.assert_installed(self.prefix, self.v2)
        self.assert_recorded(self.prefix, self.v2)
        # Only the file that differs from upstream is compared with the legacy ref.
        self.assertEqual(self.server.config_requests(ref), [f"/base/{ref}/configs/.pymolrc"])

    def test_installers_share_one_legacy_ref(self):
        self.assertEqual(legacy_ref("install.sh"), legacy_ref("install.ps1"))

    def test_unrecorded_differing_file_stops(self):
        ref = legacy_ref(self.script_name)
        self.server.publish(ref, configs("legacy"))
        self.server.publish("testref", self.v2)
        self.seed(self.prefix, {".pymolrc": b"my own rc\n", ".pymolrc.py": self.v2[".pymolrc.py"]})
        result = self.stopped(self.lone(), self.prefix_flag, str(self.prefix))
        err = result.stderr.decode()
        self.assertIn(str(self.prefix / ".pymolrc"), err)
        self.assertIn(f"{self.server.base_url}/testref/configs/.pymolrc", err)
        self.assertNotIn(str(self.prefix / ".pymolrc.py"), err)
        self.assertEqual((self.prefix / ".pymolrc").read_bytes(), b"my own rc\n")
        self.assertFalse(self.state_root.exists(), "wrote a record")

    def test_legacy_is_not_fetched_when_a_record_exists(self):
        self.install_v1_then_publish_v2()
        self.server.publish(legacy_ref(self.script_name), self.v1)
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        self.stopped(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(
            sorted(self.server.requests),
            sorted(f"/base/testref/configs/{name}" for name in NAMES),
        )

    def test_failed_legacy_download_is_an_error(self):
        self.server.publish("testref", self.v2)
        self.seed(self.prefix, {".pymolrc": b"unknown\n"})
        result = self.run_installer(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn(legacy_ref(self.script_name), result.stderr.decode())
        self.assertEqual(sorted(p.name for p in self.prefix.iterdir()), [".pymolrc"])
        self.assertEqual((self.prefix / ".pymolrc").read_bytes(), b"unknown\n")
        self.assertFalse(self.state_root.exists(), "wrote a record")
        self.assert_no_temp_left(self.prefix)

    def test_checkout_mode_never_fetches_the_legacy_ref(self):
        self.seed(self.prefix, {".pymolrc": b"unknown\n"})
        self.stopped(self.checkout(self.v2), self.prefix_flag, str(self.prefix))
        self.assertEqual(self.server.requests, [])

    # --- stopping -----------------------------------------------------------

    def test_local_edit_stops_with_exit_3_and_changes_nothing(self):
        self.install_v1_then_publish_v2()
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        record_dir = self.record_path(self.prefix, NAMES[0]).parent
        self.age(*(self.prefix / name for name in NAMES), self.prefix)
        self.age(*record_dir.iterdir(), record_dir)
        before, records = self.tree(self.prefix), self.tree(self.state_root)
        result = self.stopped(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(self.tree(self.prefix), before, "wrote into DIR")
        self.assertEqual(self.tree(self.state_root), records, "wrote a record")
        self.assert_no_temp_left(self.prefix)
        err = result.stderr.decode()
        self.assertIn(str(self.prefix / ".pymolrc"), err)
        self.assertNotIn(str(self.prefix / ".pymolrc.py"), err)
        if shutil.which("diff", path=self.env.get("PATH")) and self.script_name == "install.sh":
            self.assertIn("diff -u", err)
            self.assertIn(str(self.record_path(self.prefix, ".pymolrc")), err)
        self.assertIn(str(self.prefix / ".pymolrc.local.pml"), err)
        self.assertIn(self.force_flag, err)

    def test_each_edited_file_is_named_in_a_short_ascii_message(self):
        self.install_v1_then_publish_v2()
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        (self.prefix / ".pymolrc.py").write_bytes(b"print('mine')\n")
        result = self.stopped(self.lone(), self.prefix_flag, str(self.prefix))
        err = result.stderr.decode("ascii")  # raises on anything but ASCII
        self.assertLessEqual(len(err.splitlines()), 20, err)
        for name in NAMES:
            self.assertIn(f"{self.prefix / name}\n", err.replace("\r", "") + "\n")

    def test_override_command_carries_prefix_ref_and_base_url(self):
        self.install_v1_then_publish_v2()
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        result = self.stopped(self.lone(), self.prefix_flag, str(self.prefix))
        line = self.override_line(result)
        for word in (self.prefix_flag, str(self.prefix), "PYMOLSCRIPTS_REF", "testref",
                     "PYMOLSCRIPTS_BASE_URL", self.server.base_url):
            self.assertIn(word, line)
        self.run_override(line)
        self.assert_installed(self.prefix, self.v2)
        self.assert_recorded(self.prefix, self.v2)
        backups = self.backups(self.prefix)
        self.assertEqual([p.read_bytes() for p in backups], [self.edited])

    def test_override_command_runs_the_current_installer_for_an_old_ref(self):
        # An installer from before this one takes no --force (and before
        # 09544c8 there is none to fetch), so the printed line must fetch the
        # installer from main and only the configs from PYMOLSCRIPTS_REF.
        env = dict(self.env, PYMOLSCRIPTS_REF="oldref")
        self.server.publish("oldref", self.v1)
        self.install(self.lone(), self.prefix_flag, str(self.prefix), env=env)
        self.server.publish("oldref", self.v2)
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        line = self.override_line(self.stopped(self.lone(), self.prefix_flag, str(self.prefix), env=env))
        self.run_override(line)
        self.assert_installed(self.prefix, self.v2)
        self.assert_recorded(self.prefix, self.v2)
        self.assertEqual([p.read_bytes() for p in self.backups(self.prefix)], [self.edited])
        self.assertIn(f"{self.server.base_url}/main/{self.script_name}", line)
        self.assertEqual(self.server.config_requests("main"), [])

    def test_checkout_override_command_names_the_script(self):
        self.install(self.checkout(self.v1, "old"), self.prefix_flag, str(self.prefix))
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        script = self.checkout(self.v2)
        line = self.override_line(self.stopped(script, self.prefix_flag, str(self.prefix)))
        self.assertIn(str(script), line)
        self.assertIn(self.prefix_flag, line)
        self.assertNotIn("PYMOLSCRIPTS_", line)

    # --- --force --------------------------------------------------------------

    def test_force_backs_up_the_edited_file_and_updates(self):
        self.install_v1_then_publish_v2()
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        self.age(self.prefix / ".pymolrc")
        start = time.strftime("%Y%m%d%H%M%S", time.localtime(time.time() - 2))
        result = self.install(self.lone(), self.prefix_flag, str(self.prefix), self.force_flag)
        end = time.strftime("%Y%m%d%H%M%S", time.localtime(time.time() + 2))
        self.assert_installed(self.prefix, self.v2)
        self.assert_recorded(self.prefix, self.v2)
        backups = self.backups(self.prefix)
        self.assertEqual(len(backups), 1, backups)
        backup = backups[0]
        name, stamp = BACKUP.match(backup.name).groups()
        self.assertEqual(name, ".pymolrc")
        self.assertTrue(start <= stamp <= end, (start, stamp, end))
        self.assertIsNone(PYMOL_RUNS.search(backup.name), "PyMOL would run the backup")
        self.assertEqual(backup.read_bytes(), self.edited)
        self.assertIn(str(backup), result.stdout.decode())

    def test_force_backup_name_gets_a_suffix_when_taken(self):
        self.install_v1_then_publish_v2()
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        now = time.time()
        taken = [
            self.prefix / (".pymolrc.local-" + time.strftime("%Y%m%d%H%M%S", time.localtime(now + s)))
            for s in range(-1, 60)
        ]
        for path in taken:
            path.write_bytes(b"taken\n")
        self.install(self.lone(), self.prefix_flag, str(self.prefix), self.force_flag)
        self.assert_installed(self.prefix, self.v2)
        for path in taken:
            self.assertEqual(path.read_bytes(), b"taken\n", f"{path} was overwritten")
        new = [p for p in self.backups(self.prefix) if p not in taken]
        self.assertEqual(len(new), 1, new)
        self.assertRegex(new[0].name, r"\.local-\d{14}-\d+$")
        self.assertEqual(new[0].read_bytes(), self.edited)

    def test_force_with_nothing_edited_makes_no_backup(self):
        self.install_v1_then_publish_v2()
        self.install(self.lone(), self.prefix_flag, str(self.prefix), self.force_flag)
        self.assert_installed(self.prefix, self.v2)
        self.assert_no_temp_left(self.prefix)

    def test_link_to_unknown_content_stops(self):
        self.install_v1_then_publish_v2()
        target = self.root / "mine.pml"
        target.write_bytes(b"my own rc\n")
        os.remove(self.prefix / ".pymolrc")
        self.symlink(str(target), str(self.prefix / ".pymolrc"))
        before = self.tree(self.prefix)
        result = self.stopped(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertIn(str(self.prefix / ".pymolrc"), result.stderr.decode())
        self.assertEqual(self.tree(self.prefix), before)
        self.assertEqual(target.read_bytes(), b"my own rc\n")

    def test_force_replaces_that_link_and_leaves_its_target(self):
        self.install_v1_then_publish_v2()
        target = self.root / "mine.pml"
        target.write_bytes(b"my own rc\n")
        os.remove(self.prefix / ".pymolrc")
        self.symlink(str(target), str(self.prefix / ".pymolrc"))
        result = self.install(self.lone(), self.prefix_flag, str(self.prefix), self.force_flag)
        self.assert_installed(self.prefix, self.v2)
        self.assertEqual(target.read_bytes(), b"my own rc\n")
        self.assertEqual(self.backups(self.prefix), [])
        self.assertIn("target", result.stdout.decode())

    # --- write order and failures -----------------------------------------

    def test_unwritable_record_dir_is_an_error_before_any_rename(self):
        self.state_root.parent.mkdir(parents=True, exist_ok=True)
        self.state_root.write_bytes(b"not a directory\n")
        self.server.publish("testref", self.v1)
        result = self.run_installer(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn(str(self.state_root), result.stderr.decode())
        self.assertFalse(self.prefix.exists(), "wrote into DIR")
        self.assert_no_temp_left()

    def test_record_that_is_a_directory_is_an_error(self):
        self.server.publish("testref", self.v1)
        self.record_path(self.prefix, ".pymolrc.py").mkdir(parents=True)
        result = self.run_installer(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertFalse(self.prefix.exists(), "wrote into DIR")

    @unittest.skipUnless(CAN_CHMOD, "needs POSIX permissions and a non-root user")
    def test_records_are_written_after_the_renames(self):
        # A failure between the renames and the records must not leave a
        # record newer than the file, which the next run would call an edit.
        self.install_v1_then_publish_v2()
        record_dir = self.record_path(self.prefix, NAMES[0]).parent
        os.chmod(record_dir, 0o500)
        self.addCleanup(os.chmod, record_dir, 0o700)
        result = self.run_installer(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assert_installed(self.prefix, self.v2)
        os.chmod(record_dir, 0o700)
        self.assert_recorded(self.prefix, self.v1)
        result = self.install(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertIn("already current", result.stdout.decode())
        self.assert_recorded(self.prefix, self.v2)


@unittest.skipIf(ON_WINDOWS, "install.sh is the POSIX installer")
class ShLocalEditTests(ShRunner, LocalEditContract, unittest.TestCase):
    # What every install.sh before --force existed does with it.
    old_installer = (
        b"#!/bin/sh\n"
        b"for arg; do\n"
        b"    case $arg in --force) echo \"install.sh: unknown option: $arg\" >&2; exit 2 ;; esac\n"
        b"done\n"
    )

    def run_override(self, line):
        if shutil.which("curl", path=self.env.get("PATH")) is None:
            self.skipTest("curl not found")
        self.publish_installers()
        result = subprocess.run(
            ["/bin/sh", "-c", line], env=self.override_env(), stdin=subprocess.DEVNULL,
            capture_output=True, timeout=180,
        )
        self.assertEqual(result.returncode, 0, self.describe(result))

    def wrapped_path(self, body, *tools):
        """A PATH on which each of `tools` first runs the sh code `body`, with
        $last its last argument, $real_ln the real ln and $done a file that
        does not exist yet, then the real tool, unless `body` exits."""
        bindir = self.root / "wrapped"
        bindir.mkdir()
        real_ln = shutil.which("ln", path=self.env["PATH"])
        for tool in tools:
            real = shutil.which(tool, path=self.env["PATH"])
            if real is None or real_ln is None:
                self.skipTest(f"{tool} or ln not found")
            script = bindir / tool
            script.write_text(
                "#!/bin/sh\n"
                f"real_ln='{real_ln}' done='{self.root}/wrapped.done'\n"
                'for last; do :; done\n'
                f"{body}\n"
                f"exec '{real}' \"$@\"\n"
            )
            script.chmod(0o755)
        return f"{bindir}{os.pathsep}{self.env['PATH']}"

    def force_while_a_link_is_planted(self, victim):
        """Run --force while another user who can write to DIR (--prefix in a
        group directory) plants a link to `victim` at the backup name between
        the check that it is free and the copy: cp and ln play that user and
        win, the first one asked to write a name directly in DIR finding the
        link there. Check the backup and return the run."""
        self.install_v1_then_publish_v2()
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        plant = (
            f"case ${{last#'{self.prefix}'/}} in\n"
            '    "$last" | */*) ;;\n'
            f"    *) [ -e \"$done\" ] || {{ : >\"$done\"; \"$real_ln\" -s '{victim}' \"$last\"; }} ;;\n"
            "esac"
        )
        env = dict(self.env, PATH=self.wrapped_path(plant, "cp", "ln"))
        result = self.install(self.lone(), "--prefix", str(self.prefix), "--force", env=env)
        self.assertTrue((self.root / "wrapped.done").exists(), "nothing was planted")
        self.assert_installed(self.prefix, self.v2)
        planted = [p for p in self.backups(self.prefix) if p.is_symlink()]
        self.assertEqual([os.readlink(p) for p in planted], [str(victim)])
        backups = [p for p in self.backups(self.prefix) if not p.is_symlink()]
        self.assertEqual([p.read_bytes() for p in backups], [self.edited])
        self.assertTrue(backups[0].is_file())
        self.assertIn(f"kept the edited file as {backups[0]})", result.stdout.decode())
        return result

    def test_force_backup_never_writes_through_a_link_planted_meanwhile(self):
        victim = self.root / "victim"
        victim.write_bytes(b"the runner's own file\n")
        self.force_while_a_link_is_planted(victim)
        self.assertEqual(victim.read_bytes(), b"the runner's own file\n", "wrote through the planted link")

    def test_force_backup_skips_a_directory_link_planted_meanwhile(self):
        # ln given a directory, or a link to one, links into it instead.
        victim = self.root / "victim"
        victim.mkdir()
        (victim / ".pymolrc.local").write_bytes(b"the runner's own file\n")
        self.force_while_a_link_is_planted(victim)
        self.assertEqual(sorted(p.name for p in victim.iterdir()), [".pymolrc.local"])
        self.assertEqual((victim / ".pymolrc.local").read_bytes(), b"the runner's own file\n")

    def test_force_backup_takes_no_name_that_ln_linked_into(self):
        # When that directory has no entry of the copy's name, ln succeeds
        # without making the backup name a file of ours: the next name is used.
        victim = self.root / "victim"
        victim.mkdir()
        self.force_while_a_link_is_planted(victim)

    def test_force_without_hard_links_changes_nothing(self):
        # The backup is linked into DIR, which needs a file system with hard
        # links; without them --force stops before anything is replaced.
        # .pymolrc.py is edited and comes second, so .pymolrc, which is only
        # updated, would be replaced first if backups were made in turn.
        self.install_v1_then_publish_v2()
        (self.prefix / ".pymolrc.py").write_bytes(b"print('mine')\r\n")
        record_dir = self.record_path(self.prefix, NAMES[0]).parent
        self.age(*(self.prefix / name for name in NAMES))
        self.age(*record_dir.iterdir(), record_dir)
        records = self.tree(self.state_root)

        def entries():  # DIR's own mtime shows the work directory that came and went
            return {k: v for k, v in self.tree(self.prefix).items() if k != "."}

        before = entries()
        refuse = 'echo "ln: failed to create hard link \'$last\': Operation not permitted" >&2; exit 1'
        env = dict(self.env, PATH=self.wrapped_path(refuse, "ln"))
        result = self.run_installer(self.lone(), "--prefix", str(self.prefix), "--force", env=env)
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn("hard link", result.stderr.decode())
        self.assertEqual(entries(), before, "changed DIR")
        self.assertEqual(self.tree(self.state_root), records, "wrote a record")
        self.assert_no_temp_left(self.prefix)

    def test_record_dir_defaults_to_home_local_state(self):
        env = {k: v for k, v in self.env.items() if k != "XDG_STATE_HOME"}
        self.server.publish("testref", self.v1)
        self.install(self.lone(), env=env)
        record = self.home / ".local" / "state" / "pymolscripts" / "installed"
        record = record.joinpath(*str(self.home).strip("/").split("/"), ".pymolrc")
        self.assertEqual(record.read_bytes(), self.v1[".pymolrc"])
        self.assertFalse(self.state_root.exists())

    def test_relative_xdg_state_home_is_ignored(self):
        env = dict(self.env, XDG_STATE_HOME="relative/state")
        self.server.publish("testref", self.v1)
        self.install(self.lone(), self.prefix_flag, str(self.prefix), env=env, cwd=self.root)
        self.assertFalse((self.root / "relative").exists())
        self.assertTrue((self.home / ".local" / "state" / "pymolscripts").is_dir())

    def test_unset_home_needs_xdg_state_home_for_the_record(self):
        env = {k: v for k, v in self.env.items() if k not in ("HOME", "XDG_STATE_HOME")}
        result = self.run_installer(self.checkout(self.v1), self.prefix_flag, str(self.prefix), env=env)
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn("XDG_STATE_HOME", result.stderr.decode())
        self.assertFalse(self.prefix.exists())
        env["XDG_STATE_HOME"] = str(self.xdg_state)
        self.install(self.checkout(self.v1, "again"), self.prefix_flag, str(self.prefix), env=env)
        self.assert_recorded(self.prefix, self.v1)

    @unittest.skipUnless(PWSH, "pwsh not found")
    def test_sh_and_pwsh_share_the_record(self):
        # Installed with sh, then run with pwsh: pwsh finds sh's record.
        self.install_v1_then_publish_v2()
        (self.prefix / ".pymolrc").write_bytes(self.edited)
        pwsh = PowerShellRunner()
        pwsh.exe = PWSH
        script = self.root / "pwsh" / "install.ps1"
        script.parent.mkdir()
        shutil.copy2(REPO / "install.ps1", script)

        def run_pwsh():
            return subprocess.run(
                pwsh.command(script, ["-Prefix", str(self.prefix)]),
                env=self.env, stdin=subprocess.DEVNULL, capture_output=True, timeout=180,
            )

        result = run_pwsh()
        self.assertEqual(result.returncode, 3, self.describe(result))
        (self.prefix / ".pymolrc").write_bytes(self.v1[".pymolrc"])
        result = run_pwsh()
        self.assertEqual(result.returncode, 0, self.describe(result))
        self.assert_installed(self.prefix, self.v2)
        self.assert_recorded(self.prefix, self.v2)


class PowerShellLocalEditContract(PowerShellRunner, LocalEditContract):
    # What every install.ps1 before -Force existed does with it.
    old_installer = (
        b"foreach ($a in $args) {\n"
        b"    if ($a -eq '-Force') { [Console]::Error.WriteLine(\"install.ps1: unknown option: $a\"); exit 2 }\n"
        b"}\n"
    )

    def run_override(self, line):
        self.publish_installers()
        result = subprocess.run(
            [self.exe, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", line],
            env=self.override_env(), stdin=subprocess.DEVNULL, capture_output=True, timeout=180,
        )
        self.assertEqual(result.returncode, 0, self.describe(result))

    @unittest.skipUnless(ON_WINDOWS, "LOCALAPPDATA holds the record on Windows only")
    def test_record_dir_is_localappdata_on_windows(self):
        self.server.publish("testref", self.v1)
        self.install(self.lone())
        drive, rest = os.path.splitdrive(str(self.home))
        record = self.localappdata.joinpath(
            "pymolscripts", "installed", drive.rstrip(":"), *re.split(r"[\\/]+", rest.strip("\\/")),
            ".pymolrc",
        )
        self.assertEqual(record.read_bytes(), self.v1[".pymolrc"])
        self.assertFalse((self.xdg_state / "pymolscripts").exists())


@unittest.skipUnless(PWSH, "pwsh not found")
class PwshLocalEditTests(PowerShellLocalEditContract, unittest.TestCase):
    exe = PWSH or ""


@unittest.skipUnless(WINDOWS_POWERSHELL, "Windows PowerShell 5.1 not found")
class WindowsPowerShellLocalEditTests(PowerShellLocalEditContract, unittest.TestCase):
    exe = WINDOWS_POWERSHELL or ""


if __name__ == "__main__":
    unittest.main()
