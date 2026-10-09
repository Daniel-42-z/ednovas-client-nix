# EdNovas Cloud for NixOS

This branch packages the official [EdNovas Cloud AppImage](https://download.ednovas.org/) without modifying its application code, bytecode, Electron, libraries, cores, or updater metadata. Nix provides a conventional Linux filesystem and library environment through `appimageTools`. Initially supports `x86_64-linux`.

The `capability-tun` Jujutsu bookmark preserves the earlier implementation. This `appimage-privileged` branch provides a normal-user proxy launcher and an authenticated root GUI/core launcher for TUN. Only the privileged runtime has a small ASAR startup shim to hide and restore its window via the tray; the ordinary application package remains unchanged. This branch exposes only a NixOS module.

## Build and run

```sh
nix build .#ednovas-cloud --option max-jobs 1 --option cores 1
./result/bin/ednovas-cloud
```

The ordinary launcher uses your existing user profile. Both launch modes pass `--no-sandbox`, matching the upstream AppImage desktop entry. This disables Electron's Chromium sandbox. The privileged mode gives the GUI and cores root privileges; that includes application code interacting with remote support and analytics services.

Build checks never launch the GUI:

```sh
nix flake check --print-build-logs --option max-jobs 1 --option cores 1
```

The AppImage is extracted at build time using one decompression thread, with no FUSE mount or execution of the downloaded binary. The desktop icon uses an absolute store path. The application files remain in the read-only Nix store, so use the flake updater for package upgrades; the upstream in-app updater cannot replace those files.

## NixOS configuration

Add this input to your system flake during local testing:

```nix
inputs.ednovas-cloud.url = "path:/home/daniel/Projects/ednovas-client";
```

Import `inputs.ednovas-cloud.nixosModules.default` in your system's `modules` list, then configure:

```nix
{ lib, ... }: {
  nixpkgs.config.allowUnfreePredicate = package:
    builtins.elem (lib.getName package) [
      "ednovas-cloud" "ednovas-cloud-privileged-runtime"
    ];
  programs.ednovas-cloud = {
    enable = true;
    tun.enable = true;
    # tun.group = "ednovas-cloud";
  };
  users.users.daniel.extraGroups = [ "ednovas-cloud" ];
}
```

If you already allow unfree packages globally, the predicate is unnecessary. Rebuild, then log out and back in if group membership changed. Your desktop must have a Polkit authentication agent; most complete desktop environments supply one, while a standalone window manager may need one configured.

Launch **EdNovasCloud** / `ednovas-cloud` for proxy mode as your user. Launch **EdNovas Cloud (Privileged TUN)** / `ednovas-cloud-tun` to authenticate and run the GUI and core as root. The latter requires membership in the configured group, an active local desktop session, and administrator authentication. It does not grant passwordless access. Sign in to its separate profile and enable TUN in the GUI.

Privileged profiles live in `/var/lib/ednovas-cloud/<caller-uid>/` with root ownership and mode `0700`. They have a separate HOME, config, cache, and data directory plus an explicit Electron user-data directory. The application shares the host `/tmp` so the desktop's user-owned StatusNotifier host can read temporary tray icon files. Your existing credentials and subscription state are not copied automatically.

The launcher forwards selected desktop connection settings for X11/Wayland and starts a user-owned session-bus proxy for the privileged GUI. It also starts a **user-owned StatusNotifier tray item**, because the upstream root GUI does not reliably register one on Wayland desktops. In the privileged package, an Electron shim intercepts window close to hide the GUI while keeping the application running. Left-clicking the tray icon shows and focuses the existing window (including on niri); right-clicking opens a menu with **Quit EdNovas Cloud** to exit the application. The tray item and bus proxy exit with the privileged application. The root GUI uses its own root-owned runtime directory in `/run/ednovas-cloud/<caller-uid>/` rather than the caller-owned `XDG_RUNTIME_DIR`; its `WAYLAND_DISPLAY` points explicitly to the caller's Wayland socket. It does not forward library-loading variables or the whole environment with `sudo -E`. Only a fixed store executable can be started; supported optional flags are `--disable-gpu`, `--enable-logging=stderr`, `--v=0` through `--v=3`, and `--ozone-platform=x11` / `--ozone-platform=wayland`.

Both launchers use a fixed Fontconfig configuration with bundled Noto CJK Sans fonts. Chinese text remains available in the separate root profile even when your desktop font settings or fonts live in your user profile.

The privileged FHS runtime shares the host network and preserves root capabilities so TUN changes affect the host. The module loads `tun` and defaults reverse path filtering to `"loose"`. No per-core capability wrappers are installed. Setting `tun.enable = false` installs only the ordinary launcher.

| Option | Default |
| --- | --- |
| `programs.ednovas-cloud.enable` | `false` |
| `programs.ednovas-cloud.package` | Bundled flake package |
| `programs.ednovas-cloud.tun.enable` | `false` |
| `programs.ednovas-cloud.tun.group` | `"ednovas-cloud"` |

Custom packages must support `.override { privileged = true; }`. The `ednovas-cloud-privileged-runtime` flake package is an internal runtime for the authenticated launcher; use the NixOS module to launch TUN. This branch has no Home Manager module and no autostart at boot or login.

## Check and update releases

```sh
nix run .#update -- --check
nix run .#update -- --write
```

Run in this repository, or pass `--repo /path/to/repository`. Check mode outputs JSON without changing files. Write mode downloads each configured AppImage, checks its ELF architecture and embedded SquashFS bounds, computes its SHA-256 hash, and atomically replaces `nix/release.json`. Errors leave the manifest untouched. Unchanged or older versions do not write, commit, or push.

Builds also validate the advertised desktop version and bundled payload. To support ARM64 later, add an `aarch64-linux` source with `appImageArchitecture = "arm64"` and validate on that architecture. The updater requires every configured architecture in the newest release.

## GitHub automation

The update workflow runs daily at 03:23 UTC and supports manual dispatch. It checks for a new release, runs `nix flake check` (including ordinary and privileged runtime builds), and only then pushes the reusable `ednovas-update` branch, creates or refreshes one PR, and requests squash auto-merge. The exact tested commit receives the `ednovas-update/build` status. Failed downloads or validation do not publish the candidate or create a PR.

The CI workflow uses Git for its update branch, commit, and push. Local version control uses Jujutsu. GitHub's built-in `GITHUB_TOKEN` is sufficient; downloads are public, and no PAT or App secret is required.

Configure the GitHub repository after publishing:

- Enable **Allow GitHub Actions to create and approve pull requests** under Actions → General → Workflow permissions. Organization settings may also control this permission.
- Enable **Allow auto-merge** and **Allow squash merging** under General → Pull Requests.
- If requiring validation on the update branch, require the `ednovas-update/build` status. PR/push workflows usually do not run for changes made with `GITHUB_TOKEN`; the update workflow supplies the build status itself.
- Required human reviews and other branch protection rules still apply. Auto-merge waits for those requirements and does not bypass them. Requiring a separate PR check that this token cannot trigger will prevent auto-merge.

The ordinary package-check workflow runs for human pushes and PRs. GitHub's remote PR/merge behavior can only be validated after publication; local checks do not contact a remote.

## Manual usage test

Close other EdNovas instances before testing each mode. Keep the ordinary and privileged GUI instances from running concurrently, since their cores can conflict on ports or routing.

- Verify ordinary launch, sign-in, tray, notifications, subscription loading, and proxy traffic.
- Verify privileged launch requests administrator authentication, sign-in works in the separate profile, and TUN routes traffic.
- Verify switching nodes and the compatible core, then disable TUN and confirm connectivity is restored.
- Confirm your ordinary `~/.config/EdNovasCloud` remains owned by your user after privileged launch.
- Check X11/Wayland and tray behavior in your actual desktop session; root session-bus integration depends on the desktop.
- Confirm a user outside the configured group cannot request privileged launch.

Capture privileged application logs from a terminal:

```sh
ednovas-cloud-tun --enable-logging=stderr 2>&1 | tee ednovas-cloud-tun.log
```

Upstream logs can contain credentials or subscription tokens; redact them before publishing. Automated checks cover both runtime builds, module authorization policy, profile/input validation, updater behavior, and workflow linting. GUI, authentication-dialog, and live TUN usage remain manual checks.
