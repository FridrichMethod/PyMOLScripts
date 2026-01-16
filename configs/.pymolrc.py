import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from pymol import cmd

CHECK_INTERVAL_SECONDS = 7 * 24 * 3600  # seven days


def _load_pymol_script_repo():
    """Load the PyMOL script repository from GitHub, updating at most once every 7 days."""
    home = os.path.expanduser("~")
    repo_dir = os.path.join(home, "Pymol-script-repo")

    # Use XDG_CACHE_HOME or ~/.cache as the marker location
    cache_dir = os.environ.get("XDG_CACHE_HOME", os.path.join(home, ".cache"))
    os.makedirs(cache_dir, exist_ok=True)
    marker_file = os.path.join(cache_dir, ".pymol_script_repo_last_update")

    def should_check() -> bool:
        if not os.path.isdir(repo_dir):
            # Never cloned: must check (i.e. clone)
            return True
        if not os.path.isfile(marker_file):
            # No previous timestamp: must check
            return True
        # If marker is older than our interval, we should check again
        return (time.time() - os.path.getmtime(marker_file)) >= CHECK_INTERVAL_SECONDS

    if should_check():
        if os.path.isdir(repo_dir):
            print("Checking for updates to PyMOL script repository…")
            fetch = subprocess.run(
                ["git", "-C", repo_dir, "fetch", "--quiet"],
                capture_output=True,
                text=True,
                check=False,
            )
            if fetch.returncode != 0:
                print(
                    "Warning: unable to fetch remote updates. "
                    "Falling back to local status."
                )
            status = subprocess.run(
                ["git", "-C", repo_dir, "status", "-uno", "-b"],
                capture_output=True,
                text=True,
                check=False,
            )
            if "Your branch is behind" in status.stdout:
                print("Updates available. Pulling latest scripts…")
                subprocess.call(["git", "-C", repo_dir, "pull"])
            else:
                print("PyMOL script repository is already up to date.")
        else:
            print("Cloning PyMOL script repository for the first time…")
            subprocess.call(
                [
                    "git",
                    "clone",
                    "https://github.com/Pymol-Scripts/Pymol-script-repo.git",
                    repo_dir,
                ]
            )

        # Touch the marker file to record this check time
        open(marker_file, "a").close()
        os.utime(marker_file, None)

    # Finally, add to sys.path and set environment variable
    modules_dir = os.path.join(repo_dir, "modules")
    sys.path.append(repo_dir)
    sys.path.append(modules_dir)
    os.environ["PYMOL_GIT_MOD"] = modules_dir
    print("PyMOL script repository loaded.")


def _load_colorbrewer():
    """Load ColorBrewer palettes into PyMOL."""
    url = "https://gist.githubusercontent.com/frankrowe/9007567/raw/colorbrewer.js"
    js_text = urllib.request.urlopen(url).read().decode("utf-8")

    # Extract the JavaScript object containing the ColorBrewer palettes
    obj_text = re.search(
        r"var\s+colorbrewer\s*=\s*(\{.*\});", js_text, flags=re.S
    ).group(1)
    json_text = re.sub(r"(\b[a-zA-Z0-9_]+)\s*:", r'"\1":', obj_text)
    json_text = json_text.replace("'", '"')

    palettes = json.loads(json_text)

    # Register new colors and collect their names
    new_colors = []
    for pal, classes in palettes.items():
        for n, hex_list in classes.items():
            for i, hx in enumerate(hex_list, start=1):
                name = f"{pal}_{n}_{i}"
                rgb = [int(hx[j : j + 2], 16) for j in (1, 3, 5)]
                cmd.set_color(name, rgb)
                new_colors.append(name)

    def _combined_color_sc():
        builtin_colors = [n for n, _ in cmd.get_color_indices()]
        return cmd.Shortcut(builtin_colors + new_colors)

    # Reassign auto_arg for the first argument
    cmd.auto_arg[0]["color"] = [_combined_color_sc, "color name", ", "]
    cmd.auto_arg[0]["set_color"] = [_combined_color_sc, "new color name", ""]

    print("Colorbrewer palettes loaded.")


_load_pymol_script_repo()
_load_colorbrewer()
