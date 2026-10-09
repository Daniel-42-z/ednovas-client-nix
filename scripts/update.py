"""Check public EdNovas downloads and atomically update the pinned release."""

import argparse
import base64
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

downloadPage = "https://download.ednovas.org/"
downloadPattern = re.compile(
    r"https://storage\.ednovas\.org/desktop/(?P<version>\d+\.\d+\.\d+)/"
    r"EdNovas-Cloud-(?P=version)-Linux-(?P<architecture>amd64|arm64)\.deb"
)


class DownloadParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.downloads = {}

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        url = dict(attrs).get("href", "")
        match = downloadPattern.fullmatch(url)
        if match:
            self.downloads.setdefault(match["version"], {})[match["architecture"]] = url


def VersionKey(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid release version: {version!r}")
    return tuple(int(part) for part in version.split("."))


def FindLatest(pageData, requiredArchitectures):
    parser = DownloadParser()
    parser.feed(pageData)
    if not parser.downloads:
        raise ValueError("No recognized Linux Debian releases on the download page")
    latestVersion = max(parser.downloads, key=VersionKey)
    latestSources = parser.downloads[latestVersion]
    if not set(requiredArchitectures).issubset(latestSources):
        raise ValueError("Newest release is missing a configured architecture; refusing a partial update")
    return latestVersion, latestSources


def Download(url, destination):
    subprocess.run([
        "curl", "--fail", "--location", "--silent", "--show-error",
        "--proto", "=https", "--proto-redir", "=https",
        "--connect-timeout", "15", "--max-time", "600",
        "--retry", "3", "--retry-delay", "2", "--output", str(destination), url,
    ], check=True)


def ValidateDeb(archivePath, version, architecture):
    controlData = None
    hasPayload = False
    with archivePath.open("rb") as archiveFile:
        if archiveFile.read(8) != b"!<arch>\n":
            raise ValueError("Download is not a Debian archive")
        while header := archiveFile.read(60):
            if len(header) != 60 or header[58:60] != b"`\n":
                raise ValueError("Malformed Debian archive member")
            memberName = header[:16].decode().strip().rstrip("/")
            memberSize = int(header[48:58])
            if memberSize < 0:
                raise ValueError("Invalid Debian archive member size")
            if memberName.startswith("control.tar"):
                if memberSize > 16 * 1024 * 1024:
                    raise ValueError("Unexpectedly large Debian control archive")
                memberData = archiveFile.read(memberSize)
                with tarfile.open(fileobj=io.BytesIO(memberData)) as controlArchive:
                    for member in controlArchive:
                        if member.name.removeprefix("./") == "control" and member.isfile():
                            if member.size > 1024 * 1024:
                                raise ValueError("Unexpectedly large Debian control metadata")
                            controlData = controlArchive.extractfile(member).read().decode()
            else:
                if memberName.startswith("data.tar") and memberSize > 0:
                    hasPayload = True
                archiveFile.seek(memberSize, 1)
            if memberSize % 2:
                archiveFile.seek(1, 1)
            if archiveFile.tell() > archivePath.stat().st_size:
                raise ValueError("Truncated Debian archive")
    if not controlData or not hasPayload:
        raise ValueError("Debian archive lacks control metadata or payload")
    fields = dict(line.split(": ", 1) for line in controlData.splitlines()
                  if ": " in line and not line.startswith(" "))
    if (fields.get("Package"), fields.get("Version"), fields.get("Architecture")) != (
        "ednovas-cloud", version, architecture
    ):
        raise ValueError("Debian package identity does not match the advertised release")


def HashFile(filePath):
    with filePath.open("rb") as sourceFile:
        digest = hashlib.file_digest(sourceFile, "sha256").digest()
    return "sha256-" + base64.b64encode(digest).decode()


def UpdateRelease(manifestPath, write=False):
    manifest = json.loads(manifestPath.read_text())
    currentVersion = manifest["version"]
    VersionKey(currentVersion)
    with tempfile.TemporaryDirectory(prefix="ednovas-update-") as temporaryDirectory:
        workPath = Path(temporaryDirectory)
        pagePath = workPath / "downloads.html"
        Download(downloadPage, pagePath)
        latestVersion, latestSources = FindLatest(
            pagePath.read_text(), [source["debArchitecture"] for source in manifest["sources"].values()]
        )
        hasUpdate = VersionKey(latestVersion) > VersionKey(currentVersion)
        result = {"currentVersion": currentVersion, "latestVersion": latestVersion,
                  "updateAvailable": hasUpdate, "changed": False}
        if not write or not hasUpdate:
            return result
        candidate = {"version": latestVersion, "sources": {}}
        for system, source in manifest["sources"].items():
            architecture = source["debArchitecture"]
            archivePath = workPath / f"{architecture}.deb"
            sourceUrl = latestSources[architecture]
            print(f"Downloading and validating {latestVersion} ({architecture})", file=sys.stderr)
            Download(sourceUrl, archivePath)
            ValidateDeb(archivePath, latestVersion, architecture)
            candidate["sources"][system] = {
                **source, "url": sourceUrl, "hash": HashFile(archivePath),
            }
        # Every architecture must succeed before the manifest is replaced.
        with tempfile.NamedTemporaryFile(mode="w", dir=manifestPath.parent,
                                         prefix=".release-", delete=False) as outputFile:
            temporaryPath = Path(outputFile.name)
            json.dump(candidate, outputFile, indent=2)
            outputFile.write("\n")
        try:
            temporaryPath.replace(manifestPath)
        finally:
            temporaryPath.unlink(missing_ok=True)
        result["changed"] = True
        return result


def Main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Report the latest release without writing (default)")
    mode.add_argument("--write", action="store_true", help="Download, validate, and update the release manifest")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Packaging repository (default: current directory)")
    args = parser.parse_args()
    try:
        result = UpdateRelease(args.repo.resolve() / "nix/release.json", args.write)
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Update failed: {error}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    Main()
