"""Host-owned route fields must not be writable by a selector plugin."""

from tui_gateway import server


def _host(monkeypatch):
    monkeypatch.setattr(server, "_resolve_model", lambda: "gpt-6-sol")
    monkeypatch.setattr(server, "_config_model_target", lambda: ("gpt-6-sol", "openai-codex"))
    monkeypatch.setattr(server, "_load_reasoning_config", lambda model: {"enabled": True, "effort": "high"})
    monkeypatch.setattr("hermes_cli.plugins.has_hook", lambda name: name == "select_session_runtime")


def test_selector_cannot_forge_effective_binding_or_ownership(monkeypatch):
    _host(monkeypatch)
    session = {"source": "desktop", "session_key": "authority", "history": [], "agent": None}
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kwargs: [{
        "model": "gpt-6-luna", "provider": "openai-codex", "reasoning_effort": "low",
        "metadata": {
            "status": "unrouted/default", "owner": {"model": "user", "reasoning": "user"},
            "model": "forged", "provider": "forged", "reasoning_effort": "ultra",
            "original_runtime": {"model": "forged", "provider": "forged"}, "label": "Policy",
        },
    }])

    server._apply_first_prompt_route(session, "Summarize this", "ui")

    route = session["session_route"]
    assert route["status"] == "routed"
    assert route["owner"] == {"model": "router", "reasoning": "router"}
    assert (route["model"], route["provider"], route["reasoning_effort"]) == (
        "gpt-6-luna", "openai-codex", "low")
    assert route["original_runtime"]["model"] == "gpt-6-sol"
    assert session["route_fallback"]["model"] == "gpt-6-sol"


def test_late_progress_cannot_replace_final_host_binding(monkeypatch):
    _host(monkeypatch)
    session = {"source": "desktop", "session_key": "late", "history": [], "agent": None}
    events = []
    callbacks = []
    monkeypatch.setattr(server, "_emit", lambda kind, sid, payload: events.append(payload))

    def decide(name, **kwargs):
        progress = kwargs["report_status"]
        callbacks.append(progress)
        progress({"status": "routed", "owner": {"model": "user"}, "model": "forged", "label": "Policy"})
        return [{"model": "gpt-6-luna", "provider": "openai-codex", "reasoning_effort": "low",
                 "metadata": {"status": "routed", "label": "Policy"}}]

    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", decide)
    server._apply_first_prompt_route(session, "Summarize this", "ui")
    assert events[0]["session_route"]["status"] == "selecting"
    assert events[0]["session_route"]["owner"] == {"model": "default", "reasoning": "default"}
    assert events[0]["session_route"]["model"] == "gpt-6-sol"
    final = dict(session["session_route"])
    callbacks[0]({"status": "routed", "model": "forged", "owner": {"model": "user"}})
    assert session["session_route"] == final
    assert len(events) == 2


def test_metadata_only_result_cannot_claim_a_route(monkeypatch):
    _host(monkeypatch)
    session = {"source": "desktop", "session_key": "abstain", "history": [], "agent": None}
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kwargs: [{
        "metadata": {"status": "routed", "owner": {"model": "router"}, "label": "Policy"},
    }])

    server._apply_first_prompt_route(session, "Summarize this", "ui")

    route = session["session_route"]
    assert route["status"] == "unrouted/default"
    assert route["owner"] == {"model": "default", "reasoning": "default"}
    assert not session.get("model_override")
    assert not session.get("route_fallback")


def test_explicit_preserve_directive_keeps_manual_effort_host_owned(monkeypatch):
    _host(monkeypatch)
    session = {"source": "desktop", "session_key": "manual-policy", "history": [], "agent": None}
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kwargs: [{
        "model": "gpt-6-luna", "provider": "openai-codex", "preserve_reasoning": True,
        "metadata": {"status": "routed", "owner": {"reasoning": "router"}},
    }])

    server._apply_first_prompt_route(session, "Summarize this", "ui")

    assert session["session_route"]["owner"] == {"model": "router", "reasoning": "user"}
    assert session["session_route"]["reasoning_effort"] == "high"
    assert session["model_override"]["model"] == "gpt-6-luna"
    assert session.get("create_reasoning_override") is None


def test_invalid_preserve_directive_cannot_change_host_ownership(monkeypatch):
    _host(monkeypatch)
    session = {"source": "desktop", "session_key": "bad-policy", "history": [], "agent": None}
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kwargs: [{
        "model": "gpt-6-luna", "provider": "openai-codex", "preserve_reasoning": "yes",
        "metadata": {"status": "routed", "owner": {"reasoning": "user"}},
    }])

    server._apply_first_prompt_route(session, "Summarize this", "ui")

    assert session["session_route"]["status"] == "unrouted/default"
    assert session["session_route"]["owner"] == {"model": "default", "reasoning": "default"}
    assert not session.get("model_override")


def test_preserve_directive_conflicting_with_effort_is_rejected(monkeypatch):
    _host(monkeypatch)
    session = {"source": "desktop", "session_key": "conflicting-policy", "history": [], "agent": None}
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda name, **kwargs: [{
        "model": "gpt-6-luna", "provider": "openai-codex", "reasoning_effort": "low",
        "preserve_reasoning": True, "metadata": {"label": "Policy"},
    }])

    server._apply_first_prompt_route(session, "Summarize this", "ui")

    assert session["session_route"]["status"] == "unrouted/default"
    assert session["session_route"]["owner"] == {"model": "default", "reasoning": "default"}
    assert not session.get("model_override")
    assert not session.get("create_reasoning_override")
