{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.ednovas-cloud;
in
{
  options.programs.ednovas-cloud = {
    enable = lib.mkEnableOption "EdNovas Cloud in proxy mode";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ../package.nix { }";
      description = "Proxy-only EdNovas Cloud package. TUN requires the NixOS module.";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = !(cfg.package.tunSupport or false);
        message = "The Home Manager EdNovas Cloud module only supports proxy mode; use the NixOS module for TUN.";
      }
    ];
    home.packages = [ cfg.package ];
  };
}
