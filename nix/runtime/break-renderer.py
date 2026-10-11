"""Acceptance-only renderer fault; the resulting package must still build."""
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("asar", sys.argv[2])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
archivePath = Path(sys.argv[1])
header, contents = module.ReadArchive(archivePath.read_bytes())
assert "dist/index.html" in contents
contents["dist/index.html"] = b"<!doctype html><html><body>Renderer deliberately unavailable</body></html>"
archivePath.write_bytes(module.WriteArchive(header, contents))
