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
  # Exercise the production FHS settings through the real module launcher,
  # substituting only the executable. Never start or modify upstream Electron.
  recordingPackage = lib.makeOverridable (
    {
      privileged ? false,
    }:
    let
      productionPackage = appPackage.override { inherit privileged; };
    in
    pkgs.buildFHSEnv (
      builtins.removeAttrs productionPackage.args [
        "pname"
        "src"
        "contents"
        "extraInstallCommands"
        "nativeBuildInputs"
        "passthru"
        "meta"
      ]
      // {
        name = "runtime-recording${lib.optionalString privileged "-root"}";
        runScript = pkgs.writeShellScript "runtime-recording-command" ''
          exec ${testPython}/bin/python3 -I ${./recording.py} "$@"
        '';
        meta.mainProgram = "runtime-recording${lib.optionalString privileged "-root"}";
      }
    )
  ) { };
  userFontsConf = pkgs.writeText "runtime-user-fonts.conf" ''
    <fontconfig>
      <dir>${pkgs.noto-fonts-cjk-sans}/share/fonts</dir>
      <dir>${pkgs.nerd-fonts.symbols-only}/share/fonts</dir>
      <alias binding="same">
        <family>sans-serif</family>
        <prefer><family>Liberation Sans</family><family>Noto Sans CJK SC</family></prefer>
      </alias>
    </fontconfig>
  '';
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
  integration = pkgs.testers.runNixOSTest {
    name = "ednovas-runtime-integration";
    qemu.package = pkgs.qemu_kvm;
    qemu.forceAccel = true;
    globalTimeout = 1800;
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
              package = recordingPackage;
              tun.enable = true;
            };
            specialisation.passwordless.configuration.programs.ednovas-cloud.tun.passwordless = true;
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
            fonts.enableDefaultPackages = false;
            fonts.packages = lib.mkForce [ pkgs.dejavu_fonts ];
            # Reproduce Home Manager-only fonts: the system has no CJK or
            # Nerd Fonts, and a custom font is installed under XDG_DATA_HOME.
            systemd.tmpfiles.rules = [
              "d /home/alice/.config 0700 alice users -"
              "d /home/alice/.config/fontconfig 0700 alice users -"
              "d /home/alice/.config/fontconfig/conf.d 0700 alice users -"
              "L+ /home/alice/.config/fontconfig/conf.d/10-hm-fonts.conf - alice users - ${userFontsConf}"
              "d /home/alice/.local 0700 alice users -"
              "d /home/alice/.local/share 0700 alice users -"
              "L+ /home/alice/.local/share/fonts - alice users - ${pkgs.liberation_ttf}/share/fonts"
            ];
            environment.systemPackages = [
              session
              ui
              pkgs.fontconfig
              pkgs.xwininfo
              pkgs.xdotool
              pkgs.wmctrl
            ];
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
            services.xserver.displayManager.sessionCommands = ''
              ${session}/bin/runtime-session &
            '';
          }
        ];
      };
    testScript = ''
      recordingCommand = "${lib.getExe recordingPackage}"
    ''
    + builtins.readFile ./integration.py;
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
  runtime-integration-driver = integration.driver;
  runtime-network-driver = network.driver;
}
