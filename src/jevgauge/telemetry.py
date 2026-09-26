"""Versioned, prompt-free evidence storage. This module also ships with the plugin.

Only Python's standard library is required in the host Hermes interpreter.
Connections are short lived, transaction boundaries serialize initialization and
append, and event identity rejects conflicting replay rather than replacing history.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3

SCHEMA_VERSION = 1
MAX_EVENTS = 100000
TEXT_FIELDS = {'event_id', 'conversation_id', 'project', 'provider', 'policy_version',
    'reason_code', 'outcome', 'default_model', 'default_effort', 'selected_model',
    'requested_effort', 'model_tier', 'effort_tier', 'eligibility_source', 'attempt_id',
    'requested_model', 'reported_model', 'reported_effort', 'overhead_source'}
FIELDS = TEXT_FIELDS | {'schema_version', 'kind', 'timestamp', 'latency_ms', 'eligible_models',
    'status', 'manual_override', 'fallback', 'usage', 'overhead_usd', 'billed_usd'}


def _nonnegative(value, *, integer=False):
    if value is None:
        return
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or not 0 <= value <= 10**15:
        raise ValueError('Invalid numeric evidence')


def validate_event(event):
    if not isinstance(event, dict) or set(event) - FIELDS:
        raise ValueError('Unknown event fields')
    if type(event.get('schema_version')) is not int or event['schema_version'] != SCHEMA_VERSION:
        raise ValueError('Unsupported event schema')
    if event.get('kind') not in ('route', 'attempt', 'overhead', 'override'):
        raise ValueError('Invalid event kind')
    for field in TEXT_FIELDS:
        value = event.get(field)
        if value is not None and (not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value)):
            raise ValueError('Invalid text evidence')
    if not event.get('event_id') or not event.get('conversation_id'):
        raise ValueError('Missing event identity')
    try:
        stamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError('Expected aware UTC timestamp') from None
    if len(event['timestamp']) > 40:
        raise ValueError('Invalid timestamp')
    for key in ('manual_override', 'fallback'):
        if key in event and type(event[key]) is not bool:
            raise ValueError('Invalid ownership flag')
    if 'status' in event and event['status'] not in ('started', 'completed', 'rejected', 'failed'):
        raise ValueError('Invalid attempt status')
    for key in ('latency_ms', 'overhead_usd', 'billed_usd'):
        _nonnegative(event.get(key))
    eligible = event.get('eligible_models')
    if eligible is not None:
        if not isinstance(eligible, list) or len(eligible) > 100:
            raise ValueError('Invalid eligibility evidence')
        for item in eligible:
            if not isinstance(item, dict) or set(item) != {'model', 'capability'} or not isinstance(item['model'], str) or not 0 < len(item['model']) <= 256:
                raise ValueError('Invalid eligible model')
            _nonnegative(item['capability'], integer=True)
            if item['capability'] is None:
                raise ValueError('Missing capability ordering')
    usage = event.get('usage')
    if usage is not None:
        if not isinstance(usage, dict) or set(usage) != {'input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_tokens', 'source'} or usage['source'] != 'provider':
            raise ValueError('Invalid usage evidence')
        for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_tokens'):
            _nonnegative(usage[key], integer=True)
        for total, detail in (('input_tokens', 'cached_input_tokens'), ('output_tokens', 'reasoning_tokens')):
            if usage[total] is not None and usage[detail] is not None and usage[detail] > usage[total]:
                raise ValueError('Usage subset exceeds total')
    normalized = dict(event, timestamp=stamp.isoformat(timespec='microseconds').replace('+00:00', 'Z'))
    serialized = json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(serialized.encode()) > 32768:
        raise ValueError('Event too large')
    return normalized, serialized


class HistoryLimitError(ValueError):
    """Complete evidence exceeds the bounded dashboard query budget."""


class EventStore:
    def __init__(self, path):
        self.path = Path(path)

    def _connect(self, *, create):
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ValueError('Symlink storage refused')
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Set private permissions before SQLite ever writes evidence.
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(descriptor)
        db = sqlite3.connect(str(self.path), timeout=0.25)
        try:
            db.execute('PRAGMA busy_timeout=250')
            db.execute('BEGIN IMMEDIATE')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError('Unsupported storage schema')
            if version == 0:
                db.execute('CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, payload TEXT NOT NULL)')
                db.execute('CREATE INDEX IF NOT EXISTS events_time ON events(timestamp, event_id)')
                db.execute('PRAGMA user_version=1')
            db.commit()
            return db
        except Exception:
            db.close()
            raise

    def append(self, event):
        normalized, payload = validate_event(event)
        db = self._connect(create=True)
        try:
            with db:
                db.execute('BEGIN IMMEDIATE')
                previous = db.execute('SELECT payload FROM events WHERE event_id=?', (normalized['event_id'],)).fetchone()
                if previous:
                    if previous[0] != payload:
                        raise ValueError('Conflicting event identity')
                    return False
                db.execute('INSERT INTO events VALUES (?, ?, ?)', (normalized['event_id'], normalized['timestamp'], payload))
            return True
        finally:
            db.close()

    def read_events(self, limit=50000):
        if type(limit) is not int or not 1 <= limit <= MAX_EVENTS:
            raise ValueError('Invalid event limit')
        if not self.path.exists():
            return []
        db = self._connect(create=False)
        try:
            # Fetch earliest evidence: truncating into a conversation could orphan its baseline.
            rows = db.execute('SELECT payload FROM events ORDER BY timestamp, event_id LIMIT ?', (limit + 1,)).fetchall()
            if len(rows) > limit:
                raise HistoryLimitError('History exceeds query limit')
            return [validate_event(json.loads(row[0]))[0] for row in rows]
        finally:
            db.close()

    def close(self):
        """Compatibility no-op: every operation owns its connection."""


def record_event(home, event):
    """Fail open without logging potentially secret-bearing exception text."""
    try:
        return EventStore(Path(home) / 'jevgauge' / 'events.sqlite3').append(event)
    except Exception:
        return False
