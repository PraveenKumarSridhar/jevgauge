# Contributing

Start with an issue describing a reproducible behavior and supported environment. Keep routing policy in the plugin and generic lifecycle behavior in the Hermes integration. Do not introduce request-only rewrites, private runtime patches, or per-turn rerouting.

For changes: add a regression test, observe it fail, implement the smallest fix, run the affected suite, then review adversarially before committing. Review malformed API responses, late timeout completion, manual ownership, restart/resume, cross-profile isolation, and safe filesystem behavior. Keep commits scoped and reviewable.

Install `.[test]`, run `python -m pytest`, and build the wheel with `python -m build`. Reinstall after CLI changes to test the installed package. See docs/verification.md for Hermes lifecycle and live verification.

Do not commit keys, Hermes homes, raw private logs, or account exports. Use synthetic prompts and temporary homes. Windows/Linux/macOS unit coverage must not be described as live provider coverage.
