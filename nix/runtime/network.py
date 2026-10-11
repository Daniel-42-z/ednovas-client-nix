import json
import shlex
import time
import uuid
from pathlib import Path


def Phase(name):
    Path(machine.out_dir, "phase.json").write_text(json.dumps({"phase": name}))


def Request(proxy=""):
    token = uuid.uuid4().hex
    url = f"http://203.0.113.2:18080/{token}"
    output = machine.succeed(f"env -u http_proxy -u HTTP_PROXY -u https_proxy -u HTTPS_PROXY -u ALL_PROXY -u all_proxy -u NO_PROXY -u no_proxy curl --fail --max-time 10 {proxy} {url}")
    assert output.strip() == f"ednovas-fixture:/{token}", output
    return token


def StopCore(coreName):
    # Let the actual core complete its signal handler while its FHS parent is
    # alive. Killing the entire cgroup can terminate the namespace init first.
    source = """import os, signal
from pathlib import Path
pids=[]
for path in Path('/proc').glob('[0-9]*'):
 try:
  if os.readlink(path / 'exe').endswith('/resources/bin/CORE'):
   pids.append(int(path.name))
 except OSError: pass
assert len(pids) == 1, pids
os.kill(pids[0], signal.SIGTERM)
""".replace("CORE", coreName)
    machine.succeed("python3 -c " + shlex.quote(source))
    machine.wait_until_fails("systemctl is-active runtime-core", timeout=30)


def Routing():
    return {key: json.loads(machine.succeed(f"ip -j {command}")) for key, command in
            [("routes", "route show table all"), ("rules", "rule show"), ("links", "link show")]}


def CleanState(state):
    # Interface flags/counters can change while traffic flows; compare route/rule
    # identity and interface names, not volatile fields.
    return {"routes": sorted(({key: value for key, value in route.items() if key != "expires"} for route in state["routes"]), key=lambda route: json.dumps(route, sort_keys=True)), "rules": state["rules"],
            "links": sorted(link["ifname"] for link in state["links"])}


try:
    start_all()
    machine.wait_for_unit("multi-user.target", timeout=120)
    fixture.wait_for_unit("fixture.service", timeout=120)
    machine.succeed("ip route add 203.0.113.2/32 via $(getent ahostsv4 fixture | head -1 | cut -d' ' -f1)")
    machine.wait_until_succeeds("ip -4 route show default | grep -q default", timeout=120)
    Request("--noproxy '*'")
    baseline = CleanState(Routing())
    machine.succeed("cp /etc/runtime/core.yaml /tmp/core.yaml")
    for coreName in ["EdNovas-Core", "EdNovas-Core-compatible"]:
        with subtest(coreName):
            machine.succeed(f"mkdir -p /tmp/{coreName}; runtime-{coreName} -v | tee /tmp/{coreName}/version.txt | grep -i mihomo")
            machine.succeed(f"systemd-run --unit=runtime-core --collect runtime-{coreName} -d /tmp/{coreName} -f /tmp/core.yaml")
            machine.wait_until_succeeds("ip link show ednovas-test && curl -fsS --max-time 2 http://127.0.0.1:19090/version", timeout=120)
            for _ in range(30):
                machine.succeed("systemctl is-active runtime-core; ip link show ednovas-test")
                time.sleep(1)
            Phase("proxy")
            Request("--noproxy '' --proxy http://127.0.0.1:17890")
            Request("--noproxy '' --proxy socks5h://127.0.0.1:17891")
            Phase("tun")
            # The fixture needs a specific LAN route for DIRECT sockets. Override
            # its usual bypass for unmarked clients, while marked core sockets
            # retain the direct route and cannot loop into their own TUN.
            machine.succeed("ip rule add pref 10 fwmark 6666 lookup main; ip rule add pref 11 to 203.0.113.2/32 lookup 2022")
            machine.succeed(f"systemd-run --unit=runtime-capture --collect tcpdump -U -n -i ednovas-test -w /tmp/{coreName}/tun.pcap tcp port 18080")
            machine.wait_until_succeeds("pgrep tcpdump", timeout=30)
            time.sleep(1)
            token = Request("--noproxy '*'")
            machine.wait_until_succeeds(
                f"tcpdump -nn -A -r /tmp/{coreName}/tun.pcap > /tmp/{coreName}/tun.txt 2>/dev/null && "
                f"grep -F 'GET /{token}' /tmp/{coreName}/tun.txt && grep -F 'ednovas-fixture:/{token}' /tmp/{coreName}/tun.txt",
                timeout=30,
            )
            machine.succeed("systemctl stop runtime-capture")
            packets = machine.succeed(f"tcpdump -nn -A -r /tmp/{coreName}/tun.pcap")
            assert f"GET /{token}" in packets and f"ednovas-fixture:/{token}" in packets, packets
            machine.succeed(f"ip -j route show table all > /tmp/{coreName}/routes.json; ip -j rule > /tmp/{coreName}/rules.json; journalctl -u runtime-core > /tmp/{coreName}/core.log")
            StopCore(coreName)
            machine.wait_until_fails("ip link show ednovas-test", timeout=30)
            Request("--noproxy '*'")
            machine.succeed("ip rule del pref 10; ip rule del pref 11")
            assert CleanState(Routing()) == baseline, Routing()
            Request("--noproxy '*'")
            machine.fail("curl -fsS --max-time 2 --noproxy '' --proxy http://127.0.0.1:17890 http://203.0.113.2:18080/stopped")
    Path(machine.out_dir, "suite.json").write_text(json.dumps({"suite": "network", "result": "success"}))
finally:
    for node in [machine, fixture]:
        try:
            node.execute("journalctl -b > /tmp/journal.txt; ps auxf > /tmp/processes.txt; ip route show table all > /tmp/routes.txt; ip rule > /tmp/rules.txt; ip addr > /tmp/interfaces.txt", timeout=30)
            for path in ["/tmp/journal.txt", "/tmp/processes.txt", "/tmp/routes.txt", "/tmp/rules.txt", "/tmp/interfaces.txt"]:
                node.copy_from_machine(path, node.name)
        except Exception as error:
            Path(machine.out_dir, node.name + "-diagnostic-error.txt").write_text(str(error))
    for coreName in ["EdNovas-Core", "EdNovas-Core-compatible"]:
        try:
            if machine.execute(f"test -d /tmp/{coreName}", timeout=30)[0] == 0:
                machine.copy_from_machine(f"/tmp/{coreName}")
        except Exception as error:
            Path(machine.out_dir, coreName + "-diagnostic-error.txt").write_text(str(error))
