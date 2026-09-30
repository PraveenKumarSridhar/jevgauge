"""Exercise the distributable Jev callback against one exact native Hermes contract."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

HERMES_REPO = Path(os.environ["HERMES_REPO"]).resolve()
JEV_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERMES_REPO))


def _load_router():
    source = JEV_ROOT / "jev-router" / "__init__.py"
    spec = importlib.util.spec_from_file_location("jev_native_contract_plugin", source)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_packaged_plugin_registers_only_the_declared_native_contract():
    from hermes_cli.middleware import TURN_ROUTE_API_VERSION, VALID_MIDDLEWARE

    assert TURN_ROUTE_API_VERSION == 1
    assert "turn_route" in VALID_MIDDLEWARE

    class Context:
        def __init__(self):
            self.middleware = []
            self.hooks = []
            self.commands = []

        def register_middleware(self, name, callback):
            self.middleware.append((name, callback))

        def register_hook(self, name, callback):
            self.hooks.append((name, callback))

        def register_command(self, name, callback, **_kwargs):
            self.commands.append((name, callback))

        def get_config(self, _key, default=None):
            return default

    router = _load_router()
    ctx = Context()
    router.register(ctx)

    assert [name for name, _callback in ctx.middleware] == ["turn_route"]
    assert router.runtime_health(ctx)["routing"] == {
        "status": "available",
        "reason": "host contract declared",
        "api_version": 1,
        "contract": "turn_route",
        "scope": "addressed_profile",
    }
