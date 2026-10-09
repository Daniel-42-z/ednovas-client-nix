# EdNovas Cloud for Nix

A flake package for the official [EdNovas Cloud Linux client](https://download.ednovas.org/), repackaged from its AppImage. The GUI, bundled Electron, standard and compatible proxy cores, and geodata stay together. Initially supports `x86_64-linux`.

## Build and run locally

```sh
nix build .#ednovas-cloud
./result/bin/ednovas-cloud
```

Or run directly:

```sh
nix run .#ednovas-cloud
```

The default package supports ordinary proxy use. Use the NixOS module below for TUN; building the TUN variant alone does not install privileged wrappers.

```sh
nix build .#ednovas-cloud-tun --out-link result-tun
nix flake check --print-build-logs
```

The launcher matches the upstream AppImage desktop entry with `--no-sandbox`. This disables Electron’s Chromium sandbox and reduces process isolation. It runs as your normal user; do not launch the GUI with sudo. To opt into the user namespace sandbox, override the package with `electronSandbox = true` (the host must permit unprivileged user namespaces). On Wayland, set `NIXOS_OZONE_WL=1` to request native Wayland; otherwise Electron can use XWayland.

Your application configuration and credentials use the upstream application's normal user data directory. The Nix store stays read-only, and the bundled binary update manifest is removed. Update the package through this flake.

## NixOS with optional TUN

For local testing, add an input to your system flake:

```nix
inputs.ednovas-cloud.url = "path:/home/daniel/Projects/ednovas-client";
```

When the repository is published, replace that URL with its GitHub flake URL. Add the module to your system's existing `modules` list:

```nix
inputs.ednovas-cloud.nixosModules.default
```

Then configure:

```nix
{ lib, ... }: {
  nixpkgs.config.allowUnfreePredicate = package:
    builtins.elem (lib.getName package) [ "ednovas-cloud" ];

  programs.ednovas-cloud = {
    enable = true;
    tun.enable = true;
    # tun.group = "ednovas-cloud"; # default
  };

  users.users.daniel.extraGroups = [ "ednovas-cloud" ];
}
```

Rebuild your NixOS configuration and log out and back in so your session receives the new group membership. Start `ednovas-cloud` as your normal user, sign in, and enable TUN inside the GUI. The module permits TUN; it does not turn on the application's TUN toggle automatically.

TUN grants only `CAP_NET_ADMIN` and `CAP_NET_RAW` to the two core executables through NixOS's `/run/wrappers/bin` mechanism. The wrappers are executable only by root and the configured group. The GUI stays under your user account. Both cores are statically linked, so privilege transitions do not depend on library environment variables.

The module loads `tun` and defaults reverse path filtering to `"loose"`, which accommodates TUN routing. An explicitly strict reverse path filter produces an assertion explaining the conflict.

Setting `tun.enable = false` installs only the proxy package and creates no privileged wrappers. Use only one module to install this application into a given desktop session: installing both the Home Manager proxy package and the NixOS TUN package can put the proxy-only launcher first in `PATH`.

Options:

| Module | Option                              | Default               |
| ------ | ----------------------------------- | --------------------- |
| Both   | `programs.ednovas-cloud.enable`     | `false`               |
| Both   | `programs.ednovas-cloud.package`    | Bundled flake package |
| NixOS  | `programs.ednovas-cloud.tun.enable` | `false`               |
| NixOS  | `programs.ednovas-cloud.tun.group`  | `"ednovas-cloud"`     |

If overriding `package` in NixOS, preserve its `tunSupport` override and `coreRelativePaths` passthru attributes. The overlay is available as `inputs.ednovas-cloud.overlays.default`; modules work without adding it.

## Home Manager for proxy use

Import `inputs.ednovas-cloud.homeManagerModules.default` in your Home Manager module list, then set:

```nix
{ lib, ... }: {
  nixpkgs.config.allowUnfreePredicate = package:
    builtins.elem (lib.getName package) [ "ednovas-cloud" ];
  programs.ednovas-cloud.enable = true;
}
```

Home Manager has no TUN option and installs the proxy-only package. If Home Manager uses NixOS's global package set, put the unfree predicate in the NixOS configuration instead.

## Check and update releases

Run from the packaging repository, or pass `--repo /path/to/repository`:

```sh
nix run .#update -- --check
nix run .#update -- --write
nix flake check --print-build-logs
```

Check mode is the default and outputs JSON without changing files. Write mode downloads each configured AppImage, verifies its ELF architecture and embedded SquashFS header and bounds, computes its SHA-256 source hash, and atomically replaces `nix/release.json`. Errors leave the manifest untouched; older or unchanged versions produce no changes. It does not commit or push anything.

The release manifest maps Nix systems to upstream URLs, hashes, and AppImage architectures. To add ARM64 later, add an `aarch64-linux` entry with `appImageArchitecture = "arm64"` and validate the package on that architecture. The flake derives its supported systems from this map, and the updater requires every configured architecture in the newest release.

The build verifies the AppImage desktop version and extracts its filesystem without running the downloaded executable, using a single decompression thread. No FUSE mount is required. The desktop entry uses an absolute icon path so icon theme caching does not hide it.

The AppImage's obsolete GTK2 AppIndicator, Indicator, and GConf libraries are omitted because their dependencies are unavailable in current Nixpkgs. The package supplies GTK3 AppIndicator for Electron's tray integration.

The loader patch checks the upstream version, entrypoint, loader contents, and required bytecode integration markers. If these change, builds fail and the updater cannot publish an update PR until the integration is reviewed.

## GitHub automation

The update workflow runs daily at 03:23 UTC and supports manual dispatch. It checks for a new release, runs `nix flake check` (including both package builds), and only then pushes the reusable `ednovas-update` branch, creates or refreshes one PR, and requests squash auto-merge. The exact tested commit receives the `ednovas-update/build` status. Failed downloads or validation do not publish the candidate or create a PR.

The CI workflow uses Git for its update branch, commit, and push. Local version control uses Jujutsu. GitHub's built-in `GITHUB_TOKEN` is sufficient; downloads are public, and no PAT or App secret is required.

Configure the GitHub repository after publishing:

- Enable **Allow GitHub Actions to create and approve pull requests** under Actions → General → Workflow permissions. Organization settings may also control this permission.
- Enable **Allow auto-merge** and **Allow squash merging** under General → Pull Requests.
- If requiring validation on the update branch, require the `ednovas-update/build` status. PR/push workflows usually do not run for changes made with `GITHUB_TOKEN`; the update workflow supplies the build status itself.
- Required human reviews and other branch protection rules still apply. Auto-merge waits for those requirements and does not bypass them. Requiring a separate PR check that this token cannot trigger will prevent auto-merge.

The ordinary package-check workflow runs for human pushes and PRs. GitHub's remote PR/merge behavior can only be validated after publication; local checks do not contact a remote.

## Usage test

The Debian-based build froze during login on the development machine. The raw AppImage works as a normal user with `--no-sandbox`; the repackaged AppImage still needs the same manual usage test. Builds do not launch Electron. If it still freezes, try `ednovas-cloud --disable-gpu` to investigate graphics acceleration.

- Launch, sign in, load a subscription, and verify the tray and notifications on your desktop.
- Test ordinary proxy traffic and switching nodes. On desktops without an applicable system proxy backend, use the app's local HTTP/SOCKS port in your browser or environment settings.
- With the NixOS TUN module active and group membership refreshed, toggle TUN, verify its network interface and traffic, and confirm routing is restored when TUN stops.
- Verify the compatible core also works through its wrapper and a user outside the configured group cannot execute either wrapper.
- Confirm the GUI process runs under your own UID and no root relaunch prompt appears when TUN is available.

Automated checks cover builds, module configuration, updater failure behavior, archive compatibility guards, and core-launch interception. Live subscriptions, desktop integration, and TUN traffic need this usage test.
