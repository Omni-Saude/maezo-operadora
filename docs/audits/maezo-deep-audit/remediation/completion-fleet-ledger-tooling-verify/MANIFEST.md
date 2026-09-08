# Manifesto da verificação independente

Candidato: `ac775f8dd86418402bc42bae1eb11756fe90c038`.
Tree: `4090bc7449f1b10dfda2c3397aa2cef5077fa0e0`.
Baseline: `7b34cf44ae9e531092f92994a8347fbe0116784c`.
Gerado UTC: 2026-09-08T20:21:38.224867+00:00.

Worktree: `/Users/familia/code/maezo-completion-wt/fleet-ledger-tooling-verify`.
Os logs TXT incluem argv JSON, cwd, UTC e rc; o diff é o patch exato dos quatro caminhos de implementação/ledger contra o baseline.

| Artefato | SHA-256 | Bytes |
|---|---|---|
| `REPORT.md` | `47a779af86745386de7f61d0124db3fbbc6224550962c3b5be070aa59324b4a4` | 7417 |
| `raw/adversarial-controls.py` | `b1bdf14e5c9fdbe46b1615caa47bf13c088406e0fc31461929e340c71996024f` | 4124 |
| `raw/adversarial-controls.txt` | `b86a6e508bfb915c80ea9f46e2e005fe9f3af3fb55a7f1e6ba36544a1b7cece5` | 2706 |
| `raw/candidate.diff` | `12639779655889ccbfb2f8a9c97af567ce2c35c54e73624db0d892e3b72ffd45` | 63029 |
| `raw/cell-count.txt` | `67866eacbcf4d8b5c6ce17f4479a6c1e6143d829fba3024260a0cb3ed375bd34` | 453 |
| `raw/focused-tests.txt` | `6618bd9e943b74c5329007648ea578a47a55ff36bc1b4cc2e6802ab6346bd879` | 629 |
| `raw/identity-preservation-static.txt` | `2b7c8debd2f5a247eff3aed7c10d835a37f499c17c7a0de0b8b2cf431693ce53` | 3896 |
| `raw/stacked-ledger.txt` | `cfb82dcdd99b5c43765c6e0b0d534d03876f8af630047c7dc61bc9b1521036e3` | 5210 |

A preparação do ambiente foi `uv sync --frozen --extra dev` (rc0, CPython 3.12.13, venv próprio).
O script de controles usa apenas repositórios temporários próprios; nenhuma edição de produto/ledger do candidato.
Somente arquivos desta pasta são novos no checkout do verificador.
