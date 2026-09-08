# Manifesto do delta independente FLTV-01–04

**Veredito: APPROVE qualificado ao tooling revisado.**

- Candidato: `4ea318f28923c73bf5aacdc01eab647398c2aaee`.
- Tree: `43172b5c3cf21b95bfe3b49cc7a9a4681b1b5644`.
- Baseline: `ac775f8dd86418402bc42bae1eb11756fe90c038`.
- Worktree: `/Users/familia/code/maezo-completion-wt/fleet-ledger-tooling-delta`.
- Gerado UTC: `2026-09-08T21:19:54.946610+00:00`.
- Python: CPython 3.12.13, venv próprio via `uv sync --offline --frozen --extra dev` (rc0).

Os logs TXT preservam argv JSON, cwd, revisão, start/end UTC e rc.
O script original é imutável e termina com rc1 esperado na rejeição do DSN; o deadline foi verificado em controle próprio separado.
O diff contém somente os caminhos funcionais e o ledger contra ac775f8d.

| Artefato | Bytes | SHA-256 |
|---|---:|---|
| `REPORT.md` | 6571 | `7e108ac336ffcc7de2eb5668cf9b3f26e78414c0414c2effb2db50b51d9e7b13` |
| `raw/candidate.diff` | 18889 | `2aec5bf25d1035a708f6570641a4fb41d0713f5f9e59c30dc24cbc6066171f23` |
| `raw/delta-controls.py` | 4685 | `82fc23065ea9f68eb3e0472f13ba434b96e6bb3fddfc5b10e4d6c0f94fa5f67d` |
| `raw/delta-controls.txt` | 3519 | `66061b02a0aab5e152503873d8ee258ed0ec99c2df828d06f2e880867145b54a` |
| `raw/focused-tests.txt` | 785 | `b71f4a6ceb32ecaab1e7def0b352c1c186773952a6ad7d0dfe635bea1f6fc4ec` |
| `raw/identity-preservation-static.txt` | 8871 | `51c4944489c0b80cb8ff7eb7f2222fe9ddac8a73b52cce8d0776f629546a44d7` |
| `raw/original-controls.py` | 4124 | `b1bdf14e5c9fdbe46b1615caa47bf13c088406e0fc31461929e340c71996024f` |
| `raw/original-controls.txt` | 2797 | `c0bb0beecc01f0b6208662f114076862f78b47a5352b9bf4645e002f473a22f8` |
| `raw/stacked-ledger.txt` | 5546 | `f9ef673d25d09c511c9bdbbbb288397d9782e51b98466cff1b8a0adf926d3800` |

O relatório original REVISE e seu manifesto não foram alterados.
Este pacote adiciona somente arquivos neste subdiretório de delta. Não houve alteração de produto ou ledger pelo verificador.
O MANIFEST não inclui hash de si próprio. CI remoto e gates completos no novo SHA permanecem responsabilidade do ROOT.
