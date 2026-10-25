# GAP-CATALOG A1–A10 — mapa de admissões do portal vs autoridade em main (`C6-4-frozen@3c619d91`)

**Data:** 2026-10-24 · **Base:** `main` = `7f6c2576` (`origin/main`, checkout limpo) · **Item do plano:** BRDG-3.3 (W3), decisão **D-16** do dono (`docs/plans/maezo-completion-program.md` §Decisões — nada re-decidido aqui) · **Schema do registro maquinável:** `omni-commercial-gap-catalog.v1` · **Pacote:** **SOMENTE-DOCUMENTOS/MAPA** — zero mudança de comportamento, zero flag, zero edição de `src/**`, zero edição de v3.

Este catálogo é a **resolução do GAP-CATALOG pela autoridade em main** pré-condicionada no registro de congelamento (BRDG-3.2, `CONTRACT-FREEZE-C6-4-REGISTER.md:7` e `.json:153`). Ele **não admite nada**: `runtime_admission` permanece **UNBOUND** e o admission layer do portal permanece bloqueado. Cada linha distingue o que o **contrato congelado expõe**, o que a **maezo em main implementa HOJE**, o **GAP** e o **teste negativo PLANEJADO** que o pacote de admissão futuro (um PR por admissão, D-16) deverá provar. Nada aqui é implementado.

## 0. Autoridade consumida (re-verificada neste worktree)

| Fonte | Evidência | Estado nesta base |
|---|---|---|
| Contrato congelado C6-4 | `tests/candidate_artifacts/commercial/contract-c6-4.json` | SHA-256 recomputado **`d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea`**, 320.163 bytes — **bate** com `CONTRACT-FREEZE-C6-4-REGISTER.md:15` |
| Linhagem do congelamento | 13 arquivos da seção 3 do register | **13/13 SHA-256 recomputados e batendo** no tip `7f6c2576` (o register declarava validade "enquanto main = 3c619d91 para os arquivos da seção 3" — os arquivos não se moveram) |
| Pin espelhado p/ portal | `docs/delivery/commercial-platform/portal-mirror/PORTAL-MIRROR-PIN-C6-4.json` | SHA-256 `bcfe88f918a90a91efeaa250b25a0f92dc311500dcea9293f6af0f6a6db25f67` |
| Cópia no portal | `/Users/familia/code/omni_sales_platform/docs/delivery/commercial-platform/PORTAL-MIRROR-PIN-C6-4.json` | **SHA-256 idêntico (`bcfe88f9…`) — conferência dos dois lados OK** (o arquivo está untracked no working tree do portal; o ato de commit/PR lá é do dono — aqui registrado como cópia verificada byte a byte) |
| Estado do portal | `omni_sales_platform` @ `917c1cbd1a8384ccf6a6b9acaefad029a608c487` | Bate com `PORTAL-MIRROR-PIN-C6-4.json:145` (`portal_state_observed.head`) |
| Índice das admissões | `omni_sales_platform/docs/delivery/admissions/A-INDEX.json` (schema `omni-sales-admissions-index.v1`) | 10 dossiês, todos `status: pending` (`A-INDEX.json:2,37-180`) |
| Admission layer do portal | `omni_sales_platform/src/server/admission/sales.ts:5-7` | Bloqueio constante `blocked/qualification_required`; não lê arquivo algum |
| Autoridade runtime maezo | `SP-OP-*` (19 process-keys únicos em `src/`, sendo 17 reais — 15 da allowlist + SP-OP-SUPP-001 + SP-OP-ANS-CRON-001 — e 2 placeholders), allowlist `src/maezo/gateway/effect_pep.py:174-192` (`KNOWN_PROCESS_KEYS`, 15 keys), contrato de deploy `src/maezo/platform/deploy/engine_deploy.py` | Intactos por este pacote |
| Matriz de interfaces | `omni_sales_platform/docs/compatibility/maezo-interface-matrix-p1-successor.json` | 51 linhas + 8 lacunas, `PLANNING_ONLY_UNQUALIFIED` — nenhuma linha movida (regra `mapping: at_admission`, `A-INDEX.json:28`) |

## 1. Declaração literal de A1–A10 (o que o pin/portal declara)

A definição autoritativa de A1–A10 **não está no contrato C6-4 e nem em arquivo maezo** — está na prateleira do portal externo (`omni_sales_platform/docs/delivery/admissions/`, schema `omni-sales-admissions-index.v1`), com a tabela v3 §3 como fonte de verdade declarada (`A-INDEX.json:7-9`). O contrato congelado declara as **capacidades** (42 comandos, `x-*` blocks) que essas admissões gateiam; o `A-INDEX.json` declara as **10 admissões** como objetos de decisão owner-gated. Mapeamento literal, sem invenção:

| A-ID | Dossiê (portal) | Título literal | Desbloqueia | Owners (papéis) | GAP | NRs | Sementes de matriz | `A-INDEX.json` |
|---|---|---|---|---|---|---|---|---|
| A1 | `A1-CR-F2-CATALOGO.md` | CR-F2 catálogo | P6.1 | produto, comercial, legal | GAP-CATALOG | NR-01..04 | OP02 | `:38-52` |
| A2 | `A2-CR-F1-AUTH-SALES.md` | CR-F1 auth Sales | P6.2 (sessão) | identity, channel, security, DPO | GAP-SALES-AUTH | NR-01, 05, 10 | OP01 | `:53-66` |
| A3 | `A3-CR-F3-ACEITE-MATRICULA.md` | CR-F3 aceite/matrícula | P6.2 (efeitos) | cliente, issuer | GAP-ACCEPTANCE-ENROLLMENT | NR-05, 06 | OP03, OP04, HTTP-13, HTTP-14 | `:67-80` |
| A4 | `A4-GAP-COVERAGE-COBERTURA.md` | GAP-COVERAGE cobertura vigente | P6.3 | steward AMH, direito | GAP-COVERAGE | NR-06 | — | `:81-94` |
| A5 | `A5-CR-F5-SUPORTE.md` | CR-F5/F6 suporte | P6.4 (suporte) | case owner, channel owner, human owners | GAP-SUPPORT | NR-07 | OP07..OP11, HTTP-04..12 | `:95-108` |
| A6 | `A6-GP11-NAO-CONTATO.md` | GP11 não-contato | P6.4 (honra), NR-08 | owner (origem), DPO (origem) | GAP-NO-CONTACT | NR-08 | — | `:109-122` |
| A7 | `A7-GAP-PORTABILITY-PORTABILIDADE.md` | GAP-PORTABILITY portabilidade | NR-11 (decisão) | produto, legal | GAP-PORTABILITY | NR-11 | — | `:123-136` |
| A8 | `A8-GAP-ATTRIBUTION-COMISSAO.md` | GAP-ATTRIBUTION-COMMISSION comissão | stub NR-09 (decisão) | comercial, legal, DPO | GAP-ATTRIBUTION-COMMISSION | NR-09 | NOTICE-VENDOR | `:137-150` |
| A9 | `A9-NR10-LIFECYCLE-ANALYTICS.md` | NR-10 lifecycle/analytics | métricas/eventos (decisão) | DPO | GAP-SALES-AUTH (dim. NR-10) | NR-10 | — | `:151-164` |
| A10 | `A10-VENDOR-MEMBERSHIP.md` | Vendor membership/canal | P6.5 | engenharia origem, comercial | GAP-SALES-AUTH (dim. NR-09) | NR-09 | JR4, OP13, OP15, NOTICE-VENDOR, HTTP-35, HTTP-36 | `:165-179` |

**Contagem: o contrato/pin nomeia exatamente 10 admissões** — não há agrupamento divergente. A1–A9 são a nomenclatura v3 §3 sobre a tabela "Registro NO-GO por capacidade" do handoff (`A-INDEX.json:14`, mapeamento 1:1 por `handoff_row_ref`); **A10 é complemento registrado** (a tabela v3 §3 não tem linha para o gatilho real de P6.5 — `A-INDEX.json:171`). Todos os 10 dossiês nasceram `pending` e **nenhum** `A<n>-ADMISSION-RECORD.json` existe (`ADMISSIONS-README.md:22`).

## 2. Catálogo por admissão — contrato exposto × maezo HOJE × GAP × teste negativo planejado

Veredito por admissão = estado do substrato consumível **em main** que a admissão gateará: `PRESENT` (substrato completo, só falta o ato owner), `PARTIAL` (camada tipada/roteiro fail-closed existe, autoridade/fonte ausente), `ABSENT` (nada de consumível existe). A soma checada está na §4.

### A1 — CR-F2 catálogo (P6.1) — veredito maezo: **PARTIAL**

- **(i) Contrato congelado:** `"catalog.get"` com scope `interop/sales.catalog.read` (`contract-c6-4.json:49`); bloco `x-public-catalog` com os 3 transports GET (`"maezo": "GET /api/v1/sales/public/catalog"`, `query_or_body_allowed: false`, `proposed_scope: interop/sales.catalog.public.read` — `:11020-11030`); `x-public-price` (`:11122`); `x-journey-formal-entry` com `plan_choices` e `"status": "PROPOSED_NOT_ADMITTED"` (`:11215-11222`); `x-catalog-associations` (`:11098`).
- **(ii) Maezo HOJE:** rota `GET /api/v1/sales/public/catalog` implementada e **fail-closed** — exige `AuthenticatedPublicCatalogAdmission` instalada e recusa 503 `source_unavailable` caso contrário (`src/maezo/portal/api/commercial.py:110-159`); composição `compose_public_catalog_gateway` retorna gateway **unbound** por default (`src/maezo/gateway/commercial_admission.py:207-231`, guarda `:219`); app default `public_catalog_gateway=None` (`src/maezo/portal/api/app.py:234,342-343`); codec C6-4 cobre `catalog.*`/`operator.*` com 231 UNIT (`MAEZO-C6-4-DELTA.md:13`). A **autoridade de fonte comercial** é que não existe: readiness CR-F2 = `COMMERCIAL_AUTHORITY_ABSENT_FROM_PIN;POPULATION_PORT_PENDING`, `published_for_new_scope: false` (`docs/design/v21-capabilities/source-readiness.json:191,198`).
- **(iii) GAP:** fonte de catálogo admitida (objeto publicável CR-F2: contrato de leitura público/privado, currentness/withdrawal, `publishedCriteria` — `A1` dossiê §"Objeto publicável"). Mecanismo: maezo-side o gateway já é fail-closed sem autoridade (nada a erguer para bloquear); **portal-side** o gap é o loader fail-closed do pin — hoje o portal é constant-blocked (`sales.ts:5-7`) e não lê o pin espelhado (`portal-mirror/README.md:13`); o pacote futuro do portal precisará de um loader que valide o sha `d1d796b8…` do pin e permaneça fechado em ausência/divergência.
- **(iv) Teste negativo PLANEJADO:** (a) sem `A1-ADMISSION-RECORD.json` `admitted`, `GET /api/v1/sales/public/catalog` com a composição default retorna 503 `source_unavailable` e nunca lê a cópia TEST do contrato; (b) com loader de pin, sha divergente de `d1d796b8…` ⇒ loader recusa e o portal permanece `blocked/qualification_required` (fail-closed provado, não só verde); (c) `journey.get` sem admissão retorna `plan_choices: []` (recuperação não faz bootstrap — invariantes `x-journey-formal-entry.recovery`).

### A2 — CR-F1 auth Sales (P6.2 sessão) — veredito maezo: **PARTIAL**

- **(i) Contrato:** `"sales_admission": "NOT_ESTABLISHED"` (`contract-c6-4.json:18`); `x-server-only-evidence` (`:340`); transports com a rota maezo `POST /api/v1/sales/commercial` (`x-transports`, ver `AmhCommercialAdapter`/`ports/commercial.py`).
- **(ii) Maezo HOJE:** camada de admissão tipada existe — `AuthenticatedSalesAdmission` com verificação Bearer+`x-commercial-connection` **antes do body** (`src/maezo/gateway/commercial_admission.py:69-115`, guarda `:74-90`); portas abstratas `SalesCallerVerifier`/`SalesScopeAuthority` (`:46-66`); **nenhuma implementação concreta em `src/`** (0 hits fora do próprio módulo — grep 2026-10-24); `compose_commercial_gateway` devolve gateway unbound quando `caller/scope_authority/source_authority/amh_origin` faltam (`:142-143`); rota recusa 503 `binding_unqualified` sem gateway admitido (`src/maezo/portal/api/commercial.py:50-52`). Portal: `getSalesAdmission()` constante `blocked` (`sales.ts:5-7`).
- **(iii) GAP:** provedor concreto de identidade/sessão (objeto CR-F1: caller Origin/BFF, sessão/delegação, memberships/purpose, CSRF/origin, expiry/revoke — dossiê A2 §"Objeto publicável"); readiness CR-F1 = `PARTIAL_IMPLEMENTED_LIVE_UNVERIFIED`, `published_for_new_scope: false` (`source-readiness.json:121,128`). Mecanismo: a camada abstrata já existe — o pacote de admissão liga o provedor **só com o registro** `A2-ADMISSION-RECORD.json` (`admitted`); sem registro, a composição continua unbound (chokepoint já fail-closed).
- **(iv) Teste negativo PLANEJADO:** (a) sem A2 admitida, qualquer chamada a `POST /api/v1/sales/commercial` sem headers ou com headers parciais ⇒ 403 `denied/admission_required` antes de 1 byte de body (cobre `commercial_admission.py:74-90`); (b) sem A2, não existe composição que instale `SalesCallerVerifier` concreto — teste de composição prova que a factory unbound não expõe efeito; (c) portal-side: com loader de pin, ausência/divergência do registro A2 mantém `getSalesAdmission()` bloqueado (cookie/role/env-flag não mudam — contrato do `sales.ts:4`).

### A3 — CR-F3 aceite/matrícula (P6.2 efeitos) — veredito maezo: **PARTIAL**

- **(i) Contrato:** comando `"acceptance.record"` scope `interop/sales.contract` (`contract-c6-4.json:187`); `x-customer-journey` com save/get/composition/tracking (`:11107-11121`); `x-journey-continuation` (`:11206`); invariantes "Contract acceptance/issuance/enrollment/coverage remain independent" (`:454`).
- **(ii) Maezo HOJE:** codec valida o wire de aceite e a autoridade de resultado — checks C6-3 (quote/linkage/aceite/coerência de receipt, declined não sustenta issued) e C6-4 (`plan_choices` sem conversão de `optionRef`), 231 UNIT zero-skips (`MAEZO-C6-3-DELTA.md:13-22`, `MAEZO-C6-4-DELTA.md:13`); `CommercialResultAuthority` exige proof pós-IO cobrindo todo fato novo (`MAEZO-C6-3-DELTA.md:20-22`). Runtime de issuer (receipts OP04, timeline de matrícula) **não existe**: readiness CR-F3 = `BUSINESS_WRITES_ABSENT_FROM_PIN;LIVE_OUTCOME_PUBLISHER_UNVERIFIED` (`source-readiness.json:259,267`); no snapshot do portal OP03/OP04 seguem `PROPOSED_INTERNAL_CONTRACT_NOT_PUBLISHED`/`binding_admitted: false` (dossiê A3 §"O que conta como publicado").
- **(iii) GAP:** issuer qualificado + wire OP03/OP04 publicados + idempotência/clock/custódia demonstrados (`A3` dossiê). Mecanismo: mesma forquilha do A2 — admissão maezo-side liga o executor de `acceptance.record` só com registro; portal-side loader fail-closed do pin + regra de arquivo do `A-INDEX.json:31-35`.
- **(iv) Teste negativo PLANEJADO:** (a) `accepted` sem receipt da fonte ⇒ recusa/fabricação proibida (invariante já coberta pelos checks C6-3 — o pacote futuro repina como asserção de regressão); (b) sem A3 admitida, nenhum efeito OP03/OP04 executa: composição unbound recusa e o vendor channel toma `PURPOSE_DENIED` no binding antes do driver (`src/maezo/gateway/capabilities/vendor_journey/binding.py:149-153`); (c) duplo-clique/back/reload sem `idempotency_key` ⇒ `acceptance.uncertain` reconcile-only, sem redispatch.

### A4 — GAP-COVERAGE cobertura vigente (P6.3) — veredito maezo: **ABSENT**

- **(i) Contrato:** **nenhum comando de cobertura** entre os 42 (`x-commands`, `:36-289`); só a invariante (`:454`) e o shape nominal `CustomerCoverageRead` (`:8622-8663`) — projeção, não comando.
- **(ii) Maezo HOJE:** o único substrato é leitura clínica read-only `ClinicalContextPort.get_subject_coverage` (`src/maezo/adapters/amh/subject_context.py:346`), que o dossiê A4 **proíbe** de virar projeção comercial ("o port read-only `ClinicalContextPort.get_subject_coverage` não publica projeção comercial de ativação" — A4 §Identidade); readiness CR-F4 = `READ_ONLY_COVERAGE_PRESENT;ENROLLMENT_AGENDA_ABSENT_FROM_PIN` (`source-readiness.json:329,337`).
- **(iii) GAP:** fonte de vigência própria (issuer ≠ fonte de cobertura), com `coverage_state`/`coverage_generation_ref`/`evidence_basis: coverage_source` e invariante `issued ≠ coverage_active` (`A4` dossiê §"Objeto publicável"). Mecanismo: nova fonte servida + admissão steward AMH/direito; loader fail-closed do pin no portal.
- **(iv) Teste negativo PLANEJADO:** qualquer caminho que derive `active` de OP04/pagamento/clique/leitura clínica ⇒ recusa tipada (teste de invariante `never issued→active`); sem A4 admitida, leitura de cobertura comercial retorna `unknown` (`unknown_reason` allowlisted) — nunca `active` nem vazio-otimista; stale/revogada ⇒ `unknown` com razão.

### A5 — CR-F5/F6 suporte (P6.4 suporte) — veredito maezo: **PARTIAL**

- **(i) Contrato:** comando `"support.handoff"` (`contract-c6-4.json:283`), fila de trabalho `work.list/claim/assign/handoff/reconcile` (`:241-276`), `x-workflow` com audiência "Authenticated qualified staff only; vendors/customers have no work commands" (`:11067-11069`); `x-customer-journey.support` ("Unknown source topic/protocol remains unavailable") (`:11115`).
- **(ii) Maezo HOJE:** `support.handoff` e `work.*` existem como comandos do codec (validação de payload/scope, não executor); substrato de escalonamento humano vivo — `SP-OP-ESCALATION-001` é a 1ª key da allowlist `KNOWN_PROCESS_KEYS` (`src/maezo/gateway/effect_pep.py:174-192`). O consumidor de broker dos casos (case kinds/status/audiência publicados) **não existe** no pacote do adapter AMH: readiness CR-F5 = `FOUNDATION_PRESENT;BROKER_CONSUMER_ABSENT_FROM_AMH_ADAPTER_PACKAGE`; CR-F6 = `ENVELOPE_PRESENT;NEW_DOMAIN_SEMANTICS_UNVERIFIED` (`source-readiness.json:398,407,471,479`).
- **(iii) GAP:** membros de `DeclaredCaseKind`/`DeclaredCaseStatus`/audiência/papel publicados + ACK humano + canal vivo + wait clocks (`A5` dossiê; hoje os quatro enums vazios ⇒ nenhuma instância construível). Mecanismo: publicação na origem + registro A5; portal-side loader fail-closed.
- **(iv) Teste negativo PLANEJADO:** (a) sem A5 admitida, nenhum `DeclaredCaseKind`/`Status` tem membro — teste de schema prova que nenhuma instância de caso é construível; (b) `support.handoff` com `topic_ref` não publicado ⇒ `unavailable` (nunca free-text clínico); (c) confirmação de resolução via OP03 ⇒ recusa (sempre OP07); (o) portal nunca computa prazo/SLA — relógio só da fonte.

### A6 — GP11 não-contato (P6.4 honra / NR-08) — veredito maezo: **PARTIAL**

- **(i) Contrato:** OP20 `suppression.record` **não integra o denominador** do contrato C6-4 (0 comandos de supressão nos 42) e nem da matriz 51 (dossiê A6 §Identidade) — a ausência é a própria lacuna GAP-NO-CONTACT.
- **(ii) Maezo HOJE:** substrato forte e fail-closed — camada 3 GP11 materializada (`src/maezo/gateway/vendor_suppression.py:1-40`: k-anon k=100, taxonomia C1–C6, SLA 15du com `t_deadline` no nascimento e `DEADLINE_UNCOMPUTABLE` fail-closed `:187,227,272`, gate de audiência 300, KPI presence-only; rota `REGISTRO_HONRADO` sempre explícita da DMN engine-side `:39`); dois handlers externos registráveis (`src/maezo/tools/workers/suppression.py:238-266`, seams `suppression_store`/`suppression_dmn_transport` ausentes ⇒ recusa loud todo tick); migração 0021 existe (`src/maezo/platform/migrations/versions/0021_vendor_suppression_records.py`); BPMN existe (`spec/processes/bpmn/SP-OP-SUPP-001_Direitos_de_Nao_Contato.bpmn`). **Porém:** (1) a composição runtime **não passa o seam** `suppression_store` (`src/maezo/runtime/worker_runtime/service.py:326-336` passa só `dmn/engine/audit_sink/denial_notice_host/tenant_id/kafka/dossier_dispatcher/document_request_host_installed`) — a leitura do dossiê A6 contra `e996e809` segue verdadeira em `7f6c2576`; (2) `SP-OP-SUPP-001` **não está** na allowlist `KNOWN_PROCESS_KEYS` (`effect_pep.py:174-192` — 15 keys); (3) o processo segue `DRAFT (v1.0.0)` com `requires human review before any deploy` (`docs/processes/contracts/SP-OP-SUPP-001.md:3`); (4) contrassinatura DPO = decisão humana pendente (dossiê A6 §Objeto).
- **(iii) GAP:** cadeia Track B — PR-2 da origem + store/transport servidos no wiring + contrassinatura DPO + operação de lista honrada (`A6` dossiê §"Cadeia de dependência Track B"). Mecanismo: admissão owner+DPO com registro; até lá o runtime recusa (seam ausente) e o PEP recusa qualquer `EffectCall` nomeando a key.
- **(iv) Teste negativo PLANEJADO:** (a) `registered ≠ honored`: `honorState:"honored"` sem evento `.honored_in_list` + `sourceReceiptRef` + `listPlanRevision` ⇒ recusa; (b) store ausente/sem forma ⇒ recusa tipada em todo tick (loud, não silenciosa — comportamento hoje, a repinar como asserção); (c) `start_process_idempotent`/`EffectCall` com `SP-OP-SUPP-001` fora da allowlist ⇒ recusa (cerca `effect_pep.py:168-192`); (d) registro sem deadline computável ⇒ `DEADLINE_UNCOMPUTABLE` antes de qualquer I/O.

### A7 — GAP-PORTABILITY portabilidade (NR-11) — veredito maezo: **ABSENT**

- **(i) Contrato:** **zero menções** a portabilidade (grep `-i "portab"` em `contract-c6-4.json` = 0 hits).
- **(ii) Maezo HOJE:** único hit de "portabilidade" em `src/` é o direito DSR LGPD de portabilidade de dados (`src/maezo/tools/workers/lgpd.py:198` — `SP-OP-LGPD-DSR-001`), que é **colisão de nome**, não o substrato comercial de portabilidade de carências. Nenhum read/intake de portabilidade de planos.
- **(iii) GAP:** Forma 1 (fonte própria com case kind específico na cadeia CR-F5) **ou** Forma 2 (teto `orientation_only` registrado por decisão) (`A7` dossiê §"Objeto publicável"; o schema portal `portability.proposed.v1` já nasce `orientation_only` com cláusulas `not` contra vereditos). Mecanismo: decisão produto/legal + registro; loader fail-closed portal-side.
- **(iv) Teste negativo PLANEJADO:** (a) sem A7 admitida, nenhuma superfície exibe compatibilidade/prazo — só orientação com pendência explícita; (b) campos `compatibilityVerdict`/`eligible`/`remainingWaitingDays`/`cancellationRequested`/`requestSubmitted`/`protocolRef` ⇒ rejeitados pelo schema (teste de cláusula `not`); (c) nenhum cancelamento do plano de origem a partir do portal (REG-006).

### A8 — GAP-ATTRIBUTION-COMMISSION comissão (stub NR-09) — veredito maezo: **ABSENT**

- **(i) Contrato:** **zero menções** a comissão/attribution (grep `-i "commission|attribution"` = 0 hits); `x-status.human_acts/tasy_write_homologation: NOT_ESTABLISHED` (`contract-c6-4.json:14-25`).
- **(ii) Maezo HOJE:** 0 hits de "commission" em `src/` (grep 2026-10-24); OP17 `commission.settle`/OP19 `value_transfer.register` permanecem `candidate_not_live` (GP5–GP10 — fora do denominador 51, dossiê A8 §Identidade). Nada implementado — coerente com "Q10 não" e com o stub NR-09 sem valores.
- **(iii) GAP:** política/base/attribution/fato gerador ratificados (Forma 1) **ou** fora-de-escopo explícito (Forma 2) — decisão comercial/legal/DPO (`A8` dossiê). Mecanismo: registro de admissão; nenhum cálculo maezo/portal; loader fail-closed.
- **(iv) Teste negativo PLANEJADO:** sem A8 admitida, nenhuma superfície calcula/exibe comissão, score, payout, ranking patrocinado ou comparação de concorrentes (teste de ausência de superfície — "ausência que não é flag", molde A9); stub `commission-policy.proposed.v1` permanece sem valores.

### A9 — NR-10 lifecycle/analytics (métricas/eventos) — veredito maezo: **ABSENT**

- **(i) Contrato:** analytics só aparece como **proibição** — intake "never financial journal, logs, analytics or browser persistence" (`contract-c6-4.json:446`); persistência de termos "…logs, analytics or browser persistence" (`:11016`). Nenhum comando/evento de analytics.
- **(ii) Maezo HOJE:** nenhum contrato de lifecycle/analytics para o canal Sales; o que existe é observabilidade interna da maezo (`src/maezo/platform/observability.py`, `src/maezo/runtime/turn_telemetry.py`) e a nota de egresso do gateway (`src/maezo/portal/contracts/models.py:108` — "analytics, logging or General-Zone egress" como coisa a NÃO fazer sem deriva humana/DPO). Analytics do portal: off (`A9` dossiê; handoff "Analytics: off").
- **(iii) GAP:** contrato finalidade/base/schema/retention/erasure/owners/consent/dedupe com mecanismo qualificado — decisão DPO (`A9` dossiê §"Objeto publicável"). Mecanismo: registro DPO; até lá UNMEASURED é estado, não toggle.
- **(iv) Teste negativo PLANEJADO:** (a) sem A9 admitida, nenhum evento de analytics é emitido por nenhum incremento (teste de ausência de emissão em P6.i); (b) browser nunca emite `accepted`/`enrolled`/`coverage_active` autoritativos; (c) nenhum payload pessoal em URL/DOM/storage/logs (códigos bounded); (d) opt-out não quebra suporte/contratação.

### A10 — Vendor membership/canal (P6.5) — veredito maezo: **PARTIAL**

- **(i) Contrato:** a superfície vendor não é comando do C6-4 (OP13/OP15/JR4 não integram os 42) — o contrato só carrega os **valores de enum** `vendor_channel` (`:7148,7637,7911,8390,10167,10606`) e `vendor_seat_refused` (`:8467`), e a audiência de work "vendors/customers have no work commands" (`:11069`).
- **(ii) Maezo HOJE:** plano vendor v21 existente e **default-off** — allowlist fechada `VENDOR_OPERATIONS = {access.resolve, offer.compose, external_wait.settle}` (`src/maezo/gateway/capabilities/vendor_journey/binding.py:63`) com `PURPOSE_DENIED` fora dela (`:149-153`); `VendorJourneyConfig` default-off por construção (`binding.py:81-96`, "`enabled` é a flag: `False` (o default)"); projeção OP15 read-only (`src/maezo/gateway/capabilities/vendor_journey/projection.py:1`); ato administrativo OP13 (`src/maezo/tools/workers/vendor_admin.py`); store de canal + submissões (`migrations/versions/0019_vendor_channel_authority.py`, `0020_vendor_channel_submissions.py`); rota de submissão vendor DOCUMENTED but unbound (`src/maezo/portal/api/app.py:335-338`). O que falta é exatamente o objeto da admissão: grants Portal por OP publicados (Track B), membership **publicada** (não `pending`), canal `active` na única fonte acreditada e capability `vendor` projetada pelo deployment (dossiê A10 §"Objeto publicável").
- **(iii) GAP:** publicação de membership/canal pela origem + decisão de canal do comercial. Mecanismo: admissão engenharia-origem+comercial; JR4 permanece guard default-off até bind qualificado; loader fail-closed portal-side.
- **(iv) Teste negativo PLANEJADO:** (a) sem A10 admitida, `VendorJourneyConfig.enabled=False` ⇒ JR4 ausente da superfície (teste de default-off); (b) canal vendor tentando OP03/OP04 ⇒ `PURPOSE_DENIED` no binding antes do driver (`binding.py:149-153`); (c) membership `pending`/revogada ⇒ `AUTHORITY_UNPROVEN`; store ausente ⇒ `SOURCE_UNAVAILABLE` (UNKNOWN, nunca zero); (d) deployment sem capability `vendor` projetada ⇒ rota 403/503 (comportamento documentado em `app.py:335-338`, a repinar como asserção de regressão).

## 3. Divergências e notas de reconciliação contrato × pin (registro literal, nada oculto)

1. **Vocabulário OP ≠ comandos do contrato.** As sementes de matriz dos dossiês (`candidate_matrix_ids`) e o GAP-NR-map usam o vocabulário OP01..OP11/OP13/OP15 da matriz de 51 interfaces (`A-INDEX.json:13-21,22-29`), que vem dos contratos de capacidade v21 (`docs/design/v21-capabilities/contracts.json`) — **não** dos 42 comandos do C6-4. Em particular `OP02 offer.compose`, `OP04 enrollment.request`, `OP05 reservation.command`, `OP06 fulfillment.observe` não existem como comandos do contrato (o C6-4 tem `offer.quote`, `contract.issue`, `party.submit/link`, `acceptance.record`). Não é contradição — a regra `mapping: at_admission` (`A-INDEX.json:35`, `PORTAL-MIRROR-PIN-C6-4.json:135-139`) justamente impede ler linha OP como comando —, mas o mapa fica registrado para que nenhum pacote de admissão trate OP04 como comando C6-4.
2. **O índice A1–A10 vive só no portal.** `docs/delivery/admissions/A-INDEX.json` não existe em main maezo (o register cita-o como contexto do portal, `.json:152`). Este GAP-CATALOG é agora o espelho rastreado maezo-side do mapa — criado em `docs/delivery/commercial-platform/` (mesma casa dos artefatos de custódia comercial), sem tocar `src/**` do portal (regra `A-INDEX.json:31`).
3. **Leituras do dossiê A6 re-verificadas verdadeiras no tip atual:** nenhum callsite runtime passa `suppression_store` (`worker_runtime/service.py:326-336`), `SP-OP-SUPP-001` fora da allowlist (`effect_pep.py:174-192`), processo `DRAFT` (`docs/processes/contracts/SP-OP-SUPP-001.md:3`), migração 0021 presente.
4. **Cópia do pin no portal verificada byte a byte** (sha `bcfe88f9…` idêntico dos dois lados — §0), porém o arquivo segue untracked no working tree do portal; o commit/PR lá é ato do dono (fora do write-scope deste orquestrador).
5. **"3 public GETs" do register** = os 3 transports do `x-public-catalog` (`contract-c6-4.json:11021-11025`), coerente com os 42 comandos (`x-commands`).
6. **Nenhuma linha da matriz moveu:** 51 interfaces + 8 lacunas, `PLANNING_ONLY_UNQUALIFIED`, 51/51 NOT_RUN (`PORTAL-MIRROR-PIN-C6-4.json:135-141`).

## 4. Soma checada — veredito × 10

| A-ID | Admissão | Veredito maezo HOJE | Uma linha |
|---|---|---|---|
| A1 | CR-F2 catálogo | **PARTIAL** | Rota pública fail-closed + codec/catalog prontos; autoridade de fonte comercial ausente (`source-readiness.json:198`) |
| A2 | CR-F1 auth Sales | **PARTIAL** | Camada de admissão tipada unbound; 0 provedores concretos em `src/` |
| A3 | CR-F3 aceite/matrícula | **PARTIAL** | `acceptance.record` + checks de autoridade 231 UNIT; runtime de issuer/OP04 ausente |
| A4 | GAP-COVERAGE | **ABSENT** | Só leitura clínica read-only (explicitamente fora do escopo comercial); nenhum comando de cobertura |
| A5 | CR-F5/F6 suporte | **PARTIAL** | `support.handoff`/`work.*` + `SP-OP-ESCALATION-001` allowlisted; broker-consumer ausente do pacote AMH |
| A6 | GP11 não-contato | **PARTIAL** | Camada 3 + workers + migração 0021 fail-closed; seam não servido, key fora da allowlist, DPO e draft pendentes |
| A7 | GAP-PORTABILITY | **ABSENT** | Só colisão de nome (DSR LGPD); zero substrato comercial |
| A8 | GAP-ATTRIBUTION | **ABSENT** | 0 hits de comissão; OP17/OP19 `candidate_not_live` |
| A9 | NR-10 lifecycle/analytics | **ABSENT** | Só proibições no contrato e observabilidade interna; analytics UNMEASURED por decisão |
| A10 | Vendor membership/canal | **PARTIAL** | Plano vendor v21 default-off completo (allowlist/OP15/OP13/migrações 0019-0020); membership/canal publicados ausentes |
| **Total** | | **6 PARTIAL + 4 ABSENT + 0 PRESENT = 10** | Soma checada ✔ |

`PRESENT = 0` é o estado esperado e coere com `runtime_admission: UNBOUND` (`CONTRACT-FREEZE-C6-4-REGISTER.json:149`) e com o bloqueio constante do portal (`sales.ts:5-7`): nenhuma admissão existe, logo nenhum substrato consumível está completo.

## 5. Ordem recomendada de pacotes futuros (D-16 — uma admissão por PR, teste negativo por admissão)

| Ordem | Pacote | Justificativa de dependência |
|---|---|---|
| 1 | **A1** | Fila oficial: "PRIMEIRA ADMISSÃO da fila Track A" (`A-INDEX.json:44`). Substrato maezo mais adiantado (rota + codec + 231 UNIT); destrava P6.1, que alimenta catálogo/conteúdo dos demais incrementos |
| 2 | **A2** | Sessão é porta de todo efeito (P6.2 abre com A2); readiness CR-F1 `PARTIAL_IMPLEMENTED_LIVE_UNVERIFIED` — camada tipada só falta o provedor |
| 3 | **A3** | Dossiê A3: "completa o incremento P6.2 quando combinada à admissão de sessão A2" — efeitos exigem sessão |
| 4 | **A4** | Mesma NR-06 que A3 (timeline OP04 vs fato cobertura); a invariante `issued ≠ coverage_active` só tem sentido pós-issuer qualificado |
| 5 | **A5** | P6.4 suporte; consumo portal das linhas HTTP-04..12 exige sessão (A2) e catálogo explicativo (A1); enums publicados são pré-condição do A7-Forma-1 |
| 6 | **A6** | P6.4 honra (agrupa com A5); dependência humana forte (contrassinatura DPO) + wiring de store — pode atrasar sem bloquear 1–5 |
| 7 | **A7** | Decisão (fonte **ou** teto); Forma 1 depende do case kind da cadeia CR-F5 (A5); independente do resto |
| 8 | **A8** | Atribuição pressupõe canal (A10 logicamente; formalmente os dossiês só dividem dimensões NR-09 — A8 antes de A10 evita re-trabalho de escopo de canal) |
| 9 | **A9** | Gate transversal de medição: "métricas somente com A9 admitido; ausência de medição continua UNMEASURED" (`omni_sales_platform/docs/delivery/admissions/A9-NR10-LIFECYCLE-ANALYTICS.md:14`); vem depois dos incrementos de integração e ANTES de qualquer P8 medir |
| 10 | **A10** | Último por dependência declarada: "O consumo vendor allowlistado de OP01/OP02/OP08 movimenta linhas cuja qualificação pertence às admissões das respectivas capacidades (A2/A1/A5)" (`A10` dossiê §Identidade) — P6.5 fecha a fila |

Regra transversal a todos: nenhum pacote de admissão cria "preparação de código" que simule a admissão (anti-padrão v3 §9.1, `ADMISSIONS-README.md:23`); cada PR traz o teste negativo da §2(iv) correspondente como asserção de regressão, e o loader fail-closed do pin (portal-side) valida o sha do contrato `d1d796b8…` em toda leitura.

## 6. Reprodução

```sh
# contrato congelado (deve bater d1d796b8…)
shasum -a 256 tests/candidate_artifacts/commercial/contract-c6-4.json

# linhagem 13/13 (seção 3 do CONTRACT-FREEZE-C6-4-REGISTER.md)
shasum -a 256 tests/candidate_artifacts/commercial/contract-c6-4.json \
  tests/candidate_artifacts/commercial/PROVENANCE-C6-4.json \
  config/integrations/amh/commercial-c6-4.source.lock.json \
  config/integrations/amh/contracts.lock.json \
  docs/delivery/commercial-platform/AMH-C6-4-MAIN-ORIGIN.json \
  docs/delivery/commercial-platform/AMH-C6-4-MAIN-ORIGIN.md \
  docs/delivery/commercial-platform/MAEZO-C6-4-DELTA.md \
  src/maezo/ports/commercial.py src/maezo/adapters/amh/commercial.py \
  src/maezo/adapters/amh/commercial_http.py src/maezo/gateway/commercial.py \
  src/maezo/gateway/commercial_admission.py src/maezo/portal/api/commercial.py

# pin espelhado × cópia no portal (deve ser idêntico bcfe88f9…)
shasum -a 256 docs/delivery/commercial-platform/portal-mirror/PORTAL-MIRROR-PIN-C6-4.json \
  /Users/familia/code/omni_sales_platform/docs/delivery/commercial-platform/PORTAL-MIRROR-PIN-C6-4.json

# prateleira do portal (leitura read-only)
git -C /Users/familia/code/omni_sales_platform rev-parse HEAD   # 917c1cbd…
cat /Users/familia/code/omni_sales_platform/docs/delivery/admissions/A-INDEX.json
```

Este documento não aprova, não ratifica e não habilita nada (ADR-0007); blocos de decisão humana permanecem com os donos nomeados. Registro maquinável espelho: `GAP-CATALOG-A1-A10.json` (mesmo diretório).
