"""Inject acceptance-only faults into a disposable upstream archive."""
import hashlib
import json
from pathlib import Path
import struct
import sys


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


def InjectFault(archiveData, fault):
    header, contents = ReadArchive(archiveData)
    if fault == "renderer":
        assert "dist/index.html" in contents
        contents["dist/index.html"] = b"<!doctype html><html><body>Renderer deliberately unavailable</body></html>"
    else:
        hooks = {
            "close": "app.on('browser-window-created', (_event, window) => { window.prependListener('close', event => { event.preventDefault = () => {}; }); });",
            "restore": "app.on('browser-window-created', (_event, window) => { window.on('hide', () => { window.show = () => {}; }); });",
            "quit": "app.on('before-quit', event => event.preventDefault()); app.exit = () => {};",
        }
        loader = contents["dist-electron/main.js"]
        assert loader.strip() == b"'use strict';\nrequire('bytenode');\nrequire('./main.jsc');"
        contents["dist-electron/main.js"] = (
            b"'use strict';\nconst { app } = require('electron');\n"
            + hooks[fault].encode() + b"\n" + loader
        )
    return WriteArchive(header, contents)


if __name__ == "__main__":
    archivePath = Path(sys.argv[1])
    archivePath.write_bytes(InjectFault(archivePath.read_bytes(), sys.argv[2]))
