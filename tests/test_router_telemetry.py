import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from jevgauge.telemetry import EventStore

spec = importlib.util.spec_from_file_location('router_evidence', Path(__file__).parents[1] / 'jev-router' / '__init__.py')
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)


class Config:
    def __init__(self, **values): self.values = dict(enabled=True, **values)
    def get_config(self, key, default=None): return self.values.get(key, default)


def test_route_captures_original_and_eligible_without_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(router, '_get_secret', lambda *_args: 'secret-key')
    monkeypatch.setattr(router, '_live_models', lambda: ['gpt-6-luna', 'gpt-6-sol'])
    monkeypatch.setattr(router, '_supported_efforts', lambda *a: ['low'])
    monkeypatch.setattr(router, '_decide', lambda *a: {'answers': {'model_tier': {'choice':'economical','confidence':.9}, 'effort_tier': {'choice':'low','confidence':.9}}})
    result = router.route(ctx=Config(), source='desktop', session_key='chat', message='PRIVATE PROMPT', provider='openai-codex', model='gpt-6-sol', reasoning_config={'effort':'high'}, user_model=False, user_reasoning=False)
    events = EventStore(tmp_path / 'jevgauge/events.sqlite3').read_events()
    assert len(events) == 1
    row = events[0]
    assert (row['default_model'], row['default_effort'], row['selected_model']) == ('gpt-6-sol','high','gpt-6-luna')
    assert row['eligible_models'] == [{'model':'gpt-6-luna','capability':0}, {'model':'gpt-6-sol','capability':2}]
    assert row['model_tier'] == 'economical'
    assert row['requested_effort'] == 'low'
    assert 'PRIVATE' not in str(row) and 'secret-key' not in str(row)
    assert result['metadata']['default_model'] == 'gpt-6-sol'


def test_telemetry_failure_does_not_change_route(monkeypatch):
    monkeypatch.setattr(router, '_route', lambda **kw: {'model':'a', 'metadata': {'status':'routed','model':'a'}})
    monkeypatch.setattr(router, '_record', lambda event: (_ for _ in ()).throw(OSError('SECRET')))
    result = router.route(ctx=Config(), source='desktop', session_key='chat', message='x', provider='openai-codex', model='old', reasoning_config={}, user_model=False, user_reasoning=False)
    assert result['model'] == 'a'


def test_provider_hook_rejects_arbitrary_payload_and_keeps_missing_usage(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    router.provider_attempt(event={'schema_version':1,'event_id':'a.started','kind':'attempt','conversation_id':'chat','timestamp':'2026-09-25T00:00:00Z','attempt_id':'a','status':'started','usage':None})
    router.provider_attempt(event={'schema_version':1,'event_id':'bad','kind':'attempt','conversation_id':'chat','timestamp':'2026-09-25T00:00:00Z','prompt':'SECRET'})
    assert len(EventStore(tmp_path / 'jevgauge/events.sqlite3').read_events()) == 1


def test_dashboard_registration_available_when_routing_disabled():
    ctx = Config()
    ctx.values['enabled'] = False
    commands = {}
    ctx.register_command = lambda name, fn, **kw: commands.update({name:fn})
    ctx.register_hook = lambda *a: None
    router.register(ctx)
    assert commands['jev-dashboard'] == router.dashboard


def test_confidence_abstention_keeps_observed_eligibility(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(router, '_get_secret', lambda *_args: 'synthetic')
    monkeypatch.setattr(router, '_live_models', lambda: ['gpt-6-sol'])
    monkeypatch.setattr(router, '_decide', lambda *a: {'answers': {'model_tier':{'choice':'balanced','confidence':.1}, 'effort_tier':{'choice':'low','confidence':.9}}})
    router.route(ctx=Config(), source='desktop', session_key='chat', message='PRIVATE', provider='openai-codex', model='original', reasoning_config={}, user_model=False, user_reasoning=False)
    row = EventStore(tmp_path / 'jevgauge/events.sqlite3').read_events()[0]
    assert row['eligible_models'] == [{'model':'gpt-6-sol','capability':2}]
    assert row['reason_code'] == 'low Jev confidence'


@pytest.mark.parametrize('workers', [1, 8])
def test_installed_plugin_reuses_its_stdlib_storage_module(tmp_path, monkeypatch, workers):
    import builtins
    original_import = builtins.__import__
    def without_dashboard_package(name, *args, **kwargs):
        if name == 'jevgauge.telemetry':
            raise ImportError('separate Hermes interpreter')
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', without_dashboard_package)
    monkeypatch.setattr(router, '__file__', str(tmp_path / '__init__.py'))
    (tmp_path / 'telemetry.py').write_text('import time\ntime.sleep(.05)\ncalls = 0\ndef record_event(home, event):\n    global calls\n    calls += 1\n    return calls\n')
    # Fresh plugin instance emulates installed Hermes without the package import.
    if hasattr(router, '_STORAGE_RECORD'):
        monkeypatch.setattr(router, '_STORAGE_RECORD', None)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        assert sorted(pool.map(lambda _: router._record({}), range(16))) == list(range(1,17))
