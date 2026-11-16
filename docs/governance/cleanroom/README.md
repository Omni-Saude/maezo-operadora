# Registros de sala limpa (EP-CORE-09b)

Um arquivo `CLEANROOM-OMNI-V-<nn>.json` por EXTRACT re-derivado pelo caminho B de CORE-09, no formato de
`cleanroom-record.schema.json`. Hoje **não há registro**: nenhum módulo do Maezo traz o marcador `derived-from: OMNI-V-<nn>`.

Regras verificadas por `scripts/docs/cleanroom_records.py` (guarda: `tests/governance/test_cleanroom_records.py`):

| Regra | Recusa |
|---|---|
| Toda fonte da spec é `Omni-Saude/maezo-operadora` ou `Omni-Saude/amh-data-platform` e não menciona o omni | `CleanroomRefusal.FORBIDDEN_SOURCE` |
| `spec_author` ≠ `implementer` | `CleanroomRefusal.ACTOR_NOT_SEPARATED` |
| Todo módulo com marcador `derived-from: OMNI-V-<nn>` está em `modules` do registro daquele EXTRACT | `CleanroomRefusal.RECORD_MISSING` |
| Registro completo, atestação `implementer_did_not_open_omni_files: true` assinada pelo implementador, nome do arquivo = `extract` | `CleanroomRefusal.RECORD_MALFORMED` |

O marcador vale em linha própria, com prefixo de comentário opcional (`#`, `//`, `*`, `<!--`, `--`, `;`). Nomes de pessoas: só
primeiro nome ou papel.
