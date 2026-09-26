"""Exercise the actual loopback HTTP server without provider calls."""
import importlib
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest


@pytest.fixture
def server(tmp_path):
    m = importlib.import_module('jevgauge.dashboard')
    srv = m.create_server(tmp_path, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, f'http://127.0.0.1:{srv.server_port}', tmp_path
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def request(url, path, *, data=None, headers=None):
    req = Request(url + path, data=data, headers=headers or {})
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, response.headers, json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw


def test_live_empty_store_and_response_security(server):
    _, url, home = server
    status, headers, body = request(url, '/api/dashboard')
    assert status == 200
    assert body['mode'] == 'live'
    assert body['summary']['conversation_count'] == 0
    assert body['currency'] == 'USD'
    assert headers['Cache-Control'] == 'no-store'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    assert headers['Access-Control-Allow-Origin'] is None
    assert str(home) not in json.dumps(body)


def test_host_origin_and_config_write_protection(server):
    _, url, home = server
    assert request(url, '/api/config', headers={'Host': 'evil.example'})[0] == 403
    assert request(url, '/api/config', headers={'Origin': 'https://evil.example'})[0] == 403
    status, _, config = request(url, '/api/config')
    assert status == 200
    payload = json.dumps({'settings': {'selection_timeout': 2.5}, 'revision': config['revision']}).encode()
    assert request(url, '/api/config', data=payload)[0] == 403
    auth = {'Origin': url, 'X-JevGauge-Token': config['csrf_token'], 'Content-Type': 'application/json'}
    assert request(url, '/api/config', data=payload, headers={**auth, 'Origin': 'null'})[0] == 403
    assert not (home / 'config.yaml').exists()
    status, _, saved = request(url, '/api/config', data=payload, headers=auth)
    assert status == 200 and saved['settings']['selection_timeout'] == 2.5
    assert request(url, '/api/config', data=payload, headers=auth)[0] == 409
    assert request(url, '/api/config', data=b'{', headers=auth)[0] == 400
    assert request(url, '/api/config', data=b'x' * 65537, headers=auth)[0] == 413


@pytest.mark.parametrize('query', [
    '?start=2026-09-26&end=2026-09-01', '?timezone=not-a-zone',
    '?start=bad', '?start=2026-09-01&start=2026-09-02', '?unknown=x',
    '?project=' + 'a' * 257,
])
def test_invalid_queries_rejected(server, query):
    _, url, _ = server
    assert request(url, '/api/dashboard' + query)[0] == 400


def test_corrupt_storage_is_an_error_not_empty_success(server):
    _, url, home = server
    folder = home / 'jevgauge'
    folder.mkdir(exist_ok=True)
    (folder / 'events.sqlite3').write_bytes(b'corrupt private content')
    status, _, body = request(url, '/api/dashboard')
    assert status == 503
    assert 'private content' not in json.dumps(body)
    assert 'summary' not in body


def test_demo_never_reads_or_writes_real_config_or_store(tmp_path):
    m = importlib.import_module('jevgauge.dashboard')
    (tmp_path / 'config.yaml').write_text('secret: never-return\n')
    srv = m.create_server(tmp_path, port=0, demo=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{srv.server_port}'
    try:
        status, _, result = request(url, '/api/dashboard')
        assert status == 200 and result['mode'] == 'demo'
        assert result['summary']['conversation_count'] > 0
        _, _, config = request(url, '/api/config')
        status, _, _ = request(url, '/api/config', data=json.dumps({'settings': {'enabled': True}, 'revision': config['revision']}).encode(), headers={'Origin': url, 'X-JevGauge-Token': config['csrf_token'], 'Content-Type': 'application/json'})
        assert status == 200
        assert (tmp_path / 'config.yaml').read_text() == 'secret: never-return\n'
        assert not (tmp_path / 'jevgauge').exists()
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)
