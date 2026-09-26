from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import sqlite3
import pytest

from jevgauge.telemetry import EventStore, record_event


def event(n=1, **kw):
    return dict(schema_version=1, event_id=f'e{n}', kind='route', conversation_id='chat',
                timestamp='2026-09-25T10:00:00Z', **kw)


def test_idempotent_restart_and_conflicting_duplicate(tmp_path):
    path = tmp_path / 'events.sqlite3'
    assert EventStore(path).append(event(default_model='old', eligible_models=[{'model':'a','capability':0}]))
    assert not EventStore(path).append(event(default_model='old', eligible_models=[{'model':'a','capability':0}]))
    with pytest.raises(ValueError):
        EventStore(path).append(event(default_model='changed'))
    assert EventStore(path).read_events()[0]['default_model'] == 'old'


def test_concurrent_initialization_and_append(tmp_path):
    path = tmp_path / 'events.sqlite3'
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(lambda n: EventStore(path).append(event(n)), range(50)))
    assert len(EventStore(path).read_events()) == 50


def test_migration_empty_sqlite_and_future_version(tmp_path):
    path = tmp_path / 'events.sqlite3'
    sqlite3.connect(path).close()
    EventStore(path).append(event())
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
        db.execute('PRAGMA user_version=99')
    with pytest.raises(ValueError):
        EventStore(path).read_events()


@pytest.mark.parametrize('change', [dict(schema_version=2), dict(timestamp='2026-09-25'),
    dict(kind='prompt'), dict(usage={'input_tokens': True}), dict(prompt='secret'),
    dict(event_id='x'*300), dict(usage={'input_tokens':2,'cached_input_tokens':3,'output_tokens':1,'reasoning_tokens':0,'source':'provider'}),
    dict(eligible_models=[{'model':'x','capability':-1}]), dict(billed_usd=float('nan'))])
def test_reject_malformed_and_secret_fields(tmp_path, change):
    candidate = event()
    candidate.update(change)
    with pytest.raises(ValueError):
        EventStore(tmp_path / 'events.sqlite3').append(candidate)


def test_no_creation_on_read_and_bounds(tmp_path):
    path = tmp_path / 'missing' / 'events.sqlite3'
    assert EventStore(path).read_events() == []
    assert not path.exists()
    with pytest.raises(ValueError):
        EventStore(path).read_events(limit=100001)


def test_corrupt_inaccessible_fail_open(tmp_path):
    home = tmp_path
    (home / 'jevgauge').mkdir()
    (home / 'jevgauge' / 'events.sqlite3').write_bytes(b'bad database')
    assert record_event(home, event()) is False
    assert record_event(home, {'prompt': 'do not leak'}) is False
    assert record_event(home / 'jevgauge' / 'events.sqlite3', event()) is False


def test_attempt_partial_then_terminal_persist_separately(tmp_path):
    store = EventStore(tmp_path / 'events.sqlite3')
    store.append(event(1, attempt_id='a', status='started'))
    store.append(event(2, attempt_id='a', status='completed', usage={'input_tokens':10,
        'cached_input_tokens':None,'output_tokens':5,'reasoning_tokens':None,'source':'provider'}))
    assert len(store.read_events()) == 2
    with pytest.raises(ValueError):
        store.append(event(3, usage={'input_tokens':10,'cached_input_tokens':0,'output_tokens':5,
            'reasoning_tokens':6,'source':'provider'}))


def test_slow_local_writer_queues_other_appends_but_telemetry_stays_bounded(tmp_path, monkeypatch):
    """A writer can outlast SQLite's short busy timeout without losing local peers."""
    import threading
    from concurrent.futures import TimeoutError
    entered, release = threading.Event(), threading.Event()
    actual_connect = sqlite3.connect

    class SlowFirstWriter(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql.startswith('INSERT INTO events') and not entered.is_set():
                entered.set()
                assert release.wait(5)
            return cursor

    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **kw: actual_connect(*a, factory=SlowFirstWriter, **kw))
    path = tmp_path / 'jevgauge' / 'events.sqlite3'
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(EventStore(path).append, event(1))
        try:
            assert entered.wait(2)
            second = pool.submit(EventStore(path).append, event(2))
            # Exceed the 250ms cross-process busy timeout. A same-process append
            # must queue before opening SQLite instead of failing database locked.
            with pytest.raises(TimeoutError):
                second.result(timeout=.4)
            observer = pool.submit(record_event, tmp_path, event(3))
            assert observer.result(timeout=1) is False
            # Another home is independent of this writer and can still persist.
            other = pool.submit(EventStore(tmp_path / 'other.sqlite3').append, event(4))
            assert other.result(timeout=1) is True
        finally:
            release.set()
        assert first.result(timeout=2) is True
        assert second.result(timeout=2) is True
    assert [row['event_id'] for row in EventStore(path).read_events()] == ['e1', 'e2']
