# Fresh-context pre-merge evaluation

Date: 2026-09-26. Starting revision: `311640c`, PR #1. This evaluation used a separate reviewer context, the original implementation request, V5 acceptance notes, current source, and new reproductions. Previous passing summaries were not used as proof. The reviewer added tests and reported findings; the coordinating agent made the implementation fixes. No merge, paid provider call, credential access, or active Hermes changes occurred.

## Findings and fixes

| Priority | Reproduction and effect | Fix and regression |
|---|---|---|
| P2 | `static/app.js` discarded the dashboard API's error message. A history exceeding 50,000 events showed only a generic retry, hiding the supplied archive remedy. | Keep a nonempty error string of at most 512 characters and render it through existing HTML escaping. New browser tests assert the actual remedy, inert HTML-like text, hidden totals, and generic fallback for HTML, malformed JSON, and non-string error payloads. |
| P2 | `analytics.py` selected raw terminal records independently of merged attempt start time. A request starting at 23:59 and completing at 00:01 created a next-day conversation, routed count, and `Unavailable` model bar despite zero requests. | Select merged attempts at their start time alongside actual non-attempt events. The regression checks an empty next day, preserves route-only disabled activity, and preserves the first day's complete terminal usage: $0.00056 provider cost and $0.0028 captured-default cost. |
| P2 | The visible pricing table rounded the nano cached-input rate from $0.025 to $0.03 per million tokens. The displayed rate was 20% higher than the rate used in calculations. | A separate rate formatter preserves fractional-cent precision in pricing rows. The real-service browser regression checks the exact `$0.025` cell. Normal cost-total presentation is unchanged. |

## Direct evidence

- Before fixes, the browser error regression failed with the expected archive text absent; the three malformed-error fallbacks passed.
- Before fixes, the midnight regression failed with `conversation_count == 1` instead of zero. The route-only control passed. After the fix, both Python cases passed, including the hand-calculated cost assertions.
- Before the rate fix, an isolated real demo server rendered `$0.03` where the test required `$0.025`.
- The reviewer cross-inspected all three implementation diffs. The changes are confined to error presentation, price presentation, and conversation date attribution.
- A separate in-app browser session exercised Savings, Decisions, Reliability, Jev cost, Details, the three-conversation review queue, and light/dark appearance against the running labeled demo. At a 352px viewport, the page and the open calculation panel had a document width of 352px. The temporary viewport was reset and the reviewer tab closed.

Focused regression commands:

```sh
outputs/dashboard-venv/bin/python -m pytest tests/test_fresh_eval.py -q
npm run test:browser -- tests/browser/fresh-eval.spec.js
```

Final reviewer-run focused verification: **2 Python tests passed** and **5 Chrome browser tests passed**. The reviewer's `git diff --check` also passed. The coordinator's full browser log was inspected separately: **16 passed** in `outputs/fresh-eval-browser-final.log`.

## Scope and limits

The review inspected event validation/storage, attempt merging, date attribution, default and eligibility baselines, overhead completeness, manual attribution, provider observation and fail-open paths, configuration validation, and the frontend's rendering and error flow. The coordinator separately owns remote CI, package installation, full-suite checks, and deterministic Hermes opening verification.

This is a bounded code and deterministic local review, not a claim of exhaustive security coverage or live-provider compatibility. Manual visual inspection used the existing demo preview; isolated test fixtures verify the changed source. No new signed-in Desktop trial, production telemetry capture, provider pricing research, paid call, subscription-cost measurement, or task-quality experiment was performed. The documented supported-host and telemetry boundaries still apply. No additional material finding remained in the inspected paths after the three fixes and passing focused checks.
