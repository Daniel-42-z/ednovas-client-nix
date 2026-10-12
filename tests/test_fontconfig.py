import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location('fontConfig', Path(__file__).resolve().parents[1] / 'scripts/fontconfig.py')
fontConfig = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fontConfig)


class FontConfigurationTests(unittest.TestCase):
    def test_user_fonts_and_preferences_use_absolute_paths_without_changing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'fonts.conf'
            fontConfig.WriteFontConfiguration(destination, Path('/home/user/config & preferences'),
                                              Path('/home/user/data'))
            root = ET.parse(destination).getroot()
            self.assertEqual([entry.text for entry in root.findall('include')], [
                '/home/user/config & preferences/fontconfig/conf.d',
                '/home/user/config & preferences/fontconfig/fonts.conf'])
            self.assertEqual(root.findtext('dir'), '/home/user/data/fonts')
            self.assertTrue(all(entry.get('ignore_missing') == 'yes' for entry in root.findall('include')))
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
            self.assertIsNone(root.find('cachedir'))
