# Captured evidence and Hermes integration

The dashboard reads `<Hermes home>/jevgauge/events.sqlite3`. No historical import is attempted. Each event is version 1 with an immutable ID, conversation ID and UTC timestamp. SQLite schema `user_version=1` is created transactionally from an empty v0 database; unknown future versions are rejected without mutation. Replaying an identical ID is a no-op; a conflicting payload is rejected. Initialization and inserts serialize with short transactions and a 250 ms busy timeout. A interrupted started event remains an incomplete request. Read limits raise an explicit unavailable result rather than silently dropping conversation baselines.

The installed plugin includes the same standard-library-only telemetry module as the package. Hermes need not have the dashboard package on its Python path. The installer records its dashboard interpreter in an owned `runtime.json`. Plugin writes resolve Hermes' context-local home, then its process home, so another profile's context does not redirect writes into the launch profile.

Only allowed evidence fields are stored. The host observer sends no prompt text, response text, exception message, provider URL, API key or headers. Usage counters retain unknown breakdowns as null. Input includes cached input, and output includes reasoning tokens. The observer reads the original terminal provider frame rather than Hermes' normalized response, which can substitute requested model IDs or zero missing counters. Reported effort is recorded only when the terminal frame explicitly supplies it.

## Integration scope

`integration/hermes-runtime-selection.patch` is an explicit development integration at the commit in `integration/base.json`. It adds `PROVIDER_ATTEMPT_API=1` and a generic `provider_attempt` observer hook, alongside the existing proposed runtime-selection API. This is not an upstream release API. The installer checks both markers and never patches Hermes.

Existing logical `pre_api_request` and `post_api_request` hooks cannot distinguish internal physical stream retries, and their normalized token summaries do not preserve missing breakdown evidence. The added hook wraps each physical OpenAI Codex Responses `create` call, including connect retries, stream retries, terminal failures, HTTP rejections and fallback continuations. Hermes' request clients set SDK retries to zero; the visible host retry paths create distinct attempt IDs. Start and terminal events share an attempt ID but have distinct event IDs. Partial streams without a terminal frame remain failed with unavailable usage. HTTP rejection usage is captured only if the error body explicitly contains the supported usage fields.

The generic Desktop host attaches durable route metadata to each turn's agent before fallback setup, including cold resume and manual choices. The plugin route record captures pre-routing model/effort, account-catalog eligibility intersected with configured tiers, requested pair, selected tiers, fixed reason code, elapsed routing latency and policy version. Repeated model IDs use their highest configured capacity rank; this is configuration provenance, not a quality measurement. Confidence abstention retains available eligibility. Timed-out classification may have no completed eligibility evidence. No classifier reasoning is invented. Explicit project attribution is currently unavailable and stays Unattributed. No measured Jev charge is currently available, so production net savings remain unavailable.

Other provider families, Codex app-server transport, auxiliary provider calls, bot routing and sibling-profile routing are unsupported. The accounting scope is captured main conversation Responses requests, not all account activity. Missing telemetry itself cannot establish how many requests or dollars were omitted. SQLite or observer/import failures fail open; the next provider call proceeds without retrying or changing its arguments because of an observer failure. A crashed process may leave only a started event.

## Opening the dashboard

For a loaded plugin, `/jev-dashboard` is registered through Hermes' existing `register_command` API and dispatched by the existing Desktop plugin-command RPC. It starts the installer-recorded interpreter with `-m jevgauge dashboard --home <context home>`, waits for the loopback health endpoint, verifies the service, live mode and home fingerprint, then calls the standard browser launcher. A same-home listener is reused. A conflicting port, missing interpreter, startup failure or unavailable browser produces a clear command result and does not affect chat. The command remains available while the plugin is loaded, even if its internal routing switch is off. The dashboard enable switch uses the established plugin enable/disable mechanism; disabling it unloads the plugin after restart and removes the command. Use the CLI to reopen the dashboard in that case.

## Reproduced evidence

- Storage and plugin tests: `outputs/dashboard-venv/bin/python -m pytest tests/test_telemetry.py tests/test_router_telemetry.py tests/test_jev_router.py`, **56 passed** at this writing.
- Red phases: absent storage API; route evidence absent and missing command/provider hook (4 failures); abstention discarded known eligibility (1 failure). Green phases followed implementation. The first unrelated source-import failure was diagnosed separately and is not counted as a behavior regression.
- Pinned Hermes canonical suite: eight existing lifecycle files plus `tests/agent/test_provider_attempt_evidence.py` and `tests/agent/test_codex_first_event_timing.py`, **61 passed**. The observer tests first reproduced missing physical events (3 failures), then independently reproduced broken observer import preventing a provider call (1 failure), before each fix.
- `scripts/verify_hermes_dashboard.py --hermes-repo <patched pin> --dashboard-python <installed package python>` runs under the Hermes test interpreter. It passed actual installed-plugin discovery, route hook, physical stream observer, SQLite persistence, a fresh subprocess cold resume from Hermes SessionDB without rerouting, and the actual Desktop slash-command dispatcher starting a real child dashboard. The HTTP response contained one conversation and two known-usage requests, retaining the original default. The browser launcher is stubbed to record the intended URL; browser rendering is verified separately.
- The regenerated patch applied cleanly at the exact pin. Comparing the prior patch applied at the same pin against this patch changed only the intended existing host files (`plugins.py`, `server.py`, and the newly instrumented `codex_runtime.py`), plus the new observer and its tests. Reordering and updated blob hashes account for patch-text churn.

No active Hermes installation, credentials or paid provider calls were used. This is deterministic integration evidence, not a new live-provider or installed-Desktop trial.

Exact pinned-host canonical command (run inside the isolated patched checkout):

```sh
scripts/run_tests.sh \
  tests/agent/test_provider_attempt_evidence.py \
  tests/agent/test_codex_first_event_timing.py \
  tests/tui_gateway/test_first_prompt_route_hook.py \
  tests/tui_gateway/test_route_manual_switch.py \
  tests/agent/test_first_session_route_fallback.py \
  tests/tui_gateway/test_model_switch_reasoning_flag.py \
  tests/tui_gateway/test_reasoning_session_scope.py \
  tests/tui_gateway/test_deferred_model_switch_confirm.py \
  tests/tui_gateway/test_make_agent_provider.py \
  tests/tui_gateway/contracts/test_generated.py
```

The existing Desktop UI regression command is:

```sh
npm ci
npm run test:ui --workspace hermes -- \
  src/app/chat/session-route-panel.test.tsx \
  src/app/session/hooks/use-message-stream/utils.test.ts
```

The dashboard acceptance record reports its current execution result separately; the host Python count does not include JavaScript tests.
