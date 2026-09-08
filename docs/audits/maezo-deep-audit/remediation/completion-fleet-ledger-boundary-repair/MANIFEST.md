# MANIFEST — completion-fleet-ledger-boundary-repair

- Generated UTC: `2026-09-08T21:09:13.624809+00:00`
- Worktree: `/Users/familia/code/maezo-completion-wt/fleet-ledger-boundary-repair`
- Branch: `completion/fleet-ledger-boundary-repair`
- Explicit base: `ac775f8dd86418402bc42bae1eb11756fe90c038`
- Functional candidate: `c97631d9865b87842c014037b3ac6a5c7025d26c`
- Functional tree: `da95cbc5c53949a0c48c56c1bf310f5a786ee859`
- Evidence packet commit: the commit containing this MANIFEST; it changes only this ignored
  evidence directory relative to the functional candidate.
- Python: `3.12.13`
- uv: `0.11.26 (396ef7ce4 2026-06-30 aarch64-apple-darwin)`
- Environment creation: `uv sync --offline --frozen --extra dev`
- Network/credentials: none

## Frozen artifacts

Paths are relative to
`docs/audits/maezo-deep-audit/remediation/completion-fleet-ledger-boundary-repair/`
unless shown as repository paths.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `REPORT.md` | 9039 | `dfb304f752ce09353789c44085bf790fcf10c8689d3ae6f7459157ea1a335a8d` |
| `raw/adversarial-controls-after.txt` | 2670 | `8f0646f9254282cde0a03489a03baab4336470c9b796dee3d14bfcd84c09c5f8` |
| `raw/adversarial-controls-before.txt` | 2639 | `f4932f3a8e17f3db87885442ec44627ae8b06f0971b5517a5bf77a6624cf5860` |
| `raw/adversarial-controls.py` | 4124 | `b1bdf14e5c9fdbe46b1615caa47bf13c088406e0fc31461929e340c71996024f` |
| `raw/cell-count.txt` | 453 | `f10ca4c581b25cd0ec22338fb07ffc4ea7e2111a43fe82ae7082e7300c88a018` |
| `raw/focused-tests.txt` | 740 | `dd1f7d7c64f0ca1c53180c2fdfaad275c3b18e3b174dcecb86f69278ea70a651` |
| `raw/git.txt` | 1377 | `b5a93fc24894cf8635b3791593d6bb91beee0bc6590ba00e6be8ea0a4f92c430` |
| `raw/identity-preservation.txt` | 1037 | `31636c1e9befafaab6c0cd823c5661d90fb5cfb8df9ffc50a6827a60bade3a5d` |
| `raw/ledger-preservation.txt` | 1034 | `6a4715d3c42d9b6eefba850d3e687ec8665240598764175571816643e29d59d3` |
| `raw/recipe-hash.txt` | 572 | `0be28cd8d5c4ebe44d15403433b7c7cb1bf3103e63a47d2fa9b722ea3acbaa2c` |
| `raw/stacked-launcher-relative.txt` | 2080 | `6c0ee36cd861573e32c58316d057f2d26e8aecf1dec6c3b8dc66a8f68a9bd0fe` |
| `raw/stacked-launcher-resolved-symlink.txt` | 8241 | `c687b9bad30b9d85f9c6af0ee25aaada30a205ad979dc724361a748b8e70e550` |
| `raw/stacked-ledger.txt` | 5549 | `5e2fad2684c462193c850fe0cb880c1514de80788f48d4b40bdf5d0cf61a75c0` |
| `raw/static-checks.txt` | 1396 | `a37213c605cab279f93fa35ac89e62fe16585a0a528efc20cc1640be43399fab` |
| `scripts/ci/check_evidence_ledger_hashes.py` | 74325 | `8ac5085b95b6d4e3cbaee4decfa1315306ae3747eb77a54a57024096c38bfcac` |
| `scripts/ci/generate_release_floor.py` | 29145 | `c6ff686d85a5c133e097600854fb105e915dff2b163e6327cacdbd4965f5520b` |
| `tests/unit/ci/test_check_evidence_ledger_supersession.py` | 20767 | `41e8177624b4883379d158c4928cb74b120aa04de137fff996724a915e572542` |
| `docs/evidence-ledger.md` | 1625194 | `1663c58dfd21aa991585599e89b15ae5da1fbbba0e002d1abd73bb504f251918` |

`MANIFEST.md` deliberately does not hash itself.

## Exact verification commands and outcomes

| Evidence | Exact argv | Revision | Outcome |
|---|---|---|---|
| RED controls | `[".venv/bin/python","docs/audits/maezo-deep-audit/remediation/completion-fleet-ledger-boundary-repair/raw/adversarial-controls.py"]` | `ac775f8dd86418402bc42bae1eb11756fe90c038` | Script rc0; all four defects reproduced. |
| Delta controls | same argv | worktree source after fix, base `ac775...` | Expected repairs observed; script rc1 at the now-rejected DSN before its deadline block. |
| Focused tests | `[".venv/bin/python","-m","pytest","tests/unit/ci/test_check_evidence_ledger_hashes.py","tests/unit/ci/test_check_evidence_ledger_supersession.py","tests/unit/ci/test_generate_release_floor.py","-q","-p","no:cacheprovider"]` | source `07ebb91d398c76b7e747a824c35122eb81a876ab` plus subsequently committed ledger-only change | rc0, 162 passed in 58.03s. |
| Declared recipe | `["/Users/familia/code/maezo-completion-wt/fleet-ledger-boundary-repair/.venv/bin/python","-m","pytest","tests/unit/ci/test_check_evidence_ledger_supersession.py","-v","--tb=no","-p","no:cacheprovider"]` | `07ebb91d398c76b7e747a824c35122eb81a876ab` | rc0, 45 result lines, declared hash exact. |
| Static | Ruff check, Ruff format check, mypy strict, `git diff --check` with argv in raw file | `07ebb91d398c76b7e747a824c35122eb81a876ab` | all rc0. |
| Stacked gate | `["/Users/familia/code/maezo-completion-wt/fleet-ledger-boundary-repair/.venv/bin/python","scripts/ci/check_evidence_ledger_hashes.py","--base","3125fea992e607836a6adccbfb8593a1cc062a46"]` | `c97631d9865b87842c014037b3ac6a5c7025d26c` | rc0, 21/21 proofs, 20 selected rows, 0 legacy. |
| Cell count | `["git","diff","--unified=0","ac775f8dd86418402bc42bae1eb11756fe90c038","HEAD","--","docs/evidence-ledger.md"]` | `c97631d9865b87842c014037b3ac6a5c7025d26c` | rc0, one added row, eight cells, zero removed. |

All raw files preserve argv, cwd, start/end UTC, revision and rc. The two
`stacked-launcher-*.txt` files are disclosed launcher failures and are not counted as
successful validation. No full-unit, global `--all`, Docker, PostgreSQL, CIB7, live
integration, CI remote, push or merge was run.
