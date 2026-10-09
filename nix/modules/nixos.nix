{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.ednovas-cloud;
  rootRuntime = cfg.package.override { privileged = true; };
  helperSource = pkgs.replaceVars ../../scripts/root-helper.py {
    application = lib.getExe rootRuntime;
    runtimePath = lib.makeBinPath [
      pkgs.coreutils
      pkgs.procps
      pkgs.psmisc
    ];
  };
  trayPython = pkgs.python3.withPackages (pythonPkgs: [
    pythonPkgs.dbus-next
    pythonPkgs.pillow
  ]);
  rootHelper = pkgs.writeShellScriptBin "ednovas-cloud-root" ''
    exec ${pkgs.python3}/bin/python3 -I ${helperSource} "$@"
  '';
  actionId = "org.ednovas.cloud.tun";
  tunLauncher = pkgs.writeShellScriptBin "ednovas-cloud-tun" ''
    set -euo pipefail
    if [[ -z "''${XAUTHORITY:-}" && -f "$HOME/.Xauthority" ]]; then
      export XAUTHORITY="$HOME/.Xauthority"
    fi
    : "''${XDG_RUNTIME_DIR:?A desktop runtime directory is required}"
    : "''${DBUS_SESSION_BUS_ADDRESS:?A desktop session bus is required}"
    proxyDirectory=$(${pkgs.coreutils}/bin/mktemp -d "$XDG_RUNTIME_DIR/ednovas-bus.XXXXXXXX")
    proxySocket="$proxyDirectory/bus"
    ${pkgs.xdg-dbus-proxy}/bin/xdg-dbus-proxy "$DBUS_SESSION_BUS_ADDRESS" "$proxySocket" &
    proxyPid=$!
    trayPid=""
    cleanup() {
      if [[ -n "$trayPid" ]]; then
        kill "$trayPid" 2>/dev/null || true
        wait "$trayPid" 2>/dev/null || true
      fi
      kill "$proxyPid" 2>/dev/null || true
      wait "$proxyPid" 2>/dev/null || true
      ${pkgs.coreutils}/bin/rm -rf -- "$proxyDirectory"
    }
    trap cleanup EXIT
    for attempt in {1..100}; do
      if [[ -S "$proxySocket" ]]; then break; fi
      if ! kill -0 "$proxyPid" 2>/dev/null; then
        echo 'EdNovas: session bus proxy exited before becoming ready' >&2
        exit 1
      fi
      ${pkgs.coreutils}/bin/sleep 0.05
    done
    if [[ ! -S "$proxySocket" ]]; then
      echo 'EdNovas: session bus proxy did not become ready' >&2
      exit 1
    fi
    export EDNOVAS_DBUS_PROXY_SOCKET="$proxySocket"
    # The root GUI does not reliably publish a tray item on Wayland. Keep a
    # user-owned notifier alive for exactly the authenticated GUI's lifetime.
    ${trayPython}/bin/python3 ${../../scripts/tray.py} \
      ${cfg.package}/share/icons/hicolor/2048x2048/apps/ednovas-cloud.png "$proxyDirectory/control" &
    trayPid=$!
    sessionJson=$(${pkgs.python3}/bin/python3 -I -c 'import json, os; print(json.dumps({key: os.environ[key] for key in ["DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "LANG", "EDNOVAS_DBUS_PROXY_SOCKET"] if key in os.environ}))')
    /run/wrappers/bin/pkexec ${lib.getExe rootHelper} "$sessionJson" "$@"
  '';
  desktop = pkgs.makeDesktopItem {
    name = "ednovas-cloud-tun";
    desktopName = "EdNovas Cloud (Privileged TUN)";
    exec = lib.getExe tunLauncher;
    icon = "${cfg.package}/share/icons/hicolor/2048x2048/apps/ednovas-cloud.png";
    categories = [ "Network" ];
    comment = "Authenticate to run EdNovas GUI and network core as root";
  };
  policy = pkgs.runCommand "ednovas-cloud-polkit-policy" { } ''
    mkdir -p "$out/share/polkit-1/actions"
    cat > "$out/share/polkit-1/actions/${actionId}.policy" <<'POLICY'
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN" "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
    <policyconfig>
      <action id="${actionId}">
        <description>Run EdNovas Cloud with TUN privileges</description>
        <message>Administrator authentication is required to run EdNovas Cloud as root.</message>
        <defaults><allow_any>no</allow_any><allow_inactive>no</allow_inactive><allow_active>auth_admin</allow_active></defaults>
        <annotate key="org.freedesktop.policykit.exec.path">${lib.getExe rootHelper}</annotate>
      </action>
    </policyconfig>
    POLICY
  '';
in
{
  options.programs.ednovas-cloud = {
    enable = lib.mkEnableOption "EdNovas Cloud on NixOS";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ../package.nix { }";
      description = "AppImage package supporting the privileged override.";
    };
    tun = {
      enable = lib.mkEnableOption "authenticated root GUI and core launcher for TUN";
      group = lib.mkOption {
        type = lib.types.str;
        default = "ednovas-cloud";
        description = "Local desktop users allowed to request the privileged launcher; administrator authentication is still required.";
      };
    };
  };
  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      { environment.systemPackages = [ cfg.package ]; }
      (lib.mkIf cfg.tun.enable {
        environment.systemPackages = [
          tunLauncher
          desktop
          policy
        ];
        users.groups.${cfg.tun.group} = { };
        security.polkit.enable = true;
        security.polkit.enablePkexecWrapper = true;
        security.polkit.extraConfig = ''
          polkit.addRule(function(action, subject) {
            if (action.id === ${builtins.toJSON actionId}) {
              if (subject.local && subject.active && subject.isInGroup(${builtins.toJSON cfg.tun.group})) {
                return polkit.Result.AUTH_ADMIN;
              }
              return polkit.Result.NO;
            }
          });
        '';
        systemd.tmpfiles.rules = [ "d /var/lib/ednovas-cloud 0700 root root -" ];
        boot.kernelModules = [ "tun" ];
        networking.firewall.checkReversePath = lib.mkDefault "loose";
        assertions = [
          {
            assertion =
              config.networking.firewall.checkReversePath != true
              && config.networking.firewall.checkReversePath != "strict";
            message = "EdNovas TUN requires loose or disabled reverse path filtering.";
          }
        ];
      })
    ]
  );
}
