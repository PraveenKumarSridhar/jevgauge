#!/usr/bin/env python3
"""Check a local Hermes Git revision in a disposable checkout. Never activate it.

This executes trusted candidate source, not a sandbox. Supply an independently
prepared Hermes test interpreter. Config contains data, never shell commands.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import signal
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / 'integration/compatibility.json'


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError(f'Invalid relative path: {value!r}')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or value.startswith('-'):
        raise ValueError(f'Unsafe relative path: {value!r}')
    return path


def contained(root, value):
    relative_path(value)
    target = (root / value).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes its root: {value!r}')
    return target


def load_config(path, *, content=None):
    data = json.loads(path.read_bytes() if content is None else content)
    expected = {'schema_version', 'repository', 'update_policy', 'candidates', 'patch_base_commit', 'patches',
                'fixtures', 'tests', 'required_api', 'test_runtime', 'command_timeout_seconds', 'test_timeout_seconds', 'test_workers'}
    if not isinstance(data, dict) or set(data) != expected:
        raise ValueError('Unknown or missing compatibility configuration fields')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('Unsupported compatibility schema')
    if data['update_policy'] != 'retain_verified_on_failure':
        raise ValueError('Only retain_verified_on_failure is implemented')
    runtime = data['test_runtime']
    if (not isinstance(runtime, dict) or set(runtime) != {'python', 'node'}
            or not isinstance(runtime['python'], str) or not re.fullmatch(r'3\.\d+', runtime['python'])
            or not isinstance(runtime['node'], str) or not re.fullmatch(r'\d+', runtime['node'])):
        raise ValueError('test_runtime requires Python major.minor and Node major versions')
    if not isinstance(data['repository'], str) or not data['repository'].startswith('https://'):
        raise ValueError('Repository provenance must be an HTTPS URL')
    if not isinstance(data['patch_base_commit'], str) or not re.fullmatch(r'[0-9a-f]{40}', data['patch_base_commit']):
        raise ValueError('Patch base must be a full lowercase commit SHA')
    if not isinstance(data['required_api'], dict) or not data['required_api']:
        raise ValueError('Expected API markers are required')
    for path, markers in data['required_api'].items():
        relative_path(path)
        if not isinstance(markers, dict) or not markers:
            raise ValueError('API markers must be a nonempty object')
        for name, version in markers.items():
            if not name.isidentifier() or type(version) is not int or version < 1:
                raise ValueError('Invalid API marker/version')
    for key, low, high in [('command_timeout_seconds', 1, 600),
                           ('test_timeout_seconds', 1, 3600), ('test_workers', 1, 16)]:
        if type(data[key]) is not int or not low <= data[key] <= high:
            raise ValueError(f'Invalid {key}')
    for key in ['candidates', 'patches', 'tests']:
        if not isinstance(data[key], list) or not data[key]:
            raise ValueError(f'{key} must be a nonempty list')
    if not isinstance(data['fixtures'], list):
        raise ValueError('fixtures must be a list')
    for candidate in data['candidates']:
        if not isinstance(candidate, dict) or set(candidate) != {'name', 'ref'}:
            raise ValueError('Invalid candidate')
        if any(not isinstance(v, str) or not v or v.startswith('-') for v in candidate.values()):
            raise ValueError('Invalid candidate name/ref')
    for patch in data['patches']:
        if (not isinstance(patch, dict) or
                set(patch) not in ({'path', 'exclude'}, {'path', 'exclude', 'context_lines'})):
            raise ValueError('Invalid patch specification')
        relative_path(patch['path'])
        if not isinstance(patch['exclude'], list):
            raise ValueError('Patch excludes must be a list')
        if (type(patch.get('context_lines', 3)) is not int or
                not 1 <= patch.get('context_lines', 3) <= 3):
            raise ValueError('Patch context_lines must be between 1 and 3')
        for excluded in patch['exclude']:
            relative_path(excluded)
    destinations = set()
    for fixture in data['fixtures']:
        if not isinstance(fixture, dict) or set(fixture) != {'source', 'destination'}:
            raise ValueError('Invalid fixture specification')
        relative_path(fixture['source'])
        relative_path(fixture['destination'])
        if not fixture['destination'].startswith('tests/') or fixture['destination'] in destinations:
            raise ValueError('Fixtures require unique destinations under tests/')
        destinations.add(fixture['destination'])
    for test in data['tests']:
        relative_path(test)
        if not test.startswith('tests/') or not test.endswith('.py'):
            raise ValueError('Explicit Python test files required')
    if len(set(data['tests'])) != len(data['tests']):
        raise ValueError('Duplicate test paths')
    return data


def check_api(repo, expected):
    for name, markers in expected.items():
        tree = ast.parse(contained(repo, name).read_text())
        found = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found[target.id] = node.value.value
        for marker, version in markers.items():
            if type(found.get(marker)) is not int or found[marker] != version:
                raise ValueError(f'Missing/unsupported API marker: {marker}')


EVIDENCE_PLUGIN = '''
import json, os, pytest
from pathlib import Path
discovered = []
collected = []
reports = []
def pytest_collectreport(report):
    if report.passed:
        discovered.extend(item.nodeid for item in report.result if isinstance(item, pytest.Item))
def pytest_collection_finish(session):
    collected.extend(item.nodeid for item in session.items)
def pytest_runtest_logreport(report):
    reports.append(dict(nodeid=report.nodeid, phase=report.when,
                        outcome=report.outcome, xfail=hasattr(report, "wasxfail")))
def pytest_sessionfinish(session, exitstatus):
    destination = Path(OUTPUT) / (str(os.getpid()) + ".json")
    destination.write_text(json.dumps(dict(discovered=discovered, collected=collected, reports=reports,
                                          exitstatus=int(exitstatus))))
'''


def test_evidence(directory, required):
    files = {path: set() for path in required}
    discovered = set()
    outcomes = {}
    for path in directory.glob('*.json'):
        report = json.loads(path.read_text())
        if report['exitstatus'] != 0:
            raise ValueError('A pytest subprocess failed')
        for nodeid in report['discovered']:
            test_file = nodeid.split('::', 1)[0]
            if test_file not in files or nodeid in discovered:
                raise ValueError('Unexpected or duplicate discovered test')
            discovered.add(nodeid)
        for nodeid in report['collected']:
            test_file = nodeid.split('::', 1)[0]
            if test_file not in files:
                raise ValueError('Unexpected test file in evidence')
            if nodeid in outcomes:
                raise ValueError('Duplicate test execution in evidence')
            files[test_file].add(nodeid)
            outcomes[nodeid] = []
        for item in report['reports']:
            if item['nodeid'] not in outcomes:
                raise ValueError('Uncollected test in evidence')
            outcomes[item['nodeid']].append(item)
    if discovered != set(outcomes):
        raise ValueError('Required tests were deselected before execution')
    if any(not nodes for nodes in files.values()):
        raise ValueError('Required test file produced no collected-test evidence')
    for phases in outcomes.values():
        if (len(phases) != 3 or {item['phase'] for item in phases} != {'setup', 'call', 'teardown'}
                or any(item['outcome'] != 'passed' or item['xfail'] for item in phases)):
            raise ValueError('Required test skipped, xfailed, incomplete or failed')
    return {'files': len(files), 'passed': len(outcomes), 'skipped': 0, 'failed': 0}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_environment(home, python):
    return {
        'HOME': str(home), 'HERMES_HOME': str(home / '.hermes'),
        'XDG_CONFIG_HOME': str(home / '.config'), 'XDG_CACHE_HOME': str(home / '.cache'),
        'PATH': os.pathsep.join([str(python.parent), os.defpath]),
        'LANG': 'C.UTF-8', 'TZ': 'UTC', 'PYTHONHASHSEED': '0',
        'PYTHONNOUSERSITE': '1', 'HERMES_PYTHON': str(python),
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_TERMINAL_PROMPT': '0',
    }


def run(argv, *, cwd, env, timeout, log):
    """Bound a process group and retire its children on every exit path."""
    with log.open('ab') as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    if code:
        raise RuntimeError(f'Command failed ({code}); see {log.name}')


def verify(source, ref, python, output_parent, config_path=DEFAULT_CONFIG, *, allow_remote_snapshot=False):
    if os.name != 'posix':
        raise ValueError('Candidate verification currently supports macOS and Linux')
    config_bytes = config_path.read_bytes()
    config = load_config(config_path, content=config_bytes)
    source = source.resolve(strict=True)
    # Keep venv executable spelling: resolving this symlink would bypass the venv.
    python = python.absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError('Test Python must be an executable file')
    output_parent = output_parent.resolve()
    if output_parent == source or output_parent.is_relative_to(source):
        raise ValueError('Evidence directory must be outside the source checkout')
    # Read all integration inputs before executing any candidate code.
    inputs = {}
    for item in config['patches']:
        path = contained(ROOT, item['path'])
        inputs[item['path']] = path.read_bytes()
    for item in config['fixtures']:
        path = contained(ROOT, item['source'])
        inputs[item['source']] = path.read_bytes()
    output_parent.mkdir(parents=True, exist_ok=True)
    evidence = Path(tempfile.mkdtemp(prefix='hermes-check-', dir=output_parent))
    receipt = {'schema_version': 1, 'status': 'checking', 'activated': False,
               'scope': 'backend-lifecycle-tests', 'requested_ref': ref,
               'intended_update_policy': config['update_policy'], 'activation_supported': False,
               'config_sha256': hashlib.sha256(config_bytes).hexdigest(), 'config': config,
               'inputs': {name: hashlib.sha256(value).hexdigest() for name, value in inputs.items()},
               'tests': config['tests'], 'started_at': datetime.now(timezone.utc).isoformat()}
    log = evidence / 'check.log'
    stage = 'prepare'
    try:
        with tempfile.TemporaryDirectory(prefix='hermes-candidate-') as temp:
            scratch = Path(temp)
            home = scratch / 'home'
            home.mkdir()
            repo = scratch / 'hermes'
            env = clean_environment(home, python)
            timeout = config['command_timeout_seconds']
            sha = subprocess.check_output(
                ['git', '-C', str(source), 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}'],
                env=env, timeout=timeout, stderr=subprocess.PIPE, text=True).strip()
            receipt['hermes_commit'] = sha
            stage = 'interpreter'
            python_info = scratch / 'python.json'
            probe = ('import pytest,sys,json,importlib.metadata,pathlib; '
                     'value=dict(executable=sys.executable,version=sys.version,prefix=sys.prefix,'
                     'minor_version=".".join(map(str,sys.version_info[:2])),'
                     'packages=sorted((d.metadata["Name"],d.version) for d in importlib.metadata.distributions())); '
                     f'pathlib.Path({str(python_info)!r}).write_text(json.dumps(value))')
            run([str(python), '-c', probe], cwd=scratch, env=env, timeout=timeout, log=log)
            receipt['python'] = json.loads(python_info.read_text())
            if receipt['python']['minor_version'] != config['test_runtime']['python']:
                raise ValueError('Test interpreter does not match configured Python version')
            stage = 'snapshot'
            run(['git', 'init', '--quiet', str(repo)], cwd=scratch, env=env, timeout=timeout, log=log)
            fetch = ['git', 'fetch', '--quiet', '--depth=1', '--no-tags']
            try:
                run(fetch + [source.as_uri(), sha], cwd=repo, env=env, timeout=timeout, log=log)
                receipt['snapshot_source'] = 'local'
            except RuntimeError:
                if not allow_remote_snapshot:
                    raise
                # A partial local clone may know the commit but lack its tree. Fetch
                # the already-resolved SHA, never a moving branch, into a fresh temp repo.
                shutil.rmtree(repo)
                run(['git', 'init', '--quiet', str(repo)], cwd=scratch, env=env, timeout=timeout, log=log)
                run(fetch + [config['repository'], sha], cwd=repo, env=env, timeout=timeout, log=log)
                receipt['snapshot_source'] = 'remote_exact_commit'
            fetched = subprocess.check_output(
                ['git', '-C', str(repo), 'rev-parse', 'FETCH_HEAD^{commit}'],
                env=env, timeout=timeout, text=True).strip()
            if fetched != sha:
                raise ValueError('Snapshot fetch did not return the resolved candidate commit')
            run(['git', '-c', 'core.hooksPath=' + str(scratch / 'no-hooks'), 'checkout', '--quiet', '--detach', sha],
                cwd=repo, env=env, timeout=timeout, log=log)
            stage = 'integration'
            base_fetched = False
            receipt['patch_applications'] = []
            for index, patch in enumerate(config['patches']):
                staged_patch = scratch / f'integration-{index}.patch'
                staged_patch.write_bytes(inputs[patch['path']])
                command = ['git', 'apply', '-C', str(patch.get('context_lines', 3))]
                command += ['--exclude=' + x for x in patch['exclude']]
                try:
                    run(command + ['--check', str(staged_patch)], cwd=repo, env=env, timeout=timeout, log=log)
                except RuntimeError:
                    base = config['patch_base_commit']
                    # Three-way application is allowed only for a proven descendant of the
                    # reviewed patch base. A conflict fails; no markers or guesses are accepted.
                    ancestor = subprocess.run(
                        ['git', '-C', str(source), 'merge-base', '--is-ancestor', base, sha],
                        env=env, capture_output=True, timeout=timeout, check=False)
                    if ancestor.returncode:
                        raise ValueError('Cannot prove candidate descends from reviewed patch base')
                    if not base_fetched:
                        run(['git', 'fetch', '--quiet', '--depth=1', '--no-tags', source.as_uri(), base],
                            cwd=repo, env=env, timeout=timeout, log=log)
                        base_fetched = True
                    run(command + ['--3way', '--check', str(staged_patch)],
                        cwd=repo, env=env, timeout=timeout, log=log)
                    run(command + ['--3way', str(staged_patch)],
                        cwd=repo, env=env, timeout=timeout, log=log)
                    mode = 'three_way'
                else:
                    run(command + [str(staged_patch)], cwd=repo, env=env, timeout=timeout, log=log)
                    mode = 'direct'
                receipt['patch_applications'].append({'path': patch['path'], 'mode': mode})
            for fixture in config['fixtures']:
                entry = repo / fixture['destination']
                target = contained(repo, fixture['destination'])
                if entry.is_symlink() or target.exists():
                    raise ValueError(f'Fixture would overwrite candidate file: {fixture["destination"]}')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(inputs[fixture['source']])
            for test in config['tests']:
                if not contained(repo, test).is_file():
                    raise ValueError(f'Candidate is missing required test: {test}')
            check_api(repo, config['required_api'])
            runner = contained(repo, 'scripts/run_tests.sh')
            if not runner.is_file():
                raise ValueError('Canonical Hermes test runner is unavailable')
            stage = 'tests'
            results = evidence / 'pytest'
            results.mkdir()
            plugin = repo / 'jevgauge_candidate_evidence.py'
            if plugin.exists() or plugin.is_symlink():
                raise ValueError('Candidate evidence module collision')
            plugin.write_text('OUTPUT = ' + repr(str(results)) + '\n' + EVIDENCE_PLUGIN)
            run(['bash', str(runner), '-j', str(config['test_workers']), *config['tests'],
                 '--', '-p', 'jevgauge_candidate_evidence'],
                cwd=repo, env=env, timeout=config['test_timeout_seconds'], log=log)
            stage = 'test-evidence'
            receipt['test_results'] = test_evidence(results, config['tests'])
            receipt['status'] = 'backend-tests-passed'
    except Exception as error:
        # Candidate syntax and malformed evidence are failures too. Never leave
        # a terminal invocation recorded as "checking" after an unexpected error.
        receipt.update(status='failed', failure_stage=stage, error=type(error).__name__)
        with log.open('a') as output:
            output.write(f'\nCheck failed during {stage}: {error}\n')
        # Command output is kept in the local log, never treated as an instruction.
    except KeyboardInterrupt:
        receipt.update(status='interrupted', failure_stage=stage)
    finally:
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        receipt['log_sha256'] = digest(log) if log.exists() else None
        (evidence / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return evidence, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--matrix', action='store_true', help='Print CI candidate matrix from config')
    parser.add_argument('--hermes-repo', type=Path)
    parser.add_argument('--ref', default='HEAD', help='Existing local Git revision; never fetches upstream')
    parser.add_argument('--test-python', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--allow-remote-snapshot', action='store_true',
                        help='If the local clone lacks candidate objects, fetch the exact resolved commit from manifest repository')
    args = parser.parse_args()
    try:
        if args.matrix:
            config = load_config(args.config)
            print(json.dumps({'include': [{**candidate, **config['test_runtime']}
                                          for candidate in config['candidates']]}))
            return 0
        if any(x is None for x in [args.hermes_repo, args.test_python, args.output_dir]):
            parser.error('--hermes-repo, --test-python and --output-dir are required')
        evidence, receipt = verify(args.hermes_repo, args.ref, args.test_python, args.output_dir,
                                   args.config, allow_remote_snapshot=args.allow_remote_snapshot)
        print(json.dumps({'status': receipt['status'], 'receipt': str(evidence / 'receipt.json'),
                          'activated': False}))
        return 0 if receipt['status'] == 'backend-tests-passed' else 1
    except (OSError, ValueError) as error:
        print(f'Configuration/input error: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
