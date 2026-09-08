# Evidence manifest

Tested implementation commit: `7201a588d56cd7deb73425dfefb3b22c4d4cac15`

Tested tree: `998a482a00430232eb1024e80102c61ab563a8f4`

Working directory for every recorded command:
`/Users/familia/code/maezo-completion-wt/fleet-ledger-ci-repair`

| Artifact | SHA-256 | Contents |
|---|---|---|
| `raw/ledger-gate.txt` | `ea35169f2236e584d18ed9dd6989d91eb7e661fa3eb18abc38fb0c3ca0ecd513` | Exact argv/rc and complete FLC decisive output from the 20/20 gate |
| `raw/focused-tests.txt` | `502413160ea2a9c2e767c46d6503b7eb8da09c7fb7697ddd388a02f430801785` | Exact argv/rc and 136-pass summary |
| `raw/static-checks.txt` | `cb8d80f4a904d381e3cc0358f307c90200e990696b4ef7480edcaae1fce57a1e` | Ruff, format, strict mypy and cell-count argv/rc/output |
| `raw/ledger-preservation.txt` | `862699754ce8259c483ed93ab961a59cbf7c478c1ba5cc21f640ebf7b17c3ea0` | Physical/data-row denominators and literal-prefix proof |

Tracked implementation hashes at the tested revision:

| Path | SHA-256 |
|---|---|
| `scripts/ci/check_evidence_ledger_hashes.py` | `801cc7cc6cff6d133ed823cc48c173dd44ebd888559ebba382a87b53f8dd70b0` |
| `scripts/ci/generate_release_floor.py` | `c6ff686d85a5c133e097600854fb105e915dff2b163e6327cacdbd4965f5520b` |
| `tests/unit/ci/test_check_evidence_ledger_supersession.py` | `8bbc6c346cb377c98b27b9175cfb0c3ad453b9726f1fef36115e58c419b504e1` |
| `docs/evidence-ledger.md` | `fee0ef6a8dafcd0b2de1203bff1ce4d2e38f647e806ae63c79f134d707e74f64` |

The evidence-only commit that adds this directory does not alter the tested implementation tree
paths above. Recompute every manifest digest with `sha256sum <path>` before relying on a copied
artifact.
