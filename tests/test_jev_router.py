import importlib.util
from pathlib import Path

import pytest
import yaml


PLUGIN = Path(__file__).parents[1] / "jev-router" / "__init__.py"
spec = importlib.util.spec_from_file_location("jev_router_poc", PLUGIN)
router = importlib.util.module_from_spec(spec)
spec.loader.exec_module(router)
REAL_DECIDE = router._decide


def test_manifest_declares_persistent_effort_mode():
    manifest = yaml.safe_load((PLUGIN.parent / "plugin.yaml").read_text())
    assert manifest["config_schema"]["effort_mode"]["type"] == "str"
    assert manifest["config_schema"]["effort_mode"]["default"] == "auto"


class Config:
    def __init__(self, **values):
        self.values = {"enabled": True, **values}

    def get_config(self, key, default=None):
        return self.values.get(key, default)


def call(**overrides):
    args = dict(ctx=Config(), message="Solve the complex task", source="desktop",
                provider="openai-codex", model="gpt-6-sol",
                reasoning_config={"enabled": True, "effort": "medium"},
                user_model=False, user_reasoning=False)
    args.update(overrides)
    return router.route(**args)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(router, "_get_secret", lambda: "test-key")
    monkeypatch.setattr(router, "_supported_efforts", lambda provider, model: ("none", "low", "medium", "high"))
    monkeypatch.setattr(router, "_live_models", lambda: ["gpt-6-luna", "gpt-6-sol", "gpt-6-astra"])
    monkeypatch.setattr(router, "_decide", lambda *a: {
        "model": "jev-1.test", "answers": {
            "model_tier": {"choice": "economical", "confidence": 0.9},
            "effort_tier": {"choice": "high", "confidence": 0.9},
        },
    })


def test_model_and_effort_are_independent():
    result = call()
    assert result["model"] == "gpt-6-luna"
    assert result["reasoning_effort"] == "high"
    assert result["metadata"]["owner"] == {"model": "router", "reasoning": "router"}


def test_manual_model_and_reasoning_are_preserved():
    result = call(user_model=True, user_reasoning=True,
                  model="gpt-6-sol", reasoning_config={"effort": "medium"})
    assert result["model"] is None
    assert result["reasoning_effort"] is None
    assert result["metadata"]["owner"] == {"model": "user", "reasoning": "user"}


def test_persistent_manual_effort_mode_keeps_profile_effort():
    result = call(ctx=Config(effort_mode="manual"))
    assert result["model"] == "gpt-6-luna"
    assert result["reasoning_effort"] is None
    assert result["metadata"]["reasoning_effort"] == "medium"
    assert result["metadata"]["owner"] == {"model": "router", "reasoning": "user"}


@pytest.mark.parametrize("failure", ["bad tier", "low confidence", "no catalog"])
def test_failures_preserve_defaults(monkeypatch, failure):
    if failure == "no catalog":
        monkeypatch.setattr(router, "_live_models", lambda: [])
    else:
        monkeypatch.setattr(router, "_decide", lambda *a: {"answers": {
            "model_tier": {"choice": "invalid" if failure == "bad tier" else "balanced",
                           "confidence": 0.2 if failure == "low confidence" else 0.9},
            "effort_tier": {"choice": "high", "confidence": 0.9},
        }})
    result = call()
    assert "model" not in result
    assert result["metadata"]["status"] == "unrouted/default"


def test_status_reports_saved_binding(monkeypatch):

    class DB:
        def __init__(self, read_only):
            assert read_only

        def get_session(self, key):
            assert key == "route-1"
            return {"model": "gpt-6-luna", "billing_provider": "openai-codex",
                    "model_config": {"provider": "openai-codex", "reasoning_config": {"effort": "low"},
                                     "session_route": {"status": "routed", "reason": "economical/low",
                                                       "owner": {"model": "router", "reasoning": "router"}}}}

        def close(self):
            pass

    monkeypatch.setattr(router, "_session_record", lambda: DB(True).get_session("route-1"))
    result = router.status()
    assert "gpt-6-luna (router)" in result
    assert "low (router)" in result


def test_timeout_and_other_provider_leave_defaults(monkeypatch):
    def timeout(*_args):
        raise TimeoutError()

    monkeypatch.setattr(router, "_decide", timeout)
    result = call()
    assert result["metadata"]["status"] == "unrouted/default"
    assert "model" not in result
    assert call(provider="openai") is None


def test_nonfinite_confidence_keeps_defaults(monkeypatch):
    monkeypatch.setattr(router, "_decide", lambda *a: {"answers": {
        "model_tier": {"choice": "balanced", "confidence": float("nan")},
        "effort_tier": {"choice": "medium", "confidence": 0.9},
    }})
    result = call()
    assert result["metadata"]["status"] == "unrouted/default"


def test_jev_failure_retains_manual_field_ownership(monkeypatch):
    monkeypatch.setattr(router, "_live_models", lambda: [])
    result = call(user_model=True, user_reasoning=True,
                  model="gpt-6-sol", reasoning_config={"effort": "high"})
    assert result["metadata"]["owner"] == {"model": "user", "reasoning": "user"}
    assert result["metadata"]["model"] == "gpt-6-sol"
    assert result["metadata"]["reasoning_effort"] == "high"


def test_progress_precedes_catalog_and_disabled_plugin_emits_nothing(monkeypatch):
    events = []
    def catalog():
        assert events == [{"status": "selecting", "label": "JevGauge"}]
        return ["gpt-6-luna"]
    monkeypatch.setattr(router, "_live_models", catalog)
    result = call(report_status=events.append)
    assert result["metadata"]["label"] == "JevGauge"
    events.clear()
    assert call(ctx=Config(enabled=False), report_status=events.append) is None
    assert events == []


def test_tries_later_compatible_candidate(monkeypatch):
    monkeypatch.setattr(router, "_supported_efforts", lambda p, m: ("low",) if m == "gpt-6-luna" else ("high",))
    result = call(ctx=Config(tier_models={"economical": ["gpt-6-luna", "gpt-6-sol"]}))
    assert result["model"] == "gpt-6-sol"


@pytest.mark.parametrize("confidence", [1.01, 2, True, "0.9", None, -1])
def test_invalid_confidence_fails_open(monkeypatch, confidence):
    monkeypatch.setattr(router, "_decide", lambda *a: {"answers": {
        "model_tier": {"choice": "economical", "confidence": confidence},
        "effort_tier": {"choice": "low", "confidence": 0.9}}})
    assert call()["metadata"]["status"] == "unrouted/default"


def test_overall_deadline_and_no_late_decision(monkeypatch):
    import threading
    import time
    release = threading.Event()
    monkeypatch.setattr(router, "_live_models", lambda: (release.wait(2), ["gpt-6-luna"])[1])
    started = time.monotonic()
    result = call(ctx=Config(selection_timeout=0.05))
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 0.5
    assert result["metadata"]["status"] == "unrouted/default"
    assert result["metadata"]["reason"] == "routing deadline exceeded"


@pytest.mark.parametrize('dependency', ['_live_models', '_decide', '_supported_efforts'])
def test_dependency_exception_text_never_persisted_or_logged(monkeypatch, caplog, dependency):
    def fail(*a, **kw):
        raise ValueError('synthetic-secret-do-not-persist')
    monkeypatch.setattr(router, dependency, fail)
    result = call()
    assert 'synthetic-secret' not in str(result)
    assert 'synthetic-secret' not in caplog.text


def test_timed_out_workers_are_bounded_and_recover(monkeypatch):
    import threading
    import time
    release = threading.Event()
    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(router, '_WORKERS', slots, raising=False)
    def blocked():
        release.wait(2)
        return ['gpt-6-luna']
    monkeypatch.setattr(router, '_live_models', blocked)
    try:
        for _ in range(2):
            assert call(ctx=Config(selection_timeout=0.02))['metadata']['reason'] == 'routing deadline exceeded'
        assert call(ctx=Config(selection_timeout=0.02))['metadata']['reason'] == 'routing capacity busy'
    finally:
        release.set()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        result = call()
        if result['metadata']['status'] == 'routed':
            break
        time.sleep(0.01)
    assert result['metadata']['status'] == 'routed'


def test_http_payload_is_bounded_and_secrets_only_in_header(monkeypatch):
    import httpx
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'answers': {}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(httpx, 'stream', client.stream)
        REAL_DECIDE('x' * 2000, 'synthetic-key', 'https://api.typesafe.ai/v1/systemone', 'jev-latest')
    import json
    body = json.loads(seen[0].content)
    assert len(body['state']) == 1200
    assert set(body) == {'state', 'model', 'questions'}
    assert set(body['questions']) == {'model_tier', 'effort_tier'}
    assert 'synthetic-key' not in seen[0].content.decode()
    assert seen[0].headers['authorization'] == 'Bearer synthetic-key'


def test_http_rejects_insecure_endpoints_before_transmission():
    with pytest.raises(ValueError, match='HTTPS'):
        REAL_DECIDE('private prompt', 'secret', 'http://example.com', 'jev-latest')


def test_http_limits_response_size(monkeypatch):
    import httpx
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b'x'*100))) as client:
        monkeypatch.setattr(httpx, 'stream', client.stream)
        with pytest.raises(ValueError, match='size limit'):
            router._request('GET', 'https://example.com', headers={}, limit=10)
