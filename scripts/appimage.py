"""Validate a type-2 AppImage and report its SquashFS offset without executing it."""

from pathlib import Path
import struct
import sys


def ValidateAppImage(archivePath, architecture):
    with archivePath.open("rb") as archiveFile:
        header = archiveFile.read(64)
        if (len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01"
                or header[8:11] != b"AI\x02"):
            raise ValueError("Download is not a little-endian ELF64 type-2 AppImage")
        expectedMachine = {"x86_64": 62, "arm64": 183}[architecture]
        if struct.unpack_from("<H", header, 18)[0] != expectedMachine:
            raise ValueError("AppImage architecture does not match the advertised release")
        sectionOffset = struct.unpack_from("<Q", header, 40)[0]
        sectionSize, sectionCount = struct.unpack_from("<HH", header, 58)
        offset = sectionOffset + sectionSize * sectionCount
        if sectionSize != 64 or sectionCount == 0 or offset < 64:
            raise ValueError("Unsupported AppImage ELF section layout")
        archiveFile.seek(offset)
        superblock = archiveFile.read(96)
        if len(superblock) != 96 or superblock[:4] != b"hsqs":
            raise ValueError("AppImage has no valid SquashFS superblock")
        if struct.unpack_from("<HH", superblock, 28) != (4, 0):
            raise ValueError("Unsupported SquashFS version")
        bytesUsed = struct.unpack_from("<Q", superblock, 40)[0]
        if bytesUsed < 96 or offset + bytesUsed > archivePath.stat().st_size:
            raise ValueError("Truncated AppImage filesystem")
        return offset


if __name__ == "__main__":
    try:
        print(ValidateAppImage(Path(sys.argv[1]), sys.argv[2]))
    except (OSError, ValueError, KeyError, IndexError) as error:
        sys.exit(f"Invalid AppImage: {error}")
