"""Run fixture commands in the actual active desktop session for Polkit."""
import asyncio
import json
import os
from pathlib import Path
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method


class DesktopCommands(ServiceInterface):
    def __init__(self):
        super().__init__("org.ednovas.RuntimeDesktop")

    @method()
    async def RunCommand(self, args: "as") -> "i":
        process = await asyncio.create_subprocess_exec(*args)
        return await process.wait()


async def Main():
    os.environ["XDG_CURRENT_DESKTOP"] = "Openbox"
    os.environ.pop("LD_LIBRARY_PATH", None)
    bus = await MessageBus().connect()
    bus.export("/RuntimeDesktop", DesktopCommands())
    await bus.request_name("org.ednovas.RuntimeDesktop")
    Path.home().joinpath("session.json").write_text(json.dumps(dict(os.environ)))
    await bus.wait_for_disconnect()


asyncio.run(Main())
