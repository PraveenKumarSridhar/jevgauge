#!/usr/bin/env python3
"""Controlled installed-plugin route, physical stream, cold resume and slash-command check.

Run using a pinned patched Hermes TEST interpreter (not the active installation):
  <hermes-test-python> scripts/verify_hermes_dashboard.py --hermes-repo PATH \
      --dashboard-python PATH

Creates only a temporary Hermes home. Provider and browser launcher are deterministic
stubs; plugin discovery, session database, hook dispatch, storage, child dashboard
process and HTTP are real. Port 8765 must be free. No paid calls or credential reads.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-repo', type=Path, required=True)
    parser.add_argument('--dashboard-python', type=Path, required=True)
    parser.add_argument('--resume-home', type=Path)
    args = parser.parse_args()
    # Drop inherited secrets before importing any host code.
    for key in list(os.environ):
        if key.endswith(('_API_KEY', '_TOKEN', '_SECRET', '_PASSWORD', '_CREDENTIALS')) or key.startswith('HERMES_SESSION_'):
            os.environ.pop(key, None)
    sys.path.insert(0, str(args.hermes_repo.resolve()))
    if args.resume_home:
        os.environ['HERMES_HOME'] = str(args.resume_home)
        exercise(args, args.resume_home, resume=True)
        return
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8765))
    with tempfile.TemporaryDirectory(prefix='jevgauge integration ') as temp:
        home = Path(temp) / 'home'
        home.mkdir()
        os.environ['HERMES_HOME'] = str(home)
        subprocess.run([str(args.dashboard_python), '-m', 'jevgauge', 'install', '--home', str(home),
            '--hermes-repo', str(args.hermes_repo)], check=True, capture_output=True)
        exercise(args, home, resume=False)
        subprocess.run([sys.executable, str(Path(__file__).resolve()), '--hermes-repo', str(args.hermes_repo),
            '--dashboard-python', str(args.dashboard_python), '--resume-home', str(home)], check=True)
        # A fresh process must see one original route and two physical attempts.
        spec = importlib.util.spec_from_file_location('check_storage', home / 'plugins/jev-router/telemetry.py')
        storage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(storage)
        rows = storage.EventStore(home / 'jevgauge/events.sqlite3').read_events()
        assert len([r for r in rows if r['kind'] == 'route']) == 1
        assert len([r for r in rows if r.get('status') == 'completed']) == 2
        assert rows[0]['default_model'] == 'gpt-6-sol'
        assert 'PRIVATE PROMPT' not in json.dumps(rows)
        open_command(home)
    print('PASS: installed discovery -> route -> physical usage -> SQLite -> cold resume -> RPC slash command -> child HTTP -> browser handoff')


def exercise(args, home, *, resume):
    from hermes_cli import plugins
    from tui_gateway import server
    from hermes_state import SessionDB
    from agent.codex_runtime import run_codex_stream
    plugins.discover_plugins()
    handler = plugins.get_plugin_command_handler('jev-dashboard')
    assert handler, 'Installed /jev-dashboard command not discovered'
    router = plugins.get_plugin_manager()._plugins['jev-router'].module
    if not resume:
        session = {'source':'desktop','session_key':'controlled-chat','history':[], 'agent':None}
        decision = {'answers':{'model_tier':{'choice':'economical','confidence':.9},'effort_tier':{'choice':'low','confidence':.9}}}
        with patch.object(router, '_get_secret', return_value='synthetic-test-key'), \
             patch.object(router, '_live_models', return_value=['gpt-6-luna','gpt-6-sol']), \
             patch.object(router, '_decide', return_value=decision), \
             patch.object(server, '_resolve_model', return_value='gpt-6-sol'), \
             patch.object(server, '_config_model_target', return_value=('gpt-6-sol','openai-codex')), \
             patch.object(server, '_load_reasoning_config', return_value={'effort':'high'}):
            server._apply_first_prompt_route(session, 'PRIVATE PROMPT')
        assert session['session_route']['status'] == 'routed'
        model, config = server._workdir_row_model_config(session)
        db = SessionDB()
        db.create_session('controlled-chat','desktop',model=model,model_config=config)
        db.close()
    else:
        db = SessionDB(read_only=True)
        row = db.get_session('controlled-chat')
        db.close()
        restored = server._stored_session_runtime_overrides(row)
        resume_state = server._Resume('verification', {}, 'controlled-chat')
        resume_state.found = row
        session = resume_state.record('desktop', str(home), [{'role':'assistant','content':'synthetic'}], restored)
        with patch.object(router, '_decide', side_effect=AssertionError('Resumed chat rerouted')):
            server._apply_first_prompt_route(session, 'PRIVATE PROMPT resumed')
        assert session['first_prompt_route_checked']
        assert session['session_route']['default_model'] == 'gpt-6-sol'
    agent = SimpleNamespace(session_id='controlled-chat', provider='openai-codex', model='gpt-6-luna',
        reasoning_config={'effort':'low'}, _interrupt_requested=False, _last_api_first_chunk_at=None,
        _touch_activity=lambda *_: None, _fire_stream_delta=lambda *_: None,
        _fire_reasoning_delta=lambda *_: None, _client_log_context=lambda: '', _fallback_chain=[])
    server._arm_first_call_route_fallback(session, agent)
    terminal = {'type':'response.completed','response':{'status':'completed','model':'gpt-6-luna',
        'usage':{'input_tokens':100,'input_tokens_details':{'cached_tokens':25},
                 'output_tokens':20,'output_tokens_details':{'reasoning_tokens':5}}}}
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: iter([terminal])))
    assert run_codex_stream(agent, {'model':'gpt-6-luna','reasoning':{'effort':'low'},'input':'PRIVATE PROMPT'}, client=client).status == 'completed'


def open_command(home):
    from tui_gateway import server
    children, opened = [], []
    actual_popen = subprocess.Popen
    def start(*args, **kwargs):
        child = actual_popen(*args, **kwargs)
        children.append(child)
        return child
    try:
        with patch('subprocess.Popen', side_effect=start), patch('webbrowser.open', side_effect=lambda url: opened.append(url) or True):
            result = server._dispatch_plugin('verification', {}, {'session_key':'controlled-chat'}, 'jev-dashboard', '')
            assert 'Opened JevGauge' in str(result), result
        assert opened == ['http://127.0.0.1:8765/']
        assert len(children) == 1
        with urllib.request.urlopen(opened[0] + 'api/dashboard') as response:
            body = json.load(response)
        assert body['mode'] == 'live'
        assert body['summary']['conversation_count'] == 1
        assert body['summary']['request_count'] == 2
        assert body['coverage']['known_usage_requests'] == 2
        assert body['conversations'][0]['default_model'] == 'gpt-6-sol'
        assert 'PRIVATE PROMPT' not in json.dumps(body)
        # Same-home second invocation reuses the listener and opens again.
        with patch('subprocess.Popen', side_effect=AssertionError('Duplicate child')), patch('webbrowser.open', return_value=True):
            result = server._dispatch_plugin('verification', {}, {'session_key':'controlled-chat'}, 'jev-dashboard', '')
            assert 'Opened JevGauge' in str(result)
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=10)


if __name__ == '__main__':
    main()
