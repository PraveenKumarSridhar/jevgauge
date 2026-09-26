# V5 frontend implementation and browser evidence

The production asset package is `src/jevgauge/static/{index.html,styles.css,app.js}`. It uses the approved V5 DOM structure and CSS, with local SVG charts and icons. It has no CDN script, runtime JavaScript dependency, prompt fixture, or embedded analytics dataset. All displayed evidence and accounting come from `/api/dashboard`. Explicit server demo mode has a persistent DEMO label and synthetic-source disclosures.

## Intentional evidence-driven differences from the approved rendering

- Missing complete Jev overhead or a complete comparison cohort makes net savings unavailable. The graph displays cumulative **provider-only difference**, with the corresponding zero line and labels. Measured partial overhead is a **known subtotal**. No per-call charge is inferred.
- Captured historical defaults, current versioned official model rates, and eligibility come from the API. The demo's invented gpt-6 prices and prompt previews are not shipped as production data.
- Details shows the calculation, shared-cohort exclusions, baseline estimates, versioned pricing table, source links and applicable conditions. The compact cost row remains the only default-versus-routed row below the graph.
- Date filters use actual provider attempt start timestamps and inclusive local timezone boundaries. Earlier routing snapshots supply historical defaults. Route-only activity can also appear. No claim that request dates are conversation completion dates remains.
- Decisions shows captured reason codes, selection tiers, requested and reported model/effort, attempts, usage availability, eligibility and review reasons. It states that prompt text, generated reasoning and confidence history are unavailable.
- Model usage counts each conversation once by the final requested model, with absent requests in Unavailable; requests include each actual outgoing attempt. It does not invent effort shading without an aggregate effort breakdown. More than five model bins are shown as the four largest plus an explicit Other total, keeping the chart height fixed.
- Classification outcomes use disjoint routed, abstained, disabled and routing-failed categories. Why-defaults-retained drilldowns use captured reason codes. Provider failures appear separately in technical evidence.
- Configuration writes only supported settings using revision and token checks. It displays restart/new-conversation semantics, preserves drafts after failed writes, and disables enabling when the server reports that the owned plugin is absent. Unsupported policy editing, per-project policies, memory context, providers and bots remain labeled future capabilities.
- The original light-theme warning text failed WCAG contrast on its warning background. Its light color was darkened from `#b15d13` to `#945011`, retaining the warning hue. Both themes retain V5's palette and layout.

## Requirement-driven red/green evidence

Before assets existed, six Playwright scenarios were written for API rendering, four views, honest overhead, date dismissal and invalid filters, theme/details/help controls, chart stability, escaping and review/project drilldowns, config writes, and empty/error states. Running `npx playwright test` produced **six expected failures** because required product controls were absent. An earlier missing-browser launch was an environment setup failure and is not counted as a red phase. After implementation those six tests passed.

An additional keyboard/loading/Axe test found missing landmark coverage and insufficient light-theme warning contrast. The corrected landmarks and color pass Axe across Savings, Decisions, Reliability, Jev cost and Configuration. Tests exercise arrow-key tabs and explanation focus restoration. Browser console and page errors are asserted empty for normal flows.

`tests/browser/live-server.spec.js` starts a temporary isolated live server. Its Python fixture invokes the real router with deterministic classifier/catalog stubs, sends real attempt callbacks including a duplicate completion, persists evidence, serves the actual API and renders the browser. Its independent example checks default USD 0.0025, routed USD 0.0005 and difference USD 0.0020, with one request and no retained prompt or secret. No provider calls occur.

`tests/browser/real-server.spec.js` self-starts an isolated demo server by default, exercises actual API/config interactions, asserts values against API output, and captures desktop/narrow light/dark screenshots. It can target an already running explicit demo with `JEVGAUGE_TEST_URL`. All browser flows run without skipped tests by default.

Commands:

```sh
npm ci
npx playwright install chromium
PLAYWRIGHT_CHANNEL=chromium JEVGAUGE_PYTHON=python npm run test:browser
```

On this Mac, installed Google Chrome was used (`PLAYWRIGHT_CHANNEL=chrome`, the default). Local Python selection is `JEVGAUGE_PYTHON`, then `outputs/dashboard-venv/bin/python`, `.venv/bin/python`, or `python3`. The browser tests start isolated fixture servers and a static asset server, not the active Hermes profile.

Rendered evidence is written to ignored `outputs/browser/`: desktop light/dark at 1024 px and mobile light/dark at 352 px. The default test asserts no page overflow at 352 px. The fixed model chart remains 166 px tall with identical bounding boxes across conversation/request switches. Screenshot review confirms the original V5 proportions, typography, themes, four views and compact cost row are retained; values and evidence availability intentionally differ.

Final frontend run: `npm run test:browser`, **11 passed, 0 skipped (9.5 seconds)**. The self-started servers used the latest analytics contract, including distinct routing outcomes, missing cached-input breakdown coverage, and exclusive final-request conversation model attribution.

Cross-review found a separate opening failure: if the browser launcher raised while the dashboard was already running, the command falsely reported startup failure. `tests/test_dashboard_opening_review.py` reproduced that red result. A narrow `open_running()` helper now isolates browser-launch exceptions and returns the verified running URL. The review regression plus router-telemetry tests passed (**6 passed**). Documentation now states that the dashboard disable switch removes the loaded plugin command after restart.
