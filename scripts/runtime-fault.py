"""Apply a deliberate fault only in a disposable acceptance-workflow checkout."""
from pathlib import Path
import sys

fault = sys.argv[1]


def Replace(path, old, new):
    filePath = Path(path)
    data = filePath.read_text()
    assert data.count(old) == 1, f"Fault target changed: {path}"
    filePath.write_text(data.replace(old, new))


if fault in {"close", "restore", "quit", "renderer"}:
    Replace("nix/package.nix", 'test -s "$out/resources/app.asar"',
            'python3 ${../nix/runtime/fault.py} "$out/resources/app.asar" ' + fault
            + '\n        test -s "$out/resources/app.asar"')
elif fault == "stopped-core":
    Replace("nix/runtime/network.py", 'Request("--noproxy \'\' --proxy http://127.0.0.1:17890")',
            'machine.succeed("systemctl stop runtime-core")\n            Request("--noproxy \'\' --proxy http://127.0.0.1:17890")')
elif fault == "bypass-tun":
    Replace("nix/runtime/network.py", 'token = Request("--noproxy \'*\'")',
            'machine.succeed("ip rule add pref 1 to 203.0.113.2/32 lookup main")\n            token = Request("--noproxy \'*\'")')
else:
    sys.exit("Unknown fault")
