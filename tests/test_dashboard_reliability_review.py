"""Independent reliability review cases; no live account/provider access."""
import json
import socket
import sqlite3
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from jevgauge import dashboard_config
from jevgauge.dashboard import create_server
from jevgauge.telemetry import EventStore


@pytest.fixture
def service(tmp_path):
    server=create_server(tmp_path,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield server,tmp_path
    server.shutdown(); server.server_close(); thread.join(timeout=5)


def raw_request(server,head):
    with socket.create_connection(server.server_address,timeout=5) as client:
        client.sendall(head.encode())
        chunks=[]
        while True:
            chunk=client.recv(65536)
            if not chunk: break
            chunks.append(chunk)
    return b''.join(chunks)


def test_duplicate_host_and_origin_headers_refused(service):
    server,_=service
    host=f'127.0.0.1:{server.server_port}'
    for extra in (f'Host: {host}\r\n',f'Origin: http://{host}\r\nOrigin: http://{host}\r\n'):
        response=raw_request(server,f'GET /api/config HTTP/1.1\r\nHost: {host}\r\n{extra}Connection: close\r\n\r\n')
        assert b' 403 ' in response.split(b'\r\n')[0]


def test_malformed_absolute_request_target_returns_safe_error(service):
    server,_=service
    response=raw_request(server,f'GET http://[ HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\nConnection: close\r\n\r\n')
    assert b' 400 ' in response.split(b'\r\n')[0]


def test_malformed_persisted_telemetry_never_returns_private_payload(service):
    server,home=service
    store=EventStore(home/'jevgauge/events.sqlite3')
    store.append(dict(schema_version=1,event_id='good',conversation_id='chat',kind='route',timestamp='2026-09-25T00:00:00Z'))
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE events SET payload=?',(json.dumps({'prompt':'PRIVATE-STORED-TEXT'}),))
    try: response=urlopen(f'http://127.0.0.1:{server.server_port}/api/dashboard')
    except HTTPError as error: response=error
    with response:
        body=response.read()
        assert response.status in (400,503)
        assert b'PRIVATE-STORED-TEXT' not in body
        assert b'"summary"' not in body


def test_config_read_cannot_pair_old_settings_with_new_revision(tmp_path,monkeypatch):
    path=tmp_path/'config.yaml'
    path.write_text('plugins:\n  entries:\n    jev-router:\n      settings:\n        selection_timeout: 5\n')
    original=dashboard_config._read_config
    once=[]
    def concurrent_read(home, **kwargs):
        result=original(home, **kwargs)
        if not once:
            once.append(True)
            path.write_text(path.read_text().replace('selection_timeout: 5','selection_timeout: 2'))
        return result
    monkeypatch.setattr(dashboard_config,'_read_config',concurrent_read)
    try: observed=dashboard_config.read_config(tmp_path)
    except dashboard_config.ConfigError: return  # a safe conflict is valid
    fresh=dashboard_config.read_config(tmp_path)
    assert observed['revision'] != fresh['revision'] or observed['settings']==fresh['settings']


def test_dashboard_cannot_enable_missing_or_unowned_plugin(tmp_path):
    before=dashboard_config.read_config(tmp_path)
    assert before['can_enable'] is False
    with pytest.raises(dashboard_config.ConfigError,match='install'):
        dashboard_config.update_config(tmp_path,{'enabled':True},before['revision'])
    assert not (tmp_path/'config.yaml').exists()
    plugin=tmp_path/'plugins/jev-router';plugin.mkdir(parents=True)
    (plugin/'__init__.py').write_text('# unowned')
    with pytest.raises(dashboard_config.ConfigError,match='install'):
        dashboard_config.update_config(tmp_path,{'enabled':True},before['revision'])


def test_non_ascii_csrf_token_is_refused_without_handler_exception(service):
    server,_=service
    host=f'127.0.0.1:{server.server_port}'
    # HTTP header bytes decode as Latin-1. compare_digest(str,str) must not crash.
    with socket.create_connection(server.server_address,timeout=5) as client:
        client.sendall((f'POST /api/config HTTP/1.1\r\nHost: {host}\r\nOrigin: http://{host}\r\n'
                       'X-JevGauge-Token: caf\xe9\r\nContent-Length: 0\r\nConnection: close\r\n\r\n').encode('latin1'))
        response=client.recv(65536)
    assert b' 403 ' in response.split(b'\r\n')[0]


def test_yaml_alias_expansion_budget_refuses_small_exponential_document(tmp_path):
    from jevgauge.cli import _read_config, InstallError
    text='a: &a [0,0,0,0,0,0,0,0,0,0]\n'
    for previous,current in zip('abcdef','bcdefg'):
        text+=f'{current}: &{current} ['+','.join(['*'+previous]*10)+']\n'
    (tmp_path/'config.yaml').write_text(text)
    with pytest.raises(InstallError,match='budget'):
        _read_config(tmp_path)


def test_only_one_history_aggregation_runs_and_other_endpoints_remain_available(service,monkeypatch):
    import jevgauge.analytics as analytics
    from concurrent.futures import ThreadPoolExecutor
    server,_=service
    original=analytics.summarize
    entered=threading.Event(); release=threading.Event(); calls=[]
    def blocked(events,**filters):
        calls.append(True)
        if len(calls)==1:
            entered.set(); assert release.wait(5)
        return original(events,**filters)
    monkeypatch.setattr(analytics,'summarize',blocked)
    def status(path):
        try: response=urlopen(f'http://127.0.0.1:{server.server_port}'+path,timeout=5)
        except HTTPError as exc: response=exc
        with response: return response.status
    with ThreadPoolExecutor(max_workers=1) as pool:
        first=pool.submit(status,'/api/dashboard')
        assert entered.wait(3)
        try:
            assert status('/api/health')==200
            assert status('/api/dashboard')==503
            assert len(calls)==1
        finally:
            release.set()
        assert first.result(timeout=5)==200
    assert status('/api/dashboard')==200


def test_legacy_owned_plugin_requires_upgrade_before_dashboard_enable(tmp_path):
    from jevgauge.cli import _hash,MANIFEST
    plugin=tmp_path/'plugins/jev-router';plugin.mkdir(parents=True)
    old={'__init__.py':b'# legacy plugin','plugin.yaml':b'name: jev-router\n'}
    for name,data in old.items(): (plugin/name).write_bytes(data)
    (plugin/MANIFEST).write_text(json.dumps({'owner':'jevgauge','format':1,'files':{n:_hash(d) for n,d in old.items()}}))
    assert dashboard_config.read_config(tmp_path)['can_enable'] is False
    # Ownership itself still valid for safe migration/uninstall.
    from jevgauge.cli import _verify_owned
    assert _verify_owned(plugin)['files']=={n:_hash(d) for n,d in old.items()}


def test_missing_plugin_never_claims_effectively_enabled_from_stale_yaml(tmp_path):
    (tmp_path/'config.yaml').write_text('plugins:\n  enabled: [jev-router]\n  entries:\n    jev-router:\n      settings:\n        enabled: true\n')
    config=dashboard_config.read_config(tmp_path)
    assert config['can_enable'] is False
    assert config['settings']['enabled'] is False
