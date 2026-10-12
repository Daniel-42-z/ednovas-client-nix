"""Desktop-side inventory and D-Bus interaction, outside the application."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from dbus_next.aio import MessageBus
from dbus_next import DBusError, Variant
from dbus_next.errors import InterfaceNotFoundError
from dbus_next.introspection import Node


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


# Native Electron tray/menu objects implement these standard methods even
# when they do not advertise interfaces through Introspect.
trayProtocol = Node.parse("""<node>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get">
      <arg type="s" direction="in"/><arg type="s" direction="in"/>
      <arg type="v" direction="out"/>
    </method>
  </interface>
  <interface name="org.kde.StatusNotifierItem">
    <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
  </interface>
</node>""")
menuProtocol = Node.parse("""<node>
  <interface name="com.canonical.dbusmenu">
    <method name="GetLayout">
      <arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
      <arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" direction="in"/><arg type="s" direction="in"/>
      <arg type="v" direction="in"/><arg type="u" direction="in"/>
    </method>
  </interface>
</node>""")


def MenuItems(layout):
    yield layout
    for child in layout[2]:
        yield from MenuItems(child.value)


async def Tray(command):
    bus = await MessageBus().connect()
    try:
        dbusName = "org.freedesktop.DBus"
        dbus = bus.get_proxy_object(dbusName, "/org/freedesktop/DBus",
                                   await bus.introspect(dbusName, "/org/freedesktop/DBus"))
        connections = dbus.get_interface(dbusName)
        watcherName = "org.kde.StatusNotifierWatcher"
        watcherPath = "/StatusNotifierWatcher"
        obj = bus.get_proxy_object(watcherName, watcherPath, await bus.introspect(watcherName, watcherPath))
        items = (await obj.get_interface("org.freedesktop.DBus.Properties").call_get(
            watcherName, "RegisteredStatusNotifierItems")).value
        liveItems = {}
        for item in items:
            service, path = item.split("/", 1)
            path = "/" + path
            try:
                owner = await connections.call_get_name_owner(service)
                pid = await connections.call_get_connection_unix_process_id(owner)
                process = Path("/proc", str(pid))
                args = (process / "cmdline").read_bytes().decode().split("\0")
                executable = Path(args[0]).name
                nativeProcess = executable == "ednovas-cloud"
                # D-Bus reports the relay PID for clients using our session
                # proxy. Require EdNovas properties on that live connection.
                proxyProcess = (executable == "xdg-dbus-proxy"
                                and process.stat().st_uid == os.getuid()
                                and any(arg.startswith(f"/run/user/{os.getuid()}/ednovas-bus.")
                                        and arg.endswith("/bus") for arg in args))
                if (process.stat().st_uid not in {0, os.getuid()}
                        or not (nativeProcess or proxyProcess)):
                    continue
                obj = bus.get_proxy_object(service, path, trayProtocol)
                props = obj.get_interface("org.freedesktop.DBus.Properties")
                identity = (await props.call_get("org.kde.StatusNotifierItem", "Id")).value
                title = (await props.call_get("org.kde.StatusNotifierItem", "Title")).value
                # Electron may use a generic appindicator Id and an empty
                # title. The dedicated EdNovas relay is also an identity.
                if ("ednovas" not in (identity + title).lower()
                        and not proxyProcess
                        and not any(arg.startswith("--tray-helper=") for arg in args)):
                    continue
                liveItems[(owner, path)] = (obj, props, pid, identity, title)
            except (OSError, ValueError, DBusError, InterfaceNotFoundError) as error:
                # Watchers can retain entries after their owner exits. Actions
                # occur outside this block so an action failure cannot be hidden.
                print(f"Tray lookup skipped {item}: {error}", file=sys.stderr)
                continue
        assert len(liveItems) == 1, f"Expected one native EdNovas tray, found {list(liveItems)} among {items}"
        (service, path), (obj, props, pid, identity, title) = next(iter(liveItems.items()))
        if command == "activate":
            await obj.get_interface("org.kde.StatusNotifierItem").call_activate(0, 0)
        elif command == "quit":
            menuPath = (await props.call_get("org.kde.StatusNotifierItem", "Menu")).value
            menu = bus.get_proxy_object(service, menuPath, menuProtocol)
            interface = menu.get_interface("com.canonical.dbusmenu")
            layout = await interface.call_get_layout(0, -1, [])
            quitItems = [entry for entry in MenuItems(layout[1])
                         if any(word in entry[1].get("label", Variant("s", "")).value.lower()
                                for word in ["quit", "exit", "退出"])
                         and entry[1].get("enabled", Variant("b", True)).value]
            assert len(quitItems) == 1, f"Expected one quit menu item: {layout}"
            await interface.call_event(quitItems[0][0], "clicked", Variant("s", ""), 0)
        print(json.dumps({"service": service, "path": path, "pid": pid,
                          "id": identity, "title": title, "items": items}))
    finally:
        bus.disconnect()


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
