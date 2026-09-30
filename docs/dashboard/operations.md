# Dashboard operation and provenance

The dashboard is a loopback-only process. Install the package into a persistent Python environment before installing the plugin; the installer records that interpreter for the Hermes opening command. Do not delete that environment while the plugin uses it.

```sh
python -m jevgauge dashboard --home /path/to/hermes-home --open
python -m jevgauge dashboard --home /path/to/hermes-home --demo --port 8766 --open
```

The first command shows captured evidence. The second explicitly selects synthetic demo events, does not read or write the live event database, and keeps configuration edits in memory. Demo data is not historical activity. The service binds only to `127.0.0.1`; there is no public bind option.

In an installed, compatible Hermes instance, use `/jev-dashboard`. The plugin uses its supported command registration API, starts the dashboard with the interpreter recorded during installation, checks the loopback service identity, then opens the system browser. A conflicting port or failed startup returns a diagnostic instead of a success claim. See integration verification for controlled test evidence. Automatic ordinary-composer routing still requires the native integration.

## Configuration

The gear exposes `enabled`, `effort_mode`, `selection_timeout`, and the three ordered `tier_models` lists. These are the established settings under `plugins.entries.jev-router.settings`. Close Desktop before saving and restart afterward. Changes apply to new conversations; existing bindings stay intact. Account catalog intersection still determines eligibility, and a configured model identifier is not entitlement evidence.

Writes validate the complete submitted change, use the existing installer lock, check a revision hash, preserve unrelated YAML values, and atomically replace the file. YAML comments and formatting are normalized as with the existing installer. The browser never receives other configuration values or credentials. Unsupported memory context, per-project policies, other provider families and bot routing remain labeled as future capabilities.

## Storage and privacy

New telemetry is stored in `<home>/jevgauge/events.sqlite3`. No historical import invents missing evidence. No prompt, response, attachment, API key, raw exception, or arbitrary provider payload is retained. Prompt inspection is unavailable. Project attribution is unavailable unless explicitly supplied as evidence; the current host integration does not infer project names from private paths.

The endpoint exposes at most 50,000 events as a complete cohort, rejecting larger histories rather than silently discarding their defaults. Requests, bodies, concurrent handlers and database lock waits are bounded. Archive older data offline when this deliberate small-local-dashboard bound is reached. Corrupt or inaccessible storage is an error state, never a successful empty total. Telemetry and server failure do not prevent routing.

Security controls include exact loopback Host checking, same-origin reads, no CORS, per-process CSRF token plus Origin for writes, resource allowlists, no arbitrary file endpoint, no HTTP event ingestion, no request-path logging, a restrictive content security policy and escaped evidence rendering. Any process running as the same OS user already has access to that user's local files; this service is not an OS-user isolation boundary.

## Economic interpretation

All money is USD. Published API-equivalent estimates, actual reported billed charges, same-usage counterfactual comparisons and synthetic demo assumptions are separate. Unknown rates are unpriced, not free. The checked-in price version states exact model IDs, units, verification date, official sources and conditions. Current configured gpt-6 candidates are not assigned guessed prices.

Input includes cached input; output includes reasoning tokens. No reasoning-effort price multipliers are used. Every known outgoing physical attempt is considered, including retries and rejections with reported usage. Manual attempts contribute to observed usage/cost but are excluded from router comparison claims. The captured historical default and eligible configured capability ordering define comparisons; today's default never replaces historical evidence.

Request-count coverage does not bound missing dollars. API-equivalent price differences do not establish lower subscription charges, causal savings, preserved quality or task success. Unmeasured Jev overhead prevents a net-savings claim. No arbitrary demo per-call cost is used in production.

Disabling routing through the dashboard uses the established plugin disable mechanism. After restarting Hermes, the unloaded plugin no longer registers `/jev-dashboard`. Start the dashboard with the CLI, or use `jevgauge enable --home <home> --hermes-repo <compatible-checkout>` and restart Hermes to restore the command. Legacy owned installations must complete the documented uninstall/install upgrade before the dashboard can enable them.
