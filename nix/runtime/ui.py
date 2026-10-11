"""Desktop-side inventory and D-Bus interaction, outside the application."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from dbus_next.aio import MessageBus
from dbus_next import Variant


def Run(args):
    return subprocess.check_output(args, text=True, timeout=10).strip()


def AgentWindow(pid):
    try:
        process = Path("/proc", str(pid))
        return process.stat().st_uid == os.getuid() and b"lxqt-policykit-agent" in (
            process / "cmdline").read_bytes().split(b"\0")[0]
    except OSError:
        return False


def Windows():
    if os.environ.get("NIRI_SOCKET"):
        return [w for w in json.loads(Run(["niri", "msg", "-j", "windows"]))
                if w.get("app_id") == "EdNovasCloud"]
    windows = []
    for line in Run(["wmctrl", "-lpGx"]).splitlines():
        fields = line.split(None, 9)
        if len(fields) == 10 and "ednovas" in fields[7].lower():
            windowId = fields[0]
            visible = "IsViewable" in Run(["xwininfo", "-id", windowId])
            if visible:
                windows.append({"id": windowId, "pid": int(fields[2]),
                                "is_focused": Run(["xdotool", "getactivewindow"]) == str(int(windowId, 16))})
    return windows


async def Tray(command):
    bus = await MessageBus().connect()
    watcherName = "org.kde.StatusNotifierWatcher"
    watcherPath = "/StatusNotifierWatcher"
    obj = bus.get_proxy_object(watcherName, watcherPath, await bus.introspect(watcherName, watcherPath))
    items = (await obj.get_interface("org.freedesktop.DBus.Properties").call_get(
        watcherName, "RegisteredStatusNotifierItems")).value
    for item in items:
        service, path = item.split("/", 1)
        path = "/" + path
        try:
            obj = bus.get_proxy_object(service, path, await bus.introspect(service, path))
            props = obj.get_interface("org.freedesktop.DBus.Properties")
            if (await props.call_get("org.kde.StatusNotifierItem", "Id")).value != "ednovas-cloud-tun":
                continue
            if command == "activate":
                await obj.get_interface("org.kde.StatusNotifierItem").call_activate(0, 0)
            elif command == "quit":
                menuPath = (await props.call_get("org.kde.StatusNotifierItem", "Menu")).value
                menu = bus.get_proxy_object(service, menuPath, await bus.introspect(service, menuPath))
                interface = menu.get_interface("com.canonical.dbusmenu")
                layout = await interface.call_get_layout(0, -1, [])
                quitItem = next(child.value for child in layout[1][2]
                                if child.value[1]["label"].value == "Quit EdNovas Cloud")
                await interface.call_event(quitItem[0], "clicked", Variant("s", ""), 0)
            print(json.dumps({"service": service, "path": path, "items": items}))
            bus.disconnect()
            return
        except Exception:
            # The test host can retain an entry whose D-Bus owner exited.
            # Only a live item with the expected Id may receive an action.
            continue
    bus.disconnect()
    sys.exit("No live registered privileged tray item")


async def DesktopRun(args):
    bus = await MessageBus().connect()
    name, path = "org.ednovas.RuntimeDesktop", "/RuntimeDesktop"
    obj = bus.get_proxy_object(name, path, await bus.introspect(name, path))
    status = await obj.get_interface(name).call_run_command(args)
    bus.disconnect()
    sys.exit(status)


os.environ.update(json.loads(Path("/home/alice/session.json").read_text()))
if os.getuid() == 1001:
    os.environ["XDG_RUNTIME_DIR"] = "/run/user/1001"
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/1001/bus"
command = sys.argv[1]
if command == "run":
    if os.getuid() == 1001:
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    asyncio.run(DesktopRun(sys.argv[2:]))
elif command == "inventory":
    print(Run(["niri", "msg", "-j", "windows"] if os.environ.get("NIRI_SOCKET")
              else ["wmctrl", "-lpGx"]))
elif command == "windows":
    print(json.dumps(Windows()))
elif command == "auth-ready":
    if os.environ.get("NIRI_SOCKET"):
        windows = json.loads(Run(["niri", "msg", "-j", "windows"]))
        Path("/home/alice/auth-wayland.json").write_text(json.dumps(windows))
        if any(AgentWindow(window.get("pid")) and window.get("is_focused") for window in windows):
            sys.exit(0)
    active = Run(["xdotool", "getactivewindow"])
    inventory = Run(["wmctrl", "-lpGx"])
    Path("/home/alice/auth-x11.txt").write_text(active + "\n" + inventory)
    for line in inventory.splitlines():
        fields = line.split(None, 9)
        if len(fields) == 10 and AgentWindow(fields[2]):
            if active == str(int(fields[0], 16)) and "IsViewable" in Run(["xwininfo", "-id", fields[0]]):
                sys.exit(0)
    sys.exit(1)
elif command == "clipboard":
    print(Run(["wl-paste", "--no-newline"] if os.environ.get("NIRI_SOCKET")
              else ["xclip", "-selection", "clipboard", "-o"]))
elif command == "clear-clipboard":
    args = ["wl-copy"] if os.environ.get("NIRI_SOCKET") else ["xclip", "-selection", "clipboard"]
    subprocess.run(args, input="runtime-clipboard-sentinel", text=True, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
elif command == "close":
    windows = Windows()
    assert len(windows) == 1, windows
    if os.environ.get("NIRI_SOCKET"):
        Run(["niri", "msg", "action", "focus-window", "--id", str(windows[0]["id"])])
        Run(["niri", "msg", "action", "close-window"])
    else:
        Run(["wmctrl", "-ic", str(windows[0]["id"])])
else:
    asyncio.run(Tray(command))
