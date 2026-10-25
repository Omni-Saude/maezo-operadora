# Registro de congelamento — Contrato comercial C6-4 (`C6-4-frozen@3c619d91`)

**Data:** 2026-10-23 · **Main:** `3c619d919738878331c21dadda4b112993f430ef` · **Merge #907:** `7b2dfb86f2680f0287bc37e3b4b31c23ca4bc98b` (2026-10-23) · **Decisão:** D-15 do dono (`docs/plans/maezo-completion-program.md` §Decisões — nada re-decidido aqui) · **Item do plano:** BRDG-3.2 (W3) · **Schema do registro:** `omni-commercial-contract-freeze.v1` · **Versão citável:** **`C6-4-frozen@3c619d91`**

Este registro congela a linhagem do contrato compartilhado C6-4 (`omni-commercial.proposed.v1`) no repositório maezo-operadora, logo pós-merge do PR #907, conforme D-15. Ele **não aprova, não ratifica e não habilita nada** — separa quem GERA de quem APROVA; todo bloqueio de decisão permanece com humanos nomeados.

**DECLARAÇÃO EXPLÍCITA — congelamento ≠ admissão runtime.** O `runtime_admission` permanece **UNBOUND**. Este registro não cria comandos, rotas, scopes, endpoints, URLs, capabilities ou providers de fonte. As admissões A1–A10 do portal externo `omni_sales_platform` continuam **pendentes** (10 dossiês `status: pending` em `docs/delivery/admissions/A-INDEX.json`, schema `omni-sales-admissions-index.v1`; o admission layer `src/server/admission/sales.ts` do portal retorna `blocked/qualification_required` e nenhum arquivo `A<n>-ADMISSION-RECORD.json` existe). Erguer A1–A10 **incrementalmente, com teste negativo por admissão**, é o item BRDG-3.3 (D-16) — fora do escopo deste congelamento; a resolução do GAP-CATALOG pela autoridade em main é pré-condição desse item e não é afetada aqui.

## 1. Contrato congelado

| Campo | Valor |
|---|---|
| `contract_id` | `omni-commercial.proposed.v1` |
| Revisão | C6-4 |
| **SHA-256** | `d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea` |
| Bytes | 320.163 |
| Comandos | 42 (41 recuperáveis, 3 GETs públicos) |
| Espelho local (TEST-only) | `tests/candidate_artifacts/commercial/contract-c6-4.json` |
| Recomputação no ato | `shasum -a 256` = **bate** com o `contract_sha256` dos pins existentes (`AMH-C6-4-MAIN-ORIGIN.json:12`, `commercial-c6-4.source.lock.json:12`, `SOURCE-FREEZE-C6-4.json:6`) |

## 2. Pin AMH (fonte da verdade canônica)

| Campo | Valor |
|---|---|
| Repositório de origem | `Omni-Saude/amh-data-platform` |
| `source_commit` | `a4f2ac237f02ea54873e245de3dceac922b8891d` (PR [AMH #282](https://github.com/Omni-Saude/amh-data-platform/pull/282)) |
| `source_tree` | `11ee7dd7367e608284cc9e00492c91d5bd421e35` |
| `source_blob` | `08e4d70dae5605d9000ad23db7df355eaa5fc940` |
| `source_path` | `schemas/proposals/maezo/commercial-v1/shared-contract-c6-4.proposed.v1.json` |
| Classificação | `SOURCE_PUBLISHED_PROPOSED_EXTENSION_NOT_RUNTIME_ADMISSION` |
| Pin de fonte | `config/integrations/amh/commercial-c6-4.source.lock.json` |
| Lock canônico histórico | `config/integrations/amh/contracts.lock.json` — **intocado** (`098ff7b5…`) |
| Import do candidato | commit AMH `b3ab95a07a016526aa5dc09ead22aafccd630b45` (`PROVENANCE-C6-4.json`) |

Publicação da extensão proposta no Git do AMH não significa homologação Tasy, habilitação PSP nem admissão operacional (ver `AMH-C6-4-MAIN-ORIGIN.md`). O canonical editor é somente o AMH; sucessor exige nova custódia e gates independentes.

## 3. Linhagem completa em main `3c619d91`

Todos os SHA-256 abaixo foram recomputados neste worktree (checkout limpo de `main` = `3c619d91`) no ato do congelamento.

| Caminho | SHA-256 | Papel |
|---|---|---|
| `tests/candidate_artifacts/commercial/contract-c6-4.json` | `d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea` | Espelho TEST-only do contrato (conformação UNIT do codec; NOT_PUBLISHED; nunca carregado em runtime) |
| `tests/candidate_artifacts/commercial/PROVENANCE-C6-4.json` | `227737840da47183ce72bb5113b7a821f252a77f276f04ba57cb998b928fa4f2` | Custódia imutável do candidato |
| `config/integrations/amh/commercial-c6-4.source.lock.json` | `e969e5ff32532f30ae3d4bae60bd26cb08ad6093b19e946ba379cdbae7764228` | Pin de fonte C6-4 |
| `config/integrations/amh/contracts.lock.json` | `098ff7b5077792ee5bc8126cf01fec6b9de89bd3f78d5bc0f63f8b5f73f6e55a` | Lock canônico histórico AMH1.x (preservado) |
| `docs/delivery/commercial-platform/AMH-C6-4-MAIN-ORIGIN.json` | `6db52d995f753dda6515dbe5e31abbdbf01cdf0bc87df225684d74c15a73b61d` | Custódia da origem main |
| `docs/delivery/commercial-platform/AMH-C6-4-MAIN-ORIGIN.md` | `d4e2216f9760cf89a940736ae64a74becd25e06bfb9862747b0521733efa90d8` | Narrativa da origem main |
| `docs/delivery/commercial-platform/MAEZO-C6-4-DELTA.md` | `de51ac5d173fcb45909e0ff39d8a5c1ce1e8fbf3a1e50637a4fe49c94a3aee2e` | Delta formal C6-4 (`plan_choices`; limites S03–S10 / NR-02–06,09–10 / CAP-03–16,24–29; 231 UNIT) |
| `src/maezo/ports/commercial.py` | `01e9db9618eb90389710ccff99389a63ccdfb465224e9cbd604a2af852ed8d9f` | Porta comercial (contrato interno tipado) |
| `src/maezo/adapters/amh/commercial.py` | `6f048f638ae08b4b43b74edecf84c526ee431a1a16a1600097069494ea380ca0` | Codec/consumidor AMH (confere digest dos bytes injetados; não lê a cópia TEST) |
| `src/maezo/adapters/amh/commercial_http.py` | `b151ccd4947f89a3e0b263727a21782b1bbce12f0c7889430cc688de3bbaa943` | Transporte HTTP do consumidor |
| `src/maezo/gateway/commercial.py` | `952bd672ec3f411897970cd5804549b231df3e8dabd050e8a86848ae9fa10877` | Gateway comercial |
| `src/maezo/gateway/commercial_admission.py` | `09b1aee43f5042f8d6635630c0fc5eaa4c64210579dd315e8638b93c28badd13` | Admissão runtime (UNBOUND por default) |
| `src/maezo/portal/api/commercial.py` | `6cf6daae32a7626d9a2c0568a7e99d3e8173b3162fed122b1990dd500cc9c6fc` | API do portal comercial |

**Nota de divergência (nada oculto):** os cinco freezes `SOURCE-FREEZE-C6-4*.json` foram registrados em checkpoints **pré-#907**. Em main `3c619d91`, exatamente **dois consumidores evoluíram no merge #907**: `src/maezo/adapters/amh/commercial_http.py` (pré-#907 `dd62500e1a77dd7f93647f237f454e4c031394fbf10b533c0d71bd3032032416` → hoje `b151ccd4…`) e `src/maezo/gateway/commercial.py` (pré-#907 `5ddcbe8ddc29438e0f51d02a9d6e74d67044b44701ee912b701f8c289f487c28` → hoje `952bd672…`). O SHA-256 do contrato e os dois pins (`commercial-c6-4.source.lock.json`, `contracts.lock.json`) permanecem **idênticos** aos registrados — a divergência é só nos consumidores, não no contrato.

**Nota de divergência R2 (BRDG-3.3, PR #959 — nada oculto):** em `main` pós-#959, `src/maezo/gateway/commercial_admission.py` evoluiu (SHA-256 `09b1aee43f5042f8d6635630c0fc5eaa4c64210579dd315e8638b93c28badd13` → `4b4c39b8457e156ddfae27e85c8eb798a27d64079c5f03bcb2f799332b48edca`) para o mecanismo da admissão A1 (`compose_public_catalog_gateway` passa a exigir `source_admission` registrada). O SHA-256 do contrato e os demais 12 arquivos da seção 3 permanecem identes aos registrados; `runtime_admission` permanece UNBOUND; o pin espelhado `PORTAL-MIRROR-PIN-C6-4.json` permanece o snapshot válido de `3c619d91` (o `contract_pin` `d1d796b8…` não muda) e não é reemitido por este pacote.

**Nota de divergência R3 (BRDG-3.3 — admissão A2 — nada oculto):** em `main` pós-admissão A2, `src/maezo/gateway/commercial_admission.py` evolui novamente (SHA-256 `4b4c39b8457e156ddfae27e85c8eb798a27d64079c5f03bcb2f799332b48edca` → `f66017f1b0c553d7d0e654faed69de10a85e508ffe17b59c5c518d55e4005543`) para o mecanismo da admissão A2 (`compose_commercial_gateway` passa a exigir a admissão de sessão registrada; novo módulo `src/maezo/gateway/commercial_sales_admission.py`, arquivo NOVO fora da linhagem da seção 3). O SHA-256 do contrato e os demais 12 arquivos da seção 3 permanecem identes aos registrados (recomputados neste worktree); `runtime_admission` permanece UNBOUND; o pin espelhado `PORTAL-MIRROR-PIN-C6-4.json` permanece o snapshot válido de `3c619d91` (o `contract_pin` `d1d796b8…` não muda) e não é reemitido por este pacote.

**Nota de divergência R4 (BRDG-3.3 — admissão A3 — nada oculto):** em `main` pós-admissão A3, `src/maezo/gateway/commercial_admission.py` evolui novamente (SHA-256 `f66017f1b0c553d7d0e654faed69de10a85e508ffe17b59c5c518d55e4005543` → `723455a7f19af4e37fbed24627681d017db752b83d865864b0e788d68d2abbb5`) para o mecanismo da admissão A3 (`compose_commercial_gateway` passa a abrir a dimensão de efeito `acceptance.record` somente com a admissão de aceite registrada — sem o registro A3, a sessão A2-bound recusa o comando com o fail-closed existente antes de qualquer passo de fonte; novo módulo `src/maezo/gateway/commercial_acceptance_admission.py`, arquivo NOVO fora da linhagem da seção 3). O SHA-256 do contrato e os demais 12 arquivos da seção 3 permanecem identes aos registrados (recomputados neste worktree); `runtime_admission` permanece UNBOUND; o pin espelhado `PORTAL-MIRROR-PIN-C6-4.json` permanece o snapshot válido de `3c619d91` (o `contract_pin` `d1d796b8…` não muda) e não é reemitido por este pacote.

**Nota de divergência R5 (BRDG-3.3 — admissão A4 — nada oculto):** em `main` pós-admissão A4, `src/maezo/gateway/commercial_admission.py` evolui novamente (SHA-256 `723455a7f19af4e37fbed24627681d017db752b83d865864b0e788d68d2abbb5` → `26421928a2dced7537528bd396ab5c8196e2d8a9b1ce2fbb9862282a4a451d63`) para o mecanismo da admissão A4 (`compose_commercial_gateway` mantém a dimensão de leitura de cobertura do snapshot de jornada fechada sem a admissão de cobertura registrada — o codec composto (`CoverageGatedCodec`) recusa fato de cobertura assertado pela fonte (`coverageState` `active`/`revoked` no braço `coverage` do `CustomerJourneySnapshot`) com a recusa tipada `denied/purpose_denied` antes de qualquer byte ser divulgado, sem sintetizar projeção em seu lugar, e a fonte de vigência própria (issuer ≠ fonte de cobertura) segue ausente e é custódia do dono; novo módulo `src/maezo/gateway/commercial_coverage_admission.py`, arquivo NOVO fora da linhagem da seção 3). O SHA-256 do contrato e os demais 12 arquivos da seção 3 permanecem identes aos registrados (recomputados neste worktree); `runtime_admission` permanece UNBOUND; o pin espelhado `PORTAL-MIRROR-PIN-C6-4.json` permanece o snapshot válido de `3c619d91` (o `contract_pin` `d1d796b8…` não muda) e não é reemitido por este pacote.

**Nota de divergência R6 (BRDG-3.3 — admissão A4 — reparo do gate de recuperação — nada oculto):** em `main` pós-reparo da admissão A4, `src/maezo/gateway/commercial_admission.py` evolui novamente (SHA-256 `26421928a2dced7537528bd396ab5c8196e2d8a9b1ce2fbb9862282a4a451d63` → `bc1a65df4ba2f62d341929f591c8d3d6aa4711a2a2b9c95e4d17a1acf9a19c2b`) para fechar o braço de recuperação do gate de cobertura (`CoverageGatedCodec.parse_result` passa a inspecionar também o envelope de recuperação `operation.get` — o `RecoveredCustomerJourneySnapshot` carrega o mesmo braço `coverage` do `CustomerJourneySnapshot`, e um `original_result` de `original_command == "journey.get"` com `coverageState` `active`/`revoked` era divulgado sem recusa, enquanto o `journey.get` direto recusava; a recusa tipada `denied/purpose_denied` agora cobre QUALQUER caminho de divulgação do fato de cobertura, como exige o negativo planejado (iv) do dossiê A4 — prova por probe independente do vazamento e regressão nomeada no pacote de reparo). O SHA-256 do contrato e os demais 12 arquivos da seção 3 permanecem identes aos registrados (recomputados neste worktree); `runtime_admission` permanece UNBOUND; o pin espelhado `PORTAL-MIRROR-PIN-C6-4.json` permanece o snapshot válido de `3c619d91` (o `contract_pin` `d1d796b8…` não muda) e não é reemitido por este pacote.

**Nota de divergência R7 (BRDG-3.3 — admissão A5 — nada oculto):** em `main` pós-admissão A5, `src/maezo/gateway/commercial_admission.py` evolui novamente (SHA-256 `bc1a65df4ba2f62d341929f591c8d3d6aa4711a2a2b9c95e4d17a1acf9a19c2b` → `bccb33d6be70a5c2c4b464a54e53ea28f95d63802bf18ba1f1407e0da95eb92b`) para o mecanismo da admissão A5 (`compose_commercial_gateway` mantém a dimensão suporte/work fechada sem a admissão de suporte registrada — sem o registro A5, a sessão A2-bound recusa os seis comandos do escopo (`support.handoff` + `work.list/claim/assign/handoff/reconcile`, `contract-c6-4.json:241-270,283`) com o fail-closed existente antes de qualquer passo de fonte, e o codec composto (`SupportGatedCodec`) recusa a projeção de caso assertada pela fonte (`CustomerSupportRead` `status: "projection"`) em TODOS os três portadores de divulgação — o braço `support` do snapshot `journey.get`, o MESMO braço no envelope de recuperação `operation.get` com `original_command == "journey.get"` (lição R6 aplicada de nascimento) e o `case_read` de um `support.handoff` recuperado — com a recusa tipada `denied/purpose_denied` antes de qualquer byte ser divulgado, sem sintetizar caso em seu lugar; renderings `refused` e `support_topics` byte-exatas; novo módulo `src/maezo/gateway/commercial_support_admission.py`, arquivo NOVO fora da linhagem da seção 3; reparo mínimo no rig c6 `test_actual_work_list_http_composition_requires_staff_result_authority`, que passa a portar o registro A5 para continuar exercendo a lane de result-authority staff que sempre testou — precedentes R3/A2, mesma classe de reparo de rig de composição). O SHA-256 do contrato e os demais 12 arquivos da seção 3 permanecem identes aos registrados (recomputados neste worktree); `runtime_admission` permanece UNBOUND; o pin espelhado `PORTAL-MIRROR-PIN-C6-4.json` permanece o snapshot válido de `3c619d91` (o `contract_pin` `d1d796b8…` não muda) e não é reemitido por este pacote.

## 4. Linhagem dos freezes anteriores

| Arquivo | SHA-256 | Rótulo do freeze | `checkpoint_head` |
|---|---|---|---|
| `SOURCE-FREEZE-C6-4.json` | `b820c563cca651dab60f66453e1daf925a8f7b10f73c865530b9f532d9694d9b` | `C6-4-Maezo-owned-and-factory` | `dfac0714ef8fe528149650c640761e936ad78a65` |
| `SOURCE-FREEZE-C6-4-PREBODY.json` | `7394e76d5f6054e7aa5c9bc75bc7808a97a1e464f3d9e0192f5c8cab155b1dea` | `C6-4-Maezo-prebody-third-repair` | `dfac0714ef8fe528149650c640761e936ad78a65` |
| `SOURCE-FREEZE-C6-4-MAEZO-R2.json` | `cd18c7a041a331901c753ab7a0ede2b65afc36541ab3824a5715facbf50417b3` | `C6-4-MAEZO-R2-PRE-MAIN-ORIGIN` | `5f64f0e07f8cd13ee2ef6607c7f7652e1507ba80` |
| `SOURCE-FREEZE-C6-4-MAEZO-R3.json` | `1f82ba54b8af19720761caa56e7ed11bdf9b0e5906163984802be9e4d26d6128` | `C6-4-MAEZO-R3-MAIN-ORIGIN` | `0f1602d2259cb5e374d8163df1bbd5540fff255d` |
| `SOURCE-FREEZE-C6-4-MAEZO-R4.json` | `9bb272e7039120c4ec8b46b5163b67208d3add1f59807198be2e375f65845ab2` | `C6-4-MAEZO-R4-SCANNER` | `6a7c0d9bb093a438d27f0fd7dfdb253c81d41722` |

(Todos sob `docs/delivery/commercial-platform/`. Nenhum foi editado por este pacote.)

## 5. Espelho do pin para o portal externo

O pin espelhado, no formato que o portal consome (padrões `versionedSourceBaseline` da matriz de 51 interfaces e `omni-sales-p1-source-snapshot.v1`), está em `docs/delivery/commercial-platform/portal-mirror/PORTAL-MIRROR-PIN-C6-4.json`, com instruções de destino e comando de cópia em `docs/delivery/commercial-platform/portal-mirror/README.md`. O repositório `~/code/omni_sales_platform` está **fora do write-scope deste orquestrador** — a cópia é executada pelo dono. O sha registrado "NOS DOIS repos" (aceitação W3) só se completa com essa cópia.

## 6. BRDG-3.5 — lane "plugin humano Java (mvn verify)" em main

**VERIFICADA VERDE.** Workflow `ci.yml`, branch `main`, run **40010989062** (início 2026-10-23T19:23:08Z, conclusão `success`, ~15m18s), head `3c619d91` — job **`plugin humano Java (mvn verify)` = `success`**. Run anterior do mesmo job na main também verde: 40000779997 (`#919`). Nenhuma mudança de workflow neste pacote. Comando de re-verificação: `gh run list --branch main --workflow ci.yml --limit 3` e `gh run view 40010989062 --json jobs --jq '.jobs[] | {name, conclusion}'`.

## 7. Reprodução

```sh
# sha256 do contrato (deve ser d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea)
shasum -a 256 tests/candidate_artifacts/commercial/contract-c6-4.json

# sha256 de cada linha da tabela de linhagem (seção 3)
shasum -a 256 \
  tests/candidate_artifacts/commercial/contract-c6-4.json \
  tests/candidate_artifacts/commercial/PROVENANCE-C6-4.json \
  config/integrations/amh/commercial-c6-4.source.lock.json \
  config/integrations/amh/contracts.lock.json \
  docs/delivery/commercial-platform/AMH-C6-4-MAIN-ORIGIN.json \
  docs/delivery/commercial-platform/AMH-C6-4-MAIN-ORIGIN.md \
  docs/delivery/commercial-platform/MAEZO-C6-4-DELTA.md \
  src/maezo/ports/commercial.py \
  src/maezo/adapters/amh/commercial.py \
  src/maezo/adapters/amh/commercial_http.py \
  src/maezo/gateway/commercial.py \
  src/maezo/gateway/commercial_admission.py \
  src/maezo/portal/api/commercial.py

# sha256 dos freezes anteriores (seção 4) e do registro
shasum -a 256 docs/delivery/commercial-platform/SOURCE-FREEZE-C6-4*.json \
  docs/delivery/commercial-platform/CONTRACT-FREEZE-C6-4-REGISTER.json

# base do congelamento
git rev-parse HEAD   # 3c619d919738878331c21dadda4b112993f430ef
git log -1 --format='%H %s' 7b2dfb86   # merge #907
```

O estado congelado é válido enquanto `main` = `3c619d91` para os arquivos da seção 3; qualquer divergência de sha num arquivo citado invalida esta versão e exige novo registro (o contrato em si só muda por nova publicação AMH — regra do sucessor em `CONTRACT-FREEZE-C6-4-REGISTER.json`).
