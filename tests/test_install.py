"""Tests for install.sh and install.ps1.

Every run uses a temporary HOME (and USERPROFILE), a temporary TMPDIR (and
TMP/TEMP) and, for downloads, an HTTP server on 127.0.0.1 that the test starts
itself, so nothing touches the real home or reaches the network.

install.sh runs under /bin/sh everywhere but Windows. install.ps1 runs under
every PowerShell on PATH: pwsh, plus Windows PowerShell 5.1 on Windows.
Missing shells skip their tests.

Run from the repository root:  python3 -m unittest discover -s tests -v
"""

import http.server
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAMES = (".pymolrc", ".pymolrc.py")
ON_WINDOWS = os.name == "nt"
OLD_MTIME = 1_000_000_000  # 2001-09-09: no install run produces this mtime
TEMP_PREFIX = "pymolscripts-install."
DROPPED_ENV = (
    "PYMOLSCRIPTS_BASE_URL",
    "PYMOLSCRIPTS_REF",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


def configs(tag):
    """Distinct contents for both files, with CRLF like the real configs."""
    return {
        ".pymolrc": f"set bg_rgb, {tag}\r\n".encode(),
        ".pymolrc.py": f"print('{tag}')\r\n".encode(),
    }


class ConfigServer:
    """Serves {url path: bytes} on 127.0.0.1 and records every request path."""

    def __init__(self):
        self.files = {}
        self.requests = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(self.path)
                body = owner.files.get(self.path)
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}/base"

    def publish(self, ref, files):
        for name, body in files.items():
            self.files[f"/base/{ref}/configs/{name}"] = body

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class InstallerContract:
    """Behaviour both installers share; subclasses say how to run one."""

    script_name = ""
    prefix_flag = ""
    help_flag = ""
    usage_cases = ()

    def command(self, script, args):
        raise NotImplementedError

    # --- fixtures -----------------------------------------------------------

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="pymolscripts-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        self.home = self.root / "home"
        self.home.mkdir()
        self.tmpdir = self.root / "tmp"
        self.tmpdir.mkdir()
        self.prefix = self.root / "prefix"
        self.server = ConfigServer()
        self.addCleanup(self.server.close)
        env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
        env.update(
            HOME=str(self.home),
            USERPROFILE=str(self.home),
            TMPDIR=str(self.tmpdir),
            TMP=str(self.tmpdir),
            TEMP=str(self.tmpdir),
            NO_PROXY="127.0.0.1,localhost",
            no_proxy="127.0.0.1,localhost",
            PYMOLSCRIPTS_BASE_URL=self.server.base_url,
            PYMOLSCRIPTS_REF="testref",
            POWERSHELL_TELEMETRY_OPTOUT="1",
            POWERSHELL_UPDATECHECK="Off",
        )
        self.env = env

    def checkout(self, files, name="checkout"):
        """A fake clone: the installer next to configs/ holding `files`."""
        top = self.root / name
        (top / "configs").mkdir(parents=True)
        shutil.copy2(REPO / self.script_name, top / self.script_name)
        for fname, body in files.items():
            (top / "configs" / fname).write_bytes(body)
        return top / self.script_name

    def lone(self):
        """The installer alone, as after downloading it: download mode."""
        top = self.root / "lone"
        top.mkdir(exist_ok=True)
        shutil.copy2(REPO / self.script_name, top / self.script_name)
        return top / self.script_name

    def run_installer(self, script, *args, env=None, **kwargs):
        return subprocess.run(
            self.command(script, list(args)),
            env=self.env if env is None else env,
            stdin=kwargs.pop("stdin", subprocess.DEVNULL),
            capture_output=True,
            timeout=180,
            **kwargs,
        )

    def install(self, script, *args, **kwargs):
        result = self.run_installer(script, *args, **kwargs)
        self.assertEqual(result.returncode, 0, self.describe(result))
        return result

    def symlink(self, target, link):
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlinks here: {exc}")

    # --- assertions ---------------------------------------------------------

    @staticmethod
    def describe(result):
        return (
            f"exit {result.returncode}\nstdout:\n{result.stdout.decode(errors='replace')}"
            f"\nstderr:\n{result.stderr.decode(errors='replace')}"
        )

    def assert_installed(self, directory, files):
        for name, body in files.items():
            path = directory / name
            self.assertFalse(path.is_symlink(), f"{path} is still a symlink")
            self.assertTrue(path.is_file(), f"{path} is not a regular file")
            self.assertEqual(path.read_bytes(), body, f"{path} has the wrong content")

    def assert_no_temp_left(self, directory=None):
        leftovers = [p.name for p in self.tmpdir.iterdir() if p.name.startswith(TEMP_PREFIX)]
        self.assertEqual(leftovers, [], "download staging directory left behind")
        if directory is not None and directory.exists():
            names = sorted(p.name for p in directory.iterdir())
            self.assertTrue(set(names) <= set(NAMES), f"unexpected entries in {directory}: {names}")

    @staticmethod
    def snapshot(directory):
        state = {}
        for name in NAMES:
            st = os.stat(directory / name, follow_symlinks=False)
            state[name] = (st.st_mtime_ns, st.st_ino, (directory / name).read_bytes())
        return state

    # --- sources ------------------------------------------------------------

    def test_checkout_mode_installs_into_home_without_downloading(self):
        files = configs("checkout")
        result = self.install(self.checkout(files))
        self.assert_installed(self.home, files)
        self.assertEqual(self.server.requests, [])
        out = result.stdout.decode()
        for name in NAMES:
            self.assertIn(f"installed {self.home / name}", out)

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

    def test_replaces_regular_files(self):
        self.prefix.mkdir()
        for name, body in configs("old").items():
            (self.prefix / name).write_bytes(body)
        files = configs("new")
        result = self.install(self.checkout(files), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        self.assertIn(f"updated {self.prefix / '.pymolrc'}", result.stdout.decode())
        self.assert_no_temp_left(self.prefix)

    def test_replaces_symlinks_without_writing_through_them(self):
        self.prefix.mkdir()
        targets = self.root / "targets"
        targets.mkdir()
        old = configs("old")
        for name, body in old.items():
            (targets / name).write_bytes(body)
            self.symlink(str(targets / name), str(self.prefix / name))
        files = configs("new")
        self.install(self.checkout(files), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        for name, body in old.items():
            self.assertEqual((targets / name).read_bytes(), body, "wrote through a symlink")

    def test_replaces_symlinks_even_when_their_content_is_current(self):
        files = configs("same")
        self.prefix.mkdir()
        targets = self.root / "targets"
        targets.mkdir()
        for name, body in files.items():
            (targets / name).write_bytes(body)
            self.symlink(str(targets / name), str(self.prefix / name))
        self.install(self.checkout(files), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)

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
        for name in NAMES:
            os.utime(self.prefix / name, (OLD_MTIME, OLD_MTIME))
        os.utime(self.prefix, (OLD_MTIME, OLD_MTIME))
        before = self.snapshot(self.prefix)
        result = self.install(script, self.prefix_flag, str(self.prefix))
        self.assertEqual(self.snapshot(self.prefix), before)
        self.assertEqual(os.stat(self.prefix).st_mtime_ns, OLD_MTIME * 10**9, "wrote into DIR")
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
        files = configs("new")
        self.prefix.mkdir()
        (self.prefix / ".pymolrc").write_bytes(files[".pymolrc"])
        (self.prefix / ".pymolrc.py").write_bytes(b"old\n")
        os.utime(self.prefix / ".pymolrc", (OLD_MTIME, OLD_MTIME))
        result = self.install(self.checkout(files), self.prefix_flag, str(self.prefix))
        self.assert_installed(self.prefix, files)
        self.assertEqual(os.stat(self.prefix / ".pymolrc").st_mtime_ns, OLD_MTIME * 10**9)
        lines = result.stdout.decode().strip().splitlines()
        self.assertEqual(lines, [f"PyMOLScripts: updated {self.prefix / '.pymolrc.py'}"])

    # --- failures -----------------------------------------------------------

    def test_failed_download_changes_nothing(self):
        files = configs("new")
        self.server.publish("testref", {".pymolrc": files[".pymolrc"]})  # .py: 404
        self.prefix.mkdir()
        (self.prefix / ".pymolrc").write_bytes(b"old rc\n")
        os.utime(self.prefix / ".pymolrc", (OLD_MTIME, OLD_MTIME))
        result = self.run_installer(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertEqual((self.prefix / ".pymolrc").read_bytes(), b"old rc\n")
        self.assertEqual(os.stat(self.prefix / ".pymolrc").st_mtime_ns, OLD_MTIME * 10**9)
        self.assertFalse((self.prefix / ".pymolrc.py").exists())
        self.assertIn("/base/testref/configs/.pymolrc.py", result.stderr.decode())
        self.assertEqual(sorted(p.name for p in self.prefix.iterdir()), [".pymolrc"])
        self.assert_no_temp_left(self.prefix)

    def test_empty_download_is_an_error(self):
        self.server.publish("testref", {".pymolrc": b"rc\n", ".pymolrc.py": b""})
        result = self.run_installer(self.lone(), self.prefix_flag, str(self.prefix))
        self.assertEqual(result.returncode, 1, self.describe(result))
        self.assertIn("empty", result.stderr.decode())
        self.assertFalse(self.prefix.exists())
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

    def test_help_exits_0(self):
        result = self.run_installer(self.checkout(configs("new")), self.help_flag)
        self.assertEqual(result.returncode, 0, self.describe(result))
        self.assertIn(b"Usage:", result.stdout)
        self.assertFalse((self.home / ".pymolrc").exists())


@unittest.skipIf(ON_WINDOWS, "install.sh is the POSIX installer")
class ShInstallerTests(InstallerContract, unittest.TestCase):
    script_name = "install.sh"
    prefix_flag = "--prefix"
    help_flag = "--help"
    usage_cases = (
        ["--prefix"],
        ["--prefix", ""],
        ["--prefix="],
        ["--prefix", "relative"],
        ["--bogus"],
        ["extra"],
        ["--", "extra"],
    )

    def command(self, script, args):
        return ["/bin/sh", str(script), *args]

    def restricted_path(self, *tools):
        """A PATH holding only what install.sh needs, plus `tools`."""
        bindir = self.root / "bin"
        bindir.mkdir(exist_ok=True)
        for tool in ("cat", "mkdir", "mktemp", "mv", "od", "rm", *tools):
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
        # Minimal images (Fedora, Rocky Linux) ship no diffutils, so no cmp.
        files = configs("current")
        script = self.checkout(files)
        env = dict(self.env, PATH=self.restricted_path())
        self.install(script, "--prefix", str(self.prefix), env=env)
        self.assert_installed(self.prefix, files)
        lines = self.install(script, "--prefix", str(self.prefix), env=env).stdout.decode().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("already current", lines[0])
        (self.prefix / ".pymolrc").write_bytes(files[".pymolrc"] + b"# edited\n")
        lines = self.install(script, "--prefix", str(self.prefix), env=env).stdout.decode().splitlines()
        self.assertEqual(lines, [f"PyMOLScripts: updated {self.prefix / '.pymolrc'}"])
        self.assert_installed(self.prefix, files)

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


class PowerShellContract(InstallerContract):
    script_name = "install.ps1"
    prefix_flag = "-Prefix"
    help_flag = "-Help"
    usage_cases = (
        ["-Prefix"],
        ["-Prefix", "relative"],
        ["-Bogus"],
        ["extra"],
    )
    exe = ""

    def command(self, script, args):
        return [
            self.exe,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *args,
        ]

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


PWSH = shutil.which("pwsh")
WINDOWS_POWERSHELL = shutil.which("powershell") if ON_WINDOWS else None


@unittest.skipUnless(PWSH, "pwsh not found")
class PwshInstallerTests(PowerShellContract, unittest.TestCase):
    exe = PWSH or ""


@unittest.skipUnless(WINDOWS_POWERSHELL, "Windows PowerShell 5.1 not found")
class WindowsPowerShellInstallerTests(PowerShellContract, unittest.TestCase):
    exe = WINDOWS_POWERSHELL or ""


if __name__ == "__main__":
    unittest.main()
