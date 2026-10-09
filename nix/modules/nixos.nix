{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.ednovas-cloud;
  appPackage = if cfg.tun.enable then cfg.package.override { tunSupport = true; } else cfg.package;
  MakeCoreWrapper = relativePath: {
    source = "${appPackage}/${relativePath}";
    owner = "root";
    group = cfg.tun.group;
    permissions = "u+rx,g+rx,o-rwx";
    capabilities = "cap_net_admin,cap_net_raw+ep";
  };
in
{
  options.programs.ednovas-cloud = {
    enable = lib.mkEnableOption "EdNovas Cloud";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ../package.nix { }";
      description = "EdNovas Cloud package; must support the tunSupport override for TUN mode.";
    };
    tun = {
      enable = lib.mkEnableOption "capability wrappers for EdNovas Cloud TUN mode";
      group = lib.mkOption {
        type = lib.types.str;
        default = "ednovas-cloud";
        description = "Group allowed to execute privileged proxy cores. Add users explicitly.";
      };
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      { environment.systemPackages = [ appPackage ]; }
      (lib.mkIf cfg.tun.enable {
        users.groups.${cfg.tun.group} = { };
        boot.kernelModules = [ "tun" ];
        security.wrappers = {
          ednovas-core = MakeCoreWrapper appPackage.coreRelativePaths.standard;
          ednovas-core-compatible = MakeCoreWrapper appPackage.coreRelativePaths.compatible;
        };
        networking.firewall.checkReversePath = lib.mkDefault "loose";
        assertions = [
          {
            assertion =
              config.networking.firewall.checkReversePath != true
              && config.networking.firewall.checkReversePath != "strict";
            message = "EdNovas TUN requires networking.firewall.checkReversePath = \"loose\" or false.";
          }
        ];
      })
    ]
  );
}
