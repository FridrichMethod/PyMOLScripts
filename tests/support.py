"""Fixtures shared by the installer tests (test_install.py, test_local_edits.py).

Every run uses a temporary HOME (and USERPROFILE), TMPDIR (and TMP/TEMP),
XDG_STATE_HOME and LOCALAPPDATA and, for downloads, an HTTP server on
127.0.0.1 that the test starts itself, so nothing touches the real home or
state directories or reaches the network.

This module defines mixins only, never a unittest.TestCase, so importing it
adds no tests to a module.
"""

import http.server
import os
import re
import shutil
import subprocess
import tempfile
import threading
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
    "XDG_STATE_HOME",
    "LOCALAPPDATA",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)
PWSH = shutil.which("pwsh")
WINDOWS_POWERSHELL = shutil.which("powershell") if ON_WINDOWS else None
CAN_CHMOD = not ON_WINDOWS and hasattr(os, "geteuid") and os.geteuid() != 0
# PyMOL runs the startup files matching these (pymol/invocation.py, py_re and
# script_re); a backup must match neither.
PYMOL_RUNS = re.compile(r"\.py$|\.pym$|\.PY$|\.PYM$|pymolrc$|\.pml$|\.PML$|\.p1m$|\.P1M$")


def configs(tag):
    """Distinct contents for both files, with CRLF like the real configs."""
    return {
        ".pymolrc": f"set bg_rgb, {tag}\r\n".encode(),
        ".pymolrc.py": f"print('{tag}')\r\n".encode(),
    }


def legacy_ref(script_name):
    """The pre-guard commit an installer accepts unrecorded files from."""
    text = (REPO / script_name).read_text(encoding="utf-8")
    found = re.findall(r"(?:legacy_ref=|\$LegacyRef = ')([0-9a-f]{40})\b", text)
    if len(found) != 1:
        raise AssertionError(f"{script_name}: expected one legacy ref, found {found}")
    return found[0]


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

    def publish_script(self, ref, script_name):
        self.files[f"/base/{ref}/{script_name}"] = (REPO / script_name).read_bytes()

    def config_requests(self, ref):
        prefix = f"/base/{ref}/configs/"
        return sorted(p for p in self.requests if p.startswith(prefix))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class InstallerBase:
    """Fixtures and assertions; a runner mixin says how to run an installer."""

    script_name = ""
    prefix_flag = ""
    help_flag = ""
    force_flag = ""
    usage_cases = ()
    # The name dotfiles' old payload hook gave a downloaded copy in $TMPDIR.
    temp_copy_name = ""

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
        # Neither state directory exists until something writes there.
        # PowerShell keeps caches of its own under %LOCALAPPDATA%, so the
        # installers' records are checked under their pymolscripts directory.
        self.xdg_state = self.root / "state"
        self.localappdata = self.root / "localappdata"
        self.state_root = (self.localappdata if ON_WINDOWS else self.xdg_state) / "pymolscripts"
        self.server = ConfigServer()
        self.addCleanup(self.server.close)
        env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
        env.update(
            HOME=str(self.home),
            USERPROFILE=str(self.home),
            TMPDIR=str(self.tmpdir),
            TMP=str(self.tmpdir),
            TEMP=str(self.tmpdir),
            XDG_STATE_HOME=str(self.xdg_state),
            LOCALAPPDATA=str(self.localappdata),
            NO_PROXY="127.0.0.1,localhost",
            no_proxy="127.0.0.1,localhost",
            PYMOLSCRIPTS_BASE_URL=self.server.base_url,
            PYMOLSCRIPTS_REF="testref",
            POWERSHELL_TELEMETRY_OPTOUT="1",
            POWERSHELL_UPDATECHECK="Off",
        )
        self.env = env

    def checkout(self, files, name="checkout", git=True):
        """A fake clone: the installer next to configs/ holding `files`."""
        top = self.root / name
        (top / "configs").mkdir(parents=True)
        if git:
            (top / ".git").mkdir()
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

    def seed(self, directory, files):
        """Put `files` into `directory` without running an installer."""
        directory.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            (directory / name).write_bytes(body)

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

    def stopped(self, script, *args, **kwargs):
        """Run expecting a stop for a local edit (exit 3); return the result."""
        result = self.run_installer(script, *args, **kwargs)
        self.assertEqual(result.returncode, 3, self.describe(result))
        self.assertEqual(result.stdout, b"", "a stopped run reported a change")
        return result

    def symlink(self, target, link):
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"cannot create symlinks here: {exc}")

    # --- records ------------------------------------------------------------

    def record_path(self, directory, name):
        """Where an installer keeps its copy of what it wrote to directory/name."""
        directory = str(directory)
        if ON_WINDOWS:
            drive, rest = os.path.splitdrive(directory)
            parts = [drive.rstrip(":")] + [p for p in re.split(r"[\\/]+", rest) if p]
            return Path(self.localappdata, "pymolscripts", "installed", *parts, name)
        parts = [p for p in directory.split("/") if p]
        return Path(self.xdg_state, "pymolscripts", "installed", *parts, name)

    def write_record(self, directory, files):
        for name, body in files.items():
            path = self.record_path(directory, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)

    def assert_recorded(self, directory, files):
        for name, body in files.items():
            path = self.record_path(directory, name)
            self.assertTrue(path.is_file(), f"no record at {path}")
            self.assertEqual(path.read_bytes(), body, f"{path} records the wrong content")
        record_dir = self.record_path(directory, NAMES[0]).parent
        names = sorted(p.name for p in record_dir.iterdir())
        self.assertTrue(set(names) <= set(NAMES), f"unexpected entries in {record_dir}: {names}")

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

    @staticmethod
    def tree(top):
        """Every entry under `top` (itself included) with its mtime, inode and bytes."""
        top = Path(top)
        if not os.path.lexists(top):
            return None
        state = {}
        for path in [top, *sorted(top.rglob("*"))]:
            st = os.lstat(path)
            body = path.read_bytes() if path.is_file() and not path.is_symlink() else None
            link = os.readlink(path) if path.is_symlink() else None
            state[str(path.relative_to(top))] = (st.st_mtime_ns, st.st_ino, body, link)
        return state

    @staticmethod
    def age(*paths):
        """Give `paths` an old mtime, so that any later write shows."""
        for path in paths:
            os.utime(path, (OLD_MTIME, OLD_MTIME), follow_symlinks=False)


class ShRunner:
    script_name = "install.sh"
    prefix_flag = "--prefix"
    help_flag = "--help"
    force_flag = "--force"
    temp_copy_name = "dotfiles-payload.AbC123"
    usage_cases = (
        ["--prefix"],
        ["--prefix", ""],
        ["--prefix="],
        ["--prefix", "relative"],
        ["--bogus"],
        ["--force=yes"],
        ["extra"],
        ["--", "extra"],
    )

    def command(self, script, args):
        return ["/bin/sh", str(script), *args]


class PowerShellRunner:
    script_name = "install.ps1"
    prefix_flag = "-Prefix"
    help_flag = "-Help"
    force_flag = "-Force"
    # pwsh -File needs the .ps1 extension; this is the README one-liner's name.
    temp_copy_name = "PyMOLScripts-install-1234.ps1"
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
