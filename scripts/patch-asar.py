"""Insert a reviewed loader shim without rewriting upstream V8 bytecode."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


def ReadArchive(archiveData):
    if len(archiveData) < 16:
        raise ValueError("Truncated ASAR header")
    headerLength = struct.unpack_from("<I", archiveData, 12)[0]
    header = json.loads(archiveData[16:16 + headerLength])
    dataOffset = 8 + struct.unpack_from("<I", archiveData, 4)[0]
    contents = {}

    def ReadFiles(files, prefix=""):
        for fileName, entry in files.items():
            filePath = prefix + fileName
            if "files" in entry:
                ReadFiles(entry["files"], filePath + "/")
            elif "offset" in entry and not entry.get("unpacked"):
                start = dataOffset + int(entry["offset"])
                contents[filePath] = archiveData[start:start + entry["size"]]
                if len(contents[filePath]) != entry["size"]:
                    raise ValueError(f"Truncated ASAR member: {filePath}")

    ReadFiles(header["files"])
    return header, contents


def WriteArchive(header, contents):
    dataParts = []
    dataOffset = 0

    def WriteFiles(files, prefix=""):
        nonlocal dataOffset
        for fileName, entry in files.items():
            filePath = prefix + fileName
            if "files" in entry:
                WriteFiles(entry["files"], filePath + "/")
            elif filePath in contents:
                fileData = contents[filePath]
                entry["offset"] = str(dataOffset)
                entry["size"] = len(fileData)
                if "integrity" in entry:
                    blockSize = entry["integrity"]["blockSize"]
                    entry["integrity"] = {
                        "algorithm": "SHA256",
                        "hash": hashlib.sha256(fileData).hexdigest(),
                        "blockSize": blockSize,
                        "blocks": [hashlib.sha256(fileData[start:start + blockSize]).hexdigest()
                                   for start in range(0, len(fileData), blockSize)],
                    }
                dataParts.append(fileData)
                dataOffset += len(fileData)

    WriteFiles(header["files"])
    headerData = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode()
    payload = struct.pack("<I", len(headerData)) + headerData
    payload += b"\0" * (-len(payload) % 4)
    headerPickle = struct.pack("<I", len(payload)) + payload
    return struct.pack("<II", 4, len(headerPickle)) + headerPickle + b"".join(dataParts)


def PatchArchive(archiveData, shimData, version, tunSupport):
    header, contents = ReadArchive(archiveData)
    packageData = json.loads(contents["package.json"])
    if packageData["version"] != version or packageData["main"] != "dist-electron/main.js":
        raise ValueError("Upstream version or entrypoint changed; review the integration")
    loader = contents["dist-electron/main.js"].decode().strip()
    if loader != "'use strict';\nrequire('bytenode');\nrequire('./main.jsc');":
        raise ValueError("Upstream loader changed; review the integration")
    bytecode = contents["dist-electron/main.jsc"]
    for requiredString in (
        b"EdNovas-Core", b"check-is-admin", b"check-tun-service-available",
        b"relaunch-as-admin", b"start-clash-service-secure", b"spawn",
    ):
        if requiredString not in bytecode:
            raise ValueError(f"Upstream integration point disappeared: {requiredString!r}")
    contents["dist-electron/main.js"] = (
        b"'use strict';\nconst nixTunSupport = "
        + (b"true" if tunSupport else b"false") + b";\n"
        + shimData + b"\nrequire('bytenode');\nrequire('./main.jsc');\n"
    )
    return WriteArchive(header, contents)


def Main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("shim", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tun-support", action="store_true")
    args = parser.parse_args()
    patchedData = PatchArchive(args.archive.read_bytes(), args.shim.read_bytes(),
                               args.version, args.tun_support)
    args.archive.write_bytes(patchedData)


if __name__ == "__main__":
    Main()
