{
  pkgs,
  module,
  appPackage,
}:
let
  lib = pkgs.lib;
  rootPackage = appPackage.override { privileged = true; };
  # Reuse every dependency/namespace/capability choice from the production FHS
  # runtime, replacing only the command (not the bundled core binary).
  CoreHarness =
    core:
    pkgs.buildFHSEnv (
      builtins.removeAttrs rootPackage.args [
        "pname"
        "src"
        "contents"
        "extraInstallCommands"
        "nativeBuildInputs"
        "passthru"
        "meta"
      ]
      // {
        name = "runtime-${core}";
        runScript = "${appPackage.appimageContents}/resources/bin/${core}";
      }
    );
  core = CoreHarness "EdNovas-Core";
  compatibleCore = CoreHarness "EdNovas-Core-compatible";
  testPython = pkgs.python3.withPackages (p: [ p.dbus-next ]);
  session = pkgs.writeShellScriptBin "runtime-session" ''
    ${testPython}/bin/python3 ${./session.py} &
    ${pkgs.lxqt.lxqt-policykit}/bin/lxqt-policykit-agent &
    wait
  '';
  ui = pkgs.writeShellScriptBin "runtime-ui" ''
    exec ${testPython}/bin/python3 ${./ui.py} "$@"
  '';
  Common = { ... }: {
    system.stateVersion = "26.05";
    virtualisation.memorySize = lib.mkDefault 1024;
    virtualisation.cores = 2;
    virtualisation.diskSize = 8192;
    networking.firewall.enable = false;
    networking.tempAddresses = "disabled";
    networking.dhcpcd.denyInterfaces = [ "ednovas-test" ];
    environment.systemPackages = [
      pkgs.curl
      pkgs.iproute2
      pkgs.tcpdump
      pkgs.procps
      testPython
    ];
  };
  Gui =
    desktop:
    pkgs.testers.runNixOSTest {
      name = "ednovas-runtime-${desktop}";
      qemu.package = pkgs.qemu_kvm;
      qemu.forceAccel = true;
      globalTimeout = 1800;
      extraPythonPackages = p: [ p.pillow ];
      nodes.machine =
        { ... }:
        {
          imports = [
            Common
            module
          ];
          config = lib.mkMerge [
            {
              programs.ednovas-cloud = {
                enable = true;
                package = appPackage;
                tun.enable = true;
              };
              users.users.alice = {
                isNormalUser = true;
                uid = 1000;
                password = "vm-admin-password";
                extraGroups = [
                  "wheel"
                  "ednovas-cloud"
                ];
              };
              users.users.outsider = {
                isNormalUser = true;
                uid = 1001;
                password = "vm-outsider";
              };
              security.polkit.extraConfig = lib.mkAfter ''
                polkit.addAdminRule(function(action, subject) {
                  return ["unix-user:alice"];
                });
              '';
              # logind starts autovt@tty2, an alias of the template. Overriding
              # just getty@tty2 does not override that aliased template unit.
              systemd.services."getty@".serviceConfig.ExecStart = lib.mkForce [
                ""
                "${lib.getBin pkgs.util-linux}/bin/agetty --login-program ${pkgs.shadow}/bin/login --autologin outsider --noclear %I linux"
              ];
              virtualisation.memorySize = 4096;
              hardware.graphics.enable = true;
              fonts.packages = [ pkgs.noto-fonts-cjk-sans ];
              environment.systemPackages = [
                session
                ui
                pkgs.xwininfo
                pkgs.xdotool
                pkgs.wmctrl
                pkgs.xclip
                pkgs.wl-clipboard
                (pkgs.tesseract.override {
                  enableLanguages = [
                    "eng"
                    "chi_sim"
                  ];
                })
              ];
              environment.sessionVariables = {
                LIBGL_ALWAYS_SOFTWARE = "1";
                WINIT_X11_SCALE_FACTOR = "1";
              };
            }
            {
              services.xserver.enable = true;
              services.xserver.windowManager.openbox.enable = true;
              services.xserver.displayManager.lightdm.enable = true;
              services.xserver.resolutions = [
                {
                  x = 1280;
                  y = 720;
                }
              ];
              services.displayManager.defaultSession = "none+openbox";
              services.displayManager.autoLogin = {
                enable = true;
                user = "alice";
              };
              services.xserver.displayManager.sessionCommands =
                if desktop == "x11" then
                  ''
                    ${session}/bin/runtime-session &
                  ''
                else
                  ''
                    # niri's winit backend supports llvmpipe without host GPU access.
                    LD_LIBRARY_PATH=${
                      lib.makeLibraryPath [
                        pkgs.libxcursor
                        pkgs.libxrandr
                        pkgs.libxi
                        pkgs.libx11
                      ]
                    } ${pkgs.niri}/bin/niri > /home/alice/niri.log 2>&1 &
                  '';
            }
            (lib.mkIf (desktop == "wayland") {
              programs.niri = {
                enable = true;
                useNautilus = false;
              };
              environment.etc."niri/config.kdl".text = ''
                layout { default-column-width { proportion 0.8; }; }
                hotkey-overlay { skip-at-startup; }
                spawn-at-startup "${session}/bin/runtime-session"
                binds { Mod+Shift+Q { close-window; }; }
              '';
            })
          ];
        };
      testScript = ''
        desktop = ${builtins.toJSON desktop}
      ''
      + builtins.readFile ./gui.py;
    };
  network = pkgs.testers.runNixOSTest {
    name = "ednovas-runtime-network";
    qemu.package = pkgs.qemu_kvm;
    qemu.forceAccel = true;
    globalTimeout = 1800;
    nodes.machine = { ... }: {
      imports = [ Common ];
      boot.kernelModules = [ "tun" ];
      environment.systemPackages = [
        core
        compatibleCore
      ];
      environment.etc."runtime/core.yaml".source = ./core.yaml;
    };
    nodes.fixture = { ... }: {
      imports = [ Common ];
      networking.interfaces.eth1.ipv4.addresses = [
        {
          address = "203.0.113.2";
          prefixLength = 24;
        }
      ];
      systemd.services.fixture = {
        wantedBy = [ "multi-user.target" ];
        serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${./fixture.py}";
      };
    };
    testScript = builtins.readFile ./network.py;
  };
in
{
  runtime-x11-driver = (Gui "x11").driver;
  runtime-wayland-driver = (Gui "wayland").driver;
  runtime-network-driver = network.driver;
}
