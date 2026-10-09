import json
from pathlib import Path
import sys
import struct
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update


def ReleaseLink(version, architecture="x86_64"):
    return (f"https://storage.ednovas.org/desktop/{version}/"
            f"EdNovas-Cloud-{version}-Linux-{architecture}.AppImage")


def AppImageArchive(architecture="x86_64"):
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    header[8:11] = b"AI\x02"
    struct.pack_into("<H", header, 18, 62 if architecture == "x86_64" else 183)
    struct.pack_into("<Q", header, 40, 64)
    struct.pack_into("<HH", header, 58, 64, 1)
    superblock = bytearray(96)
    superblock[:4] = b"hsqs"
    struct.pack_into("<HH", superblock, 28, 4, 0)
    struct.pack_into("<Q", superblock, 40, 112)
    return bytes(header) + b"\0" * 64 + bytes(superblock) + b"fixture payload!"


class UpdateTests(unittest.TestCase):
    def test_latest_version_is_numeric_and_ignores_foreign_links(self):
        pageData = "".join(f'<a href="{ReleaseLink(version)}">AppImage</a>'
                           for version in ["1.1.9", "1.1.80", "1.1.79"])
        pageData += '<a href="https://example.com/desktop/99.0.0/app.AppImage">foreign</a>'
        self.assertEqual(update.FindLatest(pageData, ["x86_64"])[0], "1.1.80")

    def test_missing_newest_architecture_does_not_select_an_old_release(self):
        pageData = (f'<a href="{ReleaseLink("1.1.80")}">x64</a>'
                    f'<a href="{ReleaseLink("1.1.79", "arm64")}">ARM</a>')
        with self.assertRaises(ValueError):
            update.FindLatest(pageData, ["x86_64", "arm64"])

    def test_malformed_page_is_an_error(self):
        with self.assertRaises(ValueError):
            update.FindLatest("<html>Maintenance</html>", ["x86_64"])

    def test_archive_identity_and_truncation_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            archivePath = Path(directory) / "app.AppImage"
            archivePath.write_bytes(AppImageArchive())
            update.ValidateAppImage(archivePath, "x86_64")
            with self.assertRaises(ValueError):
                update.ValidateAppImage(archivePath, "arm64")
            archivePath.write_bytes(AppImageArchive()[:-3])
            with self.assertRaises(ValueError):
                update.ValidateAppImage(archivePath, "x86_64")

    def RunUpdate(self, advertisedVersion, write, brokenArchive=False):
        with tempfile.TemporaryDirectory() as directory:
            manifestPath = Path(directory) / "release.json"
            originalManifest = {"version": "1.1.79", "sources": {
                "x86_64-linux": {"url": ReleaseLink("1.1.79"),
                                  "hash": "original", "appImageArchitecture": "x86_64"}}}
            manifestPath.write_text(json.dumps(originalManifest))
            originalData = manifestPath.read_bytes()
            calls = []

            def FakeDownload(url, destination):
                calls.append(url)
                if url == update.downloadPage:
                    destination.write_text(f'<a href="{ReleaseLink(advertisedVersion)}">AppImage</a>')
                else:
                    destination.write_bytes(b"Not an AppImage" if brokenArchive
                                            else AppImageArchive())

            with patch.object(update, "Download", FakeDownload):
                if brokenArchive:
                    with self.assertRaises(ValueError):
                        update.UpdateRelease(manifestPath, write)
                    self.assertEqual(manifestPath.read_bytes(), originalData)
                    return
                result = update.UpdateRelease(manifestPath, write)
            return result, json.loads(manifestPath.read_text()), calls, originalManifest

    def test_check_only_does_not_download_or_modify_archive(self):
        result, manifest, calls, originalManifest = self.RunUpdate("1.1.80", False)
        self.assertTrue(result["updateAvailable"])
        self.assertFalse(result["changed"])
        self.assertEqual(manifest, originalManifest)
        self.assertEqual(calls, [update.downloadPage])

    def test_equal_and_older_releases_do_not_write(self):
        for version in ["1.1.79", "1.1.78"]:
            result, manifest, calls, originalManifest = self.RunUpdate(version, True)
            self.assertFalse(result["changed"])
            self.assertEqual(manifest, originalManifest)
            self.assertEqual(calls, [update.downloadPage])

    def test_new_release_updates_version_url_and_hash(self):
        result, manifest, calls, _ = self.RunUpdate("1.1.80", True)
        self.assertTrue(result["changed"])
        self.assertEqual(manifest["version"], "1.1.80")
        self.assertEqual(manifest["sources"]["x86_64-linux"]["url"], ReleaseLink("1.1.80"))
        self.assertTrue(manifest["sources"]["x86_64-linux"]["hash"].startswith("sha256-"))
        self.assertEqual(len(calls), 2)

    def test_failed_archive_leaves_manifest_untouched(self):
        self.RunUpdate("1.1.80", True, brokenArchive=True)
