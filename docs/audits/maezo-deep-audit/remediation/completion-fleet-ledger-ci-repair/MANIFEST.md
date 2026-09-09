# Evidence manifest

Tested implementation commit: `3009bbe5490e28b89b606dc6e656d6b2b3ad98d8`

Tested tree: `2512bfb7bea1748a72ca675941ff09ca4c87232a`

Working directory for every recorded command:
`/Users/familia/code/maezo-completion-wt/fleet-ledger-ci-repair`

| Artifact | SHA-256 | Contents |
|---|---|---|
| `raw/ledger-gate.txt` | `2aa2dd2af3b07d76009acb377acd4e084b9993a02a61097a2de014dc30de9d3b` | Exact argv/rc and complete 20/20 gate output |
| `raw/focused-tests.txt` | `d5b44d1c8ddcb4ef4068f6967934e9692201e6c8a02a0b69b1e4dc448f2ab93a` | Exact argv/rc and 139-pass focused-suite output |
| `raw/static-checks.txt` | `f363adcee000774b7f02ff2be5f69dc69bfc79bc1eb9e941a51bddac58f22901` | Ruff, format, strict mypy and cell-count argv/rc/output |
| `raw/ledger-preservation.txt` | `5e13b73bee8623f7eeaec69f3e2d872775cc9296a564d8b0e6f9390edeccd236` | Physical/data-row denominators and literal-prefix proof |

Tracked implementation hashes at the tested revision:

| Path | SHA-256 |
|---|---|
| `scripts/ci/check_evidence_ledger_hashes.py` | `7f8428e54c23f1c0a8984909dd61f3011c213d013c4f1d88ac0274654a0cbf50` |
| `scripts/ci/generate_release_floor.py` | `c6ff686d85a5c133e097600854fb105e915dff2b163e6327cacdbd4965f5520b` |
| `tests/unit/ci/test_check_evidence_ledger_supersession.py` | `4204f8f7d0304fbc094cbd4981422fd3b56ba2fa203fd35f46e3e3e922652277` |
| `docs/evidence-ledger.md` | `23bee9636421beaf5e240a128533d8cbf611bf4ab6d68611b0a7742722c766bf` |

The evidence-only commit that adds this directory does not alter the tested implementation paths
above. Recompute every manifest digest with `sha256sum <path>` before relying on a copied artifact.
