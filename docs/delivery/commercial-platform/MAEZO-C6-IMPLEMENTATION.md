# Maezo C6 - comandos privados e leitura pública qualificada

2026-10-22. Autor `amh_c6_financial_preparation_repair`, agora no papel limitado de implementação Maezo; ROOT integra Git, CI, app factory e recursos. Baseline informado por ROOT: `dfac0714`. Trabalho realizado somente no checkout de recuperação. Nenhuma operação Git ou interação com o checkout canônico. O teste histórico restrito não foi lido, procurado em conteúdo, importado, executado, copiado ou incluído em manifests.

**IMPLEMENTAÇÃO CANDIDATA / REVISÃO INDEPENDENTE PENDENTE / REAL NOT_RUN / PRODUCTION NO_GO.** Este documento não aprova o próprio código. As 35 necessidades e 122 subchecks do pacote permanecem. Rastreabilidade: C6/C6-1/C6-2, REQ-C5-01/02/04/05, FD-C5-01, S03–S10 / NR-02–06,09–10 / CAP-03–16,24–29; ADR-0037 XRD-03/04/05/09. O transporte não cria regra de preço, elegibilidade, cobertura, obrigação ou decisão humana.

## Contrato exato e custódia

A implementação começou após release C6-1 `fe02aa3c347a03f5a38797b17a2a3450b7dd111d105b2d14e155209a002e4685` e terminou no sucessor **C6-2**, após os três reviews de proposta e liberação ROOT:

- SHA-256: `19a88be247a71c6bea617ae7287a03d3ac210dba96a5c0a52507f981a10db799`, 312316 bytes.
- Commit candidato AMH informado na proveniência ROOT: `5583d539d16e8f7024d1fd514479f6e0545a54bf`.
- Path AMH: `schemas/proposals/maezo/commercial-v1/shared-contract-c6-2.proposed.v1.json`.
- Blob informado na proveniência ROOT: `4fd3ec11a0e40d39a51130476c132702d4c38cef`.
- Mirror imutável **TEST ONLY**, importado por ROOT: `tests/candidate_artifacts/commercial/contract-c6-2.json` e `PROVENANCE-C6-2.json`.

O autor verificou os bytes locais e a proveniência, sem realizar verificação Git/remota. Runtime recebe bytes por composição confiável e exige o digest exato; nunca abre o artefato TEST, nunca faz fallback para fixture e não modifica pin publicado. Artifacts C0–C6-1 permanecem históricos. C6-2 conserva **42 comandos privados / 41 recuperáveis**, sem recursão; três slots de conexão continuam obrigatórios, mas permitem null explícito. Readiness usa a união qualificada dos três métodos, não a contagem de três contas PSP.

## Composição entregue

Os oito comandos adicionais passam pela rota POST existente. O codec valida schema fechado, pareamento exato de resultados/recovery, seletores, associações estruturadas e referências de recurso. O gateway exige scopes específicos para work/journey/support, além da admissão atual anterior ao body. Work exige staff e finalidade `sales_operations`; journey/support exigem vínculo atual da jornada. Um purpose OP antigo, uma identidade vendor ou simples nome de comando não concede os novos grants.

`PaymentChoiceSelection` é derivado exclusivamente do payload fechado. `PaymentChoiceAuthority` exige a escolha exata, recurso receivable/contract, provider/método/operação/uso, revisão, vinculação de conta, uso contratual e currentness, vinculados aos digests de comando e scope. A preparação é revalidada antes do efeito; o transporte verifica deadlines novamente depois de seu último IO de autoridade anterior ao POST. Recorrência continua opcional Stripe sob fonte atual; Asaas/InfinitePay não ganham mandato por aceitação de cartão. O schema mantém escolha solicitada e método observado separados, com receipt e evidência de pagamento.

Resultados de work/journey/support e capabilities qualificadas precisam de `CommercialResultAuthority` após o IO da fonte, vinculada ao resultado exato, comando, scope e grant atual. O port exige verificação real de pertencimento/payer/representação, filas/diretório, vocabulário/labels/tópicos de suporte, descoberta e capabilities; implementação ausente recusa. A chamada está composta em gateway → adapter → executor HTTP → source authority, sem inventar endpoint adicional. Essa autoridade permanece uma dependência externa não instalada, como as fontes de admissão anteriores. Digest é vinculação, não autorização por si.

Jornadas preservam aceite, contrato emitido, matrícula e cobertura como projeções nominais distintas. `CommercialDeliveryOrigins` é independente das origens PSP e da seleção de termos; verifica documento administrativo/canal externo pelo recurso, referência e revisão da fonte. Todos os deadlines de URLs/capabilities são reavaliados por uma passagem pura depois da última consulta de origem. Contexto histórico de quote expirado pode permanecer na jornada sem renovar sua validade. O account autenticado do scope permanece distinto de referências account/payer/party ausentes em fases posteriores da fonte; não foi inventada audiência prospect nem identidade anônima.

Recovery remove recursivamente hosted actions, documentos, `external_handoff` e substitui capabilities por `refresh_required`, escolhas vazias/default null, preservando escopo do recurso. O schema fechado rejeita a variante não sanitizada de jornada/suporte; contexto recuperado não é nova apresentação nem grant. A autoridade atual do comando original é revalidada na fronteira. Não há journal/replay cache Maezo nem retransmissão local de writes; a fonte AMH mantém dedupe e consulta original.

A leitura pública é uma cadeia separada:

```text
create_app(public_catalog_gateway=...)
  → GET /api/v1/sales/public/catalog
  → PublicCatalogGateway
  → HttpPublicCatalogSource
  → GET /interop/sales/v1/public/catalog
```

`compose_public_catalog_gateway` constrói o caller concreto somente com `SalesCallerVerifier`, `PublicCatalogScopeAuthority`, `AmhPublicCatalogAuthority` e origem HTTPS fixa. Sem esses providers retorna gateway fechado. `PublicCatalogScope` contém somente identidade machine, tenant/PJ/operadora, publicação/lifecycle/source binding, purpose `sales_public_catalog`, scope `interop/sales.catalog.public.read` e validade; não reutiliza `CommercialScope` de cliente.

Os dois saltos aceitam/enviam somente `Authorization` e `X-Commercial-Connection` como metadados machine, sem browser cookie/query/body. O GET Maezo recusa framing de body, query, duplicidade de metadados e overrides de autoridade; **nunca consome o stream de body**, mesmo com machine admitida, porque publicação pública não autoriza entrada de sujeito. Cookies não são interpretados nem encaminhados. Client próprio usa origem fixa, sem env proxies, redirects ou retries, com limite de bytes/tempo. O contexto AMH pode resolver a conta apenas por sua própria autenticação/configuração; o connection header não cria autoridade.

Resposta positiva é exatamente `sales-catalog-read.proposed.v3` usable, preservando preço numérico conhecido/desconhecido, sem cálculo local, e associações. A autoridade final verifica a publicação/audiência/semântica/revisões contra digest do DTO exato; depois dela ocorrem apenas checks puros de escopo/proof/DTO/deadlines. Publicação retirada, expiry, revisões/valores malformados, tipo anterior, fonte indisponível ou prova divergente resultam no mesmo HTTP503 no-store: `sales-catalog.proposed.v1 / unavailable / source_unavailable`. Essa wire fonte fica entre servidores; Sales aplica seu tradutor para a apresentação v4. Nenhum grant de preferências ou intake anônimo foi criado.

## Hook app.py de ROOT

ROOT implementou, fora do write set do autor:

- import de `PublicCatalogGateway`;
- parâmetro opcional `public_catalog_gateway` em `create_app` e `app.state.public_catalog_gateway`;
- ramo `is_public_catalog_request(request)` / `public_catalog_error()` antes do ramo comercial privado em `deployment_host`.

`public_catalog_error()` não recebe argumentos e devolve JSONResponse 503 categórica. O GET está no `commercial_router` existente, sem segundo mount. O teste final usa **create_app real**, store local explicitamente sintético e OIDC transport que falha se consultado, confirmando GET positivo, host errado e query recusados antes de novo IO AMH. Nenhuma identidade humana foi semeada ou usada para admitir o catálogo público.

## Validação e preservação

Lane exclusiva autorizada: Python 3.12 privado `.venv-commercial`, `--noconftest`, paths explícitos, `-p no:cacheprovider`, `PYTHONDONTWRITEBYTECODE=1`, TMP/basetemp e mypy cache privados. Sem descoberta ampla, Local, cloud, portas, build, engine ou operação REAL.

- **173 UNIT PASS, zero skips**, 22.64s: 112 casos predecessores + 61 novos casos C6.
- Ruff check **PASS** nos seis módulos e quatro testes escritos; format **PASS**, dez arquivos.
- Mypy strict `--follow-imports=silent`: **PASS**, seis módulos próprios.
- `test_commercial_c5.py`, `test_commercial_recovery.py`, `test_commercial_disclosure.py`: bytes idênticos aos preimages.
- Assertivas existentes em fixtures alteradas conservadas: gateway 27→27, HTTP 40→40, API 5→5. Mudanças de fixture restringem-se à proveniência C6-2, campos obrigatórios/proof sintéticos e contagem da rota POST privada separada do GET novo.

Casos novos cobrem 42/41, recusa de bytes divergentes, seleção atual por recurso, pares observado/receipt, capability de recurso errado/default inválido/revisão temporal, recovery aninhado, origens distintas, expiração durante último lookup, grants de jornada/work, resposta exata vinculada à autoridade após fonte, GET com caller concreto, abstinência de body, no-store, host/query/overrides/cookies, recusa de fonte/withdrawal/versão/valor/expiry e nulabilidade dos slots sem remover as chaves.

Execuções intermediárias ficaram preservadas: 112 PASS predecessores; 42 PASS/4 FAIL e 45 PASS/1 FAIL no primeiro lote novo (nomes/shape de fixtures de jornada/recovery corrigidos para o contrato real); 51 PASS/6 FAIL na expansão C6-2 (fixtures de estado/revisão de work/support corrigidas); 172 PASS antes do último teste de stream. Nenhuma assertiva foi removida para converter falha em PASS. Mypy inicial também passou; Ruff inicial registrou ordenação/format e simplificações antes da correção.

Comando final:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. .venv-commercial/bin/python -m pytest --noconftest -p no:cacheprovider --basetemp <private-owned-temp>/pytest-final -q tests/unit/test_commercial_gateway.py tests/unit/test_commercial_http.py tests/unit/test_commercial_api.py tests/unit/test_commercial_c5.py tests/unit/test_commercial_recovery.py tests/unit/test_commercial_disclosure.py tests/unit/test_commercial_c6.py
```

Preimages, manifests, diffs e saídas permanecem em custódia privada de ROOT; caminho exato comunicado no handoff de coordenação, sem dump da máquina no Git. O autor não alterou PLANS/CHECKPOINT/RUNBOOK, fonte financeira AMH, arquivos Helena ou recursos alheios.

## Freeze próprio e dependências

Manifesto próprio: `4240e8b4ccbb60511043d2bc61dcd41231925fbdfbf8884dc6fbd014da211ffa`. Manifesto das dependências observadas (app.py ROOT, artifact/provenance C6-2 e testes predecessores): `a6a8edc16b86a5c0681e30d30efa241df57142192476dc00316c35222d1f3b91`.

| Path | SHA-256 |
|---|---|
| `src/maezo/ports/commercial.py` | `1c002f2a5b6d5dade41f07ce0b9178da869c70ed2df512b3ded6641148adc9e1` |
| `src/maezo/gateway/commercial.py` | `5ddcbe8ddc29438e0f51d02a9d6e74d67044b44701ee912b701f8c289f487c28` |
| `src/maezo/gateway/commercial_admission.py` | `09b1aee43f5042f8d6635630c0fc5eaa4c64210579dd315e8638b93c28badd13` |
| `src/maezo/adapters/amh/commercial.py` | `5dc5bb6e2bb52f938b87837c131648551dbf27b3ff1bd685de5fdca17dd3c94c` |
| `src/maezo/adapters/amh/commercial_http.py` | `dd62500e1a77dd7f93647f237f454e4c031394fbf10b533c0d71bd3032032416` |
| `src/maezo/portal/api/commercial.py` | `6cf6daae32a7626d9a2c0568a7e99d3e8173b3162fed122b1990dd500cc9c6fc` |
| `tests/unit/test_commercial_gateway.py` | `1eb4f9bdf3e4d1f8a6e33f2c72e4c1a81b008216023bf4140eb6bdd5c77a3466` |
| `tests/unit/test_commercial_http.py` | `36fd57f2791216ea16b47310b6aecc5f2da359d5039a20c709cd8f3bbd70b331` |
| `tests/unit/test_commercial_api.py` | `1889eb24fcd531b5afaabf626df8ea9b9a820ab4cdd35f1afc7f1c112c482187` |
| `tests/unit/test_commercial_c6.py` | `2f17c08ee18512807a6ab0cdccbdeefd8d8794bdc0a1d89a7d112b3f53b49ded` |

Lane devolvida após esse freeze. Próximos gates: revisão original independente no candidato/dependências exatos, integração cross-repo, CI do HEAD final e manutenção por ROOT. Homologação/source publication, autoridades reais de identidade/pagamento/documentos/suporte, engine, validação humana e produção continuam separados e não executados. Os providers de autoridade permanecem ausentes por default; PASS UNIT não os cria.
