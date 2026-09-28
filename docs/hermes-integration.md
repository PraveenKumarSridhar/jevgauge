# Hermes integration contracts

JevGauge is a user-scoped plugin. Its code, configuration, Desktop indicator, and compatibility checks live in this repository and are installed under the Hermes user home. The installer never edits a Hermes checkout or app bundle.

Automatic first-call routing still needs Hermes to expose an early host-owned contract. JevGauge accepts two forms:

| Contract | Scope | Selection |
|---|---|---|
| Native `turn_route` middleware plus `session.turn_route.read` | Addressed Desktop profiles | Model/provider binding. Existing reasoning is preserved |
| Legacy `SESSION_RUNTIME_SELECTION_API = 1` development integration | Desktop launch profile | Model/provider and reasoning binding |

Run this before installation:

```sh
python -m jevgauge doctor --hermes-repo /path/to/hermes-agent
```

`doctor` performs a static check. It does not change files, read credentials, contact a provider, or prove that a particular Desktop process is using the inspected checkout. Unsupported Hermes updates are rejected visibly.

The native contract is the maintenance target. Its implementation is being developed as an extension of upstream [`turn_route` PR #98703](https://github.com/NousResearch/hermes-agent/pull/98703). It has not merged or shipped, so current unmodified Hermes releases must not be described as compatible unless `doctor` confirms the complete contract.

## Legacy development integration

The public [integration patch](../integration/hermes-runtime-selection.patch) is retained for reproduction and transition testing. It contains no Jev policy or credentials. Apply it only to a separate development checkout. The JevGauge installer never applies it.

Requirements: Git, a Hermes-supported Python/runtime environment, Node/npm for Desktop, and platform build requirements from [Hermes development documentation](https://hermes-agent.nousresearch.com/docs/developer-guide/). JevGauge's CLI requires Python 3.10+; Hermes has its own stricter runtime requirements.

```sh
git clone https://github.com/NousResearch/hermes-agent.git hermes-jevgauge-dev
cd hermes-jevgauge-dev
git checkout d0288be5b3330d2442e3907185b8e9d0958297bb
git switch -c jevgauge-integration
git apply --check /path/to/jevgauge/integration/hermes-runtime-selection.patch
git apply /path/to/jevgauge/integration/hermes-runtime-selection.patch
```

The pin is recorded in [base.json](../integration/base.json). Do not assume the patch applies to later commits. A clean application is necessary but does not establish lifecycle compatibility.

On macOS/Linux, run the focused integration checks with Hermes's canonical runner:

```sh
scripts/run_tests.sh \
  tests/tui_gateway/test_first_prompt_route_hook.py \
  tests/tui_gateway/test_route_manual_switch.py \
  tests/agent/test_first_session_route_fallback.py \
  tests/tui_gateway/test_model_switch_reasoning_flag.py \
  tests/tui_gateway/test_reasoning_session_scope.py \
  tests/tui_gateway/test_deferred_model_switch_confirm.py \
  tests/tui_gateway/test_make_agent_provider.py \
  tests/tui_gateway/contracts/test_generated.py
```

Use Hermes's documented Windows development environment for host integration tests. Build the matching Desktop from the same checkout:

```sh
npm ci
npm run typecheck --workspace hermes
npm run pack --workspace hermes
```

Then follow the [JevGauge installation steps](../README.md#install), passing this isolated checkout as `--hermes-repo`. A new GUI with an old backend, or the reverse, does not test the contract.

## Contract boundaries

The host must route before agent construction, resolve provider credentials itself, expose a profile-scoped plugin-secret reader, persist an allowlisted binding, restore it on resume, and expose an addressed read that does not activate another profile or build an agent. The plugin receives a public route and never receives provider credentials. It reads only its own TypeSafe token through `PluginContext.get_secret`.

The native contract records selection evidence only. It does not prove which physical provider served a request. Provider-attempt telemetry is optional and remains a separate legacy observer until Hermes exposes a supported generic execution boundary.

## Rollback

Disable or uninstall JevGauge, then use your normal Hermes installation. Existing saved conversations retain their Hermes runtime fields. Do not delete the Hermes home. The plugin directories may remain across an app replacement, but `doctor` must pass again before enabling new routing after an update.
