"""Runtime diagnostics must not mistake a saved decision for an active hook."""
import json
import sys
from types import ModuleType

import pytest

import jev_router as router


class CommandContext:
    def __init__(self):
        self.enabled = True
        self.commands = {}

    def get_config(self, key, default=None):
        assert key == 'enabled'
        return self.enabled

    def register_command(self, name, callback, **kwargs):
        self.commands[name] = callback

    def register_hook(self, *args):
        pass

    def register_middleware(self, *args):
        pass


@pytest.fixture
def command(monkeypatch):
    parent = ModuleType('hermes_cli')
    host = ModuleType('hermes_cli.plugins')
    host.SESSION_RUNTIME_SELECTION_API = 1
    host.PROVIDER_ATTEMPT_API = 1
    host.VALID_HOOKS = {'select_session_runtime', 'provider_attempt'}
    middleware = ModuleType('hermes_cli.middleware')
    middleware.VALID_MIDDLEWARE = {'turn_route'}
    parent.middleware = middleware
    # Registration alone is not evidence that the host invokes a hook.
    host.has_hook = lambda name: True
    parent.plugins = host
    monkeypatch.setitem(sys.modules, 'hermes_cli', parent)
    monkeypatch.setitem(sys.modules, 'hermes_cli.plugins', host)
    monkeypatch.setitem(sys.modules, 'hermes_cli.middleware', middleware)
    monkeypatch.setattr(router, '_session_record', lambda: {
        'model': 'gpt-6-luna',
        'model_config': json.dumps({
            'provider': 'openai-codex', 'reasoning_config': {'effort': 'low'},
            'session_route': {'status': 'routed', 'reason': 'economical/low',
                              'owner': {'model': 'router', 'reasoning': 'router'}},
            'prompt': 'PRIVATE PROMPT', 'api_key': 'PRIVATE KEY',
        }),
    })
    ctx = CommandContext()
    router.register(ctx)
    return ctx.commands['jev-status'], ctx, host


def test_missing_host_hook_wins_over_a_saved_routed_decision(command):
    invoke, _, host = command
    del host.SESSION_RUNTIME_SELECTION_API
    host.VALID_HOOKS = set()
    sys.modules['hermes_cli.middleware'].VALID_MIDDLEWARE = set()
    text = invoke('')
    assert 'Routing unavailable: host integration missing' in text
    assert 'saved binding withheld' in text
    snapshot = json.loads(invoke('--json'))
    assert snapshot['runtime']['routing']['status'] == 'unavailable'
    assert snapshot['conversation']['state'] == 'scope_unverified'
    assert router._saved_status()['source'] == 'saved_session'
    assert 'PRIVATE' not in json.dumps(snapshot)


@pytest.mark.parametrize('version,hooks', [
    (2, {'select_session_runtime'}),
    (True, {'select_session_runtime'}),
    ('1', {'select_session_runtime'}),
    (1, set()),
])
def test_unsupported_or_incomplete_contract_never_reports_available(command, version, hooks):
    invoke, _, host = command
    sys.modules['hermes_cli.middleware'].VALID_MIDDLEWARE = set()
    host.SESSION_RUNTIME_SELECTION_API = version
    host.VALID_HOOKS = hooks
    snapshot = json.loads(invoke('--json'))
    assert snapshot['runtime']['routing']['status'] == 'unavailable'
    assert snapshot['runtime']['verification'] == 'capability_only'


def test_routing_capability_and_provider_telemetry_are_independent(command):
    invoke, _, host = command
    del host.PROVIDER_ATTEMPT_API
    host.VALID_HOOKS.remove('provider_attempt')
    runtime = json.loads(invoke('--json'))['runtime']
    assert runtime['routing']['status'] == 'available'
    assert runtime['routing']['contract'] == 'turn_route'
    assert runtime['routing']['scope'] == 'addressed_profile'
    assert runtime['telemetry']['status'] == 'unavailable'
    assert runtime['verification'] == 'capability_only'
    assert 'execution unverified' in invoke('')


def test_registered_status_does_not_claim_ambient_enablement(command):
    invoke, ctx, _ = command
    for value in (True, False):
        ctx.enabled = value
        runtime = json.loads(invoke('--json'))['runtime']
        assert runtime['enabled'] is None
        assert runtime['config_status'] == 'scope_unverified'


@pytest.mark.parametrize('record', [
    {'model_config': '{PRIVATE BROKEN JSON'},
    {'model_config': []},
    {'model_config': {'session_route': ['PRIVATE']}},
    {'model_config': {'session_route': {}, 'reasoning_config': ['PRIVATE']}},
    {'model_config': {'session_route': {'owner': ['PRIVATE']}}},
])
def test_malformed_saved_state_is_reported_without_echo_or_exception(command, monkeypatch, record):
    invoke, _, _ = command
    monkeypatch.setattr(router, '_session_record', lambda: record)
    snapshot = router._saved_status()
    assert snapshot['state'] == 'invalid'
    assert 'PRIVATE' not in invoke('')
    assert 'PRIVATE' not in json.dumps(snapshot)


def test_missing_saved_record_is_distinct_from_storage_failure(command, monkeypatch):
    invoke, _, _ = command
    monkeypatch.setattr(router, '_session_record', lambda: None)
    assert router._saved_status()['state'] == 'unrecorded'

    def broken_store():
        raise OSError('PRIVATE KEY /private/user/path')

    monkeypatch.setattr(router, '_session_record', broken_store)
    snapshot = router._saved_status()
    assert snapshot['state'] == 'unavailable'
    assert 'PRIVATE' not in json.dumps(snapshot)
    assert 'PRIVATE' not in invoke('')


def test_failed_host_inspection_remains_unknown(command, monkeypatch):
    invoke, _, _ = command
    monkeypatch.setitem(sys.modules, 'hermes_cli.plugins', None)
    monkeypatch.setitem(sys.modules, 'hermes_cli.middleware', None)
    snapshot = json.loads(invoke('--json'))
    assert snapshot['runtime']['routing']['status'] == 'unknown'
    assert snapshot['conversation']['state'] == 'scope_unverified'


def test_unreadable_settings_do_not_inherit_a_previous_enabled_result(command):
    invoke, ctx, _ = command
    assert json.loads(invoke('--json'))['runtime']['enabled'] is None

    def broken_config(*args):
        raise OSError('PRIVATE KEY')

    ctx.get_config = broken_config
    snapshot = json.loads(invoke('--json'))
    assert snapshot['runtime']['enabled'] is None
    assert snapshot['runtime']['config_status'] == 'scope_unverified'
    assert 'PRIVATE' not in json.dumps(snapshot)


def test_public_command_withholds_unverified_profile_data(command, monkeypatch):
    invoke, ctx, _ = command
    def forbidden(*args):
        pytest.fail('unverified profile data must not be read')
    ctx.get_config = forbidden
    monkeypatch.setattr(router, '_session_record', forbidden)
    snapshot = json.loads(invoke('--json'))
    assert snapshot['runtime']['enabled'] is None
    assert snapshot['runtime']['config_status'] == 'scope_unverified'
    assert snapshot['conversation']['state'] == 'scope_unverified'
    assert snapshot['inspection_scope'] == 'invoking_process'


@pytest.mark.parametrize('field', ['reason', 'status'])
def test_saved_diagnostic_text_does_not_echo_arbitrary_strings(monkeypatch, field):
    monkeypatch.setattr(router, '_session_record', lambda: {
        'model_config': {'session_route': {field: 'PRIVATE TOKEN /private/example'}}})
    result = router._saved_status()
    assert 'PRIVATE' not in json.dumps(result)
    assert result['state'] == 'saved'


def test_unknown_historical_reason_has_controlled_fallback(monkeypatch):
    monkeypatch.setattr(router, '_session_record', lambda: {
        'model_config': {'session_route': {'status': 'routed', 'reason': 'old custom explanation'}}})
    assert router._saved_status()['route']['reason'] == 'saved reason not recognized'
