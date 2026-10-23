"""Recording executable used only by the VM's substitute application package."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from dbus_next.aio import MessageBus
from dbus_next.auth import AuthExternal

fontPatterns = ["sans-serif", "sans-serif:lang=zh-cn:charset=4e91", "Symbols Nerd Font:charset=f013"]


class PeerCredentials(AuthExternal):
    # The user-owned proxy supplies the desktop UID to the real bus. Request
    # that peer identity instead of dbus-next's default explicit root UID.
    def _authentication_start(self, negotiate_unix_fd=False):
        self.negotiate_unix_fd = negotiate_unix_fd
        return "AUTH EXTERNAL"

    def _receive_line(self, line):
        if line.split(" ", 1)[0] == "DATA":
            return "DATA"
        return super()._receive_line(line)


async def Main():
    bus = await MessageBus(auth=PeerCredentials()).connect()
    try:
        name = "org.freedesktop.DBus"
        obj = bus.get_proxy_object(name, "/org/freedesktop/DBus",
                                   await bus.introspect(name, "/org/freedesktop/DBus"))
        busId = await obj.get_interface(name).call_get_id()
    finally:
        bus.disconnect()
    fonts = {pattern: subprocess.check_output(
        ["fc-match", "-f", "%{family}|%{file}\n", pattern], text=True).strip()
        for pattern in fontPatterns}
    configHome = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    configHome.mkdir(parents=True, exist_ok=True)
    record = {"uid": os.getuid(), "argv": sys.argv[1:], "cwd": os.getcwd(),
              "environment": dict(os.environ), "fonts": fonts, "busId": busId}
    (configHome / "runtime-record.json").write_text(json.dumps(record))


asyncio.run(Main())
