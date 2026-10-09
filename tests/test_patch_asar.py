import importlib.util
import json
from pathlib import Path
import unittest

moduleSpec = importlib.util.spec_from_file_location(
    "patch_asar", Path(__file__).resolve().parents[1] / "scripts/patch-asar.py")
patchAsar = importlib.util.module_from_spec(moduleSpec)
moduleSpec.loader.exec_module(patchAsar)


def FixtureArchive(loader=None):
    contents = {
        "package.json": json.dumps({"version": "1.1.79", "main": "dist-electron/main.js"}).encode(),
        "dist-electron/main.js": loader or b"'use strict';\nrequire('bytenode');\nrequire('./main.jsc');\n",
        "dist-electron/main.jsc": (b"EdNovas-Core check-is-admin check-tun-service-available "
                                   b"relaunch-as-admin start-clash-service-secure spawn"),
        "dist/index.html": b"unchanged renderer",
    }
    def Entry():
        return {"offset": "0", "size": 0, "integrity": {"blockSize": 4194304}}
    header = {"files": {
        "package.json": Entry(),
        "dist-electron": {"files": {"main.js": Entry(), "main.jsc": Entry()}},
        "dist": {"files": {"index.html": Entry()}},
    }}
    return patchAsar.WriteArchive(header, contents), contents


class PatchTests(unittest.TestCase):
    def test_patch_keeps_bytecode_and_renderer_intact(self):
        archiveData, originalContents = FixtureArchive()
        for tunSupport in [False, True]:
            patchedData = patchAsar.PatchArchive(archiveData, b"// shim", "1.1.79", tunSupport)
            _, contents = patchAsar.ReadArchive(patchedData)
            self.assertEqual(contents["dist-electron/main.jsc"], originalContents["dist-electron/main.jsc"])
            self.assertEqual(contents["dist/index.html"], originalContents["dist/index.html"])
            expected = b"true" if tunSupport else b"false"
            self.assertIn(b"const nixTunSupport = " + expected, contents["dist-electron/main.js"])

    def test_changed_loader_and_version_fail_closed(self):
        archiveData, _ = FixtureArchive(loader=b"require('./different.js')")
        with self.assertRaises(ValueError):
            patchAsar.PatchArchive(archiveData, b"// shim", "1.1.79", True)
        archiveData, _ = FixtureArchive()
        with self.assertRaises(ValueError):
            patchAsar.PatchArchive(archiveData, b"// shim", "1.1.80", True)

    def test_missing_upstream_hook_fails_closed(self):
        archiveData, _ = FixtureArchive()
        header, contents = patchAsar.ReadArchive(archiveData)
        contents["dist-electron/main.jsc"] = b"changed upstream implementation"
        with self.assertRaises(ValueError):
            patchAsar.PatchArchive(patchAsar.WriteArchive(header, contents), b"// shim", "1.1.79", True)
