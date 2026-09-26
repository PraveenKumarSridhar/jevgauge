# Approved V5 implementation plan and shared contract

Base: c4c5e44, clean main matching origin/main. Baseline: 74 tests passed using `.venv/bin/python -m pytest`. No active Hermes installation will be modified; no paid provider calls.

## Sequence

1. Preserve and inspect approved V5; agree event/API contracts and acceptance matrix.
2. In parallel, implement telemetry/integration, accounting, and V5 assets using requirement-driven red/green tests.
3. Implement loopback HTTP API, safe configuration updates, packaging, demo isolation, and CLI.
4. Run deterministic lifecycle, browser, installation, and fault checks.
5. Independent accounting and reliability reviewers reproduce checks; fix findings.
6. Commit, push feature branch, open draft PR, leave local preview and exact restart instructions.

## Ownership

- Telemetry worker: `src/jevgauge/telemetry.py`, `jev-router/`, `integration/`, telemetry tests, integration verification scripts/docs.
- Analytics worker: `src/jevgauge/analytics.py`, `src/jevgauge/pricing.json`, `src/jevgauge/demo.py`, accounting tests/docs.
- Frontend worker: `src/jevgauge/static/`, browser tests and frontend-specific docs.
- Coordinator: server, config, CLI, packaging, CI, plan/acceptance, end-to-end wiring.
- All workers share this checkout and preserve others' edits. Do not commit while implementation is concurrent.

## Event contract v1

Append-only SQLite event store at `<home>/jevgauge/events.sqlite3`, separate demo provider (never writes production events). `EventStore(path).append(event)` validates, idempotent event_id, returns bool; `.read_events(limit=50000)` returns event dictionaries in timestamp/event order; `.close()` if necessary. Prefer connections per transaction. Reject unknown schema versions. Fail-open `record_event(home, event)` never raises into routing. Schema migration uses SQLite user_version and transaction. Raw prompts, credentials, response text and arbitrary payload fields are not retained.

Event required fields: schema_version=1, event_id, kind (`route`, `attempt`, `overhead`, `override`), conversation_id, timestamp (aware ISO UTC).

Optional fields:
- project (explicit evidence only, null means Unattributed), provider, policy_version, reason_code, outcome, latency_ms.
- default_model, default_effort (captured pre-routing), selected_model, requested_effort, model_tier, effort_tier.
- eligible_models: list of `{model, capability}`; capability integer ranking from configured tier ordering, not objective quality. eligibility_source.
- attempt_id, requested_model, reported_model, reported_effort, status (`started`, `completed`, `rejected`, `failed`), manual_override boolean, fallback boolean.
- usage: null or `{input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, source}`. Input includes cached; output includes reasoning. Unknown breakdown null. Source provider only for observed usage.
- overhead_usd (measured charge only), overhead_source, billed_usd (actual reported charge only).

Attempt started/completed events have distinct event IDs but same attempt_id. Analytics merges them as one actual outgoing attempt and flags incomplete evidence. Route records capture historical defaults/eligibility and persist across restart/resume. No invented classifier reasons or provider effort.

## Analytics API contract

`analytics.summarize(events, *, start=None, end=None, timezone='UTC', project=None)` returns JSON-safe dashboard object. Dates YYYY-MM-DD inclusive in named IANA timezone; reject invalid/reversed/oversize ranges; handle DST via aware boundaries. Project applies to conversations with recorded project. Production missing project stays unattributed.

Top-level output: `mode` (server sets live/demo), `currency='USD'`, `summary`, `coverage`, `conversations`, `series`, `models`, `projects`, `pricing`, `limitations`, `filters`.

`summary`: conversation_count, request_count, review_count, default_cost_usd, routed_cost_usd, provider_cost_usd, provider_difference_usd, net_savings_usd (null if overhead unavailable), median_cost_usd, strongest_cost_usd, overhead_usd, overhead_available, routed_count, abstained_count, failed_count, manual_count. Comparisons use same fully priced known-usage non-manual cohort across all baseline models; expose excluded count. All observed priced attempt cost also reported separately. Negative differences retained.

`coverage`: known_usage_requests, priced_requests, total_requests, comparison_requests, missing_usage_requests, missing_price_requests, manual_requests; request fractions do not bound missing dollars.

`conversations`: id, timestamp, project, default_model, selected_model, requested_effort, model_tier, effort_tier, reason_code, outcome, policy_version, latency_ms, eligible_models, attempts (sanitized evidence), review_reasons, manual_override, provider_cost_usd, default_cost_usd, overhead_usd, net_savings_usd. No prompt fields.

`series`: date, default_cost_usd, routed_cost_usd, provider_difference_usd (daily same cohort). `models`: model, conversations, requests. `projects`: project, conversations, requests, provider_cost_usd, provider_difference_usd. `pricing`: version, sources, rates, conditions. Analytics worker should document additions precisely and coordinate any deviations.

## HTTP/UI contract

GET `/api/dashboard?start=...&end=...&timezone=...&project=...` uses analytics; no args returns all captured events (UI sets periods). GET `/api/config` returns `{settings, revision, csrf_token, semantics, supported}`. POST `/api/config` JSON `{settings, revision}` requires `X-JevGauge-Token` from GET and exact same Origin; only enabled, effort_mode, selection_timeout, tier_models allowed, preserving all other YAML. Atomic write with existing installer lock and optimistic revision. Settings require Desktop restart and apply to new conversations; existing bindings unchanged. Demo config validates but never touches real home.

Static `/`, `/app.js`, `/styles.css` (frontend may add vendored assets). `--demo` server chooses deterministic synthetic events, visibly marks mode; never mixes live and demo. Server loopback only, validates Host and Origin, bounds body/query/history, no wildcard CORS. Errors generic, no paths or secret-bearing exception text.

## Consequential assumptions

- New evidence only; no guessed historical import. Absent attempt telemetry is unavailable, never zero or success.
- Existing subscription provider usage supports API-equivalent estimates only, never subscription bill savings.
- Unknown current model prices stay unpriced. Official published price table is versioned separately from demo assumptions.
- Configured capability tiers define strongest comparison; not price ordering or demonstrated quality.
- V5's synthetic prompts and per-call Jev charges remain confined to explicit demo mode.
