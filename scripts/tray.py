"""User-session tray item for the separately authenticated EdNovas TUN GUI.

The upstream Electron application runs as root in TUN mode and does not
register a StatusNotifierItem on some Wayland desktops. Keep the tray service
in the original user's session, independent of the privileged application.
"""

import asyncio
import json
import socket
from pathlib import Path
import subprocess
import sys

from dbus_next import BusType, DBusError
from dbus_next.constants import PropertyAccess
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, dbus_property, method
from dbus_next.signature import Variant
from PIL import Image


def IconPixels(iconPath):
    with Image.open(iconPath) as source:
        image = source.convert("RGBA").resize((48, 48), Image.Resampling.LANCZOS)
    rgba = image.tobytes()
    argb = bytearray(len(rgba))
    argb[0::4] = rgba[3::4]
    argb[1::4] = rgba[0::4]
    argb[2::4] = rgba[1::4]
    argb[3::4] = rgba[2::4]
    return [48, 48, bytes(argb)]


def FocusExistingWindow():
    """Focus an existing EdNovas window using the user's niri IPC socket."""
    try:
        output = subprocess.run(
            ["niri", "msg", "-j", "windows"],
            capture_output=True, text=True, check=True, timeout=3,
        )
        windows = json.loads(output.stdout)
        target = next((window for window in windows if window.get("app_id") == "EdNovasCloud"), None)
        if target is not None:
            subprocess.run(
                ["niri", "msg", "action", "focus-window", "--id", str(target["id"])],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=3,
            )
    except (FileNotFoundError, subprocess.SubprocessError, ValueError, KeyError):
        # Other desktop environments can still show the icon.
        pass


def SendCommand(controlPath, command):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(controlPath))
            connection.sendall((command + "\n").encode())
            return True
    except OSError as error:
        print(f"EdNovas tray: {command} failed: {error}", file=sys.stderr, flush=True)
        return False


class TrayMenu(ServiceInterface):
    def __init__(self, controlPath):
        super().__init__("com.canonical.dbusmenu")
        self.controlPath = controlPath

    @dbus_property(access=PropertyAccess.READ)
    def Version(self) -> "u":
        return 2

    @dbus_property(access=PropertyAccess.READ)
    def TextDirection(self) -> "s":
        return "ltr"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "normal"

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> "as":
        return []

    @method()
    def GetLayout(self, parentId: "i", recursionDepth: "i", propertyNames: "as") -> "u(ia{sv}av)":
        quitItem = [1, {"label": Variant("s", "Quit EdNovas Cloud"), "enabled": Variant("b", True)}, []]
        return [1, [0, {"children-display": Variant("s", "submenu")}, [Variant("(ia{sv}av)", quitItem)]]]

    @method()
    def GetGroupProperties(self, ids: "ai", propertyNames: "as") -> "a(ia{sv})":
        return [(1, {"label": Variant("s", "Quit EdNovas Cloud"), "enabled": Variant("b", True)})] if 1 in ids else []

    @method()
    def GetProperty(self, itemId: "i", name: "s") -> "v":
        return {"label": Variant("s", "Quit EdNovas Cloud"), "enabled": Variant("b", True)}.get(name, Variant("s", ""))

    @method()
    def Event(self, itemId: "i", eventId: "s", data: "v", timestamp: "u"):
        if itemId == 1 and eventId == "clicked":
            SendCommand(self.controlPath, "quit")

    @method()
    def EventGroup(self, events: "a(isvu)") -> "ai":
        for itemId, eventId, data, timestamp in events:
            self.Event(itemId, eventId, data, timestamp)
        return []

    @method()
    def AboutToShow(self, itemId: "i") -> "b":
        return False

    @method()
    def AboutToShowGroup(self, ids: "ai") -> "aiai":
        return [[], []]


class TrayItem(ServiceInterface):
    def __init__(self, iconPath, controlPath):
        super().__init__("org.kde.StatusNotifierItem")
        self.iconPath = Path(iconPath)
        self.controlPath = controlPath
        self.pixmap = [IconPixels(self.iconPath)]

    @dbus_property(access=PropertyAccess.READ)
    def Category(self) -> "s":
        return "ApplicationStatus"

    @dbus_property(access=PropertyAccess.READ)
    def Id(self) -> "s":
        return "ednovas-cloud-tun"

    @dbus_property(access=PropertyAccess.READ)
    def Title(self) -> "s":
        return "EdNovas Cloud (Privileged TUN)"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":
        return "Active"

    @dbus_property(access=PropertyAccess.READ)
    def WindowId(self) -> "i":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return str(self.iconPath)

    @dbus_property(access=PropertyAccess.READ)
    def IconPixmap(self) -> "a(iiay)":
        return self.pixmap

    @dbus_property(access=PropertyAccess.READ)
    def IconThemePath(self) -> "s":
        return str(self.iconPath.parents[3]) if len(self.iconPath.parents) > 3 else ""

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def AttentionIconPixmap(self) -> "a(iiay)":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconName(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def OverlayIconPixmap(self) -> "a(iiay)":
        return []

    @dbus_property(access=PropertyAccess.READ)
    def ToolTip(self) -> "(sa(iiay)ss)":
        return ["ednovas-cloud", self.pixmap, "EdNovas Cloud", "Privileged TUN session"]

    @dbus_property(access=PropertyAccess.READ)
    def Menu(self) -> "o":
        return "/Menu"

    @dbus_property(access=PropertyAccess.READ)
    def ItemIsMenu(self) -> "b":
        return False

    @method()
    def Activate(self, x: "i", y: "i"):
        if SendCommand(self.controlPath, "show"):
            FocusExistingWindow()

    @method()
    def SecondaryActivate(self, x: "i", y: "i"):
        pass

    @method()
    def ContextMenu(self, x: "i", y: "i"):
        pass

    @method()
    def Scroll(self, delta: "i", orientation: "s"):
        pass


async def Main(iconPath, controlPath):
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    bus.export("/StatusNotifierItem", TrayItem(iconPath, controlPath))
    bus.export("/Menu", TrayMenu(controlPath))
    registered = False
    while True:
        try:
            introspection = await bus.introspect("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher")
            watcher = bus.get_proxy_object("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher", introspection)
            properties = watcher.get_interface("org.freedesktop.DBus.Properties")
            items = await properties.call_get("org.kde.StatusNotifierWatcher", "RegisteredStatusNotifierItems")
            if f"{bus.unique_name}/StatusNotifierItem" not in items.value:
                await watcher.get_interface("org.kde.StatusNotifierWatcher").call_register_status_notifier_item(bus.unique_name)
            if not registered:
                print("EdNovas TUN: user-session tray registered", flush=True)
            registered = True
        except (DBusError, OSError) as error:
            if registered:
                print(f"EdNovas TUN: tray watcher unavailable: {error}", file=sys.stderr, flush=True)
            registered = False
        await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(Main(sys.argv[1], sys.argv[2]))
    except (KeyboardInterrupt, IndexError):
        pass
