#!/usr/bin/env bash
set -euo pipefail
suite=${1:?Use build, integration, or network}
outputPath=${2:-runtime-results/$suite}
mkdir -p "$outputPath"
if [[ -n $(find "$outputPath" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
  echo 'Use an empty output directory for a fresh validation run.' >&2
  exit 1
fi
outputPath=$(realpath "$outputPath")
if [[ "$suite" == build ]]; then
  nix flake check --print-build-logs 2>&1 | tee "$outputPath/build.log"
else
  case "$suite" in integration|network) ;; *) exit 2 ;; esac
  python3 scripts/runtime-preflight.py 2>&1 | tee "$outputPath/preflight.log"
  driverPath=$(nix build ".#runtime-$suite-driver" --no-link --print-out-paths --print-build-logs 2> >(tee "$outputPath/build.log" >&2))
  # The driver is executed on every run, even when all its builds are cached.
  hostRuntimePath=$(mktemp -d /tmp/ednovas-vm.XXXXXXXX)
  trap 'rm -rf -- "$hostRuntimePath"' EXIT
  XDG_RUNTIME_DIR="$hostRuntimePath" "$driverPath/bin/nixos-test-driver" --output_directory "$outputPath" 2>&1 | tee "$outputPath/driver.log"
  python3 - "$outputPath/suite.json" "$suite" <<'PY'
import json, sys
assert json.load(open(sys.argv[1])) == {"suite": sys.argv[2], "result": "success"}
PY
fi
