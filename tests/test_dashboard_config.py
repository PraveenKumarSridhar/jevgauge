"""Config writes must preserve unrelated settings, reject bad input, and detect races."""
import importlib
import json
from pathlib import Path

import pytest
import yaml


def module():
    return importlib.import_module('jevgauge.dashboard_config')


@pytest.fixture
def owned_plugin(tmp_path):
    from jevgauge.cli import _plugin_files, _hash, MANIFEST
    plugin=tmp_path / 'plugins/jev-router'
    plugin.mkdir(parents=True)
    contents=_plugin_files()
    for name, data in contents.items():
        (plugin/name).write_bytes(data)
    (plugin/MANIFEST).write_text(json.dumps({'owner':'jevgauge','format':1,
        'files':{name:_hash(data) for name,data in contents.items()}}))
    return plugin


def test_preserves_unrelated_values_and_requires_current_revision(tmp_path, owned_plugin):
    original = {'model': 'keep-model', 'secret': 'DO-NOT-RETURN', 'plugins': {'enabled': ['other'], 'entries': {'other': {'settings': {'x': 1}}, 'jev-router': {'settings': {'api_url': 'https://private.invalid', 'enabled': False}}}}}
    (tmp_path / 'config.yaml').write_text(yaml.safe_dump(original))
    m = module()
    before = m.read_config(tmp_path)
    assert 'DO-NOT-RETURN' not in json.dumps(before)
    assert 'private.invalid' not in json.dumps(before)
    after = m.update_config(tmp_path, {'enabled': True, 'selection_timeout': 2.5}, before['revision'])
    saved = yaml.safe_load((tmp_path / 'config.yaml').read_text())
    assert saved['secret'] == 'DO-NOT-RETURN'
    assert saved['model'] == 'keep-model'
    assert saved['plugins']['enabled'] == ['other', 'jev-router']
    assert saved['plugins']['entries']['other'] == original['plugins']['entries']['other']
    assert saved['plugins']['entries']['jev-router']['settings']['api_url'] == 'https://private.invalid'
    assert after['settings']['selection_timeout'] == 2.5
    with pytest.raises(m.ConfigError, match='changed'):
        m.update_config(tmp_path, {'enabled': False}, before['revision'])
    assert yaml.safe_load((tmp_path / 'config.yaml').read_text()) == saved


@pytest.mark.parametrize('changes', [
    {'enabled': 1}, {'effort_mode': 'max'}, {'selection_timeout': True},
    {'selection_timeout': 0}, {'selection_timeout': float('nan')},
    {'api_url': 'https://attacker.invalid'}, {'prompt_retention': True},
    {'tier_models': {'balanced': ['x']}},
    {'tier_models': {'economical': ['a\nsecret'], 'balanced': [], 'strongest': []}},
    {'tier_models': {'economical': [], 'balanced': [], 'strongest': []}},
])
def test_invalid_changes_leave_file_byte_identical(tmp_path, changes):
    path = tmp_path / 'config.yaml'
    path.write_text('# retain this when rejected\nmodel: unchanged\n')
    m = module()
    before = path.read_bytes()
    with pytest.raises(m.ConfigError):
        m.update_config(tmp_path, changes, m.read_config(tmp_path)['revision'])
    assert path.read_bytes() == before


def test_yaml_aliases_do_not_change_unrelated_settings(tmp_path, owned_plugin):
    (tmp_path / 'config.yaml').write_text('other: &settings\n  enabled: false\nplugins:\n  entries:\n    jev-router:\n      settings: *settings\n')
    m = module()
    m.update_config(tmp_path, {'enabled': True}, m.read_config(tmp_path)['revision'])
    saved = yaml.safe_load((tmp_path / 'config.yaml').read_text())
    assert saved['other']['enabled'] is False
    assert saved['plugins']['entries']['jev-router']['settings']['enabled'] is True


def test_symlink_and_existing_lock_are_not_touched(tmp_path):
    m = module()
    target = tmp_path / 'target.yaml'
    target.write_text('model: keep\n')
    (tmp_path / 'config.yaml').symlink_to(target)
    with pytest.raises(m.ConfigError):
        m.read_config(tmp_path)
    assert target.read_text() == 'model: keep\n'
    (tmp_path / 'config.yaml').unlink()
    (tmp_path / '.jevgauge-install.lock').write_text('another owner')
    with pytest.raises(m.ConfigError):
        m.update_config(tmp_path, {'enabled': True}, m.read_config(tmp_path)['revision'])
    assert (tmp_path / '.jevgauge-install.lock').read_text() == 'another owner'
