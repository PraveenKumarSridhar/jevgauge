# Verification and limits

## Reproducible checks

```sh
python -m pip install '.[test]'
python -m pytest
python -m build
python scripts/check_wheel.py
python scripts/smoke_install.py --wheel dist/jevgauge-0.1.0-py3-none-any.whl --hermes-repo /path/to/integrated/hermes
```

Unit tests use synthetic API responses and temporary homes. Built-wheel smoke runs outside the source tree in a fresh venv/home, verifies both package resources, preserves unrelated config, and tests unsupported upstream refusal. The CI matrix exercises Python 3.10 and 3.13 on Linux, macOS, and Windows. Check the actual Actions result before treating any platform as passing.

The separate Hermes job applies the explicit patch at the pinned upstream revision and runs its canonical lifecycle tests. GUI tests and builds were run locally; CI does not assert a live Desktop or authenticated provider on all OSes.

## Adversarial findings fixed before release

| Finding | Regression / correction |
|---|---|
| First candidate incompatible with manual effort | Try later compatible account-listed candidates |
| Confidence outside probability range or malformed type | Reject booleans, strings, nonfinite and out-of-range values |
| Catalog + Jev can exceed per-operation timeout | Overall caller deadline; bounded response bytes and worker slots |
| Dependency exception text could enter saved metadata | Fixed local reasons or exception class only |
| Manual effort lost during model switch | Preserve compatible manual effort; validate before mutation |
| Cold-resume overrides and status stale after switch | Normalize, persist, and emit deferred runtime fields |
| Unmanaged/modified install could be overwritten | Owned-file hashes, symlink refusal, no force option |
| YAML aliases could mutate another section | Detach aliases; reject recursive/excessive nesting |

Each reproduced correctness defect received a failing test before its fix. Independent review found further defects after the first green run; passing tests were not treated as sufficient by themselves.

## Observed live Desktop evidence

On 2026-09-25, a macOS Desktop trial sent a synthetic title-case prompt. JevGauge appeared in **CHOOSING** state before output, then showed an automatic model and effort. Actual transport logs recorded:

```text
20:38:35.253 provider=openai-codex model=gpt-6-luna effort=low
20:43:32.844 provider=openai-codex model=gpt-6-luna effort=low
```

The second call followed a full app shutdown and resumed conversation. The GUI showed the expected title-case response and `RESUMED`, respectively. No second Jev proposal appeared for that conversation. Minimize and Details were also exercised in the installed app.

One restart check stalled during reconnect and recovered after a renderer reload; no renderer console error was observed. A subsequent launch reconnected without a reload. Root cause was not established, and this should not be described as a flawless Desktop lifecycle.

This live trial predates the final packaging hardening and expanded model-switch regressions. The later trial below checks the hardened implementation separately.

## Final hardened-code trial

A second macOS trial on 2026-09-25 used the current plugin and reviewed host changes. Desktop showed CHOOSING before the answer. Transport evidence for one conversation:

| Local time | Action | Provider | Model | Effort |
|---|---|---|---|---|
| 21:12:53.327 | First prompt | openai-codex | gpt-6-luna | low |
| 21:13:52.058 | After `/reasoning high`, then `/model gpt-6-sol` | openai-codex | gpt-6-sol | high |
| 21:15:07.485 | Full quit, reopen, resume | openai-codex | gpt-6-sol | high |

Desktop displayed `Copper Rain Over Neon Towers`, `MANUAL`, and `RESUMED`. Both fields displayed MANUAL after the commands and after restart. The gateway runtime ID changed across restart. Exactly one session-route proposal was logged for this conversation; the resumed turn did not reroute. Both launches reconnected without a renderer reload.

Final local package suite: **54 passed**. Clean pinned Hermes checkout with the exported patch: **48 passed across eight canonical test files**. Independent host review also ran four files: **27 passed**. These overlapping host runs are not added together as unique tests. Built-wheel smoke passed outside the source tree in a fresh environment, including unsupported-stock refusal.

## Follow-up adversarial evaluation

A second independent review on 2026-09-25 found defects beyond the initial green suite. Failing regressions were added before fixes:

- Import-created Python caches blocked uninstall/reinstall. Retained ownership records now preserve caches and user files while allowing safe reinstall and interrupted-operation retry. Same-size upgrades execute the new code.
- Explicit reasoning off was treated as an absent effort. Policy and host now normalize it to `none`, validate compatibility, and report it accurately.
- Unvalidated response model metadata could be persisted. Only the configured Jev model identifier is saved.
- Provisional first-call fallback could undo a later manual choice. Manual changes now invalidate that rollback and restore normal Hermes fallback handling.
- Deferred reasoning changes could lose to old resume overrides. The build inputs, saved runtime, and display update together.
- Profile-owned reasoning after a model switch was mislabeled AUTO. The panel now honors DEFAULT ownership.

Post-fix local evidence: **74 policy/installer tests**, **53 Hermes lifecycle tests across eight files on a clean patched checkout**, and **14 focused Desktop UI tests**, all passing. The rebuilt-wheel smoke imports the deployed plugin before testing uninstall/reinstall. Independent reviews approved each scoped fix before its commit. The CI workflow now includes the focused Desktop UI tests as well as the platform matrix and pinned lifecycle tests.

The tested upstream pin `d0288be5b3330d2442e3907185b8e9d0958297bb` still matched upstream main when checked during this evaluation. That is a dated observation, not a promise of compatibility with future changes.

## Human acceptance checklist

- New eligible chat: selecting state appears, then a valid pair before provider call.
- Later message and tool continuation: no second Jev request, same effective pair.
- Manual reasoning: panel says MANUAL, provider request agrees.
- Manual model switch: compatible user-owned effort survives; incompatible pair rejected.
- Full quit/reopen/resume: stored pair survives and actual request agrees.
- Minimize/reopen: layout changes, routing does not.
- Missing Jev key or timeout: original defaults remain; chat still responds.
- Another provider or sibling profile: no Jev request.

## What is not established

Live Desktop/provider execution on Linux or Windows, arbitrary Hermes versions, every account/model entitlement, named sibling profiles, routing-quality gains, subscription quota savings, and absence of all defects. A token count is not a measurement of subscription quota use.

## Update resilience diagnostic worktree, 2026-09-26

The new `/jev-status` command diagnoses the loaded process contract. It intentionally withholds profile enablement and saved bindings because Hermes's command callback does not attest the owning profile. Capability availability is not provider execution evidence. Native titlebar restoration and update admission remain unimplemented.

Validation in isolated worktree:

- Project baseline: 179 passed. Initial health regressions: 15 failed before implementation.
- Astra review corrections: four additional regressions failed before fixes. Full suite: 198 passed on Python 3.11.15, pytest 8.4.2.
- Actual Hermes `command.dispatch` and `slash.exec` handlers: 2 passed through the canonical runner on a disposable patched copy of Hermes commit `959c7649fd806c3996cdd845ee4bd4e0eb1a0276`. Each test uses two disposable homes, opposite configured enablement, identical stored session IDs and distinct model labels. Both paths visibly decline profile-specific claims and never call settings/storage readers. Plugin discovery is substituted with the registered test callback. These checks do not prove addressed profile lookup works; they prove it is declined.
- Host test environment: Python 3.11.15, pytest 9.1.1. The production managed Python 3.14 environment, live Desktop, provider execution, and future Hermes versions were not validated here.
- One host harness attempt failed because the canonical runner strips custom environment variables. The fixture was corrected to copy the isolated source beside the test. No product behavior was changed for that harness failure.

To reproduce the host boundary test, copy `integration/tests/test_jev_status_scope.py` to an isolated Hermes checkout's `tests/tui_gateway/`, and copy `jev-router/__init__.py` beside it as `jev_router_scope_source.py`. Track the test in that disposable checkout for canonical discovery, then run:

```sh
HERMES_PYTHON=/path/to/hermes/python scripts/run_tests.sh -j 1 --file-retries 0 tests/tui_gateway/test_jev_status_scope.py
```

No live symlink, backend, app bundle, updater or credentials were changed.

### Live restoration completed later on 2026-09-26

The earlier isolated-only status above was superseded for the backend and native display by a later live restoration on this Mac. Current new-chat routing and cold resume were verified in Desktop against actual provider request logs. The new process-only slash-command code was still isolated at that point, and automatic protection against backend resets was still pending.

### Owned package activation completed afterward

The later owned plugin migration superseded the isolated slash-command and development-symlink status above. The process-only diagnostic was deployed, installed telemetry took precedence over editable packages, and the plugin ran from owned files with a separate wheel-installed dashboard environment. All 202 project tests passed at that point, including four telemetry-origin regressions.

Cold-start acceptance exposed a Hermes PM/source-completion cycle. A single normal CLI completion with Desktop closed successfully rebuilt and installed the app; no source pull/reset or manual marker removal was performed. Exact plugin/config/UI bytes survived. Both fresh routing and cold resume then reached `gpt-6-luna / low` according to independent provider logs. The app-replacement check is now a live observation, but automatic survival of the backend hook across source resets and future releases remains incomplete.
