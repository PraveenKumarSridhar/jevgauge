# Proposed Hermes runtime-selection API

Suggested PR title: **Add early, durable session runtime selection for plugins**

Status: local proposal, not submitted, accepted or released. The current implementation is a preview of the lifecycle, not a finished public API. `integration/base.json` identifies the original patch base. Local restoration also validated Hermes `959c7649fd806c3996cdd845ee4bd4e0eb1a0276` with the backend portion of `hermes-runtime-selection.patch` and `hermes-runtime-read.patch`.

## Upstream overlap checked 2026-09-28

There are active upstream proposals. Avoid presenting this contract as though Hermes has no routing work or opening a duplicate without reconciling their ownership and lifecycle semantics.

| Upstream proposal (open at inspection) | Reusable boundary | Gap for this installed Jev Desktop workflow |
|---|---|---|
| [#98703, pre-agent `turn_route`](https://github.com/NousResearch/hermes-agent/pull/98703), head `9eaa6dd8` | Plugin chooses public model/provider before agent construction; Hermes resolves credentials. Stable `session_key` is separate from physical `session_id`. | Implemented in Gateway and CLI paths, not `tui_gateway` Desktop path. Decision is per turn and ephemeral, with no reasoning-effort directive, durable first-conversation binding, addressed read, or physical-attempt evidence. |
| [#118985, reasoning-effort policy](https://github.com/NousResearch/hermes-agent/pull/118985), head `8e38508f` | Host-bounded effort vocabulary and persistence for cache-safe in-band effort updates. | Does not select a model or establish a Desktop session's first-call route. Depends on [#118932](https://github.com/NousResearch/hermes-agent/pull/118932) and [#114534](https://github.com/NousResearch/hermes-agent/pull/114534); none was merged at inspection. Its policy scope is per turn and transport-gated. |
| [#99053, `pre_llm_call` model override](https://github.com/NousResearch/hermes-agent/pull/99053), head `1e9c0bda` | A model-only override for an imminent call, with provider execution still host-owned. | Runs later and is explicitly ephemeral; it cannot provide Jev's durable model/provider/effort binding before Desktop agent construction. |
| [#119031, Jev adaptive effort catalog entry](https://github.com/NousResearch/hermes-agent/pull/119031), head `90629d8f` | A separate Jev effort-only policy consumer of #118985. | It explicitly does not select a model. It is not this combined Desktop routing plugin. |

The likely convergence is to extend the host-owned pre-agent route concept into `tui_gateway` and add a versioned session-binding/read contract, while composing effort capability validation with the transport work rather than duplicating it. A PR or comment should identify these gaps against the then-current heads. This is a design direction, not proof that any open PR will merge or retain its current API.

## Problem and intended outcome

A plugin needs to choose model and reasoning before provider/agent construction, persist the effective binding across cold resume, and preserve manual choices independently for each field. Request middleware is too late to establish that lifecycle.

The current local hook disappears when Hermes replaces or resets its checkout. An upstream, documented contract is the lowest-maintenance destination. Merely accepting the hook name would leave Jev dependent on private credential/catalog helpers, a separate telemetry hook, and a custom read method. These dependencies must be resolved before claiming stock-release compatibility.

## Required host responsibilities

| Boundary | Required public behavior | Current preview and gap |
|---|---|---|
| First selection | Invoke once for the first substantive message, before agent construction, under the session's ownership/locking rules | `select_session_runtime`; launch-profile Desktop sessions only |
| Capability discovery | Supply eligible provider/model/effort combinations in the owning profile, without giving the policy plugin provider credentials | Jev still imports private auth, account-catalog and effort helpers |
| Effective binding | Validate the directive, preserve manual fields, persist the effective pair before the first call, restore on resume | Implemented locally; host must also own reserved metadata and ownership fields |
| Read contract | Return the addressed session's binding and capability status without activating a session or constructing an agent | `session.runtime_selection` requires runtime and durable IDs; custom read patch |
| Attempt evidence | Identify actual provider/model/effort, attempt lifecycle and fallback outcome separately from proposed binding | Custom `provider_attempt` hook and `PROVIDER_ATTEMPT_API = 1` instrument physical `openai-codex` Responses calls; other providers and equivalence with stock observers are unproven |
| Execution bounds | Bound callback time and resource use; reject late results and stale progress after completion/cancellation | Jev bounds its selector, but both generic hooks currently dispatch synchronously without host timeout coverage |
| Compatibility | Document schema/version evolution, supported scope and migration behavior | Local markers only; no upstream compatibility commitment |

The UI already uses the native Desktop SDK's title-bar contribution and popover. The proposed host change should not include the obsolete core Desktop route panel. Native UI can survive app replacement while the backend still lacks routing support; those are separate acceptance conditions.

The read method requires both runtime and durable IDs to match. It must not resume a stale runtime, query the database, change attachments or build an agent. Its `session_binding` evidence proves a live-memory binding, not persistence or execution.

## Contract shape

The existing selector receives first-message text, session key, source, current provider/model/reasoning, separate user-ownership flags, and an optional `report_status(metadata)` callback. It returns optional model/provider/reasoning effort and metadata. This describes the preview, not a frozen schema.

The public form additionally needs host-resolved capability data and explicit owning-profile identity. Keep plugin annotations in a bounded, namespaced field. The host must synthesize effective model/provider/effort, per-field ownership, lifecycle status, fallback eligibility and original runtime. Plugin annotations must not control those fields. A policy request to preserve effort, such as Jev's manual-effort mode, needs a validated directive rather than forged ownership. Progress reports must carry an invocation generation and become inert when selection finishes or times out.

The current preview validates manual field writes but accepts plugin ownership/status metadata. An arbitrary selector can mislabel a host-inferred manual effort as router-owned, causing a later model switch or resume to lose its preservation signal. Jev currently returns consistent ownership; this is a generic API correctness gap, not a demonstrated Jev incident or privilege escalation. Adding a hook to a timeout allowlist alone would be insufficient because its abandoned worker could still call the current mutating progress callback.

`SESSION_RUNTIME_SELECTION_API = 1` and `PROVIDER_ATTEMPT_API = 1` currently identify local capabilities. The read schema independently distinguishes session binding from verified provider execution. Unknown/missing versions must yield explicit unavailable status, not stale saved state presented as live routing.

Current dispatch calls every registered selector before the host chooses the first valid result. Multiple plugins can therefore classify and publish conflicting progress. The public API needs deterministic arbitration and a total deadline, not only separate per-plugin timeouts.

## Scope and lifecycle

- Current scope is the Desktop gateway launch profile. Same-process sibling profiles are skipped. Generic profile support needs profile-scoped plugin dispatch, capability lookup and state access, with concurrent A/B/A tests.
- Selection never automatically reruns for tools, follow-ups or cold resumes.
- Eligibility currently excludes blank text, slash-prefixed text and existing history. The once-only flag begins in memory. Normal resume tests do not establish exactly-once classification across a crash before persistence; the host must define persistence-failure and crash-retry behavior before making that guarantee.
- Model and reasoning ownership are independent. Explicit model/effort changes invalidate provisional fallback and persist their effective state.
- A rejected first candidate can fall back once, only before usable output or tool effects. Persistence and actual attempt evidence must reflect the fallback.
- Failed, invalid or timed-out selection preserves the original effective runtime. Provider unavailability, missing host support and policy fallback remain distinguishable.
- The policy may send first-message content to its configured classifier, but Hermes must not send provider credentials in the selector payload.

## Work required before submission

1. Agree on a typed host-owned binding, plugin annotation schema, capability input and multiple-selector precedence.
2. Implement host-enforced deadlines, resource bounds and generation-gated progress/results. Test timeout followed by late callback and a concurrent manual action.
3. Close metadata ownership and validation gaps. Test adversarial selector output and subsequent live/deferred model switches.
4. Choose documented attempt evidence, either the proposed observer or proven stock observer coverage. Test streaming, retries, fallback, failure and resume before removing the custom dependency.
5. Keep the read contract and native UI aligned, including identity collisions across connections/profiles and unsupported sibling-profile behavior.
6. Replace Jev's private credential/catalog/effort imports with the public capability input. Upstream hook acceptance alone does not finish this migration.
7. Split the backend proposal from the historical core UI patch, and document compatibility/versioning with maintainers.

No automatic patcher or second updater is part of this proposal. Until a supported host API ships, local source updates can still remove routing. Retaining an older verified runtime is a separate operating-policy decision and does not establish compatibility with a new release.

## Evidence and limits

The local lifecycle tests cover first-prompt ordering, concurrent/one-time selection, durable restore, manual effort through model changes, invalid selection and provisional rejection. The read and slash-status boundary tests cover addressed reads and refusal to infer conversation/profile state from ambient command context. Native controller tests cover stale responses and bounded reads.

On this Mac, an owned Jev installation and native UI survived an actual Desktop source rebuild/app replacement. Fresh and cold-resumed calls independently recorded `openai-codex` / `gpt-6-luna` / `low`. This does not establish arbitrary-plugin safety, all-profile support, other OS live-provider behavior, or compatibility with an unpatched future release. See [verification](verification.md) and [update compatibility](update-compatibility.md).
