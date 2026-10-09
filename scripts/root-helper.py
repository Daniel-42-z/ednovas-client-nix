"""Authenticated EdNovas launcher; the executable path is fixed at Nix build time."""

import json
import os
from pathlib import Path
import re
import stat
import sys

application = "@application@"
runtimePath = "@runtimePath@"
stateBase = Path("/var/lib/ednovas-cloud")
runtimeBase = Path("/run/ednovas-cloud")
sessionKeys = {"DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "LANG", "EDNOVAS_DBUS_PROXY_SOCKET"}


def ValidateFlags(flags):
    for flag in flags:
        if flag not in {"--disable-gpu", "--enable-logging=stderr", "--ozone-platform=x11", "--ozone-platform=wayland"} and not re.fullmatch(r"--v=[0-3]", flag):
            raise ValueError(f"Unsupported launch flag: {flag}")
    return flags


def ValidateSession(session, callerUid):
    if not isinstance(session, dict) or set(session) - sessionKeys:
        raise ValueError("Unexpected desktop environment fields")
    if any(not isinstance(value, str) or len(value) > 4096 or "\0" in value for value in session.values()):
        raise ValueError("Invalid desktop environment value")
    display = session.get("DISPLAY", "")
    wayland = session.get("WAYLAND_DISPLAY", "")
    if not re.fullmatch(r"(?::\d+(?:\.\d+)?)?", display):
        raise ValueError("Only local X11 displays are supported")
    if not re.fullmatch(r"[A-Za-z0-9_.-]*", wayland) or wayland in {".", ".."}:
        raise ValueError("Invalid Wayland socket name")
    if not display and not wayland:
        raise ValueError("Launch from an active desktop session")
    runtimeDirectory = Path(f"/run/user/{callerUid}")
    runtimeStat = runtimeDirectory.lstat()
    if not stat.S_ISDIR(runtimeStat.st_mode) or runtimeStat.st_uid != callerUid:
        raise ValueError("Desktop runtime directory has an unexpected owner or type")
    proxySocket = Path(session.get("EDNOVAS_DBUS_PROXY_SOCKET", ""))
    if (not proxySocket.is_absolute() or proxySocket.name != "bus"
            or not re.fullmatch(r"ednovas-bus\.[A-Za-z0-9]{8}", proxySocket.parent.name)
            or proxySocket.parent.parent != runtimeDirectory):
        raise ValueError("Invalid desktop bus proxy location")
    proxyDirectoryStat = proxySocket.parent.lstat()
    proxyStat = proxySocket.lstat()
    if (not stat.S_ISDIR(proxyDirectoryStat.st_mode) or proxyDirectoryStat.st_uid != callerUid
            or proxyDirectoryStat.st_mode & 0o077
            or not stat.S_ISSOCK(proxyStat.st_mode) or proxyStat.st_uid != callerUid):
        raise ValueError("Desktop bus proxy has an unexpected owner or type")
    authority = session.get("XAUTHORITY", "")
    if authority:
        authorityPath = Path(authority)
        if not authorityPath.is_absolute():
            raise ValueError("Xauthority must be an absolute path")
        authorityStat = authorityPath.stat()
        if not stat.S_ISREG(authorityStat.st_mode) or authorityStat.st_uid != callerUid:
            raise ValueError("Xauthority must be a regular file owned by the caller")
    for key in ["XDG_CURRENT_DESKTOP", "LANG"]:
        if not re.fullmatch(r"[A-Za-z0-9_.:@-]*", session.get(key, "")):
            raise ValueError(f"Invalid {key}")
    if session.get("XDG_SESSION_TYPE", "") not in {"", "wayland", "x11"}:
        raise ValueError("Invalid session type")
    return {key: value for key, value in session.items() if value}


def EnsurePrivateDirectory(directory):
    directory.mkdir(mode=0o700, exist_ok=True)
    directoryStat = directory.lstat()
    if not stat.S_ISDIR(directoryStat.st_mode) or directoryStat.st_uid != 0:
        raise ValueError(f"Privileged state directory has an unexpected owner or type: {directory}")
    directory.chmod(0o700)


def BuildEnvironment(session, callerUid, profile, runtimeDirectory):
    proxySocket = session["EDNOVAS_DBUS_PROXY_SOCKET"]
    desktop = {key: value for key, value in session.items() if key != "EDNOVAS_DBUS_PROXY_SOCKET"}
    if desktop.get("WAYLAND_DISPLAY"):
        # A root-owned XDG_RUNTIME_DIR must not hide the caller's Wayland socket.
        desktop["WAYLAND_DISPLAY"] = f"/run/user/{callerUid}/{desktop['WAYLAND_DISPLAY']}"
    return {
        **desktop,
        "PATH": runtimePath,
        "HOME": str(profile / "home"), "USER": "root", "LOGNAME": "root",
        "XDG_CONFIG_HOME": str(profile / "config"),
        "XDG_CACHE_HOME": str(profile / "cache"),
        "XDG_DATA_HOME": str(profile / "data"),
        "XDG_RUNTIME_DIR": str(runtimeDirectory),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={proxySocket}",
        "ELECTRON_ENABLE_LOGGING": "1",
        "EDNOVAS_CONTROL_SOCKET": str(Path(proxySocket).with_name("control")),
    }


def Main():
    if os.geteuid() != 0:
        raise ValueError("This helper must be launched through pkexec")
    callerText = os.environ.get("PKEXEC_UID", "")
    if not re.fullmatch(r"[0-9]+", callerText) or int(callerText) == 0:
        raise ValueError("No authenticated desktop caller supplied by pkexec")
    callerUid = int(callerText)
    if len(sys.argv) < 2 or len(sys.argv[1]) > 32768:
        raise ValueError("Missing or oversized desktop environment")
    session = ValidateSession(json.loads(sys.argv[1]), callerUid)
    flags = ValidateFlags(sys.argv[2:])
    EnsurePrivateDirectory(stateBase)
    profile = stateBase / str(callerUid)
    EnsurePrivateDirectory(profile)
    for name in ["home", "config", "cache", "data", "profile"]:
        EnsurePrivateDirectory(profile / name)
    EnsurePrivateDirectory(runtimeBase)
    runtimeDirectory = runtimeBase / str(callerUid)
    EnsurePrivateDirectory(runtimeDirectory)
    os.chdir(profile)
    os.execve(application, [application, f"--user-data-dir={profile / 'profile'}", *flags],
              BuildEnvironment(session, callerUid, profile, runtimeDirectory))


if __name__ == "__main__":
    try:
        Main()
    except (OSError, ValueError) as error:
        sys.exit(f"EdNovas privileged launch failed: {error}")
