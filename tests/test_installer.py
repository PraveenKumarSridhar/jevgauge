from pathlib import Path
import json
import pytest
import yaml
from jevgauge import cli


@pytest.fixture
def installation(tmp_path, monkeypatch):
    repo = tmp_path / 'Hermes checkout'
    for relative, names in cli.REQUIRED_SYMBOLS.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(f'def {name}(): pass' for name in names))
    plugins = repo / 'hermes_cli/plugins.py'
    plugins.write_text(plugins.read_text() + '\nSESSION_RUNTIME_SELECTION_API = 1\nPROVIDER_ATTEMPT_API = 1\n')
    home = tmp_path / 'User home'
    source = tmp_path / 'source'
    source.mkdir()
    (source / '__init__.py').write_text('# router\n')
    (source / 'plugin.yaml').write_text('name: jev-router\n')
    (source / 'telemetry.py').write_text('# telemetry fixture\n')
    (source / 'runtime.json').write_text('{}')
    monkeypatch.setattr(cli, '_plugin_files', lambda: {name: (source / name).read_bytes() for name in cli.PLUGIN_FILES})
    return repo, home


def run(installation, command):
    repo, home = installation
    return cli.main([command, '--hermes-repo', str(repo), '--home', str(home)])


def config(home):
    return yaml.safe_load((home / 'config.yaml').read_text())


def test_install_enable_disable_uninstall_preserves_other_settings(installation):
    repo, home = installation
    home.mkdir()
    (home / 'config.yaml').write_text('model: original\nsecret: hidden\nplugins:\n  enabled: [other]\n  entries:\n    other:\n      settings: {x: 3}\n')
    assert run(installation, 'install') == 0
    assert run(installation, 'install') == 0
    assert config(home)['plugins']['enabled'] == ['other', 'jev-router']
    assert run(installation, 'disable') == 0
    assert config(home)['plugins']['enabled'] == ['other']
    assert run(installation, 'enable') == 0
    assert run(installation, 'uninstall') == 0
    assert not (home / 'plugins/jev-router').exists()
    assert config(home) == {'model': 'original', 'secret': 'hidden', 'plugins': {'enabled': ['other'], 'entries': {'other': {'settings': {'x': 3}}}}}


def test_missing_hook_refuses_without_writing(installation, capsys):
    repo, home = installation
    path = repo / 'hermes_cli/plugins.py'
    path.write_text(path.read_text().replace('SESSION_RUNTIME_SELECTION_API = 1', '# SESSION_RUNTIME_SELECTION_API = 1'))
    assert run(installation, 'install') == 1
    assert not home.exists()
    assert 'upstream' in capsys.readouterr().err


def test_missing_dependency_refuses(installation):
    repo, home = installation
    (repo / 'agent/reasoning_effort.py').write_text('')
    assert run(installation, 'doctor') == 1
    assert not home.exists()


@pytest.mark.parametrize('operation', ['install', 'uninstall'])
def test_unmanaged_directory_never_overwritten(installation, operation):
    _, home = installation
    plugin = home / 'plugins/jev-router'
    plugin.mkdir(parents=True)
    (plugin / 'precious').write_text('keep')
    assert run(installation, operation) == 1
    assert (plugin / 'precious').read_text() == 'keep'


@pytest.mark.parametrize('operation', ['install', 'enable', 'uninstall'])
def test_modified_managed_files_refused(installation, operation):
    _, home = installation
    assert run(installation, 'install') == 0
    plugin = home / 'plugins/jev-router'
    (plugin / '__init__.py').write_text('local changes')
    assert run(installation, operation) == 1
    assert (plugin / '__init__.py').read_text() == 'local changes'


@pytest.mark.parametrize('target', ['plugin', 'plugins', 'config'])
def test_symlinks_refused(installation, tmp_path, target):
    _, home = installation
    home.mkdir()
    external = tmp_path / 'external'
    external.mkdir()
    if target == 'config':
        (external / 'config.yaml').write_text('secret: keep')
        (home / 'config.yaml').symlink_to(external / 'config.yaml')
    elif target == 'plugins':
        (home / 'plugins').symlink_to(external, target_is_directory=True)
    else:
        (home / 'plugins').mkdir()
        (home / 'plugins/jev-router').symlink_to(external, target_is_directory=True)
    assert run(installation, 'install') == 1
    assert list(external.iterdir()) == ([external / 'config.yaml'] if target == 'config' else [])


@pytest.mark.parametrize('text', ['plugins: wrong', 'plugins:\n  enabled: wrong', 'plugins:\n  entries: wrong', 'plugins:\n  entries:\n    jev-router: wrong', 'plugins:\n  entries:\n    jev-router:\n      settings: wrong', '- bad'])
def test_invalid_config_unchanged(installation, text):
    _, home = installation
    home.mkdir()
    (home / 'config.yaml').write_text(text)
    assert run(installation, 'install') == 1
    assert (home / 'config.yaml').read_text() == text
    assert not (home / 'plugins/jev-router').exists()


def test_secrets_never_printed_even_invalid_yaml(installation, capsys):
    _, home = installation
    home.mkdir()
    (home / 'config.yaml').write_text('secret: [SUPERSECRET')
    assert run(installation, 'install') == 1
    captured = capsys.readouterr()
    assert 'SUPERSECRET' not in captured.out + captured.err


def test_disable_works_without_compatible_hermes(installation):
    repo, _ = installation
    assert run(installation, 'install') == 0
    (repo / 'hermes_cli/plugins.py').unlink()
    assert run(installation, 'disable') == 0
    assert run(installation, 'uninstall') == 0


def test_manifest_path_traversal_refused(installation, tmp_path):
    _, home = installation
    assert run(installation, 'install') == 0
    manifest = home / 'plugins/jev-router' / cli.MANIFEST
    data = json.loads(manifest.read_text())
    data['files']['../../config.yaml'] = 'not-a-hash'
    manifest.write_text(json.dumps(data))
    assert run(installation, 'uninstall') == 1
    assert (home / 'config.yaml').exists()


def test_missing_home_default_uses_environment(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    assert cli.parser().parse_args(['doctor']).home == tmp_path


def test_reexported_api_symbols_are_supported(installation):
    repo, _ = installation
    (repo / 'hermes_cli/auth.py').write_text('from hermes_cli.auth_codex import resolve_codex_runtime_credentials\n')
    assert run(installation, 'doctor') == 0


def test_aliases_do_not_modify_other_settings(installation):
    _, home = installation
    home.mkdir()
    (home / 'config.yaml').write_text('shared: &shared {enabled: false, custom: keep}\nplugins:\n  entries:\n    jev-router:\n      settings: *shared\n')
    assert run(installation, 'install') == 0
    assert config(home)['shared'] == {'enabled': False, 'custom': 'keep'}
    assert run(installation, 'uninstall') == 0
    assert config(home)['plugins']['entries']['jev-router']['settings'] == {'custom': 'keep'}


def test_recursive_alias_is_refused_without_mutating(installation):
    _, home = installation
    home.mkdir()
    text = 'cyclic: &cyclic {again: *cyclic}'
    (home / 'config.yaml').write_text(text)
    assert run(installation, 'install') == 1
    assert (home / 'config.yaml').read_text() == text


def test_uninstall_preserves_unknown_files(installation):
    _, home = installation
    assert run(installation, 'install') == 0
    extra = home / 'plugins/jev-router/user-notes.txt'
    extra.write_text('keep')
    assert run(installation, 'uninstall') == 0
    assert extra.read_text() == 'keep'


def test_config_replace_failure_preserves_original(installation, monkeypatch):
    _, home = installation
    home.mkdir()
    text = 'secret: never-print\n'
    (home / 'config.yaml').write_text(text)
    def fail(*args):
        raise OSError('never-print')
    monkeypatch.setattr(cli.os, 'replace', fail)
    assert run(installation, 'install') == 1
    assert (home / 'config.yaml').read_text() == text
    assert not (home / '.jevgauge-install.lock').exists()
    assert not list(home.glob('.config.yaml.*'))


def test_live_lock_refuses_without_changes(installation):
    _, home = installation
    home.mkdir()
    lock = home / '.jevgauge-install.lock'
    lock.write_text('')
    assert run(installation, 'install') == 1
    assert lock.exists()
    assert not (home / 'config.yaml').exists()


def test_reinstall_after_python_import_preserves_bytecode(installation):
    import py_compile
    _, home = installation
    assert run(installation, 'install') == 0
    plugin = home / 'plugins/jev-router'
    cache = Path(py_compile.compile(str(plugin / '__init__.py'), doraise=True))
    cached_bytes = cache.read_bytes()
    assert run(installation, 'uninstall') == 0
    assert cache.read_bytes() == cached_bytes
    assert run(installation, 'install') == 0
    assert (plugin / '__init__.py').is_file()
    assert cache.read_bytes() == cached_bytes


def test_upgrade_preserves_unknown_files_and_custom_settings(installation, monkeypatch):
    _, home = installation
    assert run(installation, 'install') == 0
    plugin = home / 'plugins/jev-router'
    (plugin / 'notes.txt').write_text('keep')
    assert run(installation, 'uninstall') == 0
    assert run(installation, 'uninstall') == 0
    assert run(installation, 'enable') == 1
    monkeypatch.setattr(cli, '_plugin_files', lambda: {'__init__.py': b'# new version', 'plugin.yaml': b'name: jev-router\n'})
    assert run(installation, 'install') == 0
    assert (plugin / '__init__.py').read_text() == '# new version'
    assert (plugin / 'notes.txt').read_text() == 'keep'


def test_tombstone_never_overwrites_new_user_code(installation):
    _, home = installation
    assert run(installation, 'install') == 0
    plugin = home / 'plugins/jev-router'
    (plugin / 'notes.txt').write_text('keep')
    assert run(installation, 'uninstall') == 0
    (plugin / '__init__.py').write_text('# user replacement')
    assert run(installation, 'install') == 1
    assert run(installation, 'uninstall') == 1
    assert (plugin / '__init__.py').read_text() == '# user replacement'


def test_partial_uninstall_can_be_retried(installation, monkeypatch):
    _, home = installation
    assert run(installation, 'install') == 0
    plugin = home / 'plugins/jev-router'
    original = Path.unlink
    def fail_yaml_once(path, *args, **kwargs):
        if path == plugin / 'plugin.yaml':
            raise PermissionError('simulated transient access failure')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', fail_yaml_once)
        assert run(installation, 'uninstall') == 1
    assert run(installation, 'uninstall') == 0
    assert not plugin.exists()


def test_same_size_upgrade_does_not_execute_stale_cache(installation, monkeypatch):
    import importlib.util
    import py_compile
    import os
    _, home = installation
    monkeypatch.setattr(cli, '_plugin_files', lambda: {'__init__.py': b'VALUE = 1\n', 'plugin.yaml': b'name: jev-router\n', 'telemetry.py': b'', 'runtime.json': b'{}'})
    assert run(installation, 'install') == 0
    source = home / 'plugins/jev-router/__init__.py'
    timestamp = 1800000000
    os.utime(source, (timestamp, timestamp))
    py_compile.compile(str(source), doraise=True)
    assert run(installation, 'uninstall') == 0
    monkeypatch.setattr(cli, '_plugin_files', lambda: {'__init__.py': b'VALUE = 2\n', 'plugin.yaml': b'name: jev-router\n', 'telemetry.py': b'', 'runtime.json': b'{}'})
    monkeypatch.setattr(cli.time, 'time', lambda: timestamp)
    assert run(installation, 'install') == 0
    spec = importlib.util.spec_from_file_location('installer_cache_test', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.VALUE == 2


def test_partial_reinstall_can_be_retried(installation, monkeypatch):
    _, home = installation
    assert run(installation, 'install') == 0
    plugin = home / 'plugins/jev-router'
    (plugin / 'notes.txt').write_text('keep')
    assert run(installation, 'uninstall') == 0
    write = cli._atomic_write
    def fail_yaml(path, data, *args, **kwargs):
        if path == plugin / 'plugin.yaml':
            raise PermissionError('temporary failure')
        return write(path, data, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(cli, '_atomic_write', fail_yaml)
        assert run(installation, 'install') == 1
    assert run(installation, 'enable') == 1
    assert run(installation, 'install') == 0
    assert (plugin / 'notes.txt').read_text() == 'keep'


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), 'bad', -1, 2**100])
def test_malformed_tombstone_timestamp_refused(installation, invalid):
    _, home = installation
    assert run(installation, 'install') == 0
    plugin = home / 'plugins/jev-router'
    (plugin / 'notes.txt').write_text('keep')
    assert run(installation, 'uninstall') == 0
    manifest = plugin / cli.MANIFEST
    data = json.loads(manifest.read_text())
    data['source_mtime'] = invalid
    manifest.write_text(json.dumps(data))
    assert run(installation, 'install') == 1
    assert not (plugin / '__init__.py').exists()


def test_distributable_plugin_has_standalone_telemetry_and_dashboard_runtime():
    import sys
    files = cli._plugin_files()
    assert 'telemetry.py' in files
    assert b'class EventStore' in files['telemetry.py']
    assert json.loads(files['runtime.json']) == {'python': sys.executable}


def test_dashboard_cli_dispatches_without_installing(tmp_path, monkeypatch):
    import jevgauge.dashboard as dashboard
    observed = []
    monkeypatch.setattr(dashboard, 'serve', lambda home, **kwargs: observed.append((home, kwargs)))
    assert cli.main(['dashboard', '--home', str(tmp_path), '--demo', '--port', '8766', '--open']) == 0
    assert observed == [(tmp_path, {'port': 8766, 'demo': True, 'open_browser': True})]
    assert not list(tmp_path.iterdir())


def test_legacy_plugin_can_be_uninstalled_before_dashboard_upgrade(installation):
    _, home = installation
    assert run(installation, 'install') == 0
    plugin = home / 'plugins/jev-router'
    manifest_path = plugin / cli.MANIFEST
    manifest = json.loads(manifest_path.read_text())
    for name in ('telemetry.py', 'runtime.json'):
        (plugin / name).unlink()
        del manifest['files'][name]
    manifest_path.write_text(json.dumps(manifest))
    assert run(installation, 'uninstall') == 0
    assert not plugin.exists()


def test_legacy_upgrade_preserves_unowned_new_filenames(installation):
    _, home = installation
    assert run(installation, 'install') == 0
    plugin = home / 'plugins/jev-router'
    manifest_path = plugin / cli.MANIFEST
    manifest = json.loads(manifest_path.read_text())
    for name in ('telemetry.py', 'runtime.json'):
        del manifest['files'][name]
    manifest_path.write_text(json.dumps(manifest))
    (plugin / 'telemetry.py').write_text('# user-owned unrelated file')
    assert run(installation, 'uninstall') == 0
    assert (plugin / 'telemetry.py').read_text() == '# user-owned unrelated file'
    assert run(installation, 'install') == 1
    assert (plugin / 'telemetry.py').read_text() == '# user-owned unrelated file'


def test_dashboard_install_refuses_host_without_physical_attempt_hook(installation, capsys):
    repo, home = installation
    path = repo / 'hermes_cli/plugins.py'
    path.write_text(path.read_text().replace('PROVIDER_ATTEMPT_API = 1', '# PROVIDER_ATTEMPT_API = 1'))
    assert run(installation, 'install') == 1
    assert not home.exists()
    assert 'PROVIDER_ATTEMPT_API' in capsys.readouterr().err
