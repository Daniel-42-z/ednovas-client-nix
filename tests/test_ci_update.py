import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest


class CiGateTests(unittest.TestCase):
    def RunWorkflow(self, changed, buildSuccess, malformedOutput=False):
        with tempfile.TemporaryDirectory() as directory:
            workPath = Path(directory)
            binPath = workPath / "bin"
            binPath.mkdir()
            commandLog = workPath / "commands.log"
            updateOutput = "not-json" if malformedOutput else json.dumps({
                "changed": changed, "latestVersion": "1.1.80"})
            nixScript = f'''#!/usr/bin/env bash
echo "nix $*" >> "$COMMAND_LOG"
if [[ "$1" == run ]]; then
  echo '{updateOutput}'
else
  exit {0 if buildSuccess else 1}
fi
'''
            for command, scriptData in {
                "nix": nixScript,
                "git": '''#!/usr/bin/env bash
echo "git $*" >> "$COMMAND_LOG"
if [[ "$1" == rev-parse ]]; then echo 1234567890123456789012345678901234567890; fi
''',
                "gh": '''#!/usr/bin/env bash
echo "gh $*" >> "$COMMAND_LOG"
if [[ "$1 $2" == "pr list" ]]; then
  if [[ -e "$PR_MARKER" ]]; then echo 1; fi
fi
if [[ "$1 $2" == "pr create" ]]; then touch "$PR_MARKER"; fi
''',
            }.items():
                scriptPath = binPath / command
                scriptPath.write_text(scriptData.replace("#!/usr/bin/env bash", "#!" + shutil.which("bash")))
                scriptPath.chmod(0o755)
            scriptPath = Path(__file__).resolve().parents[1] / "scripts/ci-update.sh"
            workflowEnv = {
                **os.environ, "PATH": str(binPath) + ":" + os.environ["PATH"],
                "GH_TOKEN": "test-placeholder", "GH_REPO": "test/repo",
                "DEFAULT_BRANCH": "main", "RUN_URL": "https://example.com/run",
                "COMMAND_LOG": str(commandLog), "PR_MARKER": str(workPath / "pr-created"),
            }
            result = subprocess.run(["bash", str(scriptPath)], cwd=workPath,
                                    env=workflowEnv, capture_output=True, text=True)
            return result.returncode, commandLog.read_text().splitlines()

    def test_unchanged_release_never_touches_git_or_github(self):
        exitCode, commands = self.RunWorkflow(False, True)
        self.assertEqual(exitCode, 0)
        self.assertEqual(commands, ["nix run .#update -- --write"])

    def test_failed_build_never_publishes_branch_or_pr(self):
        exitCode, commands = self.RunWorkflow(True, False)
        self.assertNotEqual(exitCode, 0)
        self.assertEqual(commands, ["nix run .#update -- --write",
                                    "nix flake check --print-build-logs"])

    def test_malformed_update_output_fails_without_publishing(self):
        exitCode, commands = self.RunWorkflow(True, True, malformedOutput=True)
        self.assertNotEqual(exitCode, 0)
        self.assertEqual(commands, ["nix run .#update -- --write"])

    def test_successful_build_precedes_push_pr_and_merge(self):
        exitCode, commands = self.RunWorkflow(True, True)
        self.assertEqual(exitCode, 0)
        buildIndex = commands.index("nix flake check --print-build-logs")
        pushIndex = next(index for index, command in enumerate(commands) if command.startswith("git push "))
        prIndex = next(index for index, command in enumerate(commands) if command.startswith("gh pr create "))
        mergeIndex = next(index for index, command in enumerate(commands) if command.startswith("gh pr merge "))
        self.assertLess(buildIndex, pushIndex)
        self.assertLess(pushIndex, prIndex)
        self.assertLess(prIndex, mergeIndex)
        self.assertIn("--force-with-lease=refs/heads/ednovas-update:", commands[pushIndex])
        self.assertIn("--match-head-commit 1234567890123456789012345678901234567890", commands[mergeIndex])
