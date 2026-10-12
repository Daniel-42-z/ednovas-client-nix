"""Test StatusNotifier host on the real desktop bus; no authorization override."""
import asyncio
import json
import os
from pathlib import Path
from dbus_next import Message, MessageType
from dbus_next.aio import MessageBus
from dbus_next.constants import PropertyAccess
from dbus_next.service import ServiceInterface, dbus_property, method, signal


class Watcher(ServiceInterface):
    def __init__(self):
        super().__init__("org.kde.StatusNotifierWatcher")
        self.items = []

    @signal()
    def StatusNotifierItemRegistered(self, item) -> "s":
        return item

    @method()
    def RegisterStatusNotifierHost(self, service: "s"):
        pass

    @method()
    def RegisterStatusNotifierItem(self, service: "s"):
        Path.home().joinpath("tray-registrations.jsonl").open("a").write(json.dumps({"service": service}) + "\n")
        item = service if "/" in service else service + "/StatusNotifierItem"
        if item not in self.items:
            self.items.append(item)
            self.StatusNotifierItemRegistered(item)

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
    watcher = Watcher()

    def RegisterPath(message):
        if (message.message_type == MessageType.METHOD_CALL
                and message.path == "/StatusNotifierWatcher"
                and message.interface == "org.kde.StatusNotifierWatcher"
                and message.member == "RegisterStatusNotifierItem"
                and message.signature == "s" and message.body[0].startswith("/")):
            Path.home().joinpath("tray-registrations.jsonl").open("a").write(
                json.dumps({"sender": message.sender, "path": message.body[0]}) + "\n")
            item = message.sender + message.body[0]
            if item not in watcher.items:
                watcher.items.append(item)
                watcher.StatusNotifierItemRegistered(item)
            return Message.new_method_return(message)

    bus.add_message_handler(RegisterPath)
    bus.export("/StatusNotifierWatcher", watcher)
    bus.export("/RuntimeDesktop", DesktopCommands())
    await bus.request_name("org.kde.StatusNotifierWatcher")
    await bus.request_name("org.ednovas.RuntimeDesktop")
    await bus.wait_for_disconnect()


asyncio.run(Main())
