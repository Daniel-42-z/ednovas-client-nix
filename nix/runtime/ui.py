"""Desktop transport and native Polkit prompt readiness for integration tests."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from dbus_next.aio import MessageBus


def Run(args):
    return subprocess.check_output(args, text=True, timeout=10).strip()


def AgentWindow(pid):
    try:
        process = Path("/proc", str(pid))
        return process.stat().st_uid == os.getuid() and b"lxqt-policykit-agent" in (
            process / "cmdline").read_bytes().split(b"\0")[0]
    except OSError:
        return False


async def DesktopRun(args):
    bus = await MessageBus().connect()
    try:
        name, path = "org.ednovas.RuntimeDesktop", "/RuntimeDesktop"
        obj = bus.get_proxy_object(name, path, await bus.introspect(name, path))
        return await obj.get_interface(name).call_run_command(args)
    finally:
        bus.disconnect()


async def BusId():
    bus = await MessageBus().connect()
    try:
        name = "org.freedesktop.DBus"
        obj = bus.get_proxy_object(name, "/org/freedesktop/DBus",
                                   await bus.introspect(name, "/org/freedesktop/DBus"))
        print(await obj.get_interface(name).call_get_id())
    finally:
        bus.disconnect()


os.environ.update(json.loads(Path("/home/alice/session.json").read_text()))
if os.getuid() == 1001:
    os.environ["XDG_RUNTIME_DIR"] = "/run/user/1001"
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/1001/bus"
command = sys.argv[1]
if command == "run":
    if os.getuid() == 1001:
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    sys.exit(asyncio.run(DesktopRun(sys.argv[2:])))
elif command == "bus-id":
    asyncio.run(BusId())
elif command == "inventory":
    print(Run(["wmctrl", "-lpGx"]))
elif command in {"auth-ready", "auth-failed"}:
    active = Run(["xdotool", "getactivewindow"])
    inventory = Run(["wmctrl", "-lpGx"])
    for line in inventory.splitlines():
        fields = line.split(None, 9)
        if (len(fields) == 10 and AgentWindow(fields[2])
                and (command == "auth-ready" or fields[9] == "Authorization Failed")):
            if active == str(int(fields[0], 16)) and "IsViewable" in Run(["xwininfo", "-id", fields[0]]):
                sys.exit(0)
    sys.exit(1)
else:
    sys.exit("Unknown integration command")
