"""Disposable Git fixtures prove update checks cannot change the source install."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Candidate checker supports macOS and Linux')

SPEC = importlib.util.spec_from_file_location('update_check', Path(__file__).parents[1] / 'scripts/check_hermes_update.py')
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    source = tmp_path / 'source'
    root.mkdir()
    source.mkdir()
    (source / 'scripts').mkdir()
    (source / 'tests').mkdir()
    (source / 'hermes_cli').mkdir()
    (source / 'hermes_cli/plugins.py').write_text('SESSION_RUNTIME_SELECTION_API = 1\nPROVIDER_ATTEMPT_API = 1\n')
    (source / 'scripts/run_tests.sh').write_text(
        '#!/bin/bash\nset -eu\nshift 2\nargs=()\n'
        'for arg in "$@"; do [[ "$arg" == "--" ]] || args+=("$arg"); done\n'
        'exec "$HERMES_PYTHON" -m pytest "${args[@]}"\n')
    (source / 'tests/test_probe.py').write_text('def test_probe():\n    assert True\n')
    (source / 'target').write_text('before\n')
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'fixture'], check=True)
    (root / 'change.patch').write_text('diff --git a/target b/target\n--- a/target\n+++ b/target\n@@ -1 +1 @@\n-before\n+after\n')
    config = json.loads(check.DEFAULT_CONFIG.read_text())
    config.update(patches=[{'path': 'change.patch', 'exclude': []}], fixtures=[],
                  tests=['tests/test_probe.py'], command_timeout_seconds=15, test_timeout_seconds=15)
    config['test_runtime']['python'] = '.'.join(map(str, sys.version_info[:2]))
    config_path = root / 'config.json'
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(check, 'ROOT', root)
    return root, source, config_path, tmp_path / 'receipts'


def commit(source):
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'test change'], check=True)


def execute(candidate):
    root, source, config, output = candidate
    return check.verify(source, 'HEAD', Path(sys.executable), output, config)


def test_success_uses_committed_snapshot_and_preserves_dirty_source(candidate, monkeypatch):
    _, source, _, _ = candidate
    (source / 'target').write_text('unrelated dirty work\n')
    monkeypatch.setenv('EXAMPLE_API_KEY', 'must-not-pass')
    monkeypatch.setenv('__HERMES_ACTIVATED', 'must-not-pass')
    monkeypatch.setenv('PYTHONPATH', '/must-not-pass')
    evidence, receipt = execute(candidate)
    assert receipt['status'] == 'backend-tests-passed', (evidence / 'check.log').read_text()
    assert receipt['activated'] is False
    assert receipt['snapshot_source'] == 'local'
    assert (source / 'target').read_text() == 'unrelated dirty work\n'
    assert receipt['test_results']['passed'] == 1
    assert receipt['test_results']['files'] == 1
    assert receipt['python']['version']
    assert receipt['hermes_commit'] == subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    assert 'must-not-pass' not in (evidence / 'check.log').read_text()


def test_exact_remote_snapshot_recovers_incomplete_local_clone(candidate, monkeypatch):
    _, source, config_path, output = candidate
    config = json.loads(config_path.read_text())
    config['repository'] = 'https://example.invalid/hermes-agent.git'
    config_path.write_text(json.dumps(config))
    original_run = check.run
    local_fetch_failed = False

    def simulate_missing_local_object(argv, **kwargs):
        nonlocal local_fetch_failed
        if argv[:2] == ['git', 'fetch'] and source.as_uri() in argv and not local_fetch_failed:
            local_fetch_failed = True
            raise RuntimeError('local partial clone lacks candidate tree')
        if config['repository'] in argv:
            argv = [source.as_uri() if arg == config['repository'] else arg for arg in argv]
        return original_run(argv, **kwargs)

    monkeypatch.setattr(check, 'run', simulate_missing_local_object)
    evidence, receipt = check.verify(source, 'HEAD', Path(sys.executable), output, config_path,
                                     allow_remote_snapshot=True)
    assert local_fetch_failed
    assert receipt['status'] == 'backend-tests-passed', (evidence / 'check.log').read_text()
    assert receipt['snapshot_source'] == 'remote_exact_commit'
    assert receipt['activated'] is False


def test_incomplete_local_clone_fails_without_remote_opt_in(candidate, monkeypatch):
    _, source, config_path, output = candidate
    original_run = check.run

    def simulate_missing_local_object(argv, **kwargs):
        if argv[:2] == ['git', 'fetch'] and source.as_uri() in argv:
            raise RuntimeError('local partial clone lacks candidate tree')
        return original_run(argv, **kwargs)

    monkeypatch.setattr(check, 'run', simulate_missing_local_object)
    _, receipt = check.verify(source, 'HEAD', Path(sys.executable), output, config_path)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'snapshot'
    assert receipt['activated'] is False


@pytest.mark.parametrize('body', [
    'import pytest\n@pytest.mark.skip(reason="not run")\ndef test_probe(): pass\n',
    'import pytest\n@pytest.mark.xfail(reason="not proven")\ndef test_probe(): assert False\n',
])
def test_skipped_or_xfailed_required_gate_is_not_success(candidate, body):
    _, source, _, _ = candidate
    (source / 'tests/test_probe.py').write_text(body)
    commit(source)
    _, receipt = execute(candidate)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'test-evidence'


def test_zero_exit_without_pytest_evidence_is_not_success(candidate):
    _, source, _, _ = candidate
    (source / 'scripts/run_tests.sh').write_text('#!/bin/bash\nexit 0\n')
    commit(source)
    _, receipt = execute(candidate)
    assert receipt['status'] == 'failed'


def test_runner_cannot_deselect_required_test_in_a_file(candidate):
    _, source, _, _ = candidate
    (source / 'tests/test_probe.py').write_text(
        'def test_kept(): assert True\n'
        'def test_deselected(): assert False\n')
    runner = source / 'scripts/run_tests.sh'
    runner.write_text(runner.read_text().replace(
        'pytest "${args[@]}"', 'pytest -k kept "${args[@]}"'))
    commit(source)
    evidence, receipt = execute(candidate)
    assert receipt['status'] == 'failed', (evidence / 'check.log').read_text()
    assert receipt['failure_stage'] == 'test-evidence'


def test_patch_rejection_leaves_source_untouched(candidate):
    root, source, _, _ = candidate
    (root / 'change.patch').write_text('not a patch')
    _, receipt = execute(candidate)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'integration'
    assert (source / 'target').read_text() == 'before\n'


def three_way_candidate(candidate, upstream_value):
    root, source, config_path, _ = candidate
    (source / 'target').write_text('one\nmiddle\nthree\n')
    commit(source)
    base = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    (source / 'target').write_text('ONE\nmiddle\nthree\n')
    (root / 'change.patch').write_bytes(subprocess.check_output(
        ['git', '-C', str(source), 'diff', '--binary', '--', 'target']))
    subprocess.run(['git', '-C', str(source), 'checkout', '--', 'target'], check=True)
    (source / 'target').write_text(upstream_value)
    (source / 'tests/test_probe.py').write_text(
        "from pathlib import Path\n"
        "def test_probe(): assert Path('target').read_text() == 'ONE\\nmiddle\\nTHREE\\n'\n")
    commit(source)
    assert subprocess.run(['git', '-C', str(source), 'apply', '--check', str(root / 'change.patch')],
                          capture_output=True).returncode != 0
    config = json.loads(config_path.read_text())
    config['patch_base_commit'] = base
    config_path.write_text(json.dumps(config))
    return source


def test_three_way_accepts_nonconflicting_candidate_and_runs_tests(candidate):
    source = three_way_candidate(candidate, 'one\nmiddle\nTHREE\n')
    evidence, receipt = execute(candidate)
    assert receipt['status'] == 'backend-tests-passed', (evidence / 'check.log').read_text()
    assert receipt['patch_applications'] == [{'path': 'change.patch', 'mode': 'three_way'}]
    assert (source / 'target').read_text() == 'one\nmiddle\nTHREE\n'


def test_three_way_conflict_fails_without_changing_source(candidate):
    source = three_way_candidate(candidate, 'OTHER\nmiddle\nthree\n')
    _, receipt = execute(candidate)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'integration'
    assert (source / 'target').read_text() == 'OTHER\nmiddle\nthree\n'


def test_invalid_patch_base_is_rejected(candidate):
    _, _, config_path, _ = candidate
    config = json.loads(config_path.read_text())
    config['patch_base_commit'] = 'unknown'
    config_path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='Patch base'):
        check.load_config(config_path)


def test_missing_test_refused_before_runner(candidate):
    _, _, config_path, _ = candidate
    config = json.loads(config_path.read_text())
    config['tests'] = ['tests/missing.py']
    config_path.write_text(json.dumps(config))
    _, receipt = execute(candidate)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'integration'


def test_environment_is_allowlisted(tmp_path, monkeypatch):
    monkeypatch.setenv('TOKEN', 'private')
    env = check.clean_environment(tmp_path, Path(sys.executable))
    assert 'TOKEN' not in env
    assert 'PYTHONPATH' not in env
    assert '__HERMES_ACTIVATED' not in env
    assert env['HOME'] == str(tmp_path)
    assert env['HERMES_HOME'] == str(tmp_path / '.hermes')


@pytest.mark.parametrize('path', ['../outside', '/outside', 'tests/../../outside', '-option', 'tests\\bad'])
def test_path_escape_rejected(path):
    with pytest.raises(ValueError):
        check.relative_path(path)


def test_source_symlink_escape_rejected(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'linked').symlink_to(tmp_path)
    with pytest.raises(ValueError):
        check.contained(root, 'linked/outside')


def test_timeout_kills_runner_child(tmp_path):
    pidfile = tmp_path / 'child.pid'
    source = ('import subprocess,time,pathlib,sys; '
              'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
              f'pathlib.Path({str(pidfile)!r}).write_text(str(child.pid)); time.sleep(30)')
    with pytest.raises(subprocess.TimeoutExpired):
        check.run([sys.executable, '-c', source], cwd=tmp_path,
                  env=check.clean_environment(tmp_path, Path(sys.executable)),
                  timeout=1, log=tmp_path / 'log')
    pid = int(pidfile.read_text())
    # A killed orphan may briefly remain a zombie until its parent is reaped.
    state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
    assert not state or state.startswith('Z')


def test_successful_runner_cannot_leave_child_alive(tmp_path):
    pidfile = tmp_path / 'child.pid'
    source = ('import subprocess,pathlib,sys; '
              'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
              f'pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))')
    check.run([sys.executable, '-c', source], cwd=tmp_path,
              env=check.clean_environment(tmp_path, Path(sys.executable)),
              timeout=5, log=tmp_path / 'log')
    pid = int(pidfile.read_text())
    try:
        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout.strip()
        assert not state or state.startswith('Z')
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_moving_ref_does_not_change_resolved_candidate(candidate, monkeypatch):
    _, source, _, _ = candidate
    original = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    original_run = check.run
    moved = False

    def run_and_move(argv, **kwargs):
        nonlocal moved
        result = original_run(argv, **kwargs)
        if not moved:
            moved = True
            (source / 'tests/test_probe.py').write_text('def test_probe(): assert False\n')
            commit(source)
        return result

    monkeypatch.setattr(check, 'run', run_and_move)
    _, receipt = execute(candidate)
    assert receipt['status'] == 'backend-tests-passed'
    assert receipt['hermes_commit'] == original
    assert original != subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()


def test_fixture_cannot_overwrite_candidate_file(candidate):
    root, _, config_path, _ = candidate
    (root / 'fixture.py').write_text('pass')
    config = json.loads(config_path.read_text())
    config['fixtures'] = [{'source': 'fixture.py', 'destination': 'tests/test_probe.py'}]
    config_path.write_text(json.dumps(config))
    _, receipt = execute(candidate)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'integration'


def test_missing_api_marker_is_not_compatible(candidate):
    _, source, _, _ = candidate
    (source / 'hermes_cli/plugins.py').write_text('SESSION_RUNTIME_SELECTION_API = 2\n')
    commit(source)
    _, receipt = execute(candidate)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'integration'


def test_missing_pytest_does_not_activate(candidate, tmp_path):
    _, source, config, output = candidate
    venv = tmp_path / 'no-deps'
    subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(venv)], check=True)
    evidence, receipt = check.verify(source, 'HEAD', venv / 'bin/python', output, config)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'interpreter'
    assert 'pytest' in (evidence / 'check.log').read_text()


def test_inherited_live_home_is_not_visible(candidate, monkeypatch, tmp_path):
    _, source, _, _ = candidate
    live = tmp_path / 'live'
    (live / '.hermes').mkdir(parents=True)
    guard = live / '.hermes/pytest_live_guard.py'
    guard.write_text('raise AssertionError("live hook executed")')
    monkeypatch.setenv('HOME', str(live))
    monkeypatch.setenv('EXAMPLE_API_KEY', 'private')
    monkeypatch.setenv('HERMES_HOME', str(live / '.hermes'))
    (source / 'tests/test_probe.py').write_text(
        'import os\ndef test_probe():\n'
        f'    assert os.environ["HOME"] != {str(live)!r}\n'
        '    assert "EXAMPLE_API_KEY" not in os.environ\n'
        '    assert "__HERMES_ACTIVATED" not in os.environ\n')
    commit(source)
    _, receipt = execute(candidate)
    assert receipt['status'] == 'backend-tests-passed'
    assert guard.read_text() == 'raise AssertionError("live hook executed")'


def test_unknown_config_field_is_rejected(candidate):
    _, _, path, _ = candidate
    config = json.loads(path.read_text())
    config['shell_command'] = 'bad'
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='Unknown'):
        check.load_config(path)


def test_invalid_candidate_python_has_terminal_receipt(candidate):
    _, source, _, _ = candidate
    (source / 'hermes_cli/plugins.py').write_text('def invalid(:\n')
    commit(source)
    evidence, receipt = execute(candidate)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'integration'
    assert json.loads((evidence / 'receipt.json').read_text())['status'] == 'failed'


def test_malformed_evidence_has_terminal_receipt(candidate, monkeypatch):
    original_run = check.run

    def corrupt_evidence(argv, **kwargs):
        result = original_run(argv, **kwargs)
        if argv[0] == 'bash':
            reports = kwargs['log'].parent / 'pytest'
            next(reports.glob('*.json')).write_text('{"collected": null}')
        return result

    monkeypatch.setattr(check, 'run', corrupt_evidence)
    evidence, receipt = execute(candidate)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'test-evidence'
    assert json.loads((evidence / 'receipt.json').read_text())['status'] == 'failed'


def test_duplicate_phase_cannot_pass(tmp_path):
    node = 'tests/test_probe.py::test_probe'
    phases = [{'nodeid': node, 'phase': phase, 'outcome': 'passed', 'xfail': False}
              for phase in ['setup', 'call', 'call', 'teardown']]
    (tmp_path / 'report.json').write_text(json.dumps({
        'exitstatus': 0, 'discovered': [node], 'collected': [node], 'reports': phases}))
    with pytest.raises(ValueError):
        check.test_evidence(tmp_path, ['tests/test_probe.py'])


def test_fixture_cannot_replace_dangling_candidate_symlink(candidate):
    root, source, config_path, _ = candidate
    (source / 'tests/scope.py').symlink_to('absent.py')
    commit(source)
    (root / 'fixture.py').write_text('pass')
    config = json.loads(config_path.read_text())
    config['fixtures'] = [{'source': 'fixture.py', 'destination': 'tests/scope.py'}]
    config_path.write_text(json.dumps(config))
    _, receipt = execute(candidate)
    assert receipt['status'] == 'failed'
    assert receipt['failure_stage'] == 'integration'
    assert (source / 'tests/scope.py').is_symlink()
