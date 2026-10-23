# EdNovas Cloud for NixOS

This branch packages the official [EdNovas Cloud AppImage](https://download.ednovas.org/) without modifying its application code, bytecode, Electron, libraries, cores, or updater metadata. Nix provides a conventional Linux filesystem and library environment through `appimageTools`. Initially supports `x86_64-linux`.

The `capability-tun` Jujutsu bookmark preserves the earlier implementation. This `appimage-privileged` branch provides a normal-user proxy launcher and an authenticated root GUI/core launcher for TUN. Both runtimes use the unchanged upstream application archive and native tray integration introduced in 1.1.80. This branch exposes only a NixOS module.

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
inputs.ednovas-cloud.url = "github:Daniel-42-z/ednovas-client-nix";
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
    autostart.enable = true;
    tun.enable = true;
    # tun.passwordless = true;
    # tun.group = "ednovas-cloud";
  };
  users.users.your-username.extraGroups = [ "ednovas-cloud" ];
}
```

If you already allow unfree packages globally, the predicate is unnecessary. Rebuild, then log out and back in if group membership changed. Unless `tun.passwordless` is enabled, your desktop must have a Polkit authentication agent; most complete desktop environments supply one, while a standalone window manager may need one configured.

Launch **EdNovasCloud** / `ednovas-cloud` for proxy mode as your user. Launch **EdNovas Cloud (Privileged TUN)** / `ednovas-cloud-tun` to run the GUI and core as root. The latter requires membership in the configured group and an active local desktop session. Administrator authentication is required by default. Set `tun.passwordless = true` to authorize both manual launches and login autostart without entering an administrator password. This grants the configured group password-free access to the fixed privileged EdNovas launcher. Sign in to its separate profile and enable TUN in the GUI.

Privileged profiles live in `/var/lib/ednovas-cloud/<caller-uid>/` with root ownership and mode `0700`. They have a separate HOME, config, cache, and data directory plus an explicit Electron user-data directory. The application shares the host `/tmp` so the desktop's user-owned StatusNotifier host can read temporary tray icon files. Your existing credentials and subscription state are not copied automatically.

The launcher forwards selected desktop connection settings for X11/Wayland and starts a user-owned session-bus proxy for the privileged GUI. It passes the authenticated caller UID through `PKEXEC_UID`, which upstream uses for its native user-session tray helper. Upstream controls window hiding, restoration, tray menus, proxy/mode actions, and quitting; no custom tray service or Electron shim is installed. The bus proxy exits with the privileged application. The root GUI uses its own root-owned runtime directory in `/run/ednovas-cloud/<caller-uid>/` rather than the caller-owned `XDG_RUNTIME_DIR`; its `WAYLAND_DISPLAY` points explicitly to the caller's Wayland socket. It does not forward library-loading variables or the whole environment with `sudo -E`. Only a fixed store executable can be started; supported optional flags are `--disable-gpu`, `--enable-logging=stderr`, `--v=0` through `--v=3`, and `--ozone-platform=x11` / `--ozone-platform=wayland`.

The ordinary launcher uses your desktop Fontconfig configuration. The privileged launcher links its isolated Fontconfig include directory to a private session fragment that loads your `XDG_CONFIG_HOME/fontconfig` rules and your `XDG_DATA_HOME/fonts` directory (using the standard home-directory defaults when unset). This also exposes Home Manager font packages and preferences and custom font symlinks. Application state stays in the separate root profile, and Fontconfig caches follow each process's own `XDG_CACHE_HOME`. The session font file is owned by the authenticated caller, validated by the root helper, readable by upstream's user-owned tray helper, and removed when the launcher exits. No specific font family is forced.

The privileged FHS runtime shares the host network and preserves root capabilities so TUN changes affect the host. The module loads `tun` and defaults reverse path filtering to `"loose"`. No per-core capability wrappers are installed. Setting `tun.enable = false` installs only the ordinary launcher.

| Option                                    | Default               |
| ----------------------------------------- | --------------------- |
| `programs.ednovas-cloud.enable`           | `false`               |
| `programs.ednovas-cloud.package`          | Bundled flake package |
| `programs.ednovas-cloud.autostart.enable` | `false`               |
| `programs.ednovas-cloud.tun.enable`       | `false`               |
| `programs.ednovas-cloud.tun.passwordless` | `false`               |
| `programs.ednovas-cloud.tun.group`        | `"ednovas-cloud"`     |

Set `autostart.enable = true` to launch at graphical login in desktop sessions supporting XDG autostart. This installs a system-wide entry in `/etc/xdg/autostart/ednovas-cloud.desktop`. With `tun.enable = true`, it starts the privileged TUN launcher and requests administrator authentication unless `tun.passwordless = true`; the logged-in user must belong to `tun.group`. Otherwise, it starts the ordinary proxy launcher. Autostart waits for the user session’s native StatusNotifier tray host before launching, including before any TUN authentication prompt. If no tray host becomes ready within 60 seconds, it logs a warning and launches anyway. Manual launches remain immediate. Standalone window managers must run an XDG autostart implementation to use this entry.

Custom packages must support `.override { privileged = true; }` and expose their launcher through `meta.mainProgram` for proxy autostart. The `ednovas-cloud-privileged-runtime` flake package is an internal runtime for the authenticated launcher; use the NixOS module to launch TUN. This branch has no Home Manager module and does not start the app at boot.

## Check and update releases

```sh
nix run .#update -- --check
nix run .#update -- --write
```

Run in this repository, or pass `--repo /path/to/repository`. Check mode outputs JSON without changing files. Write mode downloads each configured AppImage, checks its ELF architecture and embedded SquashFS bounds, computes its SHA-256 hash, and atomically replaces `nix/release.json`. Errors leave the manifest untouched. Unchanged or older versions do not write, commit, or push.

Builds also validate the advertised desktop version and bundled payload. To support ARM64 later, add an `aarch64-linux` source with `appImageArchitecture = "arm64"` and validate on that architecture. The updater requires every configured architecture in the newest release.

## GitHub automation

The update workflow runs daily at 03:23 UTC and supports manual dispatch. Unchanged or older releases stop before validation and publication. Preparation creates a local Git candidate and records its commit, parent, release manifest and tracked-file digest. A shared validation workflow runs production builds, unit tests, NixOS module checks, workflow linting, and two fresh VM suites. Human pushes and PRs use that same workflow and one shared candidate identity. Source archives are created from a separate clean Git snapshot, with only the candidate manifest added; the archive output is outside that snapshot.

Only the publication job has GitHub write permissions. It requires all three success records, verifies the candidate identity again, fetches with Git, and stops if the default branch advanced. It pushes with Git's explicit force-with-lease checks, verifies the published head and parent through GitHub, then creates or refreshes the update PR. The exact validated head receives the aggregate `ednovas-update/build` status. Validation happens in the scheduled workflow because `GITHUB_TOKEN` pushes ordinarily do not trigger another workflow. Failed candidates never publish an update branch or PR.

All validation jobs use `ubuntu-24.04`, a 90-minute deadline and a matrix with fail-fast disabled. Nix build outputs are cached; each VM driver still executes on every run. Runtime preflight requires writable KVM (including successfully creating a VM) and 20 GiB of free disk. Application fixture records, logs, journals, process inventories, core logs, route/interface state and TUN packet captures are uploaded when available and retained for 14 days. Each suite reports its result in the Actions summary and successful update PRs link to the evidence.

Changed releases run the build, integration and network suites once against the exact prepared candidate. Publication requires successful jobs and matching evidence for every suite, then requests squash auto-merge with `--match-head-commit`. Unchanged releases skip validation and publication.

Upstream **1.1.82 refuses to run in virtual machines**, including QEMU/KVM. Automated checks therefore validate packaging and the integrations maintained here without starting or patching upstream Electron. GUI launch, login, native tray behavior, X11/Wayland rendering and authenticated GUI networking require manual checks on a physical device. GUI checks are not prerequisites for automated updates.

Configure the GitHub repository after publishing:

- Enable **Allow GitHub Actions to create and approve pull requests** under Actions → General → Workflow permissions. Organization settings may also control this permission.
- Enable **Allow auto-merge** and **Allow squash merging** under General → Pull Requests.
- Require the `ednovas-update/build` status and [**Require branches to be up to date before merging**](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches). This prevents queued auto-merge from using validation against an earlier default-branch parent. A later daily or manual update run prepares and retests against the new parent. PR/push workflows usually do not run for changes made with `GITHUB_TOKEN`; the update workflow supplies the status itself.
- Required human reviews and other branch protection rules still apply. Auto-merge waits for those requirements and does not bypass them. Requiring a separate PR check that this token cannot trigger will prevent auto-merge.

The ordinary package-check workflow runs for human pushes and PRs. GitHub's remote PR/merge behavior can only be validated after publication; local checks do not contact a remote.

## Runtime tests locally

Run from this checkout on `x86_64-linux` with usable KVM. Each command builds the pinned driver, then executes fresh VMs; a cached build never substitutes for execution:

```sh
bash scripts/validate.sh integration
bash scripts/validate.sh network
```

The corresponding flake packages are `runtime-integration-driver` and `runtime-network-driver`. For driver-level debugging, build one package and run `result/bin/nixos-test-driver --interactive --output_directory /tmp/ednovas-debug`. Use a new output directory for each rerun, for example `bash scripts/validate.sh integration /tmp/ednovas-integration-rerun`. In Actions, rerun failed jobs; publication requires all three candidate-bound result artifacts.

The integration VM uses a recording executable inside the production FHS configuration and the real NixOS module launcher. A minimal Openbox/X11 desktop and native Polkit agent exercise administrator authentication, cancellation, passwordless launch and rejection of an active user outside the configured group. It checks caller D-Bus access, forwarded flags, root profile ownership and isolation, invalid-argument rejection, and proxy cleanup. Font checks compare ordinary and privileged FHS font matches against the caller, with Chinese and Nerd Fonts available only through user Fontconfig rules and custom XDG font files. Wayland socket forwarding remains covered by unit tests; the VM does not claim Wayland GUI coverage.

The network suite runs both extracted bundled Mihomo cores in the production FHS environment with the same namespace and capability settings. A second VM serves unique tokens over HTTP. It checks HTTP/SOCKS responses, direct requests through TUN with matching captured request and response bytes, and route/interface cleanup plus direct connectivity after graceful core shutdown. Fixture-only policy rules send unmarked requests through TUN while marked core sockets retain their LAN route; these rules are removed during teardown. Routing changes remain inside the guests. This bundled-core coverage does not prove the logged-in GUI generates or applies a working subscription configuration.

See the [NixOS test framework](https://nixos.org/manual/nixos/stable/#sec-nixos-tests) and [Mihomo TUN configuration](https://wiki.metacubex.one/en/config/inbound/tun/) for the underlying interfaces.

## Manual usage test

Close other EdNovas instances before testing each mode. Keep the ordinary and privileged GUI instances from running concurrently, since their cores can conflict on ports or routing.

- Verify ordinary launch, sign-in, tray, notifications, subscription loading, and proxy traffic.
- Verify privileged launch requests administrator authentication by default, skips the prompt with `tun.passwordless = true`, sign-in works in the separate profile, and TUN routes traffic.
- Verify switching nodes and the compatible core, then disable TUN and confirm connectivity is restored.
- Confirm your ordinary `~/.config/EdNovasCloud` remains owned by your user after privileged launch.
- Check X11/Wayland and tray behavior in your actual desktop session; root session-bus integration depends on the desktop.
- Confirm a user outside the configured group cannot request privileged launch.

Capture privileged application logs from a terminal:

```sh
ednovas-cloud-tun --enable-logging=stderr 2>&1 | tee ednovas-cloud-tun.log
```

Upstream logs can contain credentials or subscription tokens; redact them before publishing. Automated checks cover builds, module and input validation, updater and publication gates, launcher authorization and isolation, font propagation, and local bundled-core traffic. GUI behavior, subscription loading, authenticated profile migration and GPU-specific behavior remain manual checks on a physical device.
