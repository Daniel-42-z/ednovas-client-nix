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
    enabled: tunEnabled: autostartEnabled: passwordless:
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
            tun.passwordless = passwordless;
            autostart.enable = autostartEnabled;
          };
        }
      ];
    };
  disabled = (EvaluateNixos false false true true).config;
  proxy = (EvaluateNixos true false false true).config;
  tun = (EvaluateNixos true true false false).config;
  proxyAutostart = (EvaluateNixos true false true false).config;
  tunAutostart = (EvaluateNixos true true true true).config;
  authenticatedRule = pkgs.writeText "ednovas-authenticated-rule.js" tun.security.polkit.extraConfig;
  passwordlessRule = pkgs.writeText "ednovas-passwordless-rule.js" tunAutostart.security.polkit.extraConfig;
  FindPackage = name: packages: lib.findFirst (p: lib.getName p == name) null packages;
  launcher = FindPackage "ednovas-cloud-tun" tun.environment.systemPackages;
  policy = FindPackage "ednovas-cloud-polkit-policy" tun.environment.systemPackages;
in
{
  package = appPackage;
  package-privileged = rootPackage;
  module-evaluation =
    assert rootPackage.privileged && !appPackage.privileged;
    assert toString rootPackage.appimageContents == toString appPackage.appimageContents;
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
    assert !proxy.security.polkit.enable;
    assert lib.hasInfix "return polkit.Result.YES;" tunAutostart.security.polkit.extraConfig;
    assert builtins.elem appPackage proxy.environment.systemPackages;
    assert builtins.elem "tun" tun.boot.kernelModules;
    assert tun.networking.firewall.checkReversePath == "loose";
    assert builtins.elem "d /var/lib/ednovas-cloud 0700 root root -" tun.systemd.tmpfiles.rules;
    assert !(disabled.environment.etc ? "xdg/autostart/ednovas-cloud.desktop");
    assert !(proxy.environment.etc ? "xdg/autostart/ednovas-cloud.desktop");
    assert !(tun.environment.etc ? "xdg/autostart/ednovas-cloud.desktop");
    assert
      proxyAutostart.environment.etc."xdg/autostart/ednovas-cloud.desktop".source
      == "${appPackage}/share/applications/ednovas-cloud.desktop";
    assert lib.hasSuffix "/share/applications/ednovas-cloud-tun.desktop"
      tunAutostart.environment.etc."xdg/autostart/ednovas-cloud.desktop".source;
    pkgs.runCommand "ednovas-privileged-module-checks"
      {
        nativeBuildInputs = [
          pkgs.python3
          pkgs.nodejs
          pkgs.squashfsTools
        ];
      }
      ''
        node - ${authenticatedRule} ${passwordlessRule} <<'JS'
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        for (const [rulePath, expected] of [[process.argv[2], 'AUTH_ADMIN'], [process.argv[3], 'YES']]) {
          const rules = [];
          vm.runInNewContext(fs.readFileSync(rulePath, 'utf8'), {
            polkit: { addRule: rule => rules.push(rule), Result: { YES: 'YES', NO: 'NO', AUTH_ADMIN: 'AUTH_ADMIN' } }
          });
        const ednovasRules = rules.filter(rule => rule({ id: 'org.ednovas.cloud.tun' }, {
          local: false, active: false, isInGroup: () => false
        }) !== undefined);
        assert.equal(ednovasRules.length, 1);
          for (const local of [false, true]) {
            for (const active of [false, true]) {
              for (const member of [false, true]) {
                const subject = { local, active, isInGroup: group => member && group === 'ednovas-cloud' };
              assert.equal(ednovasRules[0]({ id: 'org.ednovas.cloud.tun' }, subject), local && active && member ? expected : 'NO');
              assert.equal(ednovasRules[0]({ id: 'org.freedesktop.policykit.exec' }, subject), undefined);
              }
            }
          }
        }
        JS
        offset=$(python3 ${../scripts/appimage.py} ${appPackage.src} ${
          lib.escapeShellArg
            (builtins.fromJSON (builtins.readFile ./release.json)).sources.${system}.appImageArchitecture
        })
        unsquashfs -cat -o "$offset" ${appPackage.src} resources/app.asar > upstream.asar
        cmp upstream.asar ${appPackage.appimageContents}/resources/app.asar
        cmp upstream.asar ${rootPackage.appimageContents}/resources/app.asar
        test -x ${lib.getExe launcher}
        grep -F '/run/wrappers/bin/pkexec' ${lib.getExe launcher}
        grep -F '/bin/xdg-dbus-proxy' ${lib.getExe launcher}
        if grep -E 'tray.py|FONTCONFIG_FILE' ${lib.getExe launcher} ${lib.getExe appPackage} ${lib.getExe rootPackage}; then
          echo 'Unexpected custom tray or Fontconfig override' >&2
          exit 1
        fi
        grep -Fx 'Exec=${lib.getExe appPackage} %U' ${
          proxyAutostart.environment.etc."xdg/autostart/ednovas-cloud.desktop".source
        }
        grep -Fx 'Exec=${lib.getExe launcher}' ${
          tunAutostart.environment.etc."xdg/autostart/ednovas-cloud.desktop".source
        }
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
          pkgs.python3
          pkgs.bash
          pkgs.jq
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
