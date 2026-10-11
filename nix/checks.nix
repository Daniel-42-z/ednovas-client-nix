{
  self,
  nixpkgs,
  system,
  pkgs,
}:
let
  lib = nixpkgs.lib;
  appPackage = self.packages.${system}.ednovas-cloud;
  rootPackage = self.packages.${system}.ednovas-cloud-privileged-runtime;
  EvaluateNixos =
    enabled: tunEnabled:
    nixpkgs.lib.nixosSystem {
      inherit system;
      modules = [
        self.nixosModules.default
        {
          nixpkgs.config.allowUnfree = true;
          system.stateVersion = "26.05";
          programs.ednovas-cloud = {
            enable = enabled;
            package = appPackage;
            tun.enable = tunEnabled;
          };
        }
      ];
    };
  disabled = (EvaluateNixos false false).config;
  proxy = (EvaluateNixos true false).config;
  tun = (EvaluateNixos true true).config;
  FindPackage = name: packages: lib.findFirst (p: lib.getName p == name) null packages;
  launcher = FindPackage "ednovas-cloud-tun" tun.environment.systemPackages;
  policy = FindPackage "ednovas-cloud-polkit-policy" tun.environment.systemPackages;
in
{
  package = appPackage;
  package-privileged = rootPackage;
  module-evaluation =
    assert rootPackage.privileged && !appPackage.privileged;
    assert toString rootPackage.appimageContents != toString appPackage.appimageContents;
    assert !rootPackage.args.privateTmp && !appPackage.args.privateTmp;
    assert !rootPackage.args.unshareUser && !rootPackage.args.unshareNet;
    assert
      rootPackage.args.extraBwrapArgs == [
        "--cap-add"
        "ALL"
      ];
    assert !(disabled.security.wrappers ? ednovas-core);
    assert !(tun.security.wrappers ? ednovas-core);
    assert !(tun.security.wrappers ? ednovas-core-compatible);
    assert FindPackage "ednovas-cloud-tun" proxy.environment.systemPackages == null;
    assert FindPackage "ednovas-cloud-tun" disabled.environment.systemPackages == null;
    assert launcher != null && policy != null;
    assert tun.security.polkit.enable;
    assert tun.security.wrappers.pkexec.setuid;
    assert tun.security.wrappers.pkexec.enable;
    assert lib.hasInfix "AUTH_ADMIN" tun.security.polkit.extraConfig;
    assert lib.hasInfix "subject.local && subject.active" tun.security.polkit.extraConfig;
    assert builtins.elem appPackage proxy.environment.systemPackages;
    assert builtins.elem "tun" tun.boot.kernelModules;
    assert tun.networking.firewall.checkReversePath == "loose";
    assert builtins.elem "d /var/lib/ednovas-cloud 0700 root root -" tun.systemd.tmpfiles.rules;
    pkgs.runCommand "ednovas-privileged-module-checks" { nativeBuildInputs = [ pkgs.python3 ]; } ''
      test -x ${lib.getExe launcher}
      grep -F '/run/wrappers/bin/pkexec' ${lib.getExe launcher}
      grep -F '/bin/xdg-dbus-proxy' ${lib.getExe launcher}
      grep -F 'tray.py' ${lib.getExe launcher}
      python3 - ${policy}/share/polkit-1/actions/org.ednovas.cloud.tun.policy <<'PY'
      import sys, shlex, xml.etree.ElementTree as ET
      from pathlib import Path
      action = ET.parse(sys.argv[1]).getroot().find('action')
      assert action.attrib['id'] == 'org.ednovas.cloud.tun'
      assert action.findtext('defaults/allow_active') == 'auth_admin'
      assert action.findtext('defaults/allow_inactive') == 'no'
      assert action.findtext('defaults/allow_any') == 'no'
      assert action.findtext('annotate').startswith('/nix/store/')
      helper = Path(action.findtext('annotate'))
      command = next(line for line in helper.read_text().splitlines() if line.startswith('exec '))
      helperSource = Path(shlex.split(command)[3])
      text = helperSource.read_text()
      assert 'application = "/nix/store/' in text
      assert '@runtimePath@' not in text
      compile(text, str(helperSource), 'exec')
      PY
      echo 'authenticated privileged launcher, private state, and no core wrappers' > "$out"
    '';
  scripts =
    pkgs.runCommand "ednovas-privileged-script-checks"
      {
        nativeBuildInputs = [
          (pkgs.python3.withPackages (pythonPkgs: [
            pythonPkgs.dbus-next
            pythonPkgs.pillow
          ]))
          pkgs.bash
          pkgs.jq
          pkgs.jujutsu
          pkgs.gitMinimal
          (lib.getBin pkgs.shellcheck)
          pkgs.actionlint
        ];
      }
      ''
        export PYTHONDONTWRITEBYTECODE=1
        cd ${../.}
        python3 -m unittest discover -s tests -p 'test_*.py'
        shellcheck scripts/ci-update.sh scripts/validate.sh
        actionlint .github/workflows/*.yml
        echo 'updater, launcher validation, and workflows checked' > "$out"
      '';
}
