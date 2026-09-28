"""Copy into Hermes tests/tui_gateway and run its canonical test runner.

Copy the isolated plugin __init__.py beside this file as jev_router_scope_source.py.
No live homes, providers or UI processes are involved.
"""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize('method', ['command.dispatch', 'slash.exec'])
def test_status_declines_unverified_profile_even_with_colliding_ids(tmp_path, monkeypatch, method):
    from hermes_cli import plugins
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from hermes_state import SessionDB
    from tui_gateway import server

    source = Path(__file__).with_name('jev_router_scope_source.py')
    spec = importlib.util.spec_from_file_location('jev_scope_probe', source)
    router = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(router)
    homes = [tmp_path / 'launch', tmp_path / 'sibling']
    for index, home in enumerate(homes):
        home.mkdir()
        (home / 'config.yaml').write_text(
            'plugins:\n  jev-router:\n    enabled: ' + ('true' if index == 0 else 'false') + '\n')
        db = SessionDB(db_path=home / 'state.db')
        try:
            db.create_session('colliding-id', 'desktop', model=f'private-profile-{index}',
                              model_config=json.dumps({'session_route': {'status': 'routed'}}))
            assert db.get_session('colliding-id')['model'] == f'private-profile-{index}'
        finally:
            db.close()

    class Context:
        def __init__(self):
            self.commands = {}
        def register_command(self, name, handler, **kw):
            self.commands[name] = handler
        def register_hook(self, *args):
            pass
        def get_config(self, *args):
            pytest.fail('status read ambient profile settings')

    context = Context()
    router.register(context)
    monkeypatch.setattr(plugins, 'get_plugin_command_handler', context.commands.get)
    monkeypatch.setattr(router, '_session_record', lambda: pytest.fail('status read ambient session DB'))
    token = set_hermes_home_override(str(homes[0]))
    try:
        for index, home in enumerate(homes):
            monkeypatch.setitem(server._sessions, 'scope-probe', {
                'session_key': 'colliding-id', 'profile_home': str(home) if index else None,
                'cwd': str(tmp_path), 'agent': None})
            params = {'session_id': 'scope-probe'}
            params.update({'name': 'jev-status', 'arg': '--json'} if method == 'command.dispatch'
                          else {'command': '/jev-status --json'})
            response = server._methods[method](1, params)
            assert 'error' not in response, response
            snapshot = json.loads(response['result']['output'])
            assert snapshot['inspection_scope'] == 'invoking_process'
            assert snapshot['runtime']['config_status'] == 'scope_unverified'
            assert snapshot['conversation']['state'] == 'scope_unverified'
            assert 'private-profile' not in json.dumps(snapshot)
    finally:
        reset_hermes_home_override(token)
