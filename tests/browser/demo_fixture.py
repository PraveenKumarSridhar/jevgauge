"""Isolated demo server for browser acceptance, never touches the real profile."""
import json
import signal
from pathlib import Path
import tempfile
from jevgauge.dashboard import create_server

def stop_fixture(signum, frame):
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop_fixture)

with tempfile.TemporaryDirectory(prefix='jevgauge-demo-browser-') as home:
    server = create_server(Path(home), port=0, demo=True)
    print(json.dumps({'url': 'http://127.0.0.1:' + str(server.server_port)}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
