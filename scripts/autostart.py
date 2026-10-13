"""Wait for the user's native tray host before launching EdNovas at login."""
import asyncio
import os
import sys
import time

from dbus_next import Message, MessageFlag, MessageType
from dbus_next.aio import MessageBus
from dbus_next.errors import AuthError, DBusError


def DisconnectBus(bus):
    bus.disconnect()
    # dbus-next shuts down the connection but leaves its socket and stream open.
    # Finalize explicitly, including when a deadline cancels authentication.
    bus._finalize()
    bus._stream.close()
    bus._sock.close()


async def TrayReady():
    bus = MessageBus()
    try:
        await bus.connect()
        reply = await bus.call(Message(
            destination="org.kde.StatusNotifierWatcher",
            path="/StatusNotifierWatcher",
            interface="org.freedesktop.DBus.Properties",
            member="Get",
            signature="ss",
            body=["org.kde.StatusNotifierWatcher", "IsStatusNotifierHostRegistered"],
            flags=MessageFlag.NO_AUTOSTART,
        ))
        return (reply.message_type == MessageType.METHOD_RETURN
                and reply.signature == "v" and len(reply.body) == 1
                and reply.body[0].signature == "b" and reply.body[0].value is True)
    finally:
        DisconnectBus(bus)


async def WaitForTray(timeout=60, pollInterval=0.5):
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            if await asyncio.wait_for(TrayReady(), min(2, remaining)):
                return True
        except (OSError, EOFError, ValueError, AuthError, DBusError, asyncio.TimeoutError):
            pass
        await asyncio.sleep(min(pollInterval, max(0, deadline - time.monotonic())))
    return False


def Main(args):
    if not args:
        raise SystemExit("Usage: autostart.py LAUNCHER [ARG ...]")
    if not asyncio.run(WaitForTray()):
        print("EdNovas: tray host unavailable after 60 seconds; launching anyway", file=sys.stderr)
    os.execv(args[0], args)


if __name__ == "__main__":
    Main(sys.argv[1:])
