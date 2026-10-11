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
        for suite in ['x11', 'wayland', 'network']:
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
        jobs = {job: {'result': 'success'} for job in ['prepare', 'validate', 'acceptance']}
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

    def test_auto_merge_requires_candidate_repetitions_and_all_fault_checks(self):
        candidate = {'release': {'version': '1.1.80'}, 'identity': self.candidate['identity'],
                     'validationSource': 'source'}
        faultChecks = [fault + '-' + suite for fault in ['close', 'restore', 'quit', 'renderer']
                       for suite in ['x11', 'wayland']] + ['stopped-core-network', 'bypass-tun-network']
        evidence = {'release': candidate['release'], 'identity': candidate['identity'],
                    'validationSource': 'source', 'baselineRuns': ['run-a', 'run-b', 'run-c'],
                    'faults': {fault: 'detected' for fault in faultChecks}}
        candidateModule.CheckAcceptance(candidate, evidence)
        for field, value in [('baselineRuns', ['same'] * 3), ('baselineRuns', ['a', 'b']),
                             ('faults', {}), ('identity', {}),
                             ('release', {'version': 'old'}), ('validationSource', 'stale')]:
            invalid = copy.deepcopy(evidence)
            invalid[field] = value
            with self.assertRaises(ValueError):
                candidateModule.CheckAcceptance(candidate, invalid)
        for field in ['commit', 'parent', 'tree']:
            invalid = copy.deepcopy(evidence)
            invalid['identity'][field] = 'different'
            with self.assertRaises(ValueError):
                candidateModule.CheckAcceptance(candidate, invalid)
        for fault in faultChecks:
            for state in ['missing', 'failure', 'skipped', 'cancelled', 'timed_out']:
                invalid = copy.deepcopy(evidence)
                if state == 'missing':
                    del invalid['faults'][fault]
                else:
                    invalid['faults'][fault] = state
                with self.assertRaises(ValueError):
                    candidateModule.CheckAcceptance(candidate, invalid)


class CiWorkflowTests(unittest.TestCase):
    def RunWorkflow(self, stage, changed=True, malformed=False, gate=True, stale=False, acceptance=True):
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
                'jj': '''if [[ "$1" == log ]]; then
  if [[ "$*" == *main@origin* ]]; then echo "$REMOTE_PARENT"; else echo aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; fi
fi
if [[ "$1 $2" == "bookmark list" ]]; then echo 'ednovas-update@origin'; fi''',
                'python3': '''if [[ "$2" == gate && "$GATE" != true ]]; then exit 1; fi
if [[ "$2" == acceptance && "$ACCEPTANCE" != true ]]; then exit 1; fi''',
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
                   'GATE':str(gate).lower(),
                   'ACCEPTANCE':str(acceptance).lower()}
            result = subprocess.run(['bash', str(scriptRoot / 'ci-update.sh'), stage], cwd=workPath, env=env, capture_output=True, text=True)
            return result.returncode, commandLog.read_text().splitlines(), result.stderr

    def test_unchanged_release_does_not_commit_or_publish(self):
        status, commands, errors = self.RunWorkflow('prepare', changed=False)
        self.assertEqual(status, 0, errors)
        self.assertFalse(any(command.startswith(('gh ', 'jj describe ', 'jj git push ')) for command in commands))

    def test_malformed_candidate_does_not_publish(self):
        status, commands, _ = self.RunWorkflow('prepare', malformed=True)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(command.startswith(('gh ', 'jj describe ')) for command in commands))

    def test_failed_gate_has_no_remote_mutation(self):
        status, commands, _ = self.RunWorkflow('publish', gate=False)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(command.startswith(('gh ', 'jj git push ', 'jj bookmark set ')) for command in commands))

    def test_advanced_default_branch_requires_retest(self):
        status, commands, _ = self.RunWorkflow('publish', stale=True)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(command.startswith(('gh ', 'jj git push ')) for command in commands))

    def test_success_publishes_exact_head_after_gate(self):
        status, commands, errors = self.RunWorkflow('publish')
        self.assertEqual(status, 0, errors)
        gateIndex = next(i for i,c in enumerate(commands) if 'candidate.py gate' in c)
        pushIndex = next(i for i,c in enumerate(commands) if c.startswith('jj git push '))
        statusIndex = next(i for i,c in enumerate(commands) if 'statuses/' in c)
        mergeIndex = next(i for i,c in enumerate(commands) if c.startswith('gh pr merge '))
        self.assertLess(gateIndex, pushIndex)
        self.assertLess(pushIndex, statusIndex)
        self.assertLess(statusIndex, mergeIndex)
        self.assertIn('--match-head-commit ' + 'a'*40, commands[mergeIndex])
        self.assertFalse(any(c.startswith('git ') for c in commands))

    def test_failed_acceptance_blocks_publication_and_auto_merge(self):
        status, commands, _ = self.RunWorkflow('publish', acceptance=False)
        self.assertNotEqual(status, 0)
        self.assertFalse(any(c.startswith(('gh ', 'jj git push ', 'jj bookmark set ')) for c in commands))


def ShlexQuote(value):
    import shlex
    return shlex.quote(value)


@unittest.skipUnless(shutil.which('jj') and shutil.which('jq'), 'Jujutsu and jq are required')
class NativeJujutsuTests(unittest.TestCase):
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

            def Jj(*args, cwd=originPath):
                result = subprocess.run(['jj', *args], cwd=cwd, text=True, capture_output=True, env=testEnv)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            subprocess.run(['jj', 'git', 'init', '--colocate', str(originPath)], check=True, capture_output=True, env=testEnv)
            Jj('config', 'set', '--repo', 'user.name', 'Runtime test')
            Jj('config', 'set', '--repo', 'user.email', 'runtime@example.invalid')
            shutil.copytree(scriptRoot, originPath / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
            (originPath / 'nix').mkdir()
            (originPath / 'nix/release.json').write_text('{"version":"1.1.79"}')
            (originPath / '.gitignore').write_text('.candidate/\nvalidation-results/\nruntime-acceptance.json\n')
            Jj('describe', '-m', 'Baseline')
            Jj('bookmark', 'set', 'main', '-r', '@')
            Jj('new', 'main')
            (originPath / 'nix/release.json').write_text('{"version":"1.1.78"}')
            Jj('describe', '-m', 'Existing update PR')
            Jj('bookmark', 'set', 'ednovas-update', '-r', '@')
            Jj('git', 'export')
            subprocess.run(['jj', 'git', 'clone', '--colocate', str(originPath), str(candidatePath)], check=True, capture_output=True, env=testEnv)
            Jj('new', 'main@origin', cwd=candidatePath)
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
                   'VALIDATION_NEEDS': json.dumps({job: {'result': 'success'} for job in ['prepare', 'validate', 'acceptance']})}
            prepared = subprocess.run(['bash', 'scripts/ci-update.sh', 'prepare'], cwd=candidatePath, env=env, capture_output=True, text=True)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            manifest = json.loads((candidatePath / '.candidate/manifest.json').read_text())
            # Synthetic evidence here tests publication plumbing; real CI
            # creates it only after fresh repetitions and fault assertions.
            faultChecks = [fault + '-' + suite for fault in ['close', 'restore', 'quit', 'renderer']
                           for suite in ['x11', 'wayland']] + ['stopped-core-network', 'bypass-tun-network']
            (candidatePath / 'runtime-acceptance.json').write_text(json.dumps({
                'identity': manifest['identity'], 'release': manifest['release'],
                'validationSource': manifest['validationSource'], 'baselineRuns': ['a', 'b', 'c'],
                'faults': {fault: 'detected' for fault in faultChecks}}))
            # Reproduce an artifact restored on a fresh runner: preserve repo
            # data, but not the preparer's external secure Jujutsu config.
            restoredPath = workPath / 'restored'
            shutil.copytree(candidatePath, restoredPath, symlinks=True)
            candidatePath = restoredPath
            configPath.rename(workPath / 'preparer-config')
            configPath.mkdir()
            for suite in candidateModule.requiredSuites:
                subprocess.run(['python3', 'scripts/candidate.py', 'record', '--suite', suite], cwd=candidatePath, env=env, check=True, capture_output=True)
            result = subprocess.run(['bash', 'scripts/ci-update.sh', 'publish'], cwd=candidatePath, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            published = Jj('log', '--no-pager', '-G', '-r', 'ednovas-update@origin', '-T', 'commit_id', cwd=candidatePath)
            self.assertEqual(published, manifest['identity']['commit'])
            self.assertTrue((workPath / 'pr-created').exists())
