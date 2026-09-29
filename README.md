<p align="center"><img src="assets/hero.png" alt="JevGauge: Choose once. Stay in control. Independent model and reasoning gauges." width="100%"></p>

# JevGauge

**One prompt. One durable route. Your conversation stays yours.**

JevGauge asks [Jev](https://docs.typesafe.ai/introduction/quickstart) to choose a model tier when a new Hermes Desktop chat starts. On the legacy development integration it can also choose a separate reasoning tier. Hermes binds the effective route before its first provider call. Later messages use that binding. Your manual choices always take precedence.

> **Developer preview, v0.1.** This is a working two-part POC: a user-scoped plugin plus a proposed generic Hermes integration. **It does not work on an unmodified Hermes release yet.** The installer checks compatibility and refuses unsupported checkouts. It never silently patches Hermes. Start with the [compatibility and integration guide](docs/hermes-integration.md).

[Install](#install) · [Try it](#try-it) · [Manual control](#stay-in-control) · [Privacy](#what-leaves-your-machine) · [Tests](#development) · [Upstream proposal](docs/upstream-proposal.md)

## What it does

- **Chooses once.** The first substantive message triggers one Jev decision. Tool calls, later turns, and resume do not retrigger it.
- **Keeps model and effort independent.** The native contract changes only the model and preserves Hermes reasoning. The legacy development integration can also select effort.
- **Keeps you in charge.** `/model` and `/reasoning` own their respective fields. Compatible manual effort survives a later model change.
- **Fails open.** Missing credentials, timeouts, invalid decisions, or no compatible candidates preserve the conversation's original defaults.
- **Shows its work.** A native titlebar indicator shows choosing, the effective route, and manual ownership. `/jev-status` diagnoses loaded host support.

```mermaid
flowchart LR
    A[First substantive prompt] --> B[Jev: model tier + effort tier]
    B --> C[Validate account models and effort]
    C --> D[Save effective route]
    D --> E[First call, tools, later turns, resume]
    B -. timeout or invalid .-> F[Keep this chat's defaults]
    C -. no compatible route .-> F
    U[Your manual choices] --> D
```

Jev returns typed choices. The displayed reason is a local policy label, **not** generated chain of thought. “Route selected” means a binding was chosen; provider acceptance is verified separately.

## Compatibility

| Area | v0.1 status |
|---|---|
| Surface | Hermes Desktop; addressed profiles with the native contract, launch profile with the legacy contract |
| Provider | Authenticated ChatGPT / Codex subscription (`openai-codex`) |
| Hermes version | A checkout with the native `turn_route` plus `session.turn_route.read` contract, or the pinned legacy development integration |
| Stock Hermes releases | Detected and refused unless they ship the required contract |
| Other providers, TUI, messaging, subagents | Not routed |
| Sibling profiles hosted in the same backend | Supported by the native addressed contract; skipped by the legacy contract |
| Installer and policy | Python 3.10+, CI matrix for Linux, macOS, Windows |
| Live Desktop/provider verification | macOS; first call and resumed turn verified locally |

A green unit-test matrix is not evidence of live Desktop behavior on every OS. Model availability depends on your account. The native adapter sends configured candidates through Hermes's host resolver and fails open when a selection is unavailable. The legacy adapter intersects tiers with the authenticated account catalog. Static model lists are not entitlement evidence.

## Install

### 1. Prepare Hermes

Install and authenticate [Hermes](https://hermes-agent.nousresearch.com/docs/). Select its ChatGPT subscription provider. Run `doctor` against that checkout. If the native contract is absent, use the [isolated legacy development integration procedure](docs/hermes-integration.md). JevGauge never modifies the checkout passed to `doctor` or `install`.

### 2. Install JevGauge's tooling

```sh
git clone https://github.com/PraveenKumarSridhar/jevgauge.git
cd jevgauge
python -m venv .venv
```

Activate the environment:

```sh
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows PowerShell. Use a Python 3.10+ installation.
.\.venv\Scripts\Activate.ps1
```

If your shell does not permit activation, invoke `.venv/bin/python` or `.venv\Scripts\python.exe` directly in place of `python` below.

```sh
python -m pip install .
python -m jevgauge doctor --hermes-repo /path/to/hermes-agent
```

`doctor` is a static API check. It does not read credentials, contact providers, or prove a live call. Quote paths that contain spaces. `--home /path/to/hermes-home` selects a non-default launch home; otherwise `HERMES_HOME` or `~/.hermes` is used.

### 3. Add your Jev key and install the plugin

Get a TypeSafe key from the [TypeSafe dashboard](https://console.typesafe.ai/). Add `TYPESAFE_API_KEY` to your Hermes secret environment, for example by editing the `.env` file in your Hermes home. Do not put the key in this repository or `config.yaml`.

Close Hermes Desktop before editing its configuration or running installer commands. Then:

```sh
python -m jevgauge install --hermes-repo /path/to/hermes-agent
```

Restart the integrated Desktop build. The installer copies the backend into `<home>/plugins/jev-router` and the native indicator into `<home>/desktop-plugins/jev-router`. It enables the backend in Hermes config; enable **Jev routing** in Desktop Capabilities → Plugins to show the indicator. Both user-scoped plugin directories survive an app bundle replacement. The internal plugin ID remains `jev-router`; its display name is JevGauge.

The plugin declares `session.turn_route` API 1 and the installer opts Jev into required update admission. A gate-aware Hermes updater refuses a candidate that drops that contract before changing the checkout. This protection is implemented in the proposed Hermes host branch and is not present in current stock releases.

The installer preserves unrelated YAML values, but normalizes YAML formatting/comments. It refuses unmanaged directories, development symlinks, and modified managed files. It never writes your API key. [Installer details](docs/installation.md).

## Try it

1. Start a **new Desktop chat**. Send: `Convert "neon signals after midnight" to title case. Return only the result.`
2. Watch **Jev: choosing → Jev: &lt;model&gt;**. With the legacy contract, the indicator also shows the selected reasoning effort.
3. Send `Reply with exactly SECOND.` The route should stay the same, with no second Jev decision.
4. Run `/reasoning high`. The effort becomes manual. Later messages must retain it.
5. Quit and reopen Desktop, resume that conversation, and send `Reply with exactly RESUMED.` Verify the saved route.

The indicator's **Minimize label / Show model in title bar** control remembers your display preference. It does not disable routing. `/jev-status` reports the loaded routing and telemetry contracts. `--json` returns a versioned process diagnostic. Contract availability does not prove routing executed. The current Hermes plugin command API does not verify the owning profile, so this command withholds enablement and saved conversation bindings. The internal saved-record parser is not a public native UI endpoint.

For actual request evidence, inspect `<home>/logs/agent.log` for `Codex request route`. Its provider, model, and effort should match the effective binding. Do not share raw logs without checking them for private content. See the [test checklist and observed evidence](docs/verification.md).

## Local dashboard

The approved V5 dashboard has Savings, Decisions, Reliability and Jev cost views. It reads prompt-free routing and physical provider-attempt evidence captured by the explicit dashboard integration patch. Unknown usage and prices stay unavailable; displayed USD comparisons are API-equivalent estimates, not subscription savings.

```sh
python -m jevgauge dashboard --home /path/to/hermes-home --open
```

In integrated Hermes, `/jev-dashboard` starts the local service and opens it in your browser. Use the same persistent Python environment for installation and dashboard operation. The default address is `http://127.0.0.1:8765/`.

To inspect the interface without captured activity, explicitly select demo mode:

```sh
python -m jevgauge dashboard --demo --port 8766 --open
```

Demo mode is visibly labeled and isolated from live storage and configuration. The configuration gear supports routing enablement, effort mode, timeout and model tiers through existing Hermes YAML settings, with restart/new-conversation semantics. Future capabilities remain labeled as such. [Operation, privacy and limitations](docs/dashboard/operations.md) · [Acceptance and evidence](docs/dashboard/acceptance.md).

## Stay in control

```text
/reasoning high
/model <eligible-model-id> --reasoning high
```

A manual field stays manual. Jev does not reset it on the next turn. Incompatible explicit model/effort pairs should be rejected rather than silently changing your setting. A new chat gets a new routing decision.

To keep manual reasoning in **all new chats**, set Hermes's profile effort (`/reasoning high --global`), then add `effort_mode: manual` under this plugin's settings. Restart Desktop. Jev can still choose the model. Restore `effort_mode: auto` to allow automatic effort selection for future chats.

```yaml
plugins:
  enabled:
    - jev-router  # retain your other enabled plugins
  entries:
    jev-router:
      settings:
        enabled: true
        effort_mode: auto
        selection_timeout: 5.0
```

The overall routing wait is capped at five seconds by default, with two-second HTTP operation limits, response size limits, and bounded worker concurrency. `selection_timeout` accepts 0.01 to 10 seconds. An expired selection cannot change the conversation later.

`tier_models` overrides the ordered `economical`, `balanced`, and `strongest` candidate lists. See [configuration](docs/configuration.md). Jev confidence below 0.55 on either choice retains defaults; this is a policy threshold, not calibrated accuracy.

### Enable, disable, remove

Close Desktop first; restart after each change. Use the same `--home` for every command if you installed into a custom home.

```sh
python -m jevgauge disable
python -m jevgauge enable --hermes-repo /path/to/hermes-agent
python -m jevgauge uninstall
```

Disabling affects new routing. Existing conversations keep their saved bindings. Uninstall removes only unchanged installer-owned plugin files; it preserves custom settings and unknown files. Upgrades use disable → uninstall → install. Existing development symlinks need deliberate migration, never forced overwrite.

## What leaves your machine

One Jev request sends **up to the first 1,200 characters of the first substantive user message**, a Jev model name, and two fixed typed-choice questions. The default endpoint is `https://api.typesafe.ai/v1/systemone`.

No attachments, later conversation history, tool results, or ChatGPT credentials are included. Your TypeSafe key goes in the authorization header. Account discovery uses your existing authenticated Hermes route. Custom endpoints must use HTTPS.

The native host stores an allowlisted model/reasoning binding with per-field ownership and a bounded reason code. The legacy integration also stores the candidate set, versions, and policy reason with the conversation. Neither route logs the prompt or dependency exception text.

## Development

```sh
python -m pip install '.[test]'
python -m pytest
python -m build
```

Tests run without Hermes installed and without provider credentials. HTTP boundaries use mock transports; the installer uses temporary homes. CI labels the patch-based matrix as legacy integration and runs a separate lane against an exact native Hermes contract commit, including the packaged Jev registration path. [Verification details](docs/verification.md).

We work in small commits: regression test → fix → independent adversarial review → commit. Found a bug? Include OS, Python version, Hermes commit, route status, and a redacted reproduction in an [issue](https://github.com/PraveenKumarSridhar/jevgauge/issues).

## Upstream path

The generic hook, durable binding, manual override handling, and addressed read belong in Hermes. Jev policy and the Desktop indicator belong here. The legacy integration patch is public and versioned, never hidden in the installer. See the [upstream proposal](docs/upstream-proposal.md) for the API and acceptance tests.

Token counts do not measure subscription quota savings. The contract here is a visible, one-time decision with durable manual control.

MIT licensed. Independent project, not affiliated with or endorsed by Nous Research or TypeSafe. Hero artwork is generated concept art, not a screenshot. [Third-party notices](THIRD_PARTY_NOTICES.md).

### Native Desktop indicator (resilience worktree)

`jev-router/desktop/plugin.js` is a standalone native Desktop extension. It uses the supported titlebar and popover APIs, and does not require a custom Desktop build. Its source is `desktop/*.mjs`; regenerate with `python scripts/build_desktop.py` and test with `node --test tests/desktop/*.test.mjs`. The wheel packages it and `jevgauge install` copies it into Hermes's user plugin directory.

The native indicator calls the addressed `session.turn_route.read` RPC first and falls back to the legacy `session.runtime_selection` RPC. A backend without either complete contract displays **Jev: unavailable**. The indicator describes a live session binding, not provider execution or measured savings. Native scope follows the addressed session profile; legacy scope remains the launch profile.

For manual development installation, copy the generated file to `<Hermes home>/desktop-plugins/jev-router/plugin.js`, then enable **Jev routing** in Capabilities → Plugins. Preserve any existing file before replacing it. The installer can adopt an exact manual copy; it refuses a different or locally modified native plugin. Packaging the UI does not prove a future Hermes host is compatible, so run `doctor` after updates. See the [update compatibility checks](docs/update-compatibility.md).
