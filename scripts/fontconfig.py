"""Expose desktop font preferences without sharing privileged application state."""
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def WriteFontConfiguration(destination, configHome, dataHome):
    root = ET.Element("fontconfig")
    # Loaded through 50-user.conf, preserving the desktop's alias ordering.
    for includePath in [configHome / "fontconfig/conf.d",
                        configHome / "fontconfig/fonts.conf"]:
        ET.SubElement(root, "include", {"ignore_missing": "yes"}).text = str(includePath)
    ET.SubElement(root, "dir").text = str(dataHome / "fonts")
    ET.indent(root)
    destination.write_bytes(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    destination.chmod(0o600)


if __name__ == "__main__":
    home = Path.home()
    configHome = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    dataHome = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    if not configHome.is_absolute() or not dataHome.is_absolute():
        sys.exit("Font configuration requires absolute XDG directory paths")
    WriteFontConfiguration(Path(sys.argv[1]), configHome, dataHome)
