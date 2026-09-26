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
