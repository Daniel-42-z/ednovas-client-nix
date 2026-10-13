import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from dbus_next import Message, MessageType, Variant
from dbus_next.aio import MessageBus

scriptPath = Path(__file__).resolve().parents[1] / 'scripts/autostart.py'
spec = importlib.util.spec_from_file_location('autostart', scriptPath)
autostart = importlib.util.module_from_spec(spec)
spec.loader.exec_module(autostart)


class AutostartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.busDirectory = tempfile.TemporaryDirectory()
        self.addCleanup(self.busDirectory.cleanup)
        configPath = Path(self.busDirectory.name) / 'bus.conf'
        configPath.write_text('''<busconfig>
          <type>session</type>
          <listen>unix:tmpdir=/tmp</listen>
          <auth>EXTERNAL</auth>
          <policy context="default">
            <allow send_destination="*"/>
            <allow receive_sender="*"/>
            <allow own="*"/>
          </policy>
        </busconfig>''')
        self.daemon = subprocess.Popen(
            ['dbus-daemon', f'--config-file={configPath}', '--nofork', '--print-address=1'],
            stdout=subprocess.PIPE, text=True,
        )
        self.busAddress = self.daemon.stdout.readline().strip()
        self.assertTrue(self.busAddress, 'Private test bus failed to start')
        self.environment = patch.dict(os.environ, {'DBUS_SESSION_BUS_ADDRESS': self.busAddress})
        self.environment.start()
        self.watcherBus = await MessageBus().connect()
        self.hostReady = False
        self.failures = 0
        self.hang = False
        self.requests = 0
        self.watcherBus.add_message_handler(self.HandleMessage)

    async def asyncTearDown(self):
        autostart.DisconnectBus(self.watcherBus)
        await self.watcherBus.wait_for_disconnect()
        self.environment.stop()
        self.daemon.terminate()
        self.daemon.wait(timeout=5)
        self.daemon.stdout.close()

    def HandleMessage(self, message):
        if (message.message_type != MessageType.METHOD_CALL
                or message.path != '/StatusNotifierWatcher'
                or message.interface != 'org.freedesktop.DBus.Properties'
                or message.member != 'Get'):
            return None
        self.assertEqual(message.body, ['org.kde.StatusNotifierWatcher', 'IsStatusNotifierHostRegistered'])
        self.requests += 1
        if self.hang:
            return True
        if self.failures:
            self.failures -= 1
            return Message.new_error(message, 'org.freedesktop.DBus.Error.Failed', 'Not ready yet')
        return Message.new_method_return(message, 'v', [Variant('b', self.hostReady)])

    async def RegisterWatcher(self):
        await self.watcherBus.request_name('org.kde.StatusNotifierWatcher')

    async def test_ready_host_launches_without_poll_delay(self):
        await self.RegisterWatcher()
        self.hostReady = True
        self.assertTrue(await autostart.WaitForTray(timeout=1, pollInterval=2))
        self.assertEqual(self.requests, 1)

    async def test_delayed_watcher(self):
        waiting = asyncio.create_task(autostart.WaitForTray(timeout=1, pollInterval=0.01))
        await asyncio.sleep(0.05)
        self.assertFalse(waiting.done())
        self.hostReady = True
        await self.RegisterWatcher()
        self.assertTrue(await waiting)

    async def test_watcher_without_host_waits(self):
        await self.RegisterWatcher()
        waiting = asyncio.create_task(autostart.WaitForTray(timeout=1, pollInterval=0.01))
        await asyncio.sleep(0.05)
        self.assertFalse(waiting.done())
        self.assertGreater(self.requests, 0)
        self.hostReady = True
        self.assertTrue(await waiting)

    async def test_transient_property_failure(self):
        await self.RegisterWatcher()
        self.hostReady = True
        self.failures = 1
        self.assertTrue(await autostart.WaitForTray(timeout=1, pollInterval=0.01))
        self.assertEqual(self.requests, 2)

    async def test_missing_watcher_times_out(self):
        self.assertFalse(await autostart.WaitForTray(timeout=0.05, pollInterval=0.01))

    async def test_unresponsive_watcher_respects_deadline(self):
        await self.RegisterWatcher()
        self.hang = True
        startTime = time.monotonic()
        self.assertFalse(await autostart.WaitForTray(timeout=0.05, pollInterval=0.01))
        self.assertLess(time.monotonic() - startTime, 0.5)

    async def test_bus_connection_failure_is_retried(self):
        originalProbe = autostart.TrayReady
        await self.RegisterWatcher()
        self.hostReady = True
        with patch.object(autostart, 'TrayReady', AsyncMock(side_effect=[OSError('unavailable'), True])) as probe:
            self.assertTrue(await autostart.WaitForTray(timeout=1, pollInterval=0.01))
            self.assertEqual(probe.await_count, 2)
        self.assertIs(autostart.TrayReady, originalProbe)

    async def CheckChildLaunch(self, ready):
        with tempfile.TemporaryDirectory() as directory:
            outputPath = Path(directory) / 'launch.jsonl'
            childCode = ('import json, os, sys; '
                         'open(sys.argv[1], "a").write(json.dumps('
                         '[sys.argv[2:], os.environ["EDNOVAS_TEST_VALUE"]]) + "\\n")')
            wrapperCode = ('import functools, runpy, sys; '
                           'module = runpy.run_path(sys.argv[1]); '
                           'main = module["Main"]; '
                           'main.__globals__["WaitForTray"] = functools.partial('
                           'module["WaitForTray"], timeout=0.4, pollInterval=0.01); '
                           'main(sys.argv[2:])')
            args = ['argument with spaces', 'https://example.test/a?b=c&d=e']
            process = await asyncio.create_subprocess_exec(
                sys.executable, '-c', wrapperCode, str(scriptPath),
                sys.executable, '-c', childCode, str(outputPath), *args,
                env={**os.environ, 'EDNOVAS_TEST_VALUE': 'preserved'},
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.sleep(0.1)
                self.assertIsNone(process.returncode)
                self.assertFalse(outputPath.exists())
                if ready:
                    self.hostReady = True
                    await self.RegisterWatcher()
                _, stderr = await asyncio.wait_for(process.communicate(), 3)
                self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertEqual(outputPath.read_text().splitlines(), [json.dumps([args, 'preserved'])])
                self.assertEqual(b'launching anyway' in stderr, not ready)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

    async def test_child_waits_and_preserves_arguments_and_environment(self):
        await self.CheckChildLaunch(True)

    async def test_timeout_warns_and_launches_once(self):
        await self.CheckChildLaunch(False)


if __name__ == '__main__':
    unittest.main()
