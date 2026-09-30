"""Profile scope and enablement checks for the installed backend route API."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace


def _module(monkeypatch):
    fastapi = ModuleType('fastapi')
    class APIRouter:
        def post(self, _path):
            return lambda function: function
    class HTTPException(Exception):
        def __init__(self, *, status_code, detail):
            super().__init__(detail)
            self.status_code = status_code
    fastapi.APIRouter = APIRouter
    fastapi.HTTPException = HTTPException
    monkeypatch.setitem(sys.modules, 'fastapi', fastapi)
    path = Path(__file__).parents[1] / 'jev-router/dashboard/plugin_api.py'
    spec = importlib.util.spec_from_file_location('jev_plugin_api_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_disabled_profile_cannot_route_with_retained_settings(monkeypatch):
    api = _module(monkeypatch)
    config_module = ModuleType('hermes_cli.config')
    config_module.load_config_readonly = lambda: {'plugins': {
        'enabled': [], 'disabled': ['jev-router'],
        'entries': {'jev-router': {'settings': {'enabled': True}}},
    }}
    hermes_cli = ModuleType('hermes_cli')
    hermes_cli.config = config_module
    monkeypatch.setitem(sys.modules, 'hermes_cli', hermes_cli)
    monkeypatch.setitem(sys.modules, 'hermes_cli.config', config_module)
    calls = []
    api._ROUTER_MODULE = SimpleNamespace(route=lambda **kwargs: calls.append(kwargs))
    result = api.plan_route({'message': 'private prompt', 'model': 'gpt-6-sol',
        'provider': 'openai-codex', 'reasoning_effort': 'high', 'eligible_models': ['gpt-6-sol']})
    assert result == {'schema_version': 'jevgauge.routed_start.v1', 'status': 'default',
                      'reason': 'plugin is disabled for this profile'}
    assert calls == []
