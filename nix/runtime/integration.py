import json
import shlex
from pathlib import Path

fontPatterns = ["sans-serif", "sans-serif:lang=zh-cn:charset=4e91", "Symbols Nerd Font:charset=f013"]
recordPath = "/var/lib/ednovas-cloud/1000/config/runtime-record.json"


def Phase(name):
    Path(machine.out_dir, "phase.json").write_text(json.dumps({"phase": name}))


def User(command, user="alice"):
    return machine.succeed(f"su - {user} -c {shlex.quote('runtime-ui ' + command)}", timeout=30)


def Keyboard(args):
    machine.succeed("su - alice -c " + shlex.quote(
        "DISPLAY=:0 XAUTHORITY=/home/alice/.Xauthority xdotool " + args))


def CleanProxy(uid=1000):
    machine.succeed(f"test -z \"$(find /run/user/{uid} -name 'ednovas-bus.*')\"")
    machine.fail(f"pgrep -u {uid} -x xdg-dbus-proxy")


def Launch(name, authenticate=False, cancel=False, flags="--disable-gpu --ozone-platform=x11 --v=2"):
    machine.succeed(f"rm -f {recordPath} /home/alice/launch.status")
    command = f"ednovas-cloud-tun {flags} > /home/alice/{name}.log 2>&1; echo $? > /home/alice/launch.status"
    User("run sh -c " + shlex.quote("(" + command + ") &"))
    if authenticate:
        machine.wait_until_succeeds("su - alice -c 'runtime-ui auth-ready'", timeout=120)
        # No recording executable may start before successful authorization.
        machine.fail(f"test -e {recordPath}")
        if cancel:
            Keyboard("key Escape")
            # The pinned agent requires acknowledging its failure dialog
            # before it completes the cancelled authorization request.
            machine.wait_until_succeeds("su - alice -c 'runtime-ui auth-failed'", timeout=30)
            Keyboard("key Return")
        else:
            Keyboard("type --clearmodifiers --delay 80 vm-admin-password")
            Keyboard("key Return")
    machine.wait_for_file("/home/alice/launch.status", timeout=120)
    status = int(machine.succeed("cat /home/alice/launch.status").strip())
    CleanProxy()
    if cancel or flags == "--remote-debugging-port=9222":
        assert status != 0, "Rejected launch unexpectedly succeeded"
        machine.fail(f"test -e {recordPath}")
        return
    assert status == 0, machine.succeed(f"cat /home/alice/{name}.log")
    record = json.loads(machine.succeed(f"cat {recordPath}"))
    Path(machine.out_dir, name + ".json").write_text(json.dumps(record, indent=2))
    assert record["uid"] == 0
    assert record["cwd"] == "/var/lib/ednovas-cloud/1000"
    assert record["argv"] == ["--user-data-dir=/var/lib/ednovas-cloud/1000/profile", *shlex.split(flags)]
    environment = record["environment"]
    for key, value in {
        "HOME": "/var/lib/ednovas-cloud/1000/home",
        "XDG_CONFIG_HOME": "/var/lib/ednovas-cloud/1000/config",
        "XDG_DATA_HOME": "/var/lib/ednovas-cloud/1000/data",
        "XDG_CACHE_HOME": "/var/lib/ednovas-cloud/1000/cache",
        "XDG_RUNTIME_DIR": "/run/ednovas-cloud/1000",
        "PKEXEC_UID": "1000", "DISPLAY": ":0",
    }.items():
        assert environment[key] == value, (key, environment)
    assert environment["DBUS_SESSION_BUS_ADDRESS"].startswith("unix:path=/run/user/1000/ednovas-bus.")
    assert "FONTCONFIG_FILE" not in environment
    assert record["busId"] == User("bus-id").strip(), "Privileged process reached a different bus"
    assert record["fonts"] == callerFonts, (record["fonts"], callerFonts)
    for directory in ["", "/home", "/config", "/cache", "/data", "/profile"]:
        machine.succeed("test $(stat -c '%U:%a' /var/lib/ednovas-cloud/1000" + directory + ") = root:700")
    machine.succeed("test $(stat -c '%U:%a' /run/ednovas-cloud/1000) = root:700")
    machine.succeed("test -z \"$(find /var/lib/ednovas-cloud/1000 ! -user root)\"; test -z \"$(find /home/alice ! -user alice)\"")


def Denied():
    machine.succeed("chmod a+x /home/alice; chmod a+r /home/alice/session.json")
    machine.succeed("systemctl start getty@tty2")
    machine.send_key("ctrl-alt-f2")
    machine.wait_until_succeeds("loginctl show-user outsider -p State --value | grep '^active$'", timeout=120)
    machine.fail("timeout 30 su - outsider -c 'runtime-ui run ednovas-cloud-tun --disable-gpu' > /tmp/denied.log 2>&1")
    machine.succeed("grep -i 'not authorized' /tmp/denied.log")
    machine.succeed("test ! -e /var/lib/ednovas-cloud/1001")
    CleanProxy(1001)
    machine.send_key("ctrl-alt-f1")
    machine.wait_until_succeeds("loginctl show-user alice -p State --value | grep '^active$'", timeout=120)


try:
    machine.start()
    machine.wait_for_unit("multi-user.target", timeout=120)
    machine.wait_for_file("/home/alice/session.json", timeout=120)
    machine.wait_until_succeeds("pgrep -u alice -f lxqt-policykit-agent", timeout=120)
    Phase("fonts")
    assert not machine.succeed("fc-list -f '%{file}\\n' ':charset=4e91'").strip(), "Fixture installed system CJK fonts"
    callerFonts = {pattern: machine.succeed("su - alice -c " + shlex.quote(
        "fc-match -f '%{family}|%{file}\\n' " + shlex.quote(pattern))).strip()
        for pattern in fontPatterns}
    assert "Liberation Sans" in callerFonts[fontPatterns[0]]
    assert "Noto Sans CJK SC" in callerFonts[fontPatterns[1]]
    assert "Symbols Nerd Font" in callerFonts[fontPatterns[2]]
    User("run " + recordingCommand)
    ordinary = json.loads(machine.succeed("cat /home/alice/.config/runtime-record.json"))
    assert ordinary["uid"] == 1000
    assert ordinary["fonts"] == callerFonts
    assert ordinary["busId"] == User("bus-id").strip()
    Phase("authentication")
    Launch("authenticated", authenticate=True)
    Phase("cancellation")
    Launch("cancelled", authenticate=True, cancel=True)
    Phase("denial")
    Denied()
    Phase("passwordless")
    machine.succeed("/run/current-system/specialisation/passwordless/bin/switch-to-configuration test")
    Launch("passwordless")
    Phase("invalid-arguments")
    Launch("invalid-arguments", flags="--remote-debugging-port=9222")
    Phase("passwordless-denial")
    Denied()
    Path(machine.out_dir, "suite.json").write_text(json.dumps({"suite": "integration", "result": "success"}))
finally:
    for command, name in [("journalctl -b", "journal"), ("ps auxf", "processes"),
                          ("su - alice -c 'runtime-ui inventory'", "windows")]:
        try:
            status, output = machine.execute(command)
            Path(machine.out_dir, name + ".txt").write_text(f"exit={status}\n{output}")
        except Exception as error:
            Path(machine.out_dir, name + ".txt").write_text(str(error))
    try:
        machine.screenshot("final.png")
        machine.copy_from_machine("/home/alice", "profile-logs")
        machine.copy_from_machine("/tmp/denied.log")
    except Exception:
        pass
