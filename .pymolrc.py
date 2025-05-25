import json
import os
import re
import subprocess
import sys
import urllib.request

from pymol import cmd


def _load_pymol_script_repo():
    """Load the PyMOL script repository from GitHub."""
    pymol_script_repo = os.path.abspath(
        os.path.join(os.path.expanduser("~"), "Pymol-script-repo")
    )

    # Check if the PyMOL script repository exists, clone it if not
    if os.path.isdir(pymol_script_repo):
        print("Checking for updates to PyMOL script repository...")

        # Check if local is behind remote using git status with upstream comparison
        status_result = subprocess.run(
            ["git", "-C", pymol_script_repo, "status", "-uno", "-b"],
            capture_output=True,
            text=True,
            check=False,
        )

        if "Your branch is behind" in status_result.stdout:
            print("Updates available. Updating PyMOL script repository...")
            subprocess.call(["git", "-C", pymol_script_repo, "pull"])
        else:
            print("PyMOL script repository is up to date.")
    else:
        print("Cloning PyMOL script repository...")
        subprocess.call(
            [
                "git",
                "clone",
                "https://github.com/Pymol-Scripts/Pymol-script-repo.git",
                pymol_script_repo,
            ]
        )

    pymol_script_repo_plugins = os.path.join(pymol_script_repo, "plugins")
    pymol_script_repo_modules = os.path.join(pymol_script_repo, "modules")
    sys.path.append(pymol_script_repo)
    sys.path.append(pymol_script_repo_modules)
    os.environ["PYMOL_GIT_MOD"] = pymol_script_repo_modules

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
