import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

scriptRoot = Path(__file__).resolve().parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('candidate', scriptRoot / 'candidate.py')
candidateModule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidateModule)


class CandidateGateTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'identity': {'commit': 'a' * 40, 'parent': 'b' * 40, 'tree': 'c' * 64}}
        self.results = {suite: {'result': 'success', 'identity': copy.deepcopy(self.candidate['identity'])}
                        for suite in candidateModule.requiredSuites}

    def test_complete_results_pass(self):
        candidateModule.RequireResults(self.candidate, self.results)

    def test_runtime_failure_states_never_pass(self):
        for suite in candidateModule.requiredSuites:
            for result in ['failure', 'skipped', 'cancelled', 'timed_out', None, '']:
                with self.subTest(suite=suite, result=result):
                    results = copy.deepcopy(self.results)
                    results[suite]['result'] = result
                    with self.assertRaises(ValueError):
                        candidateModule.RequireResults(self.candidate, results)

    def test_missing_or_extra_results_never_pass(self):
        for suite in candidateModule.requiredSuites:
            results = copy.deepcopy(self.results)
            del results[suite]
            with self.assertRaises(ValueError):
                candidateModule.RequireResults(self.candidate, results)
        self.results['unexpected'] = self.results['build']
        with self.assertRaises(ValueError):
            candidateModule.RequireResults(self.candidate, self.results)

    def test_stale_commit_parent_or_tree_cannot_authorize_publication(self):
        for suite in candidateModule.requiredSuites:
            for field in ['commit', 'parent', 'tree']:
                results = copy.deepcopy(self.results)
                results[suite]['identity'][field] = 'different'
                with self.assertRaises(ValueError):
                    candidateModule.RequireResults(self.candidate, results)

    def test_unsuccessful_or_missing_jobs_cannot_pass(self):
        jobs = {job: {'result': 'success'} for job in ['prepare', 'validate']}
        candidateModule.RequireJobs(jobs)
        for job in jobs:
            for state in ['failure', 'skipped', 'cancelled', 'timed_out', None]:
                invalid = copy.deepcopy(jobs)
                invalid[job]['result'] = state
                with self.assertRaises(ValueError):
                    candidateModule.RequireJobs(invalid)
            invalid = copy.deepcopy(jobs)
            del invalid[job]
            with self.assertRaises(ValueError):
                candidateModule.RequireJobs(invalid)


class CiWorkflowTests(unittest.TestCase):
    def RunWorkflow(self, stage, changed=True, malformed=False, gate=True, stale=False):
        with tempfile.TemporaryDirectory() as directory:
            workPath = Path(directory)
            binPath = workPath / 'bin'
            binPath.mkdir()
            commandLog = workPath / 'commands.log'
            (workPath / 'nix').mkdir()
            (workPath / 'nix/release.json').write_text('{"version":"1.1.79"}')
            (workPath / '.candidate').mkdir()
            (workPath / '.candidate/manifest.json').write_text(json.dumps({
                'identity': {'commit':'a'*40, 'parent':'b'*40, 'tree':'c'*64}, 'release':{'version':'1.1.80'}}))
            updateOutput = 'not-json' if malformed else json.dumps({'changed':changed, 'latestVersion':'1.1.80'})
            scripts = {
                'nix': f'echo {ShlexQuote(updateOutput)}',
                'git': '''if [[ "$1" == rev-parse ]]; then
  if [[ "$*" == *main* ]]; then echo "$REMOTE_PARENT"; else echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; fi
fi''',
                'python3': 'if [[ "$2" == gate && "$GATE" != true ]]; then exit 1; fi',
                'gh': '''if [[ "$1" == api ]]; then
  case "$2" in
    */commits/*) echo bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
    */git/ref/heads/main) echo bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb ;;
    */git/ref/heads/ednovas-update) echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
  esac
fi
if [[ "$1 $2" == "pr list" && -f "$PR_MARKER" ]]; then echo 1; fi
if [[ "$1 $2" == "pr create" ]]; then touch "$PR_MARKER"; fi''',
            }
            for name, body in scripts.items():
                path = binPath / name
                path.write_text('#!' + shutil.which('bash') + '\nset -eu\necho "' + name + ' $*" >> "$COMMAND_LOG"\n' + body + '\n')
                path.chmod(0o755)
            env = {**os.environ, 'PATH':str(binPath)+':'+os.environ['PATH'], 'COMMAND_LOG':str(commandLog),
                   'GH_TOKEN':'test', 'GH_REPO':'test/repo', 'DEFAULT_BRANCH':'main', 'RUN_URL':'https://example.com/run',
                   'VALIDATION_NEEDS':'{}', 'PR_MARKER':str(workPath / 'pr-marker'),
                   'REMOTE_PARENT':('d' if stale else 'b')*40,
                   'GATE':str(gate).lower()}
            result = subprocess.run(['bash', str(scriptRoot / 'ci-update.sh'), stage], cwd=workPath, env=env, capture_output=True, text=True)
            return result.returncode, commandLog.read_text().splitlines(), result.stderr

    def test_unchanged_release_does_not_commit_or_publish(self):
        status, commands, errors = self.RunWorkflow('prepare', changed=False)
        self.assertEqual(status, 0, errors)
        self.assertFalse(any(command.startswith(('gh ', 'git commit ', 'git push ')) for command in commands))

    def test_malformed_candidate_does_not_publish(self):
        status, commands, _ = self.RunWorkflow('prepare', malformed=True)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(command.startswith(('gh ', 'git commit ')) for command in commands))

    def test_failed_gate_has_no_remote_mutation(self):
        status, commands, _ = self.RunWorkflow('publish', gate=False)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(command.startswith(('gh ', 'git push ')) for command in commands))

    def test_advanced_default_branch_requires_retest(self):
        status, commands, _ = self.RunWorkflow('publish', stale=True)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(command.startswith(('gh ', 'git push ')) for command in commands))

    def test_success_publishes_exact_head_after_gate(self):
        status, commands, errors = self.RunWorkflow('publish')
        self.assertEqual(status, 0, errors)
        gateIndex = next(i for i,c in enumerate(commands) if 'candidate.py gate' in c)
        pushIndex = next(i for i,c in enumerate(commands) if c.startswith('git push '))
        statusIndex = next(i for i,c in enumerate(commands) if 'statuses/' in c)
        mergeIndex = next(i for i,c in enumerate(commands) if c.startswith('gh pr merge '))
        self.assertLess(gateIndex, pushIndex)
        self.assertLess(pushIndex, statusIndex)
        self.assertLess(statusIndex, mergeIndex)
        self.assertIn('--match-head-commit ' + 'a'*40, commands[mergeIndex])
        self.assertFalse(any(c.startswith('jj ') for c in commands))
        self.assertIn('--force-with-lease=refs/heads/ednovas-update:' + 'a'*40, commands[pushIndex])


def ShlexQuote(value):
    import shlex
    return shlex.quote(value)


@unittest.skipUnless(shutil.which('git') and shutil.which('jq'), 'Git and jq are required')
class NativeGitTests(unittest.TestCase):
    def test_pull_request_merge_checkout_can_be_archived_for_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            repoPath = Path(directory, 'repo')
            repoPath.mkdir()
            env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}

            def Git(*args):
                return subprocess.run(['git', *args], cwd=repoPath, env=env, check=True, capture_output=True, text=True).stdout.strip()

            Git('init', '-b', 'main')
            Git('config', 'user.name', 'Runtime test')
            Git('config', 'user.email', 'runtime@example.invalid')
            Git('remote', 'add', 'origin', str(repoPath))
            shutil.copytree(scriptRoot, repoPath / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            (repoPath / 'nix').mkdir()
            (repoPath / 'nix/release.json').write_text('{"version":"1.1.79"}')
            (repoPath / '.gitignore').write_text('.candidate/\ncandidate.tar.gz\n')
            Git('add', '.')
            Git('commit', '-m', 'Baseline')
            Git('checkout', '-b', 'feature')
            (repoPath / 'feature').write_text('feature')
            Git('add', 'feature')
            Git('commit', '-m', 'Feature')
            Git('checkout', 'main')
            Git('merge', '--no-ff', 'feature', '-m', 'PR merge')
            mergeCommit = Git('rev-parse', 'HEAD')
            self.assertEqual(len(Git('rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)
            Git('checkout', '--detach', mergeCommit)
            Git('commit', '--allow-empty', '-m', 'Validate checkout')
            for action in ['prepare', 'archive']:
                result = subprocess.run(['python3', 'scripts/candidate.py', action, '--baseline-release', '{"version":"1.1.79"}'], cwd=repoPath, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((repoPath / '.candidate/manifest.json').read_text())
            self.assertEqual(manifest['identity']['parent'], mergeCommit)
            self.assertTrue((repoPath / 'candidate.tar.gz').is_file())

    def test_candidate_publication_to_disposable_local_remote(self):
        with tempfile.TemporaryDirectory() as directory:
            workPath = Path(directory)
            originPath = workPath / 'origin'
            candidatePath = workPath / 'candidate'
            binPath = workPath / 'bin'
            binPath.mkdir()
            configPath = workPath / 'config'
            configPath.mkdir()
            testEnv = {**os.environ, 'XDG_CONFIG_HOME': str(configPath)}

            def Git(*args, cwd=originPath):
                result = subprocess.run(['git', *args], cwd=cwd, text=True, capture_output=True, env=testEnv)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            Git('init', '-b', 'main', str(originPath), cwd=workPath)
            Git('config', '--local', 'user.name', 'Runtime test')
            Git('config', '--local', 'user.email', 'runtime@example.invalid')
            shutil.copytree(scriptRoot, originPath / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            (originPath / 'nix').mkdir()
            (originPath / 'nix/release.json').write_text('{"version":"1.1.79"}')
            (originPath / '.gitignore').write_text('.candidate/\nvalidation-results/\ncandidate.tar.gz\n')
            Git('add', '.')
            Git('commit', '-m', 'Baseline')
            Git('checkout', '-b', 'ednovas-update')
            (originPath / 'nix/release.json').write_text('{"version":"1.1.78"}')
            Git('commit', '-am', 'Existing update PR')
            Git('checkout', 'main')
            Git('clone', str(originPath), str(candidatePath), cwd=workPath)
            Git('checkout', '--detach', 'origin/main', cwd=candidatePath)
            nixPath = binPath / 'nix'
            nixPath.write_text('#!' + shutil.which('bash') + '\nset -eu\nprintf \'{"version":"1.1.80"}\' > nix/release.json\nprintf \'{"changed":true,"latestVersion":"1.1.80"}\\n\'\n')
            nixPath.chmod(0o755)
            ghPath = binPath / 'gh'
            ghPath.write_text('#!' + shutil.which('bash') + '''
set -eu
if [[ "$1" == api ]]; then
  case "$2" in
    */git/ref/heads/main|*/commits/*) jq -r '.identity.parent' .candidate/manifest.json ;;
    */git/ref/heads/ednovas-update) jq -r '.identity.commit' .candidate/manifest.json ;;
  esac
fi
if [[ "$1 $2" == "pr list" && -e "$PR_MARKER" ]]; then echo 1; fi
if [[ "$1 $2" == "pr create" ]]; then touch "$PR_MARKER"; fi
''')
            ghPath.chmod(0o755)
            env = {**testEnv, 'PATH': str(binPath) + ':' + os.environ['PATH'],
                   'DEFAULT_BRANCH': 'main', 'GH_TOKEN': 'disposable-placeholder', 'GH_REPO': 'test/local',
                   'RUN_URL': 'https://example.invalid/run', 'PR_MARKER': str(workPath / 'pr-created'),
                   'VALIDATION_NEEDS': json.dumps({job: {'result': 'success'} for job in ['prepare', 'validate']})}
            prepared = subprocess.run(['bash', 'scripts/ci-update.sh', 'prepare'], cwd=candidatePath, env=env, capture_output=True, text=True)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            manifest = json.loads((candidatePath / '.candidate/manifest.json').read_text())
            # Match the failing Actions layout: output in the checkout, with
            # generated build outputs and local JJ state that must not ship.
            (candidatePath / '.jj').mkdir()
            (candidatePath / '.jj/private').write_text('local-only')
            (candidatePath / 'result').symlink_to(workPath)
            (candidatePath / 'generated.log').write_text('not source')
            archived = subprocess.run(['python3', 'scripts/candidate.py', 'archive'], cwd=candidatePath, env=env, capture_output=True, text=True)
            self.assertEqual(archived.returncode, 0, archived.stderr)
            restoredPath = workPath / 'restored'
            restoredPath.mkdir()
            subprocess.run(['tar', '-xzf', str(candidatePath / 'candidate.tar.gz'), '-C', str(restoredPath)], check=True)
            self.assertFalse((restoredPath / '.jj').exists())
            self.assertFalse((restoredPath / 'result').exists())
            self.assertFalse((restoredPath / 'generated.log').exists())
            self.assertFalse((restoredPath / 'candidate.tar.gz').exists())
            # Verify the archived remote is the actual origin, not the
            # preparer's temporary checkout, and publish from a fresh runner.
            self.assertEqual(Git('remote', 'get-url', 'origin', cwd=restoredPath), str(originPath))
            candidatePath = restoredPath
            configPath.rename(workPath / 'preparer-config')
            configPath.mkdir()
            verified = subprocess.run(['python3', 'scripts/candidate.py', 'verify'], cwd=candidatePath, env=env, capture_output=True, text=True)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            releasePath = candidatePath / 'nix/release.json'
            releasePath.write_text('{"version":"dirty"}')
            dirty = subprocess.run(['python3', 'scripts/candidate.py', 'verify'], cwd=candidatePath, env=env, capture_output=True, text=True)
            self.assertNotEqual(dirty.returncode, 0)
            Git('restore', 'nix/release.json', cwd=candidatePath)
            for suite in candidateModule.requiredSuites:
                subprocess.run(['python3', 'scripts/candidate.py', 'record', '--suite', suite], cwd=candidatePath, env=env, check=True, capture_output=True)
            result = subprocess.run(['bash', 'scripts/ci-update.sh', 'publish'], cwd=candidatePath, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            published = Git('rev-parse', 'refs/heads/ednovas-update', cwd=originPath)
            self.assertEqual(published, manifest['identity']['commit'])
            self.assertTrue((workPath / 'pr-created').exists())
