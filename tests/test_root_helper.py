import importlib.util
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('rootHelper', Path(__file__).resolve().parents[1] / 'scripts/root-helper.py')
rootHelper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rootHelper)


class RootHelperTests(unittest.TestCase):
    def test_arguments_cannot_override_the_root_profile_or_enable_remote_debugging(self):
        for flag in ['--user-data-dir=/home/user/config', '--remote-debugging-port=9222', '--', '/tmp/program']:
            with self.assertRaises(ValueError):
                rootHelper.ValidateFlags([flag])
        self.assertEqual(rootHelper.ValidateFlags(['--disable-gpu', '--enable-logging=stderr']),
                         ['--disable-gpu', '--enable-logging=stderr'])

    def test_session_rejects_code_loading_environment_and_foreign_sockets(self):
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)):
            for session in [{'DISPLAY': ':0', 'LD_PRELOAD': '/tmp/evil.so'},
                            {'DISPLAY': ':0', 'PKEXEC_UID': '1001'},
                            {'DISPLAY': 'remote:0'}, {'WAYLAND_DISPLAY': '../other/socket'},
                            {'WAYLAND_DISPLAY': '..'},
                            {'DISPLAY': ':0', 'EDNOVAS_DBUS_PROXY_SOCKET': '/tmp/bus'},
                            {'DISPLAY': ':0', 'EDNOVAS_DBUS_PROXY_SOCKET': '/run/user/1001/ednovas-bus.ABCDEFGH/bus'}]:
                with self.assertRaises(ValueError):
                    rootHelper.ValidateSession(session, 1000)
        directoryInfo = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
        socketInfo = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o700, st_uid=1000)
        socketPath = '/run/user/1000/ednovas-bus.ABCDEFGH/bus'
        fontInfo = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000)
        def FakeLstat(path):
            if path.name == 'fonts.conf':
                return fontInfo
            return socketInfo if str(path) == socketPath else directoryInfo
        with patch.object(Path, 'lstat', autospec=True, side_effect=FakeLstat):
            session = {'WAYLAND_DISPLAY': 'wayland-1', 'EDNOVAS_DBUS_PROXY_SOCKET': socketPath}
            self.assertEqual(rootHelper.ValidateSession(session, 1000), session)
        for mode, owner in [(stat.S_IFLNK | 0o600, 1000), (stat.S_IFREG | 0o644, 1000),
                            (stat.S_IFREG | 0o600, 1001)]:
            fontInfo.st_mode, fontInfo.st_uid = mode, owner
            with patch.object(Path, 'lstat', autospec=True, side_effect=FakeLstat):
                with self.assertRaises(ValueError):
                    rootHelper.ValidateSession(session, 1000)
        fontInfo.st_mode, fontInfo.st_uid = stat.S_IFREG | 0o600, 1000
        directoryInfo.st_uid = 1001
        with patch.object(Path, 'lstat', autospec=True, side_effect=FakeLstat):
            with self.assertRaises(ValueError):
                rootHelper.ValidateSession({'DISPLAY': ':0', 'EDNOVAS_DBUS_PROXY_SOCKET': socketPath}, 1000)

    def test_root_environment_uses_separate_state_and_fixed_bus(self):
        env = rootHelper.BuildEnvironment({'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-1', 'EDNOVAS_DBUS_PROXY_SOCKET': '/run/user/1000/ednovas-bus.ABCDEFGH/bus'}, 1000, Path('/var/lib/ednovas-cloud/1000'), Path('/run/ednovas-cloud/1000'))
        self.assertEqual(env['HOME'], '/var/lib/ednovas-cloud/1000/home')
        self.assertEqual(env['XDG_CONFIG_HOME'], '/var/lib/ednovas-cloud/1000/config')
        self.assertEqual(env['XDG_RUNTIME_DIR'], '/run/ednovas-cloud/1000')
        self.assertEqual(env['WAYLAND_DISPLAY'], '/run/user/1000/wayland-1')
        self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'], 'unix:path=/run/user/1000/ednovas-bus.ABCDEFGH/bus')
        self.assertNotIn('FONTCONFIG_FILE', env)
        self.assertEqual(env['PKEXEC_UID'], '1000')
        self.assertNotIn('EDNOVAS_CONTROL_SOCKET', env)
        self.assertNotIn('EDNOVAS_DBUS_PROXY_SOCKET', env)
        self.assertEqual(env['USER'], 'root')
        self.assertNotIn('LD_LIBRARY_PATH', env)
        self.assertNotIn('PYTHONPATH', env)

    def test_helper_rejects_unprivileged_or_unauthenticated_invocation(self):
        with patch.object(rootHelper.os, 'geteuid', return_value=1000):
            with self.assertRaises(ValueError):
                rootHelper.Main()
        with patch.object(rootHelper.os, 'geteuid', return_value=0), patch.dict(rootHelper.os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                rootHelper.Main()

    def test_profile_directory_rejects_symlinks_and_foreign_owners(self):
        for mode, owner in [(stat.S_IFLNK | 0o777, 0), (stat.S_IFDIR | 0o700, 1000)]:
            info = SimpleNamespace(st_mode=mode, st_uid=owner)
            with patch.object(Path, 'mkdir'), patch.object(Path, 'lstat', return_value=info):
                with self.assertRaises(ValueError):
                    rootHelper.EnsurePrivateDirectory(Path('/var/lib/ednovas-cloud/1000'))
