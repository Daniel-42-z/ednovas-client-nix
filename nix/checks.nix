{
  self,
  nixpkgs,
  home-manager,
  system,
  pkgs,
}:
let
  lib = nixpkgs.lib;
  appPackage = self.packages.${system}.ednovas-cloud;
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
  disabledConfig = (EvaluateNixos false false).config;
  proxyConfig = (EvaluateNixos true false).config;
  tunConfig = (EvaluateNixos true true).config;
  homeConfig =
    (home-manager.lib.homeManagerConfiguration {
      inherit pkgs;
      modules = [
        self.homeManagerModules.default
        {
          home.username = "test-user";
          home.homeDirectory = "/home/test-user";
          home.stateVersion = "26.05";
          programs.ednovas-cloud = {
            enable = true;
            package = appPackage;
          };
        }
      ];
    }).config;
  homeAssertions = builtins.all (item: item.assertion) homeConfig.assertions;
in
{
  package = appPackage;
  package-tun = self.packages.${system}.ednovas-cloud-tun;

  module-evaluation =
    assert !(disabledConfig.security.wrappers ? ednovas-core);
    assert !(proxyConfig.security.wrappers ? ednovas-core);
    assert builtins.elem appPackage proxyConfig.environment.systemPackages;
    assert tunConfig.security.wrappers.ednovas-core.capabilities == "cap_net_admin,cap_net_raw+ep";
    assert tunConfig.security.wrappers.ednovas-core.group == "ednovas-cloud";
    assert tunConfig.security.wrappers.ednovas-core.permissions == "u+rx,g+rx,o-rwx";
    assert tunConfig.security.wrappers.ednovas-core-compatible.group == "ednovas-cloud";
    assert builtins.elem "tun" tunConfig.boot.kernelModules;
    assert tunConfig.networking.firewall.checkReversePath == "loose";
    assert builtins.elem appPackage homeConfig.home.packages;
    assert homeAssertions;
    pkgs.runCommand "ednovas-module-evaluation" { } ''
      cat > "$out" <<'REPORT'
      ${builtins.toJSON {
        wrapperGroup = tunConfig.security.wrappers.ednovas-core.group;
        wrapperPermissions = tunConfig.security.wrappers.ednovas-core.permissions;
        wrapperCapabilities = tunConfig.security.wrappers.ednovas-core.capabilities;
        reversePathFilter = tunConfig.networking.firewall.checkReversePath;
        homeManagerProxyOnly = !(appPackage.tunSupport or false);
      }}
      REPORT
    '';

  scripts =
    pkgs.runCommand "ednovas-script-checks"
      {
        nativeBuildInputs = [
          pkgs.python3
          pkgs.nodejs
          pkgs.bash
          pkgs.jq
          (lib.getBin pkgs.shellcheck)
          pkgs.actionlint
        ];
      }
      ''
        export PYTHONDONTWRITEBYTECODE=1
        cd ${../.}
        python3 -m unittest discover -s tests -p 'test_*.py'
        node --test tests/runtime-shim.test.cjs
        shellcheck scripts/ci-update.sh
        actionlint .github/workflows/*.yml
        touch "$out"
      '';
}
