"""An owned plugin must not import telemetry from an unrelated editable checkout."""
import importlib.util
from pathlib import Path
import shutil
import sqlite3
import sys
from types import ModuleType

import pytest


@pytest.fixture
def deployed(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    plugin = tmp_path / 'plugin'
    plugin.mkdir()
    shutil.copyfile(root / 'jev-router/__init__.py', plugin / '__init__.py')
    spec = importlib.util.spec_from_file_location('owned_router', plugin / '__init__.py')
    router = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(router)
    home = tmp_path / 'home'
    monkeypatch.setattr(router, '_home', lambda: home)
    foreign = ModuleType('jevgauge.telemetry')
    calls = []
    foreign.record_event = lambda *args: calls.append(args) or 'foreign'
    monkeypatch.setitem(sys.modules, 'jevgauge.telemetry', foreign)
    return router, plugin, home, calls, root


def test_owned_telemetry_wins_over_importable_foreign_package(deployed):
    router, plugin, home, calls, root = deployed
    shutil.copyfile(root / 'src/jevgauge/telemetry.py', plugin / 'telemetry.py')
    event = {'schema_version': 1, 'kind': 'route', 'event_id': 'isolated-event',
             'conversation_id': 'isolated-chat', 'timestamp': '2026-09-26T00:00:00Z'}
    assert router._record(event) is True
    assert calls == []
    assert Path(router._STORAGE_RECORD.__code__.co_filename) == plugin / 'telemetry.py'
    with sqlite3.connect(home / 'jevgauge/events.sqlite3') as db:
        assert db.execute('SELECT event_id FROM events').fetchall() == [('isolated-event',)]


def test_broken_owned_telemetry_does_not_silently_import_foreign_copy(deployed):
    router, plugin, home, calls, _ = deployed
    (plugin / 'telemetry.py').write_text('raise ImportError("broken owned copy")\n')
    router.provider_attempt(event={})  # Observer failure must not break chat.
    assert calls == []
    assert router._STORAGE_RECORD is None
    assert not home.exists()


def test_development_layout_without_sibling_uses_packaged_telemetry(deployed):
    router, _, _, calls, _ = deployed
    assert router._record({}) == 'foreign'
    assert len(calls) == 1


def test_missing_owned_file_does_not_fall_back_to_foreign_copy(deployed):
    router, plugin, home, calls, _ = deployed
    (plugin / '.jevgauge-install.json').write_text('{"owner":"jevgauge"}')
    router.provider_attempt(event={})
    assert calls == []
    assert router._STORAGE_RECORD is None
    assert not home.exists()
