# Proposed Hermes integration

Suggested PR title: **Add early, durable Desktop session runtime selection for plugins**

This is a PR preparation document, not a claim that a PR is open or accepted. The patch is based on the exact revision in integration/base.json.

## Problem

A plugin cannot safely select model and reasoning via request middleware after provider/agent construction. Desktop also builds agents before the first prompt and needs a persisted binding that survives cold resume. User-owned fields must remain authoritative.

## Proposed contract

`select_session_runtime` receives first-message text, session key, source, current provider/model/reasoning, separate user-ownership flags, and optional `report_status(metadata)` callback. A selector returns a session-scoped directive with optional model/provider/reasoning effort plus metadata. Core validates it, enforces manual ownership, saves the effective pair, and constructs the agent afterward.

The proposed `SESSION_RUNTIME_SELECTION_API = 1` marker identifies this local contract. It is not registered as an upstream API today. The optional status callback uses existing session.info metadata. Core UI is generic; a plugin can supply a display label.

## Scope and isolation

- Desktop gateway launch profile only. Sibling profiles are intentionally skipped until profile-specific plugin discovery/secret access are designed and tested.
- No Jev references in host selection logic. No provider credentials sent to the selector's external endpoint by Hermes core.
- No automatic rerouting later in the conversation.
- First-response rejection fallback is one-shot and ends as soon as usable output or a tool effect exists.
- Explicit model and reasoning changes update the persisted effective state, including deferred resumed sessions.

## Tests and evidence

The patch includes first-prompt ordering, concurrent/one-time selection, durable restore, manual effort through model changes, invalid selection, and provisional rejection tests. Desktop tests cover event hydration, manual labels, fallback display, and minimized-state persistence.

The actual local Desktop path has produced a provider-confirmed `openai-codex` / `gpt-6-luna` / `low` first call and resumed call. This is macOS evidence; other OS live-provider support remains unverified. See verification.md.

## Maintainer decisions before upstream submission

1. Final hook name and versioning, and whether callback metadata needs a typed shape.
2. Whether generic routing display belongs in core or a supported Desktop extension slot.
3. Timeout/worker ownership for arbitrary plugins beyond JevGauge's own bounded selection.
4. Profile-specific plugin dispatch, which this preview deliberately does not implement.
5. Whether first-request rejection handling should integrate further with the existing provider failover API.

The patch is a testable initial proposal. It should be reviewed as a generic capability, independently of JevGauge's tier policy.
