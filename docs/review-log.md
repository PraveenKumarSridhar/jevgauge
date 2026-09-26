# Initial publication review ledger

Atomic checkpoints for the first developer preview:

| Commit | Scope | Gate |
|---|---|---|
| `b746b44` | One-shot routing policy and regressions | Independent adversarial review; fixed candidate/effort compatibility, invalid confidence, deadline/concurrency bounds, and unsafe exception details |
| `78a1756` | Portable installer, packaging, README | 54 combined policy/installer tests; fresh-environment wheel smoke; unmanaged file and configuration preservation review |
| `2ba94d9` | Explicit generic Hermes integration | Red regressions for live/deferred manual switches and stale persisted fields, then 48 tests on clean pinned upstream; independent review approved |

The final presentation and CI checkpoint adds the generated concept artwork, user/developer guides, platform matrix, wheel-content check, and this evidence ledger. Independent review checked action pins, local documentation links, compatibility claims, and targeted private-path/credential scans before publication. The live trial is recorded in [verification](verification.md).

GitHub Actions results are the authority for the remote platform matrix. A local green result does not imply another platform passed. No review proves absence of all defects.
