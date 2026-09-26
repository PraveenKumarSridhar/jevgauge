# Hermes integration: explicit development dependency

JevGauge v0.1 is not a drop-in plugin for stock Hermes. The upstream plugin API in the tested revision has no supported hook that durably selects both fields before Desktop agent construction. `llm_request` middleware is too late.

The public [integration patch](../integration/hermes-runtime-selection.patch) supplies the generic hook and optional Desktop panel. It contains no Jev policy or credentials. The installer **never** applies it automatically. `SESSION_RUNTIME_SELECTION_API = 1` is a proposed local capability marker, not an upstream promise. The source guard checks helper APIs too; it cannot prove authentication, account access, or which running Desktop binary you launched.

## Reproduce from a clean checkout

Requirements: Git, a Hermes-supported Python/runtime environment, Node/npm for Desktop, and platform build requirements from [Hermes development documentation](https://hermes-agent.nousresearch.com/docs/developer-guide/). JevGauge's CLI requires Python 3.10+; Hermes has its own stricter runtime requirements.

Clone a separate checkout. Do not apply the patch over your daily dirty checkout.

```sh
git clone https://github.com/NousResearch/hermes-agent.git hermes-jevgauge-dev
cd hermes-jevgauge-dev
git checkout d0288be5b3330d2442e3907185b8e9d0958297bb
git switch -c jevgauge-integration
# Replace /path/to/jevgauge with your JevGauge clone's absolute path.
git apply --check /path/to/jevgauge/integration/hermes-runtime-selection.patch
git apply /path/to/jevgauge/integration/hermes-runtime-selection.patch
```

The pin is recorded in [base.json](../integration/base.json). Do not assume it applies to every future commit. `git apply --check` must pass without forced context or ignored rejects.

On macOS/Linux, initialize the development environment and run the focused integration checks using Hermes's canonical runner:

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

Use Hermes's documented Windows development environment for the host integration tests. The JevGauge installer itself is portable Python; this shell-based Hermes test/build procedure is not a promise of native Windows Desktop support.

From the Hermes root, install JS dependencies and build the matching Desktop:

```sh
npm ci
npm run typecheck --workspace hermes
npm run pack --workspace hermes
```

Hermes's own setup and native build requirements apply. Run the resulting local Desktop app against **this integrated checkout**, following [Hermes's worktree guide](https://hermes-agent.nousresearch.com/docs/developer-guide/worktree-ui-dev). A new GUI with an old backend, or the reverse, does not test the contract. Confirm the backend checkout/version in Desktop before trying JevGauge.

Then follow the [JevGauge installation steps](../README.md#install), passing this checkout as `--hermes-repo`. Auth, the TypeSafe key, and plugin configuration belong in the gateway's launch home. Sibling profiles hosted by that gateway are skipped. The default provider must be authenticated `openai-codex`.

## What changes in Hermes

- Defer a fresh Desktop agent until the first substantive prompt when a selector exists.
- Invoke `select_session_runtime`, validate the pair and user ownership, then persist route metadata before provider use.
- Restore that binding across tools, later turns, and cold resume.
- Preserve compatible manual effort across model changes; reject incompatible manual pairs.
- Permit one fallback to the original binding after an eligible first-call rejection, before output/tool effects.
- Publish generic selection status through `session.info`; show a collapsible, session-scoped Desktop panel.
- Log actual Codex provider/model/effort independently from the plugin proposal.

## Rollback

Disable/uninstall JevGauge first, then use your original Hermes checkout and Desktop app. Existing saved conversations retain their normal Hermes runtime model/effort fields. Do not delete your Hermes home. Keep a backup of your original app when replacing a local build.

The project can become standalone only after an upstream Hermes release adopts the early durable API. An installer cannot remove this requirement safely.
