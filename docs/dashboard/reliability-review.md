# Independent QA and reliability review

Reviewer: analytics worker, reassigned after completing the accounting implementation. This review independently inspected the coordinator's HTTP/configuration/CLI work, the telemetry worker's storage and host integration, and the frontend worker's browser implementation. It does not count the reviewer's original accounting tests as independent accounting verification; that is recorded in the separate accounting review.

## Findings and resolution

| Severity | Finding and reproduction | Resolution and verification |
|---|---|---|
| P1 | The physical provider call imported its telemetry observer unconditionally. A failed observer import could abort routing before the provider call. | Telemetry author guarded only the observer import and directly called the provider on import failure, preserving provider exceptions and avoiding duplicate calls. A dedicated host regression first failed, then passed. Independently rerun below. |
| P2 | Config read parsed YAML, then independently read bytes for its revision. A write between these reads paired old settings with a new revision, allowing stale UI changes to pass optimistic concurrency checks. | CLI parser accepts an explicit byte snapshot. Dashboard reads at most 1 MiB plus one byte, parses and hashes the same bytes, and updates that snapshot without a second read. Reviewer regression reproduced stale 5-second settings with the revision for 2-second settings; now passes. |
| P2 | Dashboard enabled routing in an empty or unowned home, reporting success without an installed plugin. Legacy two-file installations also appeared dashboard-capable. | Enable requires verified ownership and the complete current plugin file set. API exposes installation capability and the UI disables unsupported enable. A stale YAML enable flag cannot claim an absent plugin is effectively enabled. Legacy manifests remain accepted by migration/uninstall. Missing, unowned, legacy, and stale-enabled regressions reproduced red, then passed. |
| P2 | `GET http://[ HTTP/1.1` raised an uncaught URL parsing error, closed the socket without an HTTP error, and logged a traceback containing filesystem paths. | Coordinator moved URL parsing into handled validation. Raw socket reproduction now receives safe HTTP 400. |
| P2 | A Latin-1 non-ASCII CSRF token caused `compare_digest(str,str)` to raise outside the handler's exception protection. | Coordinator made the token check safe for arbitrary header text. Raw socket regression changed from empty response plus traceback to HTTP 403. |
| P2 | A small YAML document with nested shared aliases expanded exponentially while cloning. A depth limit did not bound total allocations. | Shared alias-detaching clone now has a 100,000-node budget. The small exponential document originally completed an unnecessary large expansion instead of rejecting; it now raises a safe bounded error. Normal alias preservation tests remain green. |
| P2 | Sixteen simultaneous maximum-size summaries could multiply the measured roughly 272 MiB per request to several GiB. | Coordinator added one nonblocking history-processing slot. A second dashboard aggregation receives 503 while health/config/static remain available. The review test blocks the first aggregation, confirms health 200 and second dashboard 503, then proves the slot is released. |
| P2 | Browser accessibility check reported serious warning-badge color contrast failure. | Frontend adjusted the warning foreground and corrected landmark semantics. Independent Chrome rerun passes all four views and configuration with zero Axe violations in the tested light theme. |
| P2 | Model-use footer claimed one final requested model per conversation while the aggregation could count a conversation in multiple actual-model bars. | Accounting reviewer added an independent regression and corrected exclusive final-request attribution, with an explicit Unavailable bucket for conversations without captured attempts. Frontend labels the basis. Actual cost pricing still uses provider-reported model where present. |
| P2 | Classification outcome bars mixed mutually exclusive route outcomes with overlapping provider failures. | Coordinator independently raised this finding. Analytics separates routing failures and disabled routes; clean abstention excludes technical failures. Provider failures remain separate technical evidence. Demo now has 26 routed, 1 clean abstention, 1 disabled, 0 failed route classifications, plus 1 provider rejection. |

No unresolved material finding from this bounded review remained after the listed fixes. This is not a claim that every possible fault or host/provider environment was tested.

## Reproduced checks

Reviewer-specific tests are in `tests/test_dashboard_reliability_review.py`. They also independently confirm duplicate Host/Origin headers are refused and malformed persisted telemetry cannot return arbitrary private payload text.

Command:

```sh
outputs/dashboard-venv/bin/python -m pytest \
  tests/test_dashboard_reliability_review.py tests/test_dashboard_config.py \
  tests/test_dashboard_server.py tests/test_installer.py \
  tests/test_router_telemetry.py tests/test_telemetry.py -q
```

Result after the final config fixes: **96 passed in 8.05 seconds**.

The initial browser reproduction against an actual fresh local demo service yielded 8 passing tests and one contrast failure. After the frontend fix:

```sh
npm run test:browser
```

Result: **10 passed in 8.1 seconds**, including actual live route callback → SQLite → HTTP → rendered dashboard, the actual demo HTTP server, supported configuration state, theme controls, filters, date dismissal paths, review drilldown, stored-text escaping, keyboard tab navigation, loading/error states, and Axe checks. The live browser test verifies independently hand-calculated $0.0005 provider estimate, $0.0025 captured-default comparison, and $0.002 provider difference.

Screenshots were captured independently for the approved V5 HTML and implementation at 1024 pixels, and for the implementation at 352 pixels. The reviewer visually inspected `outputs/browser/review-reference-v5.png`, `review-implementation-v5.png`, and `review-implementation-mobile.png`. Layout, typography, green/amber palette, four tabs, compact comparison row, Details placement, and responsive stacking follow V5. Displayed data and metric wording intentionally follow available evidence, so they differ from the approved artifact's fabricated activity and overhead. Screenshots are verification artifacts, not production fixtures. Later model-label and reason-chart corrections are covered by browser tests; these earlier review screenshots are not claimed to show those final text corrections.

## Operational boundaries

- Loopback Host/Origin validation, write token protection, bounded headers/body/filter/history processing, explicit error states, private storage creation, and no external ingestion endpoint were inspected.
- The installed plugin launches the dashboard interpreter captured at installation. Its health check verifies live mode and home identity before claiming opening. Stock Hermes still needs the explicit pinned development integration; no upstream-release claim is made.
- Disabling the plugin unloads its commands after restart. `/jev-dashboard` then requires re-enabling the installed plugin, or the dashboard can be started directly by CLI. The registration unit test does not prove commands survive unloading.
- Production storage and demo source are separate. No real home, paid provider call, API credential, prompt retention, or historical import was required for this review.
- Browser launch dispatch in the controlled Hermes integration uses a browser-launcher stub that records the URL. Real browser rendering is tested separately with Chrome. The combination does not constitute a live signed-in Desktop/provider trial.
- Current local package/runtime tests are not evidence that remote CI or every supported OS has passed. Release and deployment remain outside scope.

## Independent host reproduction and cross-inspection

On the isolated pinned Hermes checkout, the reviewer reran the canonical `scripts/run_tests.sh tests/agent/test_provider_attempt_evidence.py` using the existing Hermes test interpreter: **5 passed, 0 failed in 1.6 seconds**. This includes observer-import failure, retry usage, missing optional breakdowns, stream rejection usage, and observer failure isolation.

The reviewer separately ran `scripts/verify_hermes_dashboard.py --hermes-repo <isolated-patched-checkout> --dashboard-python <dashboard-interpreter>`. It completed with: `PASS: installed discovery -> route -> physical usage -> SQLite -> cold resume -> RPC slash command -> child HTTP -> browser handoff`. The script owns its temporary home and child process and does not modify the active Hermes profile.

The accounting reviewer's narrow fixes were cross-inspected: missing cache breakdown is distinct from absent model prices, negative/nonfinite/incomplete rate rows become unavailable, and a huge JSON integer cannot escape the malformed-rate fallback through numeric overflow. The reviewer first identified the integer-overflow edge, the accounting reviewer reproduced and fixed it, and the final independent accounting-review test file was rerun successfully. Historical actual-model pricing remains independent of the UI's requested-model attribution.
