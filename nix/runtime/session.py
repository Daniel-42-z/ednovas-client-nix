"""Test StatusNotifier host on the real desktop bus; no authorization override."""
import asyncio
import json
import os
from pathlib import Path
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method


class Watcher(ServiceInterface):
    def __init__(self):
        super().__init__("org.kde.StatusNotifierWatcher")
        self.items = []

    @method()
    def RegisterStatusNotifierItem(self, service: "s"):
        item = service + "/StatusNotifierItem"
        if item not in self.items:
            self.items.append(item)

    @dbus_property(access=PropertyAccess.READ)
    def RegisteredStatusNotifierItems(self) -> "as":
        return self.items

    @dbus_property(access=PropertyAccess.READ)
    def IsStatusNotifierHostRegistered(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def ProtocolVersion(self) -> "i":
        return 0


class DesktopCommands(ServiceInterface):
    def __init__(self):
        super().__init__("org.ednovas.RuntimeDesktop")

    @method()
    async def RunCommand(self, args: "as") -> "i":
        # Spawn in the actual LightDM/niri session, rather than the root test
        # channel's su session: Polkit must see a local, active desktop caller.
        process = await asyncio.create_subprocess_exec(*args)
        return await process.wait()


async def Main():
    if os.environ.get("NIRI_SOCKET"):
        os.environ["XDG_SESSION_TYPE"] = "wayland"
        os.environ["XDG_CURRENT_DESKTOP"] = "niri"
    else:
        os.environ["XDG_CURRENT_DESKTOP"] = "Openbox"
    os.environ.pop("LD_LIBRARY_PATH", None)
    sessionEnvironment = dict(os.environ)
    Path.home().joinpath("session.json").write_text(json.dumps(sessionEnvironment))
    bus = await MessageBus().connect()
    bus.export("/StatusNotifierWatcher", Watcher())
    bus.export("/RuntimeDesktop", DesktopCommands())
    await bus.request_name("org.kde.StatusNotifierWatcher")
    await bus.request_name("org.ednovas.RuntimeDesktop")
    await bus.wait_for_disconnect()


asyncio.run(Main())
