"""Compare caller font discovery in an isolated root profile inside the FHS env."""
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

fontConfig = runpy.run_path(sys.argv[1])
with tempfile.TemporaryDirectory(prefix="runtime-fonts-") as directory:
    profile = Path(directory)
    for name in ["home", "config", "data", "cache"]:
        (profile / name).mkdir()
    destination = profile / "fonts.conf"
    fontConfig["WriteFontConfiguration"](destination, Path("/home/alice/.config"),
                                        Path("/home/alice/.local/share"))
    includes = profile / "config/fontconfig/conf.d"
    includes.mkdir(parents=True)
    (includes / "50-ednovas-desktop.conf").symlink_to(destination)
    environment = dict(os.environ, HOME=str(profile / "home"),
                       XDG_CONFIG_HOME=str(profile / "config"),
                       XDG_DATA_HOME=str(profile / "data"),
                       XDG_CACHE_HOME=str(profile / "cache"))
    subprocess.run(["fc-match", "-f", "%{family}|%{file}\n", *sys.argv[2:]],
                   env=environment, check=True)
