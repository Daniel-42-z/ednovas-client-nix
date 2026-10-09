import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update


def ReleaseLink(version, architecture="amd64"):
    return (f"https://storage.ednovas.org/desktop/{version}/"
            f"EdNovas-Cloud-{version}-Linux-{architecture}.deb")


def DebianArchive(version="1.1.80", architecture="amd64"):
    controlData = (f"Package: ednovas-cloud\nVersion: {version}\n"
                   f"Architecture: {architecture}\n").encode()
    controlBuffer = io.BytesIO()
    with tarfile.open(fileobj=controlBuffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("./control")
        member.size = len(controlData)
        archive.addfile(member, io.BytesIO(controlData))
    archiveData = b"!<arch>\n"
    for memberName, memberData in [("debian-binary", b"2.0\n"),
                                    ("control.tar.gz", controlBuffer.getvalue()),
                                    ("data.tar.xz", b"fixture payload")]:
        header = (f"{memberName + '/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}"
                  f"{len(memberData):<10}`\n").encode()
        archiveData += header + memberData + (b"\n" if len(memberData) % 2 else b"")
    return archiveData


class UpdateTests(unittest.TestCase):
    def test_latest_version_is_numeric_and_ignores_foreign_links(self):
        pageData = "".join(f'<a href="{ReleaseLink(version)}">deb</a>'
                           for version in ["1.1.9", "1.1.80", "1.1.79"])
        pageData += '<a href="https://example.com/desktop/99.0.0/app.deb">foreign</a>'
        self.assertEqual(update.FindLatest(pageData, ["amd64"])[0], "1.1.80")

    def test_missing_newest_architecture_does_not_select_an_old_release(self):
        pageData = (f'<a href="{ReleaseLink("1.1.80")}">x64</a>'
                    f'<a href="{ReleaseLink("1.1.79", "arm64")}">ARM</a>')
        with self.assertRaises(ValueError):
            update.FindLatest(pageData, ["amd64", "arm64"])

    def test_malformed_page_is_an_error(self):
        with self.assertRaises(ValueError):
            update.FindLatest("<html>Maintenance</html>", ["amd64"])

    def test_archive_identity_and_truncation_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            archivePath = Path(directory) / "app.deb"
            archivePath.write_bytes(DebianArchive())
            update.ValidateDeb(archivePath, "1.1.80", "amd64")
            with self.assertRaises(ValueError):
                update.ValidateDeb(archivePath, "1.1.80", "arm64")
            archivePath.write_bytes(DebianArchive()[:-3])
            with self.assertRaises(ValueError):
                update.ValidateDeb(archivePath, "1.1.80", "amd64")

    def RunUpdate(self, advertisedVersion, write, brokenArchive=False):
        with tempfile.TemporaryDirectory() as directory:
            manifestPath = Path(directory) / "release.json"
            originalManifest = {"version": "1.1.79", "sources": {
                "x86_64-linux": {"url": ReleaseLink("1.1.79"),
                                  "hash": "original", "debArchitecture": "amd64"}}}
            manifestPath.write_text(json.dumps(originalManifest))
            originalData = manifestPath.read_bytes()
            calls = []

            def FakeDownload(url, destination):
                calls.append(url)
                if url == update.downloadPage:
                    destination.write_text(f'<a href="{ReleaseLink(advertisedVersion)}">deb</a>')
                else:
                    destination.write_bytes(b"Not a Debian archive" if brokenArchive
                                            else DebianArchive(advertisedVersion))

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
