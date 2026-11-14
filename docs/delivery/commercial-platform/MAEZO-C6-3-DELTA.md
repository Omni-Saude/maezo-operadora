# Maezo C6-3 - continuidade qualificada da jornada

2026-10-22. Addendum separado à implementação C6-2. O documento `MAEZO-C6-IMPLEMENTATION.md` e seu freeze `4240e8b4ccbb60511043d2bc61dcd41231925fbdfbf8884dc6fbd014da211ffa` permanecem inalterados. Autor limitado: `amh_c6_financial_preparation_repair`; integração, artifacts TEST, Git, CI e app.py pertencem a ROOT. **CANDIDATO / REVIEW INDEPENDENTE PENDENTE / REAL NOT_RUN / PRODUCTION NO_GO.**

## Fonte e escopo

Release ROOT C6-3: SHA-256 `02b7f28df14b1e1b658e097b504f39d6e36dc715999929485d9006907fed400f`, 317594 bytes. Proveniência importada por ROOT informa AMH commit `7cc29049bb52c7e15a577966e44bee2c46e31aaa`, path `schemas/proposals/maezo/commercial-v1/shared-contract-c6-3.proposed.v1.json`, blob `2a6aebb10b018ada3aaa97657e119848f2c44fde`. O autor conferiu bytes/proveniência locais; não operou Git/remoto. Runtime exige esses bytes por injeção confiável, sem ler o mirror TEST. `contract-c6-3.json`/`PROVENANCE-C6-3.json` permanecem ROOT-owned; artifacts anteriores e pins publicados não foram escritos.

Somente três definições upstream mudaram: `CustomerJourneySnapshot`, `RecoveredCustomerJourneySnapshot` e `JourneyQuoteContext`. A continuidade acrescenta profile/linkage/aceite comercial e referências de leitura limitadas, além do quote completo anulável. **42 comandos / 41 recuperáveis**, rotas, scopes, URLs, capabilities e regras financeiras permanecem os mesmos. Rastreabilidade: REQ-C5-05 / retomada S07–S10, NR-05–06/09–10, CAP-07–16/24–29 e ADR-0037 XRD-03/04/05/09. Os requisitos e subchecks predecessores permanecem, sem redução do denominador ou conclusão de aceite operacional.

## Delta executável

O codec valida o schema C6-3 e aplica checks puros em snapshots frescos e recuperados:

- Quote completo não nulo deve coincidir exatamente com `quote_ref`, `source_revision`, `terms_digest` e `valid_until` do contexto.
- PartyLinkage deve coincidir com prospect/account/party/payer do snapshot.
- Aceite comercial não nulo exige linkage correspondente e um único contexto de quote com referência/revisão/digest exatos. `CustomerAcceptanceRead` OP03 continua projeção nominal separada e nunca serve de fallback para `acceptance_receipt_ref`.
- Quando há contrato emitido, seus payer/linkage e receipt de aceite/quote/digest são comparados aos fatos de continuidade presentes. Um aceite declined não sustenta um receipt issued correspondente.

A documentação de `CommercialResultAuthority` explicita que o proof já exigido após IO cobre **todos** os novos fatos e cada referência financeira sob jornada, contrato, payer, account, tenant e finalidade originais. A igualdade do digest não cria autorização: o provider precisa qualificar independentemente a resposta exata. Não existe conversão de profile a partir de account/prospect, preenchimento inferido de campos null ou descoberta que execute pagamento/aceite/emissão. Null/empty significa ausência de fato utilizável de continuidade, não inexistência de obrigações.

Nenhum método ou campo novo de proof foi necessário. A composição corrente já verifica digest do resultado completo, comando/scope, grant e currentness; testes com fonte sintética independente confirmam que profile ou referências de receivable/payment/mandate/financial-action alterados são recusados após a única chamada original. Isso não prova o pertencimento real de referências na ausência do provider, que continua indisponível por default.

Sanitização de documentos/canais/capabilities e requalificação de recovery permanecem intactas. Quote histórico sem fato completo pode ser exibido sem renovar validade; um quote completo expirado não passa como continuidade fresca. Recovery pode conservar a história sanitizada, sem conferir autoridade para nova ação. Nenhuma URL ou nova forma de capability foi introduzida.

Write set efetivamente alterado: `src/maezo/adapters/amh/commercial.py`, documentação de `src/maezo/ports/commercial.py`, pin/proveniência de fixture em `tests/unit/test_commercial_http.py`, fixture anulável e casos adicionais em `tests/unit/test_commercial_c6.py`, e este addendum. Gateway, admission, HTTP source, router e handoff C6-2 foram comparados byte a byte e permaneceram idênticos. Nenhuma expansão para a entrada de catálogo de uma jornada nova foi executada.

## Evidência

O primeiro lote executou contra o adapter com **somente pin C6-3 atualizado**, antes dos novos checks semânticos: **28 FAIL / 9 PASS**, 61 casos C6 anteriores deselected. Os 28 negativos demonstraram que shape/schema sozinho aceitava divergências de quote/linkage/aceite. A verificação existente do resultado exato já recusava referências financeiras/profile alterados. Após os checks: **37 PASS**, mesmos 61 deselected. O lote final acrescentou oito casos de contrato/recovery/duplicidade.

Resultado final: **218 UNIT PASS, zero skips**, 30.71s; 173 predecessores + 45 casos C6-3. Ruff check e format passaram nos dez paths Python do pacote; mypy strict `--follow-imports=silent` passou nos seis módulos. Casos originais foram preservados; mudanças de fixture mantêm o schema corrente exato e os campos obrigatórios explicitamente null/vazios, sem inventar fatos.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. .venv-commercial/bin/python -m pytest --noconftest -p no:cacheprovider --basetemp <private-owned-temp>/pytest-final -q tests/unit/test_commercial_gateway.py tests/unit/test_commercial_http.py tests/unit/test_commercial_api.py tests/unit/test_commercial_c5.py tests/unit/test_commercial_recovery.py tests/unit/test_commercial_disclosure.py tests/unit/test_commercial_c6.py --tb=short
```

Lane Python privada, TMP/basetemp/cache mypy privados, paths explícitos, sem conftest/discovery ampla. Preimages, before/focused/final outputs, diffs, reconstrução determinística do adapter pin-only e manifests permanecem em custódia privada ROOT. Não houve Git, app.py, pin publicado, AMH, engine, cloud, Local, portas/build ou arquivo histórico restrito. O teste restrito não foi lido/importado/executado/copied/hashed.

## Freeze e próximo gate

Manifesto delta: `2df496e8e322dbec3379a5ce82798dcada24dc816fd0c96ac4b93efa265966bb`. Dependências observadas: `ee8b9b5f2fd3b086b34d3effc22541a67abd5804a452566955a8185ae35f7753`.

| Path | SHA-256 |
|---|---|
| `src/maezo/ports/commercial.py` | `26dd3862af06d0fa7e2b1b4c9539f3c15d4520a0f0f5eed6b2640f0e9f73ff06` |
| `src/maezo/adapters/amh/commercial.py` | `fb57087d9905b9bd53927a797b9df3f8e97ffd0a2bb47d7b9b874586678f102f` |
| `tests/unit/test_commercial_http.py` | `edd10a95dae2e0d1aae6c95715af554904d390b97a4fd2f4240c4a817e658a78` |
| `tests/unit/test_commercial_c6.py` | `4b4cd6b440f13755f8fac6a7c018f9905f59cedad3ce58bc0c132a10573ad1ed` |

Lane devolvida. ROOT integra o candidato e renova a revisão independente/freeze conjunto/CI exato. Fontes e grants reais permanecem não instalados; o autor não emite self-approval, aceite humano ou conclusão operacional.
