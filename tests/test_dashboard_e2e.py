"""Deterministic routing policy -> durable evidence -> real loopback JSON API."""
import importlib.util
import json
from pathlib import Path
import threading
from urllib.request import urlopen

from jevgauge.dashboard import create_server
from jevgauge.telemetry import EventStore


def test_route_through_storage_and_http_has_hand_calculated_estimates(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('e2e_router', Path(__file__).parents[1] / 'jev-router/__init__.py')
    router = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(router)
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(router, '_get_secret', lambda *_args: 'FAKE_SECRET_DO_NOT_RETAIN')
    monkeypatch.setattr(router, '_live_models', lambda: ['gpt-4.1-nano', 'gpt-4.1-mini', 'gpt-4.1'])
    monkeypatch.setattr(router, '_supported_efforts', lambda *args: ['low'])
    monkeypatch.setattr(router, '_decide', lambda *args: {'answers': {'model_tier': {'choice': 'balanced', 'confidence': .9}, 'effort_tier': {'choice': 'low', 'confidence': .9}}})
    class Context:
        def get_config(self, key, default=None):
            return {'enabled': True, 'tier_models': {'economical': ['gpt-4.1-nano'], 'balanced': ['gpt-4.1-mini'], 'strongest': ['gpt-4.1']}}.get(key, default)
    routed = router.route(ctx=Context(), source='desktop', session_key='e2e-chat', message='PRIVATE_PROMPT_NOT_FOR_STORAGE', provider='openai-codex', model='gpt-4.1', reasoning_config={'effort': 'high'}, user_model=False, user_reasoning=False)
    assert routed['model'] == 'gpt-4.1-mini'
    store = EventStore(tmp_path / 'jevgauge/events.sqlite3')
    stamp = store.read_events()[0]['timestamp']
    base = dict(schema_version=1, kind='attempt', conversation_id='e2e-chat', timestamp=stamp, attempt_id='physical-1', provider='openai-codex', requested_model='gpt-4.1-mini', requested_effort='low')
    router.provider_attempt(event=dict(base, event_id='physical-1-started', status='started'))
    completed = dict(base, event_id='physical-1-completed', status='completed', reported_model='gpt-4.1-mini', usage=dict(input_tokens=1000, cached_input_tokens=200, output_tokens=100, reasoning_tokens=50, source='provider'))
    router.provider_attempt(event=completed)
    router.provider_attempt(event=completed)
    assert len(store.read_events()) == 3
    srv = create_server(tmp_path, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f'http://127.0.0.1:{srv.server_port}/api/dashboard', timeout=5) as response:
            result = json.load(response)
        assert result['summary']['request_count'] == 1
        assert result['summary']['conversation_count'] == 1
        assert result['summary']['provider_cost_usd'] == .0005
        assert result['summary']['default_cost_usd'] == .0025
        assert result['summary']['provider_difference_usd'] == .002
        assert result['summary']['net_savings_usd'] is None
        assert 'PRIVATE_PROMPT' not in json.dumps(result)
        assert 'FAKE_SECRET' not in json.dumps(result)
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
