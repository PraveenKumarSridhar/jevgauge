"""Paid-call-free real route -> attempt callbacks -> storage -> loopback server."""
import importlib.util
import json
import signal
import os
from pathlib import Path
import tempfile
from jevgauge.dashboard import create_server
from jevgauge.telemetry import EventStore

def stop_fixture(signum, frame):
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop_fixture)

with tempfile.TemporaryDirectory(prefix='jevgauge-browser-') as home:
    os.environ['HERMES_HOME'] = home
    spec = importlib.util.spec_from_file_location('browser_router', Path(__file__).parents[2] / 'jev-router/__init__.py')
    router = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(router)
    router._get_secret = lambda *_args: 'FAKE_SECRET_NOT_FOR_STORAGE'
    router._live_models = lambda: ['gpt-4.1-nano', 'gpt-4.1-mini', 'gpt-4.1']
    router._supported_efforts = lambda *args: ['low']
    router._decide = lambda *args: {'answers': {'model_tier': {'choice': 'balanced', 'confidence': .9}, 'effort_tier': {'choice': 'low', 'confidence': .9}}}
    class Context:
        def get_config(self, key, default=None):
            return {'enabled': True, 'tier_models': {'economical': ['gpt-4.1-nano'], 'balanced': ['gpt-4.1-mini'], 'strongest': ['gpt-4.1']}}.get(key, default)
    result = router.route(ctx=Context(), source='desktop', session_key='browser-live-chat', message='PRIVATE_PROMPT_NOT_FOR_STORAGE <img src=x onerror=alert(1)>', provider='openai-codex', model='gpt-4.1', reasoning_config={'effort': 'high'}, user_model=False, user_reasoning=False)
    assert result['model'] == 'gpt-4.1-mini'
    store = EventStore(Path(home) / 'jevgauge/events.sqlite3')
    stamp = store.read_events()[0]['timestamp']
    base = dict(schema_version=1, kind='attempt', conversation_id='browser-live-chat', timestamp=stamp, attempt_id='physical-1', provider='openai-codex', requested_model='gpt-4.1-mini', requested_effort='low')
    router.provider_attempt(event=dict(base, event_id='physical-1-started', status='started'))
    completed = dict(base, event_id='physical-1-completed', status='completed', reported_model='gpt-4.1-mini', usage=dict(input_tokens=1000, cached_input_tokens=200, output_tokens=100, reasoning_tokens=50, source='provider'))
    router.provider_attempt(event=completed)
    router.provider_attempt(event=completed)
    assert len(store.read_events()) == 3
    server = create_server(Path(home), port=0)
    print(json.dumps({'url': 'http://127.0.0.1:' + str(server.server_port)}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
