{
  description = "EdNovas Cloud desktop client with optional NixOS TUN integration";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      home-manager,
    }:
    let
      release = builtins.fromJSON (builtins.readFile ./nix/release.json);
      systems = builtins.attrNames release.sources;
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor =
        system:
        import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };
    in
    {
      overlays.default = final: prev: {
        ednovas-cloud = final.callPackage ./nix/package.nix { };
      };

      packages = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
          appPackage = pkgs.callPackage ./nix/package.nix { };
          updaterSource = pkgs.lib.fileset.toSource {
            root = ./scripts;
            fileset = pkgs.lib.fileset.unions [ ./scripts/update.py ./scripts/appimage.py ];
          };
          updater = pkgs.stdenvNoCC.mkDerivation {
            pname = "ednovas-update";
            version = "1";
            dontUnpack = true;
            dontBuild = true;
            nativeBuildInputs = [ pkgs.makeWrapper ];
            installPhase = ''
              mkdir -p "$out/share/ednovas-update" "$out/bin"
              cp ${updaterSource}/update.py "$out/share/ednovas-update/update.py"
              cp ${updaterSource}/appimage.py "$out/share/ednovas-update/appimage.py"
              test -s "$out/share/ednovas-update/update.py"
              makeWrapper ${pkgs.python3}/bin/python3 "$out/bin/ednovas-update" \
                --add-flags "$out/share/ednovas-update/update.py" \
                --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.curl ]}
            '';
            doInstallCheck = true;
            installCheckPhase = ''
              "$out/bin/ednovas-update" --help > updater-help.txt
              test -s updater-help.txt
            '';
            meta.mainProgram = "ednovas-update";
          };
        in
        {
          ednovas-cloud = appPackage;
          ednovas-cloud-tun = appPackage.override { tunSupport = true; };
          inherit updater;
          default = appPackage;
        }
      );

      apps = forAllSystems (system: {
        default = self.apps.${system}.ednovas-cloud;
        ednovas-cloud = {
          type = "app";
          program = nixpkgs.lib.getExe self.packages.${system}.ednovas-cloud;
        };
        update = {
          type = "app";
          program = nixpkgs.lib.getExe self.packages.${system}.updater;
        };
      });

      nixosModules.default = import ./nix/modules/nixos.nix;
      homeManagerModules.default = import ./nix/modules/home-manager.nix;

      checks = forAllSystems (
        system:
        import ./nix/checks.nix {
          inherit
            self
            nixpkgs
            home-manager
            system
            ;
          pkgs = pkgsFor system;
        }
      );

      formatter = forAllSystems (system: (pkgsFor system).nixfmt-tree);
    };
}
