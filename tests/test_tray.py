import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from dbus_next.signature import Variant


spec = importlib.util.spec_from_file_location('tray', Path(__file__).resolve().parents[1] / 'scripts/tray.py')
tray = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tray)


class TrayTests(unittest.TestCase):
    def test_menu_quit(self):
        menu = tray.TrayMenu('/tmp/control')
        revision, layout = menu.GetLayout.__wrapped__(menu, 0, -1, [])
        self.assertEqual(revision, 1)
        self.assertEqual(layout[2][0].value[1]['label'].value, 'Quit EdNovas Cloud')
        with patch.object(tray, 'SendCommand') as send:
            menu.Event(1, 'clicked', tray.Variant('s', ''), 0)
        send.assert_called_once_with('/tmp/control', 'quit')

    def test_menu_dbus_reply_signatures(self):
        menu = tray.TrayMenu('/tmp/control')
        methods = {method.name: method for method in menu.introspect().methods}
        self.assertEqual(''.join(arg.signature for arg in methods['GetLayout'].out_args), 'u(ia{sv}av)')
        self.assertEqual(''.join(arg.signature for arg in methods['AboutToShowGroup'].out_args), 'aiai')

    def test_left_click_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'icon.png'
            Image.new('RGBA', (48, 48), 'white').save(path)
            item = tray.TrayItem(path, '/tmp/control')
            with patch.object(tray, 'SendCommand', return_value=True) as send, patch.object(tray, 'FocusExistingWindow') as focus:
                item.Activate(0, 0)
        send.assert_called_once_with('/tmp/control', 'show')
        focus.assert_called_once()

    def test_icon_pixmap_has_status_notifier_argb_byte_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'icon.png'
            Image.new('RGBA', (48, 48), (30, 60, 90, 120)).save(path)
            width, height, pixels = tray.IconPixels(path)
        self.assertEqual((width, height), (48, 48))
        self.assertEqual(pixels[:4], bytes([120, 30, 60, 90]))
        self.assertEqual(len(pixels), 48 * 48 * 4)

    def test_dbus_serializes_all_status_notifier_properties(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'icon.png'
            Image.new('RGBA', (48, 48), 'white').save(path)
            item = tray.TrayItem(path, '/tmp/control')
            for prop in item.introspect().properties:
                value = getattr(item, prop.name)
                Variant(prop.signature, value)

    def test_focus_action_uses_existing_niri_window(self):
        windows = [
            {'app_id': 'kitty', 'id': 1},
            {'app_id': 'EdNovasCloud', 'id': 60},
        ]
        with patch.object(subprocess, 'run') as run:
            run.return_value.stdout = json.dumps(windows)
            tray.FocusExistingWindow()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0], ['niri', 'msg', 'action', 'focus-window', '--id', '60'])

    def test_missing_window_does_not_invoke_focus(self):
        with patch.object(subprocess, 'run') as run:
            run.return_value.stdout = json.dumps([{'app_id': 'kitty', 'id': 1}])
            tray.FocusExistingWindow()
        self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
