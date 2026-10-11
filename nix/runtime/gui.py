import csv
import io
import json
import re
import shlex
import time
import uuid
from pathlib import Path
from PIL import Image, ImageOps

screenCount = 0


def Phase(name):
    Path(machine.out_dir, "phase.json").write_text(json.dumps({"phase": name}))


def User(command, user="alice"):
    return machine.succeed(f"su - {user} -c {shlex.quote('runtime-ui ' + command)}", timeout=30)


def Windows():
    return json.loads(User("windows"))


def Wait(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(1)
    raise AssertionError("Runtime assertion exceeded deadline")


def Screen():
    global screenCount
    screenCount += 1
    imagePath = Path(machine.out_dir, f"screen-{screenCount:03}.png")
    machine.screenshot(str(imagePath))
    ocrPath = imagePath.with_suffix(".ocr.png")
    with Image.open(imagePath) as source:
        image = ImageOps.invert(source.convert("L"))
        image = image.resize((image.width * 3, image.height * 3), Image.Resampling.LANCZOS)
        image.save(ocrPath)
    machine.copy_from_host(str(ocrPath), "/tmp/screen.png")
    text = machine.succeed("tesseract /tmp/screen.png stdout -l chi_sim+eng --psm 11 tsv 2>/tmp/ocr.log")
    imagePath.with_suffix(".tsv").write_text(text)
    lines = {}
    for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
        if row.get("text", "").strip():
            key = (row["block_num"], row["par_num"], row["line_num"])
            lines.setdefault(key, []).append(row)
    return [{"text": "".join(row["text"] for row in rows),
             "x": min(int(row["left"]) for row in rows) // 3,
             "y": min(int(row["top"]) for row in rows) // 3,
             "right": max(int(row["left"]) + int(row["width"]) for row in rows) // 3,
             "bottom": max(int(row["top"]) + int(row["height"]) for row in rows) // 3}
            for rows in lines.values()]


def Login(timeout=120):
    emailLines = []

    def Rendered():
        lines = Screen()
        emailLines[:] = [line for line in lines
                         if re.search("邮箱|gui.*example", line["text"], re.I)]
        return bool(emailLines) and all(
            any(re.search(pattern, line["text"], re.I) for line in lines)
            for pattern in ["EdNovas", "忘记密码"])

    Wait(Rendered, timeout)
    line = emailLines[0]
    return (line["x"] + line["right"]) // 2, (line["y"] + line["bottom"]) // 2


def Keyboard(args):
    # Native X keyboard events reach either the X11 client or niri's winit
    # surface, which forwards them to the actual Wayland renderer/agent.
    command = "DISPLAY=:0 XAUTHORITY=/home/alice/.Xauthority xdotool " + args
    machine.succeed("su - alice -c " + shlex.quote(command))


def Interact(emailPoint):
    # A fresh clipboard sentinel and unique input prevent a stale selection
    # from satisfying this real renderer keyboard round trip.
    email = "gui" + uuid.uuid4().hex[:8] + "@example.test"
    User("clear-clipboard")
    Keyboard(f"mousemove --sync {emailPoint[0]} {emailPoint[1]} click 1")
    Keyboard("key --clearmodifiers ctrl+a")
    Keyboard("type --clearmodifiers --delay 20 -- " + shlex.quote(email))
    Keyboard("key --clearmodifiers ctrl+a ctrl+c")
    Wait(lambda: User("clipboard").strip() == email)
    Keyboard("key Tab")
    machine.screenshot("interaction-" + email.split("@")[0] + ".png")


def MainProcess(root):
    # Match the real Electron main executable, excluding FHS shells/renderers.
    source = '''import json, os
from pathlib import Path
items=[]
for p in Path('/proc').glob('[0-9]*'):
 try:
  args=(p/'cmdline').read_bytes().replace(b'\\0',b' ').decode()
  exe=os.readlink(p/'exe')
  if exe.endswith('/ednovas-cloud') and '--type=' not in args and p.stat().st_uid == UID:
   items.append({'pid':int(p.name),'start':(p/'stat').read_text().split()[21]})
 except (OSError,ValueError): pass
print(json.dumps(items))'''.replace("UID", "0" if root else "1000")
    return json.loads(machine.succeed("python3 -c " + shlex.quote(source)))


def Alive(process):
    return process in MainProcess(True)


def Stable(process, root):
    deadline = time.monotonic() + 30
    while True:
        assert process in MainProcess(root), "Original application process exited"
        assert len(Windows()) == 1, "Primary window lost or duplicated"
        if root:
            User("tray")
            machine.succeed("test $(find /run/user/1000 -path '*/ednovas-bus.*/control' -type s | wc -l) -eq 1")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(3, remaining))


def Launch(root, suffix):
    command = "ednovas-cloud-tun" if root else "ednovas-cloud --user-data-dir=/home/alice/ordinary"
    flags = f" --disable-gpu --ozone-platform={'wayland' if desktop == 'wayland' else 'x11'} --enable-logging=stderr"
    User(f"run sh -c {shlex.quote(command + flags + ' > /home/alice/' + suffix + '.log 2>&1 &')}")
    if root:
        Phase("authentication")
        machine.wait_until_succeeds("su - alice -c 'runtime-ui auth-ready'", timeout=120)
        Keyboard("type --clearmodifiers --delay 80 vm-admin-password")
        Keyboard("key Return")
    Phase("render")
    Keyboard("mousemove 15 740")
    emailPoint = Login(120)
    Wait(lambda: len(Windows()) == 1, 120)
    Wait(lambda: len(MainProcess(root)) == 1, 120)
    process = MainProcess(root)[0]
    if root:
        machine.wait_until_succeeds("su - alice -c 'runtime-ui tray'", timeout=120)
        machine.wait_until_succeeds("test $(find /run/user/1000 -path '*/ednovas-bus.*/control' -type s | wc -l) -eq 1", timeout=120)
    Stable(process, root)
    Interact(emailPoint)
    return process, Windows()[0]["id"], emailPoint


def Cycle(process, originalWindow, emailPoint):
    Phase("hide")
    assert len(Windows()) == 1
    User("close")
    Wait(lambda: len(Windows()) == 0)
    if desktop == "x11":
        User("run xwininfo -id " + shlex.quote(str(originalWindow)))
    for _ in range(3):
        assert Alive(process), "Close exited original application"
        User("tray")
        machine.succeed("test $(find /run/user/1000 -path '*/ednovas-bus.*/control' -type s | wc -l) -eq 1; pgrep -u alice -f tray.py; pgrep -u alice -f xdg-dbus-proxy")
        time.sleep(1)
    Phase("restore")
    User("activate")
    Wait(lambda: len(Windows()) == 1 and Windows()[0].get("is_focused", False))
    assert Alive(process)
    if desktop == "x11":
        assert Windows()[0]["id"] == originalWindow
    Interact(emailPoint)


def Quit(process):
    Phase("quit")
    User("quit")
    Wait(lambda: not Alive(process))
    Wait(lambda: not Windows())
    machine.wait_until_fails("pgrep -u alice -f 'tray.py|xdg-dbus-proxy'", timeout=30)
    machine.succeed("test -z \"$(find /run/user/1000 -maxdepth 1 -name 'ednovas-bus.*')\"")
    machine.succeed("test -z \"$(find /run/user/1000 -path '*/ednovas-bus.*/control')\"")
    machine.fail("su - alice -c 'runtime-ui tray'")


try:
    machine.start()
    machine.wait_for_unit("multi-user.target", timeout=120)
    machine.wait_for_file("/home/alice/session.json", timeout=120)
    machine.wait_until_succeeds("pgrep -u alice -f lxqt-policykit-agent", timeout=120)
    if desktop == "wayland":
        machine.wait_until_succeeds("su - alice -c 'DISPLAY=:0 XAUTHORITY=/home/alice/.Xauthority wmctrl -r niri -b add,fullscreen && wmctrl -a niri'", timeout=120)
    ordinaryProcess, _, _ = Launch(False, "ordinary")
    machine.succeed(f"kill {ordinaryProcess['pid']}")
    machine.wait_until_fails(f"kill -0 {ordinaryProcess['pid']}", timeout=30)
    Wait(lambda: not Windows())
    process, originalWindow, emailPoint = Launch(True, "privileged")
    machine.succeed("test $(stat -c '%U:%a' /var/lib/ednovas-cloud/1000) = root:700; test $(stat -c '%U:%a' /run/ednovas-cloud/1000) = root:700")
    for name in ["home", "config", "cache", "data", "profile"]:
        machine.succeed(f"test $(stat -c '%U:%a' /var/lib/ednovas-cloud/1000/{name}) = root:700")
    machine.succeed("test -z \"$(find /var/lib/ednovas-cloud/1000 ! -user root)\"; test -d /home/alice/ordinary; test -z \"$(find /home/alice ! -user alice)\"")
    for _ in range(3):
        Cycle(process, originalWindow, emailPoint)
    Quit(process)
    process, originalWindow, emailPoint = Launch(True, "relaunch")
    Cycle(process, originalWindow, emailPoint)
    Quit(process)
    Phase("denial")
    # Give the non-member the same desktop transport; pkexec still uses the
    # caller's real UID and the module's installed policy.
    machine.succeed("chmod a+x /home/alice; chmod a+r /home/alice/session.json")
    machine.succeed("systemctl start getty@tty2")
    machine.send_key("ctrl-alt-f2")
    machine.wait_until_succeeds("loginctl show-user outsider -p State --value | grep '^active$'", timeout=120)
    machine.fail("timeout 30 su - outsider -c 'runtime-ui run ednovas-cloud-tun --disable-gpu' > /tmp/denied.log 2>&1")
    machine.succeed("grep -i 'not authorized' /tmp/denied.log")
    assert not MainProcess(True)
    machine.succeed("test ! -e /var/lib/ednovas-cloud/1001; test -z \"$(find /run/user/1001 -name 'ednovas-bus.*' 2>/dev/null)\"")
    Path(machine.out_dir, "suite.json").write_text(json.dumps({"suite": desktop, "result": "success"}))
finally:
    # Collect each diagnostic independently so one failed command cannot hide
    # earlier evidence or replace the original assertion failure.
    for command, name in [("journalctl -b", "journal"), ("ps auxf", "processes"),
                          ("su - alice -c 'runtime-ui inventory'", "windows"),
                          ("su - alice -c 'runtime-ui tray'", "tray")]:
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
