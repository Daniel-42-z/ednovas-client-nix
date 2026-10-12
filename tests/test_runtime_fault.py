import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "runtimeFault", Path(__file__).resolve().parents[1] / "nix/runtime/fault.py")
runtimeFault = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtimeFault)


class RuntimeFaultTests(unittest.TestCase):
    def setUp(self):
        self.contents = {
            "dist-electron/main.js": b"'use strict';\nrequire('bytenode');\nrequire('./main.jsc');\n",
            "dist-electron/main.jsc": b"unchanged bytecode",
            "dist/index.html": b"upstream renderer",
        }
        header = {"files": {
            "dist-electron": {"files": {
                "main.js": {"offset": "0", "size": 0},
                "main.jsc": {"offset": "0", "size": 0},
            }},
            "dist": {"files": {"index.html": {"offset": "0", "size": 0}}},
        }}
        self.archive = runtimeFault.WriteArchive(header, self.contents)

    def test_each_fault_preserves_unrelated_members(self):
        for fault in ["close", "restore", "quit", "renderer"]:
            with self.subTest(fault=fault):
                _, changed = runtimeFault.ReadArchive(runtimeFault.InjectFault(self.archive, fault))
                target = "dist/index.html" if fault == "renderer" else "dist-electron/main.js"
                self.assertNotEqual(changed[target], self.contents[target])
                for name in self.contents.keys() - {target}:
                    self.assertEqual(changed[name], self.contents[name])

    def test_window_fault_refuses_changed_upstream_loader(self):
        header, contents = runtimeFault.ReadArchive(self.archive)
        contents["dist-electron/main.js"] = b"changed upstream entrypoint"
        archive = runtimeFault.WriteArchive(header, contents)
        with self.assertRaises(AssertionError):
            runtimeFault.InjectFault(archive, "close")
