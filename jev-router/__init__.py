"""Jev model router for native turn-route middleware and the legacy session hook."""

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
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from typing import Any

logger = logging.getLogger(__name__)

POLICY_VERSION = "jevgauge/1"
_WORKERS = threading.BoundedSemaphore(4)
_STORAGE_RECORD = None
_STORAGE_LOAD_LOCK = threading.Lock()


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
    return {"preserve_reasoning": bool(user_reasoning), "metadata": {
        "label": "JevGauge", "status": "unrouted/default", "reason": reason, "policy_version": POLICY_VERSION,
        "model": model, "provider": provider,
        "reasoning_effort": _effective_effort(reasoning_config),
        "owner": {"model": "user" if user_model else "default",
                  "reasoning": "user" if user_reasoning else "default"},
    }}


def _get_secret(ctx=None):
    getter = getattr(ctx, "get_secret", None)
    if callable(getter):
        return getter("TYPESAFE_API_KEY")
    # Legacy development hosts predate PluginContext.get_secret.
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


class _Response:
    def __init__(self, status_code: int, content: bytes):
        self.status_code = status_code
        self._content = content

    def raise_for_status(self):
        if not 200 <= self.status_code < 300:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self._content.decode("utf-8"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_url(request, timeout):
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


def _request(method, url, *, headers, body=None, limit=65536):
    """Bound read time and bytes as well as the outer conversation deadline."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise _Rejected("API endpoint must use HTTPS without embedded credentials")
    request_headers = dict(headers)
    request_headers.setdefault("Accept", "application/json")
    payload = None
    if body is not None:
        payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url, data=payload, headers=request_headers, method=method.upper())
    try:
        response = _open_url(request, _remaining())
    except urllib.error.HTTPError as exc:
        response = exc
    try:
        final = urlsplit(response.geturl())
        if final.scheme != "https" or not final.hostname or final.username or final.password:
            raise _Rejected("API endpoint redirected outside HTTPS")
        chunks, size = [], 0
        while True:
            _remaining()
            chunk = response.read(min(65536, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise _Rejected("API response exceeded size limit")
            chunks.append(chunk)
        return _Response(int(response.getcode()), b"".join(chunks))
    finally:
        response.close()


def _live_models() -> list[str]:
    """Only account-scoped slugs; Hermes' synthetic/static fallback is not entitlement evidence."""
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
    # Cache the installed stdlib module once: its per-path writer locks must be
    # shared across calls even when Hermes cannot import the dashboard package.
    global _STORAGE_RECORD
    if _STORAGE_RECORD is None:
        if not _STORAGE_LOAD_LOCK.acquire(timeout=0.25):
            return False
        try:
            if _STORAGE_RECORD is None:
                sibling = Path(__file__).with_name("telemetry.py")
                owned = Path(__file__).with_name(".jevgauge-install.json")
                if sibling.exists() or sibling.is_symlink() or owned.exists():
                    # Installed bytes are authoritative. Never silently select an
                    # older/editable package if this installation is damaged.
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("_jevgauge_storage", sibling)
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    record_event = module.record_event
                else:
                    # Source development layout keeps telemetry in src/jevgauge.
                    from jevgauge.telemetry import record_event
                _STORAGE_RECORD = record_event
        finally:
            _STORAGE_LOAD_LOCK.release()
    return _STORAGE_RECORD(_home(), event)


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
                 "manual_override": bool(kwargs.get("user_model") or kwargs.get("user_reasoning")
                                         or (not kwargs.get("model_only")
                                             and kwargs["ctx"].get_config("effort_mode", "auto") == "manual")),
                 "latency_ms": (time.monotonic() - started) * 1000}
        meta["telemetry_route_id"] = event["event_id"]
        _record(event)
    except Exception:
        pass
    return result


def _route(*, ctx, message: str, source: str, provider: str, model: str,
          reasoning_config: Any, user_model: bool, user_reasoning: bool,
          model_only: bool = False, host_validates: bool = False, **_kw) -> dict | None:
    if source != "desktop" or provider != "openai-codex" or not ctx.get_config("enabled", False):
        return None
    user_reasoning = user_reasoning or ctx.get_config("effort_mode", "auto") == "manual"
    kwargs = dict(ctx=ctx, message=message, provider=provider, model=model,
                  reasoning_config=reasoning_config, user_model=user_model,
                  user_reasoning=user_reasoning, model_only=model_only,
                  host_validates=host_validates)
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
        if model_only:
            logger.info("Jev proposed model=%s", metadata["model"])
        else:
            logger.info("Jev proposed model=%s effort=%s", metadata["model"], metadata["reasoning_effort"])
    return result


def _select(*, ctx, message, provider, model, reasoning_config, user_model, user_reasoning,
            model_only=False, host_validates=False):
    # A persistent manual mode leaves the profile's reasoning effort in charge for new chats.
    user_reasoning = user_reasoning or ctx.get_config("effort_mode", "auto") == "manual"
    api_key = _get_secret(ctx)
    if not api_key:
        return _default("missing Jev credential", model=model, provider=provider,
                        reasoning_config=reasoning_config,
                        user_model=user_model, user_reasoning=user_reasoning)
    eligible = {}
    try:
        tiers = ctx.get_config("tier_models", DEFAULT_TIERS)
        if not isinstance(tiers, dict):
            raise _Rejected("invalid tier mapping")
        live = None if model_only or host_validates else set(_live_models())
        eligible = {
            tier: [
                candidate.strip() for candidate in options
                if isinstance(candidate, str)
                and candidate.strip()
                and len(candidate.strip()) <= 128
                and (live is None or candidate.strip() in live)
            ][:16]
            for tier, options in tiers.items()
            if tier in DEFAULT_TIERS and isinstance(options, list)
        }
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
        chosen_effort = None if model_only else (
            _effective_effort(reasoning_config) if user_reasoning else EFFORTS[effort_tier])
        candidates = [model] if user_model else eligible[model_tier]
        chosen = (candidates[0] if candidates else None) if model_only or host_validates else next(
            (candidate for candidate in candidates
             if chosen_effort in _supported_efforts(provider, candidate)), None)
        if not chosen:
            raise _Rejected("no supported model and effort pair")
        metadata = {
            "label": "JevGauge", "status": "routed", "reason": f"{model_tier}/{'manual' if user_reasoning else effort_tier}",
            "policy_version": POLICY_VERSION, "jev_model": jev_model,
            "model": chosen, "provider": provider, "reasoning_effort": chosen_effort,
            "candidate_models": eligible, "model_tier": model_tier, "effort_tier": effort_tier, "owner": {
                "model": "user" if user_model else "router",
                "reasoning": "default" if model_only else ("user" if user_reasoning else "router")},
        }
        return {"model": None if user_model else chosen, "provider": provider,
                "reasoning_effort": None if user_reasoning else chosen_effort,
                "preserve_reasoning": bool(user_reasoning or model_only), "metadata": metadata}
    except Exception as exc:
        logger.warning("Jev routing fell back: %s", type(exc).__name__)
        result = _default(str(exc) if isinstance(exc, _Rejected) else type(exc).__name__,
                          model=model, provider=provider, reasoning_config=reasoning_config,
                          user_model=user_model, user_reasoning=user_reasoning)
        result["metadata"]["candidate_models"] = eligible
        return result


def turn_route(*, ctx, route: dict, user_message: str, source: str,
               is_first_turn: bool, internal: bool = False,
               tool_continuation: bool = False, **kwargs) -> dict | None:
    """Adapt Jev to Hermes' credential-free, pre-agent ``turn_route`` contract."""
    if (source != "desktop" or internal or tool_continuation or not is_first_turn
            or not ctx.get_config("enabled", False)
            or not isinstance(route, dict)):
        return None
    model = route.get("model")
    provider = route.get("requested_provider") or route.get("provider")
    if not isinstance(model, str) or not model.strip() or provider != "openai-codex":
        return None
    current_effort = route.get("current_reasoning_effort")
    reasoning_config = (
        {"enabled": False}
        if current_effort == "none"
        else ({"enabled": True, "effort": current_effort}
              if current_effort in {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
              else None)
    )
    result = globals()["route"](
        ctx=ctx,
        message=user_message if isinstance(user_message, str) else "",
        source=source,
        provider=provider,
        model=model,
        reasoning_config=reasoning_config,
        user_model=False,
        user_reasoning=False,
        host_validates=True,
        session_key=kwargs.get("session_key"),
    )
    metadata = (result or {}).get("metadata", {})
    selected = metadata.get("model")
    if metadata.get("status") != "routed" or not isinstance(selected, str) or not selected:
        raw_reason = str(metadata.get("reason") or "default")[:64]
        reason = "_".join(raw_reason.split())
        reason = "".join(char for char in reason if char.isalnum() or char in "_.:/-") or "default"
        return {"route": dict(route), "source": "jevgauge", "status": "default", "reason": reason}
    public_route = dict(route)
    public_route["model"] = selected
    chosen_effort = result.get("reasoning_effort")
    if isinstance(chosen_effort, str) and chosen_effort:
        public_route["reasoning_effort"] = chosen_effort
    if result.get("preserve_reasoning") is True:
        public_route["preserve_reasoning"] = True
    public_route["requested_provider"] = provider
    if isinstance(route.get("runtime"), dict):
        public_route["runtime"] = {**route["runtime"], "requested_provider": provider}
    return {
        "route": public_route,
        "source": "jevgauge",
        "status": "routed",
        "reason": str(metadata.get("model_tier") or "selected")[:64],
    }


def register(ctx) -> None:
    register_middleware = getattr(ctx, "register_middleware", None)
    try:
        from hermes_cli.middleware import TURN_ROUTE_API_VERSION, VALID_MIDDLEWARE
        native_supported = (
            type(TURN_ROUTE_API_VERSION) is int
            and TURN_ROUTE_API_VERSION == 1
            and isinstance(VALID_MIDDLEWARE, (set, frozenset, list, tuple))
            and "turn_route" in VALID_MIDDLEWARE
        )
    except (ImportError, AttributeError, TypeError):
        native_supported = False
    if callable(register_middleware) and native_supported:
        register_middleware("turn_route", lambda **kwargs: turn_route(ctx=ctx, **kwargs))
    # Keep legacy integrations available without registering unknown hooks on a
    # native-only host.
    try:
        from hermes_cli.plugins import VALID_HOOKS
        valid_hooks = set(VALID_HOOKS)
    except (ImportError, AttributeError, TypeError):
        valid_hooks = {"select_session_runtime", "provider_attempt"}
    if "select_session_runtime" in valid_hooks:
        ctx.register_hook("select_session_runtime", lambda **kwargs: route(ctx=ctx, **kwargs))
    if "provider_attempt" in valid_hooks:
        ctx.register_hook("provider_attempt", provider_attempt)
    ctx.register_command("jev-dashboard", dashboard, description="Open the local JevGauge dashboard")
    ctx.register_command("jev-status", lambda args="": status(args, ctx=ctx),
                         description="Show Jev host capability and diagnostic scope")


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


def _contract_status(host, marker: str, hook: str) -> dict:
    version = getattr(host, marker, None)
    hooks = getattr(host, "VALID_HOOKS", ())
    supported = type(version) is int and version == 1
    declared = isinstance(hooks, (set, frozenset, list, tuple)) and hook in hooks
    if supported and declared:
        return {"status": "available", "reason": "host contract declared", "api_version": version}
    reason = "host integration missing" if version is None or supported else "unsupported host integration version"
    return {"status": "unavailable", "reason": reason,
            "api_version": version if type(version) is int else None}


def _turn_route_status(host) -> dict:
    version = getattr(host, "TURN_ROUTE_API_VERSION", None)
    kinds = getattr(host, "VALID_MIDDLEWARE", ())
    declared = isinstance(kinds, (set, frozenset, list, tuple)) and "turn_route" in kinds
    supported = type(version) is int and version == 1
    if declared and supported:
        return {"status": "available", "reason": "host contract declared",
                "api_version": version, "contract": "turn_route", "scope": "addressed_profile"}
    reason = "unsupported host integration version" if version is not None and not supported else "host integration missing"
    return {"status": "unavailable", "reason": reason,
            "api_version": version if type(version) is int else None,
            "contract": None, "scope": "unknown"}


def runtime_health(ctx=None) -> dict:
    """Inspect this process, never confuse a registered callback with host support.

    Capability declarations do not prove that a selector or provider was called.
    The command API does not attest the owning profile. Do not read ambient
    settings or storage through it, even when a session key is bound.
    """
    import importlib

    unknown = {"status": "unknown", "reason": "host inspection failed", "api_version": None}
    routing, telemetry = dict(unknown), dict(unknown)
    native = None
    try:
        native = _turn_route_status(importlib.import_module("hermes_cli.middleware"))
    except Exception:
        pass
    try:
        host = importlib.import_module("hermes_cli.plugins")
        legacy = _contract_status(host, "SESSION_RUNTIME_SELECTION_API", "select_session_runtime")
        if legacy["status"] == "available":
            legacy.update(contract="select_session_runtime", scope="launch_profile_only")
        if native and native["status"] == "available":
            routing = native
        elif legacy["status"] == "available":
            routing = legacy
        elif native is not None:
            routing = native
        else:
            routing = legacy
        telemetry = _contract_status(host, "PROVIDER_ATTEMPT_API", "provider_attempt")
    except Exception:
        if native is not None:
            routing = native
    return {"routing": routing, "telemetry": telemetry, "enabled": None,
            "config_status": "scope_unverified", "verification": "capability_only"}


def _status_text(value) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 or ord(c) == 127 for c in value):
        return value
    return None


_SAVED_REASONS = frozenset({
    "missing Jev credential", "invalid selection timeout", "routing capacity busy",
    "first-call candidate rejected", "plugin hook failure", "agent already built",
    "No valid routing decision",
    "routing worker unavailable", "routing deadline exceeded", "invalid tier mapping",
    "no eligible account models", "invalid typed choices", "low Jev confidence",
    "no supported model and effort pair", "API response exceeded size limit",
    "API endpoint must use HTTPS without embedded credentials",
} | {f"{tier}/{effort}" for tier in DEFAULT_TIERS for effort in (*EFFORTS, "manual")})


def _known_text(value, allowed, fallback=None):
    return value if isinstance(value, str) and value in allowed else fallback


def _saved_status() -> dict:
    """Internal parser. Caller must establish owning-profile scope before reading.

    Model/provider are persisted display labels, not sanitized secret-free text.
    This helper is deliberately not exposed by the unscoped command API.
    """
    try:
        row = _session_record()
    except Exception:
        return {"state": "unavailable", "reason": "saved session could not be read"}
    if row is None:
        return {"state": "unrecorded", "reason": "no saved session for this conversation"}
    try:
        if not isinstance(row, dict):
            raise ValueError
        raw = row.get("model_config")
        if isinstance(raw, str):
            if len(raw) > 65536:
                raise ValueError
            raw = json.loads(raw)
        config = {} if raw is None else raw
        if not isinstance(config, dict):
            raise ValueError
        route = config.get("session_route", {})
        reasoning = config.get("reasoning_config", {})
        route = {} if route is None else route
        reasoning = {} if reasoning is None else reasoning
        if not isinstance(route, dict) or not isinstance(reasoning, dict):
            raise ValueError
        owners = route.get("owner", {})
        if not isinstance(owners, dict):
            raise ValueError
        return {"state": "saved", "source": "saved_session", "route": {
            "status": _known_text(route.get("status"), ("routed", "unrouted/default", "fallback", "selecting"), "unknown"),
            "reason": _known_text(route.get("reason"), _SAVED_REASONS,
                                  "no route recorded" if route.get("reason") is None else "saved reason not recognized"),
            "provider": _status_text(config.get("provider")) or _status_text(row.get("billing_provider")),
            "model": _status_text(row.get("model")),
            "reasoning_effort": _known_text(_effective_effort(reasoning), ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")),
            "owner": {key: owners.get(key) if owners.get(key) in ("router", "user", "default") else "default"
                      for key in ("model", "reasoning")}}}
    except (ValueError, TypeError, RecursionError):
        return {"state": "invalid", "reason": "saved routing data is malformed"}


def status_snapshot(ctx=None) -> dict:
    # A session key alone does not prove which profile owns its database row.
    # Do not add a user-settable scope override to this public command.
    return {"schema_version": 1, "inspection_scope": "invoking_process",
            "runtime": runtime_health(ctx),
            "conversation": {"state": "scope_unverified",
                             "reason": "owning profile not verified; saved binding withheld"}}


def status(_args: str = "", *, ctx=None) -> str:
    snapshot = status_snapshot(ctx)
    if _args.strip() == "--json":
        return json.dumps(snapshot, allow_nan=False)
    runtime, conversation = snapshot["runtime"], snapshot["conversation"]
    routing = runtime["routing"]
    if routing["status"] == "available":
        scope = ("addressed profile" if routing.get("scope") == "addressed_profile"
                 else "launch profile only")
        lines = [f"Routing host contract available ({scope}; execution unverified)."]
    elif routing["status"] == "unavailable":
        lines = [f"Routing unavailable: {routing['reason']}."]
    else:
        lines = [f"Routing status unknown: {routing['reason']}."]
    if runtime["enabled"] is False:
        lines.append("Routing disabled in plugin settings.")
    elif runtime["enabled"] is None:
        lines.append("Plugin enablement could not be verified.")
    lines.append(f"Provider telemetry contract: {runtime['telemetry']['status']}.")
    if conversation["state"] != "saved":
        lines.append(f"Saved conversation: {conversation['reason']}.")
        return "\n".join(lines)
    route = conversation["route"]
    lines.extend([
        f"Saved Jev route: {route['status']}",
        f"Provider: {route['provider'] or 'profile default'}",
        f"Model: {route['model'] or 'profile default'} ({route['owner']['model']})",
        f"Effort: {route['reasoning_effort'] or 'profile default'} ({route['owner']['reasoning']})",
        f"Reason: {route['reason']}",
    ])
    return "\n".join(lines)
