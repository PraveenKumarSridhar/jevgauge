# Independent accounting review

The telemetry author independently reviewed the analytics author's calculation and aggregation code, pricing snapshot, synthetic demo generator, existing accounting tests and frontend metric labels. The reviewer wrote separate hand-calculated cases in `tests/test_accounting_review.py`, reproduced failures before fixing them, and asked the analytics author to cross-review the fixes. This review is separate from the telemetry author's own instrumentation validation.

## Findings and resolution

| Finding | Reproduction | Resolution |
|---|---|---|
| Missing cache breakdown mislabeled missing price, despite a known model rate | Exact priced model with known input/output totals and null cached input produced `missing_price_requests=1` | Separate `missing_usage_breakdown_requests` and review reason; missing price now means missing model rate. Frontend displays both categories. |
| Corrupt rate table could produce negative cost, NaN, a KeyError, or integer-overflow exception | Four valid-JSON rate mutations: negative, incomplete, NaN, and enormous integer | Validate all bundled rates before use; invalid pricing becomes unavailable. Per-rate validation also protects injected test tables. |
| Healthy disabled or confidence-only requests entered review because no baseline was intentionally collected | A disabled route with an empty eligible catalog and a completed, priced default request produced a review item | Baseline-review expectation now excludes disabled and confidence-only routing. Missing usage, unknown price and provider failures still create review items. |
| Model chart did not partition conversations as labeled and conflated requested and reported models | One conversation used two requested models with differing provider aliases; a second had no requests | Conversations count once at latest requested model; no-request or missing-request-model evidence occupies Unavailable. Request bars count every outgoing requested-model attempt. Reported model still takes precedence for cost estimates. |

Initial independent tests: **3 passed, 4 failed**, reproducing cache classification and three corrupt-price failures. The chart case separately failed before its correction. Cross-review then found the enormous-integer edge case; its failing test preceded the overflow guard. A later healthy-disabled-request case reproduced an unwanted baseline review item before its correction; a confidence-only variant checks the same rule. Final accounting command:

```sh
python -m pytest tests/test_accounting_review.py tests/test_analytics.py
```

Result: **39 passed** (11 independent reviewer cases, including four pricing mutations, and 28 original cases). The original suite includes 10,000- and 50,000-event bounds and DST spring/fall cases. The reviewer cases add the following independently computed checks.

For 800,000 input tokens including 300,000 cached tokens and 200,000 output tokens including 170,000 reasoning tokens, mini costs **$0.55**, default 4.1 costs **$2.75**, and nano costs **$0.1375**. Reasoning is already part of output. Two comparable physical attempts, including a billed rejection and its retry, yield **$1.10 routed**, **$5.50 default**, and **$4.40 provider difference**. A manual attempt adds **$0.55** to observed provider cost but contributes nothing to router comparisons. Unknown-price and missing-usage attempts remain excluded, not free. A distinct-conversation review queue counts this overlapping set of issues once. Reported actual charges are separate from the API estimate.

A nano model given the highest configured capability rank wins the strongest baseline despite being cheapest. The two-model median uses the arithmetic mean per request, yielding **$2.8875** across the two comparable attempts. One observed $3 overhead charge replayed with the same ID is counted once; a one-request comparison becomes **negative $0.80 net**. A partial comparison cohort leaves net unavailable even if the overhead total is known.

The reviewer verified that requests spanning midnight belong to their captured start date, an inclusive local fall-back day includes 25 hours, and future route metadata cannot replace the historical default in a past-date filter.

## Pricing provenance

The reviewer reopened the official model pages and checked each standard text rate in `pricing.json`: [GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1) is $2 input, $0.50 cached input and $8 output; [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini) is $0.40, $0.10 and $1.60; [GPT-4.1 nano](https://developers.openai.com/api/docs/models/gpt-4.1-nano) is $0.10, $0.025 and $0.40, all USD per million tokens. These match the versioned table.

No price was invented for the router's current GPT-6 model slugs. They remain unpriced until a verified rate is added. The current snapshot is an API-equivalent scenario, not evidence of a historic invoice or reduced subscription charge. Demo usage and overhead remain explicitly synthetic. Live classifier overhead is unavailable; no per-call charge is inferred. Quality impact is not evaluated.

## Remaining verification boundary

The calculations and denominators above are verified in deterministic tests. Frontend browser tests and the final restarted preview must verify the updated breakdown labels and model denominator against the new API response. The final acceptance matrix records that separate result. This accounting review does not assert a paid-provider trial, actual bill savings, completeness of absent telemetry, or future model pricing.
