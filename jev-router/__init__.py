"""Jev model and effort router for the generic select_session_runtime hook."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from datetime import datetime, timezone
import uuid
import json
import math
import contextvars
import queue
import threading
import time
from urllib.parse import urlsplit
from typing import Any

logger = logging.getLogger(__name__)

POLICY_VERSION = "jevgauge/1"
_WORKERS = threading.BoundedSemaphore(4)


class _Rejected(ValueError):
    """A fixed local policy reason safe to persist."""


_DEADLINE = contextvars.ContextVar("jevgauge_deadline", default=None)
DEFAULT_TIERS = {
    "economical": ["gpt-6-luna", "gpt-5.6-luna"],
    "balanced": ["gpt-6-sol", "gpt-5.6-sol"],
    "strongest": ["gpt-6-astra", "gpt-6-sol", "gpt-5.6-sol"],
}
EFFORTS = {"low": "low", "medium": "medium", "high": "high"}


def _effective_effort(reasoning_config: Any) -> str | None:
    """Hermes represents explicit reasoning off without an effort key."""
    if not isinstance(reasoning_config, dict):
        return None
    return "none" if reasoning_config.get("enabled") is False else reasoning_config.get("effort")


def _default(reason: str, *, model: str, provider: str, reasoning_config: Any,
             user_model: bool, user_reasoning: bool) -> dict:
    return {"metadata": {
        "label": "JevGauge", "status": "unrouted/default", "reason": reason, "policy_version": POLICY_VERSION,
        "model": model, "provider": provider,
        "reasoning_effort": _effective_effort(reasoning_config),
        "owner": {"model": "user" if user_model else "default",
                  "reasoning": "user" if user_reasoning else "default"},
    }}


def _get_secret():
    from agent.secret_scope import get_secret
    return get_secret("TYPESAFE_API_KEY")


def _supported_efforts(provider, model):
    from agent.reasoning_effort import route_supported_efforts
    return route_supported_efforts(provider, model)


def _remaining():
    deadline = _DEADLINE.get()
    remaining = deadline - time.monotonic() if deadline else 2.0
    if remaining <= 0:
        raise TimeoutError("routing deadline exceeded")
    return min(2.0, remaining)


def _request(method, url, *, headers, body=None, limit=65536):
    """Bound read time and bytes as well as the outer conversation deadline."""
    import httpx
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise _Rejected("API endpoint must use HTTPS without embedded credentials")
    with httpx.stream(method, url, headers=headers, json=body, timeout=_remaining()) as response:
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            _remaining()
            size += len(chunk)
            if size > limit:
                raise _Rejected("API response exceeded size limit")
            chunks.append(chunk)
        return httpx.Response(response.status_code, content=b"".join(chunks), request=response.request)


def _live_models() -> list[str]:
    """Only account-scoped slugs; Hermes' synthetic/static fallback is not entitlement evidence."""
    import httpx
    from hermes_cli.auth import resolve_codex_runtime_credentials
    from hermes_cli.auth_codex import _codex_base_url
    from agent.codex_headers import codex_account_headers
    from agent.model_metadata import fetch_codex_catalog_entries
    from hermes_cli.codex_models import _ranked_slugs

    creds = resolve_codex_runtime_credentials(read_only=True)
    token = str(creds.get("api_key") or "")
    if not token:
        return []
    base_url = str(creds.get("base_url") or "").rstrip("/") or _codex_base_url()
    headers = {"Authorization": f"Bearer {token}", **codex_account_headers(token)}
    entries, status = fetch_codex_catalog_entries(
        lambda url: _request("GET", url, headers=headers, limit=2_000_000), base_url=base_url)
    return _ranked_slugs(entries) if status == 200 else []


def _decide(state: str, api_key: str, api_url: str, jev_model: str) -> dict:
    response = _request("POST", api_url, headers={"Authorization": f"Bearer {api_key}"}, body={
        "state": state[:1200], "model": jev_model,
        "questions": {
            "model_tier": {"type": "choice", "instructions": "Choose task model capacity. Prefer economical only for clearly bounded low-risk transformations; balanced for ordinary, vague, or mixed tasks; strongest when the task is demanding or a weak answer is costly.",
                           "criteria": {"economical": "Clearly bounded low-risk transformation", "balanced": "Ordinary, vague or mixed task", "strongest": "Demanding or high cost of a weak answer"}},
            "effort_tier": {"type": "choice", "instructions": "Choose reasoning depth independently of model capacity. Choose high only when multi-step reasoning materially matters.",
                            "criteria": {"low": "Simple direct reasoning", "medium": "Ordinary analysis or planning", "high": "Demanding multi-step reasoning"}},
        },
    })
    response.raise_for_status()
    return response.json()


def _home():
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home().expanduser().absolute()
    except ImportError:
        return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().absolute()


def _record(event):
    # Installer copies this exact stdlib-only storage module next to the plugin.
    # Hermes need not share the Python environment that installed the dashboard.
    try:
        from jevgauge.telemetry import record_event
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_jevgauge_storage", Path(__file__).with_name("telemetry.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        record_event = module.record_event
    return record_event(_home(), event)


def provider_attempt(*, event, **_kw):
    """Observer only: storage failures never affect provider execution."""
    try:
        _record(event)
    except Exception:
        pass


def route(**kwargs):
    started = time.monotonic()
    result = _route(**kwargs)
    # Capture only supported Desktop conversations; no fabricated historical import.
    if kwargs.get("source") != "desktop" or kwargs.get("provider") != "openai-codex" or not kwargs.get("session_key"):
        return result
    try:
        meta = (result or {}).get("metadata", {})
        defaults = {"default_model": kwargs.get("model"),
                    "default_effort": _effective_effort(kwargs.get("reasoning_config"))}
        meta.update(defaults)
        candidates = meta.get("candidate_models", {})
        capabilities = {}
        for rank, tier in enumerate(("economical", "balanced", "strongest")):
            for model in candidates.get(tier, []):
                capabilities[model] = max(rank, capabilities.get(model, 0))
        event = {"schema_version": 1, "event_id": str(uuid.uuid4()), "kind": "route",
                 "conversation_id": kwargs["session_key"],
                 "timestamp": datetime.now(timezone.utc).isoformat(),
                 **defaults, "provider": kwargs["provider"], "policy_version": POLICY_VERSION,
                 "selected_model": meta.get("model", kwargs.get("model")),
                 "requested_effort": meta.get("reasoning_effort", defaults["default_effort"]),
                 "model_tier": meta.get("model_tier"), "effort_tier": meta.get("effort_tier"),
                 "reason_code": meta.get("reason", "routing disabled"),
                 "outcome": "routed" if meta.get("status") == "routed" else "abstained" if result else "disabled",
                 "eligible_models": [{"model": model, "capability": rank} for model, rank in sorted(capabilities.items())],
                 "eligibility_source": "account catalog intersected with configured capacity tiers" if capabilities else None,
                 "manual_override": bool(kwargs.get("user_model") or kwargs.get("user_reasoning") or kwargs["ctx"].get_config("effort_mode", "auto") == "manual"),
                 "latency_ms": (time.monotonic() - started) * 1000}
        meta["telemetry_route_id"] = event["event_id"]
        _record(event)
    except Exception:
        pass
    return result


def _route(*, ctx, message: str, source: str, provider: str, model: str,
          reasoning_config: Any, user_model: bool, user_reasoning: bool, **_kw) -> dict | None:
    if source != "desktop" or provider != "openai-codex" or not ctx.get_config("enabled", False):
        return None
    user_reasoning = user_reasoning or ctx.get_config("effort_mode", "auto") == "manual"
    kwargs = dict(ctx=ctx, message=message, provider=provider, model=model,
                  reasoning_config=reasoning_config, user_model=user_model, user_reasoning=user_reasoning)
    def default(reason):
        return _default(reason, model=model, provider=provider, reasoning_config=reasoning_config,
                        user_model=user_model, user_reasoning=user_reasoning)
    report = _kw.get("report_status")
    if callable(report):
        try:
            report({"status": "selecting", "label": "JevGauge"})
        except Exception:
            pass  # An optional display observer cannot stop chat.
    budget = ctx.get_config("selection_timeout", 5.0)
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not 0.01 <= budget <= 10:
        return default("invalid selection timeout")
    slots = _WORKERS
    if not slots.acquire(blocking=False):
        return default("routing capacity busy")
    results = queue.Queue(maxsize=1)
    context = contextvars.copy_context()
    def select():
        token = _DEADLINE.set(time.monotonic() + budget)
        try:
            results.put(_select(**kwargs))
        except Exception as exc:
            results.put(default(type(exc).__name__))
        finally:
            _DEADLINE.reset(token)
            slots.release()
    try:
        threading.Thread(target=context.run, args=(select,), daemon=True, name="jevgauge-select").start()
    except Exception:
        slots.release()
        return default("routing worker unavailable")
    try:
        result = results.get(timeout=budget)
    except queue.Empty:
        return default("routing deadline exceeded")
    metadata = result["metadata"]
    if metadata["status"] == "routed":
        logger.info("Jev proposed model=%s effort=%s", metadata["model"], metadata["reasoning_effort"])
    return result


def _select(*, ctx, message, provider, model, reasoning_config, user_model, user_reasoning):
    # A persistent manual mode leaves the profile's reasoning effort in charge for new chats.
    user_reasoning = user_reasoning or ctx.get_config("effort_mode", "auto") == "manual"
    api_key = _get_secret()
    if not api_key:
        return _default("missing Jev credential", model=model, provider=provider,
                        reasoning_config=reasoning_config,
                        user_model=user_model, user_reasoning=user_reasoning)
    eligible = {}
    try:
        live = set(_live_models())
        tiers = ctx.get_config("tier_models", DEFAULT_TIERS)
        if not isinstance(tiers, dict):
            raise _Rejected("invalid tier mapping")
        eligible = {tier: [candidate for candidate in options if candidate in live]
                    for tier, options in tiers.items() if tier in DEFAULT_TIERS and isinstance(options, list)}
        if not any(eligible.values()):
            raise _Rejected("no eligible account models")
        jev_model = str(ctx.get_config("jev_model", "jev-latest"))
        _remaining()
        answer = _decide(message, api_key, str(ctx.get_config("api_url", "https://api.typesafe.ai/v1/systemone")),
                         jev_model)
        answers = answer["answers"]
        model_answer, effort_answer = answers["model_tier"], answers["effort_tier"]
        model_tier, effort_tier = model_answer["choice"], effort_answer["choice"]
        if model_tier not in eligible or effort_tier not in EFFORTS:
            raise _Rejected("invalid typed choices")
        confidences = (model_answer["confidence"], effort_answer["confidence"])
        if not all(type(value) in (int, float) and math.isfinite(value) and 0.55 <= value <= 1 for value in confidences):
            raise _Rejected("low Jev confidence")
        chosen_effort = (_effective_effort(reasoning_config) if user_reasoning else EFFORTS[effort_tier])
        candidates = [model] if user_model else eligible[model_tier]
        chosen = next((candidate for candidate in candidates
                       if chosen_effort in _supported_efforts(provider, candidate)), None)
        if not chosen:
            raise _Rejected("no supported model and effort pair")
        metadata = {
            "label": "JevGauge", "status": "routed", "reason": f"{model_tier}/{'manual' if user_reasoning else effort_tier}",
            "policy_version": POLICY_VERSION, "jev_model": jev_model,
            "model": chosen, "provider": provider, "reasoning_effort": chosen_effort,
            "candidate_models": eligible, "model_tier": model_tier, "effort_tier": effort_tier, "owner": {
                "model": "user" if user_model else "router",
                "reasoning": "user" if user_reasoning else "router"},
        }
        return {"model": None if user_model else chosen, "provider": provider,
                "reasoning_effort": None if user_reasoning else chosen_effort, "metadata": metadata}
    except Exception as exc:
        logger.warning("Jev routing fell back: %s", type(exc).__name__)
        result = _default(str(exc) if isinstance(exc, _Rejected) else type(exc).__name__,
                          model=model, provider=provider, reasoning_config=reasoning_config,
                          user_model=user_model, user_reasoning=user_reasoning)
        result["metadata"]["candidate_models"] = eligible
        return result


def register(ctx) -> None:
    ctx.register_hook("select_session_runtime", lambda **kwargs: route(ctx=ctx, **kwargs))
    ctx.register_hook("provider_attempt", provider_attempt)
    ctx.register_command("jev-dashboard", dashboard, description="Open the local JevGauge dashboard")
    ctx.register_command("jev-status", status, description="Show this chat's Jev routing binding")


def dashboard(_args: str = "") -> str:
    """Launch the installed dashboard through Hermes' supported command registry."""
    import subprocess
    import hashlib
    import urllib.request
    import webbrowser
    home = _home()
    url = "http://127.0.0.1:8765/"
    def open_running():
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False
        return "Opened JevGauge at " + url if opened else "JevGauge is running at " + url + " (browser opening unavailable)."

    try:
        # The health identity prevents opening an unrelated process on this port.
        def running():
            with urllib.request.urlopen(url + "api/health", timeout=0.3) as response:
                identity = json.load(response)
            return (identity.get("service") == "jevgauge" and identity.get("mode") == "live"
                    and identity.get("home_id") == hashlib.sha256(str(home).encode()).hexdigest())
        try:
            if running():
                return open_running()
        except Exception:
            pass
        runtime = json.loads(Path(__file__).with_name("runtime.json").read_text())
        python = Path(runtime["python"])
        if not python.is_absolute() or not python.is_file():
            raise ValueError("Missing dashboard interpreter")
        process = subprocess.Popen([str(python), "-m", "jevgauge", "dashboard", "--home", str(home)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(40):
            if process.poll() is not None:
                break
            try:
                if running():
                    return open_running()
            except Exception:
                pass
            time.sleep(0.1)
        if process.poll() is None:
            process.terminate()
    except Exception:
        pass
    return "JevGauge dashboard could not start. Run the installed jevgauge dashboard command and check the loopback port and installation."



def _session_record():
    """Read only the current conversation's durable routing record."""
    from gateway.session_context import get_session_env
    from hermes_state import SessionDB

    key = get_session_env("HERMES_SESSION_KEY")
    if not key:
        return None
    db = SessionDB(read_only=True)
    try:
        row = db.get_session(key)
    finally:
        db.close()
    return row


def status(_args: str = "") -> str:
    row = _session_record()
    if not row:
        return "No saved routing decision for this conversation."
    raw = row.get("model_config") or {}
    config = json.loads(raw) if isinstance(raw, str) else raw
    config = config if isinstance(config, dict) else {}
    route = config.get("session_route") or {}
    reasoning = config.get("reasoning_config") or {}
    return (f"Jev route: {route.get('status', 'unrouted/default')}\n"
            f"Provider: {config.get('provider') or row.get('billing_provider') or 'profile default'}\n"
            f"Model: {row.get('model') or 'profile default'} "
            f"({(route.get('owner') or {}).get('model', 'default')})\n"
            f"Effort: {_effective_effort(reasoning) or 'profile default'} "
            f"({(route.get('owner') or {}).get('reasoning', 'default')})\n"
            f"Reason: {route.get('reason') or 'no route recorded'}")
