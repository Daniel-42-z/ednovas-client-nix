"""Bind validation and publication to the exact Git candidate."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import shutil

requiredSuites = {"build", "x11", "wayland", "network"}


def Run(*args):
    return subprocess.check_output(args, text=True).strip()


def Identity():
    # Bind evidence to committed content, never an unsnapshotted working tree.
    subprocess.run(["git", "diff", "--exit-code", "--quiet"], check=True)
    subprocess.run(["git", "diff", "--cached", "--exit-code", "--quiet"], check=True)
    revisions = Run("git", "rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(revisions) != 2:
        raise ValueError("Candidate must have exactly one parent")
    digest = hashlib.sha256()
    for name in sorted(Run("git", "ls-files", "-z").rstrip("\0").split("\0")):
        path = Path(name)
        if path.is_symlink():
            raise ValueError("Candidate symlinks are unsupported")
        data = path.read_bytes()
        digest.update(json.dumps([name, path.stat().st_mode & 0o111, len(data)]).encode() + b"\0" + data)
    return {"commit": revisions[0], "parent": revisions[1], "tree": digest.hexdigest()}


def Archive(manifestPath, outputPath):
    candidate = json.loads(manifestPath.read_text())
    if Identity() != candidate["identity"]:
        raise ValueError("Cannot archive a changed candidate")
    outputPath = outputPath.resolve()
    # Clone only committed Git content: no .jj state, build outputs, runner
    # credentials, or generated artifacts. Write outside the archived tree.
    with tempfile.TemporaryDirectory() as directory:
        snapshotPath = Path(directory, "snapshot")
        subprocess.run(["git", "clone", "--no-local", "--no-checkout", ".", str(snapshotPath)], check=True)
        subprocess.run(["git", "-C", str(snapshotPath), "checkout", "--detach", candidate["identity"]["commit"]], check=True)
        origin = Run("git", "remote", "get-url", "origin")
        subprocess.run(["git", "-C", str(snapshotPath), "remote", "set-url", "origin", origin], check=True)
        snapshotManifest = snapshotPath / ".candidate/manifest.json"
        snapshotManifest.parent.mkdir()
        shutil.copyfile(manifestPath, snapshotManifest)
        archivePath = Path(directory, "candidate.tar.gz")
        with tarfile.open(archivePath, "w:gz") as archive:
            archive.add(snapshotPath, arcname=".")
        shutil.copyfile(archivePath, outputPath)


def RequireResults(candidate, results):
    if set(results) != requiredSuites:
        raise ValueError("All four validation suites are required")
    for suite, result in results.items():
        if result != {"result": "success", "identity": candidate["identity"]}:
            raise ValueError(f"Missing, stale, or unsuccessful {suite} validation")


def RequireJobs(needs):
    if set(needs) != {"prepare", "validate", "acceptance"} or any(job.get("result") != "success" for job in needs.values()):
        raise ValueError("Failed, skipped, cancelled, timed-out, or missing jobs cannot publish")


def CheckAcceptance(candidate, evidence):
    # This attestation is produced only by runtime-acceptance.yml after actual
    # candidate repetitions and deliberate failures in the same update run.
    if evidence.get("release") != candidate["release"]:
        raise ValueError("Acceptance evidence is for a different candidate release")
    if evidence.get("identity") != candidate["identity"]:
        raise ValueError("Acceptance evidence is for a different commit, parent, or tree")
    baselineRuns = evidence.get("baselineRuns", [])
    if len(baselineRuns) != 3 or len(set(baselineRuns)) != 3 or not all(isinstance(run, str) and run for run in baselineRuns):
        raise ValueError("Three independent successful baseline runs are required")
    expectedFaults = {fault + "-" + suite for fault in ["close", "restore", "quit", "renderer"]
                      for suite in ["x11", "wayland"]}
    expectedFaults |= {"stopped-core-network", "bypass-tun-network"}
    if set(evidence.get("faults", {})) != expectedFaults or any(
            value != "detected" for value in evidence["faults"].values()):
        raise ValueError("All runtime fault demonstrations are required")
    if evidence.get("validationSource") != candidate["validationSource"]:
        raise ValueError("Acceptance evidence is stale for the validation implementation")


def ValidationSource():
    digest = hashlib.sha256()
    paths = [Path("flake.nix"), Path("flake.lock"), Path("nix/checks.nix"), Path("nix/package.nix"), Path("nix/modules/nixos.nix")]
    paths += list(Path("nix/runtime").glob("*"))
    paths += [Path("scripts") / name for name in ["root-helper.py", "fontconfig.py", "validate.sh", "runtime-fault.py", "runtime-preflight.py", "candidate.py", "ci-update.sh"]]
    paths += list(Path(".github/workflows").glob("*.yml"))
    for path in sorted(paths):
        if path.is_file():
            digest.update(str(path).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def Main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify", "record", "gate", "acceptance", "archive"])
    parser.add_argument("--manifest", default=".candidate/manifest.json")
    parser.add_argument("--suite")
    parser.add_argument("--results", default="validation-results")
    parser.add_argument("--needs")
    parser.add_argument("--baseline-release")
    parser.add_argument("--output", default="candidate.tar.gz")
    parser.add_argument("--evidence", default="runtime-acceptance.json")
    args = parser.parse_args()
    manifestPath = Path(args.manifest)
    if args.action == "archive":
        Archive(manifestPath, Path(args.output))
        return
    if args.action == "prepare":
        manifestPath.parent.mkdir(parents=True, exist_ok=True)
        manifestPath.write_text(json.dumps({"identity": Identity(), "release": json.loads(Path("nix/release.json").read_text()),
                                           "baselineRelease": json.loads(args.baseline_release), "validationSource": ValidationSource()}, indent=2))
        return
    candidate = json.loads(manifestPath.read_text())
    if Identity() != candidate["identity"]:
        raise ValueError("Working candidate differs from validated commit, parent, or tree")
    if json.loads(Path("nix/release.json").read_text()) != candidate["release"]:
        raise ValueError("Candidate release manifest changed")
    if args.action == "record":
        if args.suite not in requiredSuites:
            raise ValueError("Unknown suite")
        resultPath = Path(args.results, args.suite + ".json")
        resultPath.parent.mkdir(parents=True, exist_ok=True)
        resultPath.write_text(json.dumps({"result": "success", "identity": candidate["identity"]}))
    elif args.action == "gate":
        needs = json.loads(args.needs)
        RequireJobs(needs)
        results = {path.stem: json.loads(path.read_text()) for path in Path(args.results).glob("*.json")}
        RequireResults(candidate, results)
    elif args.action == "acceptance":
        CheckAcceptance(candidate, json.loads(Path(args.evidence).read_text()))


if __name__ == "__main__":
    Main()
