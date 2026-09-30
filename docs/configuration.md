# Configuration

Settings live under `plugins.entries.jev-router.settings`. Merge these into existing configuration; retain other plugins and settings.

| Setting | Default | Meaning |
|---|---|---|
| enabled | false in manifest, installer sets true | Route new eligible chats |
| effort_mode | auto | `manual` retains the profile/session effort while Jev may select model |
| selection_timeout | 5.0 | Overall caller wait, 0.01 through 10 seconds |
| api_url | https://api.typesafe.ai/v1/systemone | HTTPS Jev endpoint |
| jev_model | jev-latest | Jev decision model |
| tier_models | See below | Ordered candidates, filtered by account catalog and effort support |

The current policy defaults are:

```yaml
tier_models:
  economical: [gpt-6-luna, gpt-5.6-luna]
  balanced: [gpt-6-sol, gpt-5.6-sol]
  strongest: [gpt-6-astra, gpt-6-sol, gpt-5.6-sol]
```

These are candidate identifiers, not a guarantee your account has them. Replace them with identifiers supported by your Hermes checkout and returned by your authenticated account catalog. If the selected tier has no compatible candidate, the original conversation defaults are retained. The first compatible candidate wins, including when manual effort excludes an earlier candidate.

Both typed confidence values must be finite numbers within [0.55, 1]. Strings, booleans, malformed choices, and missing answers are rejected. This threshold is not a correctness probability.

`TYPESAFE_API_KEY` is read through Hermes secret scope, not config.yaml. Never include credentials in endpoint URLs. Endpoints must use HTTPS; redirects are not automatically followed.

Changes to policy or enabled state require restarting Desktop. Saved conversations retain their binding. The panel's Minimize/Details preference is separate and applies immediately.

## Hermes compatibility and update checks

Release refs, test runtimes, required APIs, patch order, fixtures and validation gates live in `integration/compatibility.json`, separately from routing policy. See [update compatibility](update-compatibility.md). The checker never switches the live installation; its retention setting records the intended future activation policy.
