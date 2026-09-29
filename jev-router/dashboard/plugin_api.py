"""Profile-scoped route planning for Jev's native Desktop routed-start action."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()
_ROUTER_MODULE = None
_MAX_MESSAGE_CHARS = 12_000


class _ApiContext:
    def __init__(self, config: dict[str, Any]):
        self._config = config

    def get_config(self, key: str, default: Any = None) -> Any:
        plugins = self._config.get("plugins")
        entries = plugins.get("entries") if isinstance(plugins, dict) else None
        entry = entries.get("jev-router") if isinstance(entries, dict) else None
        if not isinstance(entry, dict):
            return default
        settings = entry.get("settings")
        if not isinstance(settings, dict):
            settings = entry.get("config")
        if not isinstance(settings, dict):
            return default
        current: Any = settings
        for segment in key.split("."):
            if not isinstance(current, dict) or segment not in current:
                return default
            current = current[segment]
        return current

    @staticmethod
    def get_secret(name: str) -> str | None:
        from agent.secret_scope import get_secret
        return get_secret(name)


def _load_router_module():
    global _ROUTER_MODULE
    if _ROUTER_MODULE is not None:
        return _ROUTER_MODULE
    source = Path(__file__).resolve().parents[1] / "__init__.py"
    for name, module in tuple(sys.modules.items()):
        if not name.startswith("hermes_plugins.jev_router"):
            continue
        try:
            if Path(module.__file__).resolve() == source:
                _ROUTER_MODULE = module
                return module
        except (AttributeError, OSError, TypeError):
            continue
    name = "hermes_dashboard_plugin_jev_router_policy"
    spec = importlib.util.spec_from_file_location(name, source, submodule_search_locations=[str(source.parent)])
    if spec is None or spec.loader is None:
        raise RuntimeError("Jev routing policy could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    _ROUTER_MODULE = module
    return module


@router.post("/route")
def plan_route(body: dict[str, Any]) -> dict[str, Any]:
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=400, detail="A first prompt is required")
    if len(message) > _MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=413, detail="First prompt exceeds the routing limit")
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
        model = body.get("model")
        provider = body.get("provider")
        effort = body.get("reasoning_effort")
        available = body.get("eligible_models")
        if not isinstance(model, str) or not model or len(model) > 120:
            raise RuntimeError("Hermes did not report a valid default model")
        if provider != "openai-codex":
            return {"schema_version": "jevgauge.routed_start.v1", "status": "default",
                    "reason": "provider is not openai-codex"}
        if effort not in {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}:
            raise RuntimeError("Hermes did not report a valid reasoning effort")
        if not isinstance(available, list) or not all(isinstance(item, str) and 0 < len(item) <= 120 for item in available):
            raise RuntimeError("Hermes did not report a valid model inventory")
        reasoning = {"enabled": False} if effort == "none" else {"enabled": True, "effort": effort}
        result = _load_router_module().route(
            ctx=_ApiContext(config), message=message, source="desktop", provider=provider,
            model=model, reasoning_config=reasoning, user_model=False, user_reasoning=False,
            session_key=None, host_validates=True, eligible_models=available[:256],
        )
        metadata = (result or {}).get("metadata") or {}
        if metadata.get("status") != "routed":
            return {"schema_version": "jevgauge.routed_start.v1", "status": "default",
                    "reason": str(metadata.get("reason") or "selection kept profile defaults")[:240]}
        selected = metadata.get("model")
        effort = metadata.get("reasoning_effort")
        selected_provider = metadata.get("provider")
        if not all(isinstance(value, str) and value for value in (selected, effort, selected_provider)):
            raise RuntimeError("Jev returned an incomplete routing plan")
        return {"schema_version": "jevgauge.routed_start.v1", "status": "routed",
                "model": selected, "provider": selected_provider, "reasoning_effort": effort,
                "reason": str(metadata.get("reason") or "selected")[:240]}
    except HTTPException:
        raise
    except Exception as exc:
        # Error types are sufficient for diagnosis. Never return credentials or provider bodies.
        return {"schema_version": "jevgauge.routed_start.v1", "status": "default",
                "reason": type(exc).__name__}
