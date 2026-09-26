"""Independent frontend review of opening failures, no real browser or profile."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import urllib.request
import webbrowser


def test_running_dashboard_browser_failure_returns_url_without_respawning(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('opening_review_router', Path(__file__).parents[1] / 'jev-router/__init__.py')
    router = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(router)
    monkeypatch.setattr(router, '_home', lambda: tmp_path)
    identity = {'service':'jevgauge', 'mode':'live', 'home_id':hashlib.sha256(str(tmp_path).encode()).hexdigest()}
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps(identity).encode()))
    def unavailable(*args, **kwargs):
        raise webbrowser.Error('browser executable missing')
    monkeypatch.setattr(webbrowser, 'open', unavailable)
    spawned = []
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: spawned.append(a))
    result = router.dashboard()
    assert 'running at http://127.0.0.1:8765/' in result
    assert 'browser' in result.lower()
    assert not spawned
