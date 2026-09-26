"""Loopback-only dashboard. No ingestion or arbitrary filesystem HTTP endpoints."""
from __future__ import annotations

import copy
import hashlib
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import dashboard_config as config

MAX_EVENTS = 50000
MAX_BODY = 65536


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 16

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def _query(raw):
    # Python 3.10 strict parsing rejects the valid unfiltered empty query.
    pairs = (parse_qs(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=5)
             if raw else {})
    if set(pairs) - {'start', 'end', 'timezone', 'project'} or any(len(v) != 1 for v in pairs.values()):
        raise ValueError('Invalid filters.')
    values = {key: val[0] for key, val in pairs.items()}
    if any(len(v) > 256 for v in values.values()):
        raise ValueError('Filter exceeds the supported size.')
    for field in ('start', 'end'):
        if field in values:
            parsed = date.fromisoformat(values[field])
            if parsed.isoformat() != values[field]:
                raise ValueError('Use YYYY-MM-DD dates.')
    if values.get('start') and values.get('end'):
        first, last = date.fromisoformat(values['start']), date.fromisoformat(values['end'])
        if first > last or (last - first).days > 3660:
            raise ValueError('Date range must be ordered and at most ten years.')
    ZoneInfo(values.get('timezone', 'UTC'))
    return values


def create_server(home: Path, *, port=8765, demo=False):
    home = Path(home)
    csrf = secrets.token_urlsafe(32)
    demo_settings = copy.deepcopy(config.DEFAULTS)
    demo_revision = 0
    state_lock = threading.Lock()
    aggregation_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = 'JevGauge'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args):
            # Request paths can include user-controlled text. Keep them out of logs.
            pass

        def respond(self, status, body, content_type='application/json; charset=utf-8'):
            data = json.dumps(body, allow_nan=False).encode() if content_type.startswith('application/json') else body
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self, write=False):
            host = f'127.0.0.1:{self.server.server_port}'
            origin = f'http://{host}'
            hosts, origins = self.headers.get_all('Host', []), self.headers.get_all('Origin', [])
            if hosts != [host] or len(origins) > 1 or (origins and origins != [origin]):
                self.respond(403, {'error': 'Only same-origin loopback requests are allowed.'})
                return False
            supplied_token = self.headers.get('X-JevGauge-Token', '')
            if write and (origins != [origin] or not supplied_token.isascii() or not secrets.compare_digest(supplied_token, csrf)):
                self.respond(403, {'error': 'Reload the dashboard before saving configuration.'})
                return False
            if len(self.path) > 2048:
                self.respond(414, {'error': 'Request URL is too long.'})
                return False
            return True

        def config_result(self):
            if demo:
                result = {'settings': copy.deepcopy(demo_settings), 'revision': str(demo_revision),
                          'supported': list(config.DEFAULTS), 'semantics': 'Demo changes stay in memory and never modify Hermes.'}
            else:
                result = config.read_config(home)
            return {**result, 'csrf_token': csrf, 'mode': 'demo' if demo else 'live'}

        def do_GET(self):
            if not self.allowed():
                return
            try:
                parsed = urlsplit(self.path)
                if parsed.path == '/api/health':
                    self.respond(200, {'service': 'jevgauge', 'mode': 'demo' if demo else 'live', 'home_id': hashlib.sha256(str(home.expanduser().absolute()).encode()).hexdigest()})
                elif parsed.path == '/api/config':
                    with state_lock:
                        self.respond(200, self.config_result())
                elif parsed.path == '/api/dashboard':
                    try:
                        filters = _query(parsed.query)
                    except (ValueError, ZoneInfoNotFoundError):
                        self.respond(400, {'error': 'Invalid project, timezone, or date range. The active filter was not changed.'})
                        return
                    if not aggregation_lock.acquire(blocking=False):
                        self.respond(503, {'error': 'A history query is already running. Retry shortly.'})
                        return
                    try:
                        from .analytics import summarize
                        if demo:
                            from .demo import events
                            captured = events()
                        else:
                            from .telemetry import EventStore, HistoryLimitError
                            store = EventStore(home / 'jevgauge' / 'events.sqlite3')
                            try:
                                captured = store.read_events(limit=MAX_EVENTS + 1)
                            except HistoryLimitError:
                                self.respond(503, {'error': 'History exceeds the 50,000-event dashboard limit. Archive older data offline before viewing. No partial totals are shown.'})
                                return
                        if len(captured) > MAX_EVENTS:
                            self.respond(503, {'error': 'History exceeds the 50,000-event dashboard limit. Archive older data offline before viewing. No partial totals are shown.'})
                            return
                        result = summarize(captured, **filters)
                        result['mode'] = 'demo' if demo else 'live'
                        result['generated_at'] = datetime.now(timezone.utc).isoformat()
                        result['history_limit'] = MAX_EVENTS
                        self.respond(200, result)
                    finally:
                        aggregation_lock.release()
                else:
                    name = {'/': 'index.html', '/app.js': 'app.js', '/styles.css': 'styles.css', '/favicon.svg': 'favicon.svg'}.get(parsed.path)
                    if not name:
                        self.respond(404, {'error': 'Not found.'})
                        return
                    data = resources.files('jevgauge').joinpath('static', name).read_bytes()
                    mime = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css', 'svg': 'image/svg+xml'}[name.rsplit('.', 1)[1]]
                    self.respond(200, data, mime + '; charset=utf-8')
            except config.ConfigError as exc:
                self.respond(400, {'error': str(exc)})
            except ValueError:
                self.respond(400, {'error': 'Invalid evidence or filters. No totals are shown.'})
            except Exception:
                self.respond(503, {'error': 'Dashboard evidence is unavailable. Check storage access and integrity. Routing is independent of this service.'})

        def do_POST(self):
            nonlocal demo_revision
            if not self.allowed(write=True):
                return
            if self.path != '/api/config':
                self.respond(404, {'error': 'Not found.'})
                return
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json' or self.headers.get('Transfer-Encoding'):
                self.respond(400, {'error': 'Use a bounded JSON request.'})
                return
            try:
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1:
                    raise ValueError
                length = int(lengths[0])
                if length < 0:
                    raise ValueError
                if length > MAX_BODY:
                    self.respond(413, {'error': 'Configuration request is too large.'})
                    return
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != {'settings', 'revision'}:
                    raise ValueError
                with state_lock:
                    if demo:
                        config.validate(payload['settings'])
                        if payload['revision'] != str(demo_revision):
                            raise config.ConfigError('Configuration changed since it was loaded. Reload before saving.')
                        demo_settings.update(payload['settings'])
                        demo_revision += 1
                    else:
                        config.update_config(home, payload['settings'], payload['revision'])
                    self.respond(200, self.config_result())
            except config.ConfigError as exc:
                self.respond(409 if 'changed since' in str(exc) else 400, {'error': str(exc)})
            except (ValueError, UnicodeError):
                self.respond(400, {'error': 'Invalid JSON configuration request.'})
            except Exception:
                self.respond(503, {'error': 'Configuration could not be saved.'})

    return DashboardServer(('127.0.0.1', port), Handler)


def serve(home, *, port=8765, demo=False, open_browser=False):
    server = create_server(home, port=port, demo=demo)
    url = f'http://127.0.0.1:{server.server_port}/'
    print(f'JevGauge {"DEMO (synthetic)" if demo else "live captured evidence"}: {url}', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
