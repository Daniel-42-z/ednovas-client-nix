{
  lib,
  stdenv,
  fetchurl,
  squashfsTools,
  autoPatchelfHook,
  wrapGAppsHook3,
  makeWrapper,
  python3,
  alsa-lib,
  at-spi2-atk,
  at-spi2-core,
  cairo,
  cups,
  dbus,
  expat,
  glib,
  gsettings-desktop-schemas,
  gtk3,
  libGL,
  libappindicator-gtk3,
  libdrm,
  libgbm,
  libnotify,
  libsecret,
  libuuid,
  libxcb,
  libxkbcommon,
  nspr,
  nss,
  pango,
  systemd,
  xdg-utils,
  libx11,
  libxcomposite,
  libxdamage,
  libxext,
  libxfixes,
  libxrandr,
  libxrender,
  libxscrnsaver,
  libxtst,
  procps,
  psmisc,
  tunSupport ? false,
  electronSandbox ? false,
}:
let
  release = builtins.fromJSON (builtins.readFile ./release.json);
  source =
    release.sources.${stdenv.hostPlatform.system}
      or (throw "EdNovas Cloud: no upstream source for ${stdenv.hostPlatform.system}");
in
stdenv.mkDerivation {
  pname = "ednovas-cloud";
  inherit (release) version;
  src = fetchurl { inherit (source) url hash; };

  nativeBuildInputs = [
    squashfsTools
    autoPatchelfHook
    wrapGAppsHook3
    makeWrapper
    python3
  ];
  buildInputs = [
    alsa-lib
    at-spi2-atk
    at-spi2-core
    cairo
    cups
    dbus
    expat
    glib
    gtk3
    gsettings-desktop-schemas
    libGL
    libappindicator-gtk3
    libdrm
    libgbm
    libnotify
    libsecret
    libuuid
    libxcb
    libxkbcommon
    nspr
    nss
    pango
    stdenv.cc.cc
    systemd
    libx11
    libxcomposite
    libxdamage
    libxext
    libxfixes
    libxrandr
    libxrender
    libxscrnsaver
    libxtst
  ];
  runtimeDependencies = [
    (lib.getLib libGL)
    (lib.getLib libsecret)
    (lib.getLib libnotify)
    (lib.getLib libappindicator-gtk3)
  ];
  dontWrapGApps = true;
  dontConfigure = true;
  dontBuild = true;

  unpackPhase = ''
    runHook preUnpack
    offset=$(python3 ${../scripts/appimage.py} "$src" ${lib.escapeShellArg source.appImageArchitecture})
    unsquashfs -processors 1 -no-progress -o "$offset" -d appimage "$src"
    runHook postUnpack
  '';

  installPhase = ''
    runHook preInstall
    mkdir -p "$out/lib" "$out/bin" "$out/share"
    cp -r appimage "$out/lib/ednovas-cloud"
    grep -Fx ${lib.escapeShellArg "X-AppImage-Version=${release.version}"} appimage/ednovas-cloud.desktop
    mkdir -p "$out/share/applications"
    cp appimage/ednovas-cloud.desktop "$out/share/applications/"
    cp -r appimage/usr/share/icons "$out/share/"
    appDir="$out/lib/ednovas-cloud"
    # Electron uses GTK3; omit obsolete GTK2 fallbacks whose dependencies
    # are unavailable in current Nixpkgs. GTK3 AppIndicator is supplied below.
    rm "$appDir/usr/lib/libappindicator.so.1" \
      "$appDir/usr/lib/libindicator.so.7" "$appDir/usr/lib/libgconf-2.so.4"
    # No FUSE mounts or setuid Electron sandbox in the Nix store.
    chmod 0755 "$appDir/chrome-sandbox"
    python3 ${../scripts/patch-asar.py} \
      "$appDir/resources/app.asar" ${../scripts/runtime-shim.js} \
      --version ${lib.escapeShellArg release.version} \
      ${lib.optionalString tunSupport "--tun-support"}
    rm -f "$appDir/resources/app-update.yml"
    printf 'nix\n' > "$appDir/resources/package-type"
    substituteInPlace "$out/share/applications/ednovas-cloud.desktop" \
      --replace-fail 'Exec=AppRun --no-sandbox %U' "Exec=$out/bin/ednovas-cloud %U" \
      --replace-fail 'Icon=ednovas-cloud' "Icon=$out/share/icons/hicolor/2048x2048/apps/ednovas-cloud.png"
    runHook postInstall
  '';

  preFixup = ''
    makeWrapper "$out/lib/ednovas-cloud/ednovas-cloud" "$out/bin/ednovas-cloud" \
      "''${gappsWrapperArgs[@]}" \
      --suffix PATH : ${
        lib.makeBinPath [
          xdg-utils
          glib
          procps
          psmisc
        ]
      } \
      --add-flags ${if electronSandbox then "--disable-setuid-sandbox" else "--no-sandbox"} \
      --add-flags "\''${NIXOS_OZONE_WL:+\''${WAYLAND_DISPLAY:+--ozone-platform-hint=auto}}"
  '';

  passthru = {
    inherit tunSupport electronSandbox;
    coreRelativePaths = {
      standard = "lib/ednovas-cloud/resources/bin/EdNovas-Core";
      compatible = "lib/ednovas-cloud/resources/bin/EdNovas-Core-compatible";
    };
  };

  meta = {
    description = "Official EdNovas Cloud desktop proxy client";
    homepage = "https://download.ednovas.org/";
    license = lib.licenses.unfree;
    platforms = builtins.attrNames release.sources;
    mainProgram = "ednovas-cloud";
    sourceProvenance = [ lib.sourceTypes.binaryNativeCode ];
  };
}
