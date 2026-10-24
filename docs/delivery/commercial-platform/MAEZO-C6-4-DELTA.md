# Maezo C6-4 - escolhas atuais de entrada formal

2026-10-22. Delta final limitado, sob release ROOT. Os handoffs e freezes C6-2/C6-3 permanecem inalterados. **CANDIDATO / REVISÃO INDEPENDENTE PENDENTE / REAL NOT_RUN / PRODUCTION NO_GO.**

Contrato C6-4 exato: `d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea`, 320163 bytes; proveniência ROOT informa AMH commit `b3ab95a07a016526aa5dc09ead22aafccd630b45`, blob `08e4d70dae5605d9000ad23db7df355eaa5fc940`, path `schemas/proposals/maezo/commercial-v1/shared-contract-c6-4.proposed.v1.json`. O autor conferiu bytes/proveniência locais. Mirror `contract-c6-4.json`/`PROVENANCE-C6-4.json` é TEST-only e ROOT-owned; runtime continua recebendo bytes por injeção confiável, sem abrir artifacts TEST ou modificar pin publicado.

O resultado atual de `journey.get` passa a exigir `plan_choices`, limitado a 100 objetos fechados `{option_ref,catalog_ref,catalog_version,product_ref,label}`. O codec rejeita repetição de `option_ref` mesmo quando catálogo, versão, produto ou label divergem. A autoridade de resposta atual, já composta após IO, cobre a escolha exata para owner/operadora/tenant/finalidade e catálogo ativo qualificado. Um resultado sem jornadas pessoais pode oferecer escolhas atuais somente sob essa autoridade. Listar escolhas não cria prospect, quote, aceite, contrato ou efeito financeiro.

Não há conversão de `optionRef` público, criação de identificadores ou fallback de fonte. O shape público camelCase não substitui a escolha privada. Sanitização recursiva transforma `plan_choices` em `[]`; o schema de recovery também exige vazio e recusa a variante histórica não sanitizada. Nenhum bootstrap de escolhas acontece via recovery. Não foram criados comandos, rotas, scopes, endpoints, URLs ou capabilities: permanecem **42 comandos / 41 recuperáveis** e os limites do pacote S03–S10 / NR-02–06,09–10 / CAP-03–16,24–29, ADR-0037 XRD-03/04/05/09. Requisitos/subchecks predecessores permanecem.

## Evidência e custódia

Antes dos dois novos guards, com somente pin C6-4 e fixture exigida atualizados: **5 FAIL / 8 PASS**, 106 casos C6 anteriores deselected. Falhas: quatro duplicidades conflitantes e choices preservadas por sanitização antiga. Os casos de fonte/owner divergentes já eram recusados pela prova exata atual. Após o delta: **231 UNIT PASS / zero skips**, 33.23s; 218 predecessores + 13 casos C6-4. Ruff check/format PASS nos dez paths Python; mypy strict PASS nos seis módulos. Casos e assertivas anteriores foram preservados.

Lane privada Python/TMP/basetemp/mypy cache, `--noconftest`, paths explícitos, cache pytest desabilitado. Sem engine, Local, cloud, portas, build, Git, app.py ou arquivo histórico restrito. Source providers continuam ausentes por default.

ROOT confirmou o ignore da área `docs/delivery/commercial-platform/repair-preimages.local/`. Toda a evidência privada própria das três entregas Maezo foi copiada para os subdiretórios `maezo-c6-2-author/`, `maezo-c6-3-author/` e `maezo-c6-4-author/`, com comparação byte/SHA-256, diretórios 0700 e arquivos 0600. O recibo de custódia fica no mesmo diretório ignorado. Por orientação posterior de ROOT, originais privados permanecem até seu readback, sem exclusão antecipada. Não há contexto da máquina ou dumps privados neste documento público.

## Freeze

Manifesto delta: `de26211b3bb5577bef49d703a3a317f54814c356a6017df65be0ea6dea7aa93f`. Dependências observadas: `d6fa902f6a89e8a6f57727a53184a17e0c9970bebdd1d06ca1e37117d39054a0`.

| Path | SHA-256 |
|---|---|
| `src/maezo/ports/commercial.py` | `01e9db9618eb90389710ccff99389a63ccdfb465224e9cbd604a2af852ed8d9f` |
| `src/maezo/adapters/amh/commercial.py` | `6f048f638ae08b4b43b74edecf84c526ee431a1a16a1600097069494ea380ca0` |
| `tests/unit/test_commercial_http.py` | `1d0d1e2bac80c3d2dc8153a1d4cc675fa3f5fe01e0b01ef808e79433cc6d782a` |
| `tests/unit/test_commercial_c6.py` | `878dec53b9ab16b31cc2871ad186a6fc0cf30f23f4ff7b2aa117587e7c911c58` |

Lane liberada. ROOT integra, verifica custódia e renova reviews independentes/CI do HEAD exato. Nenhuma aprovação do próprio autor ou aceitação operacional/humana foi emitida. Não há trabalho funcional adicional nesta lane.
