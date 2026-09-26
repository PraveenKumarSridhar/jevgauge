# Accounting implementation and reproducible evidence

Owner: analytics implementation worker. This document is author evidence, not independent review. Reviewers should reproduce the tests and inspect the implementation.

## Public contract

`jevgauge.analytics.summarize(events, *, start=None, end=None, timezone='UTC', project=None)` returns the plan's dashboard schema. A `pricing=` argument permits deterministic isolated tests; the HTTP layer does not accept a pricing override. `jevgauge.demo.events()` returns fresh deterministic synthetic events without writing storage. Its fixed evidence period is September 2026. `as_of` in the API is the latest evidence timestamp, allowing demo date controls to anchor to evidence rather than the wall clock.

Additions to the shared schema:

- `project_options` lists all evidenced projects before filtering, with an explicit `Unattributed` option.
- `summary.billed_cost_usd` and `billed_requests` report known actual charge evidence separately from published-rate estimates.
- `summary.comparison_excluded_requests` is the number outside the shared comparison cohort.
- Conversations include `request_count`, `provider_difference_usd`, and `overhead_available`.
- Attempts include historical `default_model`, `policy_version`, `eligibility_source`, `provider_cost_usd`, `default_cost_usd`, `routed_cost_usd`, `median_cost_usd`, `strongest_cost_usd`, `provider_difference_usd`, `billed_usd`, `known_usage`, and `comparable`, in addition to sanitized provider evidence.
- Series include `conversation_count`, `request_count`, and known `overhead_usd`. Daily `net_savings_usd` remains null because the implementation does not invent a daily allocation for session overhead.
- With no comparable requests, comparison costs/differences are null. With no priced requests, observed provider cost is null. Explicit provider-reported zero usage is still priced zero.

## Calculation and provenance

Currency is USD only. Production estimates use `pricing.json`, version `2026-09-26.openai-text-standard.v1`. Rates were checked directly against these official model pages on 2026-09-26:

| Model | Input / million | Cached input / million | Output / million | Official source |
|---|---:|---:|---:|---|
| GPT-4.1 | $2.00 | $0.50 | $8.00 | [OpenAI model reference](https://developers.openai.com/api/docs/models/gpt-4.1) |
| GPT-4.1 mini | $0.40 | $0.10 | $1.60 | [OpenAI model reference](https://developers.openai.com/api/docs/models/gpt-4.1-mini) |
| GPT-4.1 nano | $0.10 | $0.025 | $0.40 | [OpenAI model reference](https://developers.openai.com/api/docs/models/gpt-4.1-nano) |

Only these exact family names and listed dated snapshots are priced. The current account catalog is not assumed to contain them. In particular, unknown GPT-6 identifiers stay unpriced. Published standard text token rates estimate API equivalence. They do not model subscription charges, service-tier discounts or surcharges, tool fees, regional pricing, multimodal pricing, or an account's actual bill. This table is a verified rate snapshot, not proof of the prices charged on historical request dates. Missing/corrupt pricing files preserve evidence and show estimates as unavailable.

For input I, cached input C (a subset of I), and output O (including reasoning tokens), cost is `((I-C)*input_rate + C*cached_rate + O*output_rate)/1,000,000`. No reasoning multiplier or added reasoning-token term exists. Missing cache counts mean cost cannot be priced even when input/output totals are known. Coverage reports this separately as `missing_usage_breakdown_requests`. `missing_usage_requests` counts unknown input/output totals, and `missing_price_requests` counts missing model rates for requests with known totals. These evidence gaps can overlap.

All distinct outgoing attempts count, including failed/rejected attempts with usage, retries, fallback and manually selected requests. The actual reported model takes precedence over the requested model when pricing; provider-reported effort is never inferred from the requested effort. A start and terminal event with the same conversation and attempt ID represent one request. Duplicate event IDs are ignored. Terminal evidence is not erased by a later start record.

Comparison cohort: known input/output usage, known cache breakdown, priced actual model, priced captured default, and a fully priced eligible set with recorded capability ranks and eligibility provenance. Manual requests are excluded. Every baseline uses exactly this cohort. Historical route snapshots are selected at each request's start timestamp, including routes before the selected date window. A later policy cannot rewrite an earlier request's default. Median baseline is the median of eligible model costs per attempt (for even sets, arithmetic mean of the two central costs). Strongest baseline uses the maximum recorded configured capability rank; tied models choose the lexicographically first identifier. Price does not determine capability. Negative provider differences are retained.

Measured overhead events are deduplicated by event ID and each distinct recorded charge counts once. The event producer must preserve the identity of a charge across retries; new IDs cannot establish that two records refer to one actual charge. Net savings are available only when every selected conversation has overhead evidence covering its visible routes and every selected request belongs to the comparison cohort. A partial provider-only cohort is never labeled net savings. Demo overhead is explicitly synthetic and never persisted.

Actual billed charges are separate nullable subtotals, with the number of requests carrying such evidence. They are not substituted into a published-rate counterfactual.

## Review queue and date semantics

Review count is a union of distinct conversation IDs. Reasons include provider failures, fallback, incomplete attempts, missing route/attempt/usage/price/baseline evidence, or a technical routing failure. Multiple flags in one conversation still count once. Low-confidence abstentions and disabled routing alone are excluded. A provider failure or missing evidence on their actual attempts still warrants review. No task-success, classifier-explanation, historical-confidence, or quality claim is inferred.

Date endpoints are inclusive calendar days in an explicit IANA zone, implemented as `[local start midnight, midnight after local end)`. A request belongs to its first observed attempt timestamp, even if completion falls after midnight. Prior route snapshots remain available for accounting. At most 3,660 days may be selected in a two-sided range; at most 50,000 input records may be summarized. Empty valid ranges are supported. One-sided windows remain bounded by the event-count limit. Project attribution is only taken from explicit telemetry. No project is inferred from text or filesystem paths.

The original DST test assumed Vancouver would fall back in November 2026. Installed IANA data reports a permanent UTC-7 offset then, so the test was corrected to America/Los_Angeles, where both 23-hour and 25-hour dates exist. This is a fixture correction, not a product exception to IANA data.

## Hand calculations

For one mini request with I=1,000,000, C=250,000, O=100,000 and 40,000 reasoning tokens already included in O:

- Actual-model estimate: `.75*.40 + .25*.10 + .10*1.60 = $0.485`.
- Captured GPT-4.1 default: `.75*2 + .25*.5 + .10*8 = $2.425`.
- Nano same usage: `.75*.10 + .25*.025 + .10*.40 = $0.12125`.
- Median of nano/mini/4.1: `$0.485`; highest configured tier: `$2.425`.
- Provider difference: `$1.94`; with one measured $0.01 overhead charge, net: `$1.93`.
- One rejected attempt plus one retry, each with that usage: provider `$0.970`, default `$4.850`, net after one overhead `$3.870`.
- Historical nano default with a mini actual request: difference `$0.12125 - $0.485 = -$0.36375`.
- One old 4.1-default request and one later nano-default request: default total `$2.425 + $0.12125 = $2.54625`.

The demo has 28 conversations, 65 outgoing requests, 3 review conversations, and 2 manual requests. Its request pattern has 70 potential requests, minus 1 disabled-conversation request and 4 confidence-abstention requests. Demo data deliberately includes unavailable usage, unknown price, a rejected attempt with fallback, and manual usage, so total demo net savings remain unavailable.

## TDD evidence

Run: `outputs/dashboard-venv/bin/python -m pytest tests/test_analytics.py -q`.

1. Requirement-derived accounting tests were written before `analytics.py`. The first two runs found an unrelated environment import problem (`jevgauge` absent in the old hidden venv), which is not counted as a valid red phase. With `PYTHONPATH=src`, collection failed specifically because `jevgauge.analytics` did not exist. After implementation, 14 of the initial 15 cases passed; the remaining Vancouver fixture issue is described above. Corrected jurisdiction: 15 passed.
2. Added privacy and technical-abstention regressions before their fixes. Actual red output: 2 failed, 18 passed. Nested arbitrary usage/eligibility fields leaked, and `selection_timeout` was not reviewed. Added nested allowlists and technical-abstention reason classification. Green: 20 passed.
3. Added override-event timing, historical displayed metadata, and missing-pricing-file regressions before their fixes. Actual red output: 3 failed, 21 passed. Applied time-indexed overrides, filtered metadata snapshot selection, and unavailable pricing fallback. Green: 24 passed.
4. Added a strongest-tier tie regression. Actual red output: expected $0.485, obtained $0.303125. The first implementation incorrectly averaged tied highest-tier costs, producing an imaginary model. It now selects a deterministic actual highest-rank model. Green: 25 passed.

The demo test initially used an incorrect hand count of 64 requests. Recounting `70-1-4` corrected it to 65. This is recorded as a test expectation correction, not a behavior fix.

No paid provider calls, external writes, active Hermes changes, or historical imports were used for accounting verification. Independent review is still required before describing this work as reviewed.

## Representative history check

A standalone 50,000-attempt summary completed in 0.944 seconds on the implementation machine, with process peak RSS 285,065,216 bytes (about 272 MiB, includes input, calculation and JSON response copies). No timing claim is made for other machines. The implementation rejects the 50,001st input record. Regression cases at 10,000 and 50,000 attempts assert counts, hand-calculated provider totals, missing-baseline nulls and a coarse 20-second ceiling to detect accidental quadratic work. This is bounded local processing, not a load-tested multi-user service.
