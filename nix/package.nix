{
  lib,
  stdenv,
  fetchurl,
  runCommand,
  python3,
  squashfsTools,
  appimageTools,
  makeWrapper,
  privileged ? false,
}:
let
  release = builtins.fromJSON (builtins.readFile ./release.json);
  source =
    release.sources.${stdenv.hostPlatform.system}
      or (throw "EdNovas Cloud: no source for ${stdenv.hostPlatform.system}");
  pname = if privileged then "ednovas-cloud-privileged-runtime" else "ednovas-cloud";
  src = fetchurl { inherit (source) url hash; };
  appimageContents =
    runCommand "ednovas-cloud-${release.version}-upstream"
      {
        nativeBuildInputs = [
          python3
          squashfsTools
        ];
      }
      ''
        offset=$(python3 ${../scripts/appimage.py} ${src} ${lib.escapeShellArg source.appImageArchitecture})
        unsquashfs -processors 1 -no-progress -o "$offset" -d "$out" ${src}
        grep -Fx ${lib.escapeShellArg "X-AppImage-Version=${release.version}"} "$out/ednovas-cloud.desktop"
        test -s "$out/resources/app.asar"
        test -x "$out/resources/bin/EdNovas-Core"
        test -x "$out/resources/bin/EdNovas-Core-compatible"
      '';
in
appimageTools.wrapAppImage {
  inherit pname src;
  inherit (release) version;
  contents = appimageContents;
  nativeBuildInputs = [ makeWrapper ];
  extraPkgs = pkgs: [
    pkgs.procps
    pkgs.psmisc
    pkgs.getent
    pkgs.libappindicator-gtk3
    pkgs.libnotify
  ];
  # AppIndicator/Electron can publish tray icon files under /tmp. The user's
  # StatusNotifier host must be able to read those paths outside the FHS env.
  # The privileged profile itself stays isolated under /var/lib/ednovas-cloud.
  multiArch = false;
  privateTmp = false;
  chdirToPwd = false;
  unshareUser = false;
  unshareNet = false;
  extraBwrapArgs = lib.optionals privileged [
    "--cap-add"
    "ALL"
  ];
  extraInstallCommands = ''
    mv "$out/bin/${pname}" "$out/bin/.${pname}-fhs"
    makeWrapper "$out/bin/.${pname}-fhs" "$out/bin/${pname}" \
      --add-flags --no-sandbox
    ${lib.optionalString (!privileged) ''
      mkdir -p "$out/share/applications" "$out/share/icons/hicolor/2048x2048/apps"
      cp ${appimageContents}/ednovas-cloud.desktop "$out/share/applications/ednovas-cloud.desktop"
      ln -s ${appimageContents}/ednovas-cloud.png "$out/share/icons/hicolor/2048x2048/apps/ednovas-cloud.png"
      substituteInPlace "$out/share/applications/ednovas-cloud.desktop" \
        --replace-fail 'Exec=AppRun --no-sandbox %U' "Exec=$out/bin/ednovas-cloud %U" \
        --replace-fail 'Icon=ednovas-cloud' "Icon=$out/share/icons/hicolor/2048x2048/apps/ednovas-cloud.png"
    ''}
  '';
  passthru = { inherit privileged appimageContents; };
  meta = {
    description = "Unmodified upstream EdNovas Cloud AppImage in an FHS runtime";
    homepage = "https://download.ednovas.org/";
    license = lib.licenses.unfree;
    platforms = builtins.attrNames release.sources;
    mainProgram = pname;
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
