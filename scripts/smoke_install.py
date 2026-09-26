#!/usr/bin/env python3
"""Exercise a built wheel in an isolated environment, without live API calls.

Usage: python scripts/smoke_install.py --wheel dist/jevgauge-0.1.0-py3-none-any.whl \
    --hermes-repo /path/to/hermes-agent

The supplied checkout is read only. All installations and configuration changes
happen under a temporary directory. pip needs access to wheel dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv


def run(command, *, env, cwd, expected=0):
    result = subprocess.run([str(part) for part in command], env=env, cwd=cwd,
                            capture_output=True, text=True, check=False)
    if result.returncode != expected:
        # Do not replay environment, config, or pip logs, which may contain secrets.
        raise RuntimeError(f'Smoke command {Path(str(command[0])).name} failed: expected exit {expected}, got {result.returncode}')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--hermes-repo', type=Path, required=True)
    args = parser.parse_args(argv)
    wheel, repo = args.wheel.resolve(strict=True), args.hermes_repo.resolve(strict=True)
    if wheel.suffix != '.whl':
        parser.error('--wheel must name a built .whl file')
    with tempfile.TemporaryDirectory(prefix='jevgauge wheel smoke ') as temporary:
        workspace = Path(temporary)
        environment = dict(os.environ)
        for name in tuple(environment):
            if name.endswith(('_API_KEY', '_TOKEN', '_SECRET')) or name in ('PYTHONPATH', 'PYTHONHOME', 'HERMES_HOME', 'HERMES_REPO'):
                environment.pop(name, None)
        home = workspace / 'Hermes home with spaces'
        home.mkdir()
        environment['HERMES_HOME'] = str(home)
        environment['HOME'] = str(workspace)
        environment['USERPROFILE'] = str(workspace)
        environment['PYTHONNOUSERSITE'] = '1'
        environment['PIP_CONFIG_FILE'] = os.devnull
        environment['PIP_INDEX_URL'] = 'https://pypi.org/simple'
        environment['PIP_KEYRING_PROVIDER'] = 'disabled'
        environment.pop('PIP_EXTRA_INDEX_URL', None)
        venv_path = workspace / 'isolated venv'
        venv.EnvBuilder(with_pip=True).create(venv_path)
        python = venv_path / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        run([python, '-m', 'pip', 'install', '--disable-pip-version-check', str(wheel)], env=environment, cwd=workspace)
        config = home / 'config.yaml'
        config.write_text('model: original\nprivate_setting: fixture-only\nplugins:\n  enabled: [other]\n  entries:\n    other:\n      settings: {keep: true}\n', encoding='utf-8')
        original = "{'model':'original','private_setting':'fixture-only','plugins':{'enabled':['other'],'entries':{'other':{'settings':{'keep':True}}}}}"
        for action in ('doctor', 'install', 'install', 'disable', 'enable', 'uninstall', 'install', 'disable', 'uninstall'):
            run([python, '-m', 'jevgauge', action, '--hermes-repo', repo], env=environment, cwd=workspace)
            if action in ('install', 'enable', 'disable'):
                enabled = action != 'disable'
                assertion = f'''from pathlib import Path
import yaml
c=yaml.safe_load(Path({str(config)!r}).read_text())
assert c['model']=='original' and c['private_setting']=='fixture-only'
assert c['plugins']['entries']['other']=={{'settings':{{'keep':True}}}}
assert ('jev-router' in c['plugins']['enabled']) is {enabled!r}
assert c['plugins']['entries']['jev-router']['settings']['enabled'] is {enabled!r}
'''
                run([python, '-c', assertion], env=environment, cwd=workspace)
            if action == 'enable':
                installed_source = home / 'plugins/jev-router/__init__.py'
                load_plugin = f'''import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('smoke_plugin', {str(installed_source)!r})
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert callable(module.register)
assert list(Path({str(installed_source.parent)!r}).glob('__pycache__/__init__.*.pyc'))
'''
                run([python, '-c', load_plugin], env=environment, cwd=workspace)
                print('PASS deployed plugin import created Python cache without provider calls')
            print(f'PASS wheel {action}')
        run([python, '-c', f'import yaml; from pathlib import Path; assert yaml.safe_load(Path({str(config)!r}).read_text()) == {original}'], env=environment, cwd=workspace)
        assert not (home / 'plugins/jev-router/__init__.py').exists()
        assert list((home / 'plugins/jev-router/__pycache__').glob('__init__.*.pyc'))
        # Build a read-only compatibility fixture from real modules, replacing
        # only plugins.py with committed HEAD (the local upstream base).
        required = run([python, '-c', 'import json; from jevgauge.cli import REQUIRED_SYMBOLS; print(json.dumps(list(REQUIRED_SYMBOLS)))'], env=environment, cwd=workspace)
        upstream = workspace / 'upstream API fixture'
        for relative in json.loads(required.stdout):
            destination = upstream / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / relative, destination)
        committed = run(['git', '-C', repo, 'show', 'HEAD:hermes_cli/plugins.py'], env=environment, cwd=workspace).stdout
        (upstream / 'hermes_cli/plugins.py').write_text(committed, encoding='utf-8')
        result = run([python, '-m', 'jevgauge', 'install', '--hermes-repo', upstream], env=environment, cwd=workspace, expected=1)
        assert 'upstream' in result.stderr and 'SESSION_RUNTIME_SELECTION_API' in result.stderr
        assert not (home / 'plugins/jev-router/__init__.py').exists()
        assert list((home / 'plugins/jev-router/__pycache__').glob('__init__.*.pyc'))
        print('PASS committed upstream API refuses unsupported installation')
        print('PASS unrelated configuration preserved; no user home or credentials used')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
