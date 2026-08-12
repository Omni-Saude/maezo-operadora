# Drills index

Quarterly operational drills for Maezo Healthcare Plan, supporting the knowledge-concentration
mitigation goal in the hardening program (PLANS.md §6/§0.8): each drill should be run by a
maintainer who has **not** most recently touched the relevant code, on a cadence the owning team
sets (quarterly is the working assumption; adjust per real incident/on-call rotation needs).

| Drill | Purpose |
|---|---|
| [`recovery-drill.md`](recovery-drill.md) | Restore the audit DB from a backup, verify hash-chain integrity, and cross-check against the engine's own process history. |
| [`key-rotation-drill.md`](key-rotation-drill.md) | Rotate the Agent Card HMAC signing key end-to-end in a dev environment. |

Each drill document states: purpose, preconditions, step-by-step commands (verified to exist
against the tree at the "Last updated" date in each document — re-verify before running an old
copy), expected evidence, abort criteria, and — where true — an explicit "requires infra not yet
present" marker rather than a step that only works in imagination.
