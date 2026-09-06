# LGPD DSR — Contract ↔ BPMN ↔ Code Topic Reconciliation (T2.8, defects B8/B12)

**Status:** `DRAFT — analysis drafted, pending R1 verification; topic edits are NOT applied here`
**Author:** compliance-analyst (R1) · **Scope:** SP-OP-LGPD-DSR-001 external-task topic vocabulary
**Deliverable type:** ANALYSIS + reconciliation-direction recommendation for the orchestrator and DPO.
This document is READ-ONLY on contracts/spec/code — it recommends a direction per divergence; it
applies none of them.

## 0. Method and sources (all evidence path:line)

Three topic vocabularies were extracted verbatim and diffed:

| Source | Role | Evidence |
|---|---|---|
| Contract (SME-facing) | mirror of the process, sent to DPO/jurídico | `docs/processes/contracts/SP-OP-LGPD-DSR-001.md:55-69` (§Tópicos) |
| BPMN (executable spec — **SoT**, constraint 5) | `camunda:topic` on each external service task | `spec/processes/bpmn/SP-OP-LGPD-DSR-001_Direitos_do_Titular.bpmn` (topics inline, cited per row) |
| DMN (executable spec — router) | routing decision consumed by BPMN | `spec/processes/dmn/lgpd_dsr_routing.dmn:32-107` |
| Worker code | `WorkerBase.topic` per class + `register_lgpd_workers` bootstrap | `src/maezo/tools/workers/lgpd.py` (post-T1.2) |
| Unit tests (this repo, editable — NOT donor) | assert code topic names | `tests/unit/tools/workers/test_lgpd_erasure.py:26-345` |
| Integration test-spec (drives T3.1 donor port) | references BPMN/contract topic names | `docs/processes/test-specs/SP-OP-LGPD-DSR-001.md:15-92` |

**Governing rule (constraint 5):** `spec/` (BPMN + DMN) is the source of truth. The contract mirrors
it. Therefore, where the code diverges from a spec/ topic, the **default reconciliation direction is
code → spec**, unless a divergence carries LGPD-obligation or L0-guard semantics that require DPO/SME
sign-off before any code change (flagged per row).

## 1. Headline finding

**Contract and BPMN are 100% aligned** on all seven external-task topics, the one BPMN message, and the
three domain events. **The entire divergence is Contract/BPMN ↔ Code.** The worker code (`lgpd.py`) was
written to a *different decomposition* than the ratified BPMN: only 1 of the code's 6 worker topics
(`operadora.lgpd.verify_identity`) matches a `camunda:topic` in the BPMN. The bootstrap comment in
`lgpd.py:407-416` already acknowledges this drift as pre-existing (from T1.1), out of scope for T1.2.

- **Aligned topics:** 1 (`operadora.lgpd.verify_identity`).
  *(Atualização 2026-09-06: são **5** — R-B `request_additional_proof`, R-F `send_response`,
  R-G `notify_sla_risk` e R-181 `execute_request` foram construídos/conformados desde então.)*
- **BPMN/contract topics with NO worker:** 5 (`request_additional_proof`, `compile_data_package`,
  `execute_request`, `send_response`, `notify_sla_risk`).
  *(Atualização 2026-09-06: sobra **1** — `compile_data_package`, R-C, DPO/SME-gated.)*
- **Orphan code topics not in BPMN/contract:** 5 (`assess_request`, `execute_export`,
  `execute_rectification`, `execute_erasure`, `publish_completed`).
  *(Atualização 2026-09-06: sobra **ZERO** — O1/R-E em T2.8, O5/R-H em `abb9d60`, O2-O4/R-181
  em 2026-09-06.)*
- **Shared / deliberately-unregistered:** 1 (`operadora.events.publish` — generic publisher, present
  in the BPMN on 5 tasks, intentionally NOT registered per any single process per ADR-0026 §2b;
  `lgpd.py:425` explicitly declares no seam. **Not a defect.**).

**Recommended direction: 7 reconciliation actions, all `code → BPMN/contract` (spec/ is SoT). 3 are
gated on DPO/SME sign-off (they move where LGPD/L0 guards live); 4 are mechanical wiring/renames.**

## 2. Three-way external-task topic table

Legend: ✓ present · ✗ absent · ► BPMN task id.

| # | Contract topic (`:55-69`) | BPMN `camunda:topic` (task) | Worker class · topic (`lgpd.py`) | Unit test asserts | Verdict |
|---|---|---|---|---|---|
| T1 | `operadora.lgpd.verify_identity` | ✓ ►`ST_VerificarIdentidade` (`bpmn:86`) | `ValidateIdentityWorker` · `verify_identity` (`:48`) | ✓ (`:29`) | **ALIGNED** |
| T2 | `operadora.lgpd.request_additional_proof` | ✓ ►`ST_PedirProvaAdicional` (`bpmn:134`) | ✗ no worker | — | **MISSING WORKER** |
| T3 | `operadora.lgpd.compile_data_package` | ✓ ►`ST_CompilarPacote` (`bpmn:182`) | ✗ no worker | — | **MISSING WORKER** |
| T4 | `operadora.lgpd.execute_request` | ✓ ►`ST_ExecutarRequisicao` (`bpmn:271`) | ✓ `ExecuteRequestWorker` (R-181) | ✓ | **ALIGNED — FECHADA 2026-09-06** (R-181 / decisão do dono; só a TOPOLOGIA, a habilitação da execução real segue DPO-gated) |
| T5 | `operadora.lgpd.send_response` | ✓ ►`ST_EnviarResposta` (`bpmn:278`) | ✗ no worker | — | **MISSING WORKER** |
| T6 | `operadora.lgpd.notify_sla_risk` | ✓ ►`ST_NotificarRiscoSla` (`bpmn:217`) + ►`ST_NotificarJuridicoBreach` (`bpmn:325`) | ✗ no worker | — | **MISSING WORKER** |
| T7 | `operadora.events.publish` (generic) | ✓ 5 service tasks (`bpmn:64,110,156,287,315`) | ✗ (shared/out-of-scope, ADR-0026 §2b; `lgpd.py:425`) | — | **SHARED — not a defect** |
| O1 | ✗ (routing is a DMN) | ✗ (routing = ►`BRT_RotearDsr` DMN `lgpd_dsr_routing`, `bpmn:172`) | `AssessRequestWorker` · `assess_request` (`:112`) | ✓ (`:99`) | **ORPHAN CODE TOPIC** |
| O2 | ✗ | ✗ | ~~`ExecuteExportWorker` · `execute_export` (`:175`)~~ **COLAPSADO** em `ExecuteRequestWorker` (T4) | ~~✓~~ asserts migrados | **ORPHAN CODE TOPIC — FECHADA 2026-09-06** (R-181, decisão do dono; ver R-D) |
| O3 | ✗ | ✗ | ~~`ExecuteRectificationWorker` · `execute_rectification` (`:235`)~~ **COLAPSADO** em `ExecuteRequestWorker` (T4) | ~~✓~~ asserts migrados | **ORPHAN CODE TOPIC — FECHADA 2026-09-06** (R-181, decisão do dono; ver R-D) |
| O4 | ✗ | ✗ | ~~`ExecuteErasureWorker` · `execute_erasure` (`:292`)~~ **COLAPSADO** em `ExecuteRequestWorker` (T4) | ~~✓~~ asserts migrados | **ORPHAN CODE TOPIC — FECHADA 2026-09-06** (R-181, decisão do dono; ver R-D) |
| O5 | ✗ (BPMN publishes via T7) | ✗ | ~~`PublishCompletedWorker` · `publish_completed` (`:380`)~~ **RETIRADO** | ~~✓ (`:312`)~~ **3 testes removidos com o worker** | **ORPHAN CODE TOPIC — FECHADA 2026-09-04** (R-H aplicado; gap `LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC`, decisão do dono R-103) |

**Message + domain events (all three sources aligned — no divergence):**
`msg.lgpd.proof_received` (`contract:69`, `bpmn:13,146`); domain events
`agents.events.lgpd_dsr.{received,sla_breached,completed}` (`contract:59-61`, `bpmn:67,113,159,290,318`).
The code references `agents.events.lgpd_dsr.completed` as a return-payload string (`lgpd.py:216,270,334,362,402`)
but does not own `sla_breached` emission — consistent, because emission is the BPMN's generic-publisher job (T7),
not a worker-owned topic.

## 3. Per-divergence reconciliation direction (grounded in LGPD Art. 18/19 + spec/ SoT)

Each row states the recommended fix side, why, and whether it is mechanical or requires DPO/SME
sign-off. **All directions point `code → BPMN/contract`** — the BPMN is the ratified SoT and the
donor integration fixtures (§4) already expect the BPMN topic names.

### R-A · `verify_identity` (T1) — NO ACTION
Aligned across all three. GAP-LGPD-6 identity guard (`ERR_DSR_IDENTITY_UNVERIFIED`) matches in code
(`lgpd.py:74`) and BPMN (`bpmn:14,105`). Keep.

### R-B · `request_additional_proof` (T2) — FIX CODE · MECHANICAL
Add a worker on the BPMN topic. Anti-social-engineering identity path (LGPD Art. 18 subject-rights
request is a classic exfiltration vector — `bpmn:87`). No denial/adverse semantics; pure wiring.

### R-C · `compile_data_package` (T3) — FIX CODE · **DPO/SME SIGN-OFF**
Add a worker on the BPMN topic. In the BPMN, `ST_CompilarPacote` runs *before* the human review and
branches by `roteamento_dsr.fluxo`, and its documentation (`bpmn:183`) says it must include the
"mapa de bases legais e obrigações de retenção aplicáveis." The **topic name is mechanical**, but the
**content** (legal-bases map / retention-obligation matrix per data type) is exactly the
"matriz de bases legais de retenção por tipo de dado" listed as pending in `contract:103-104` and
`review-queue.md:22` → **DPO + jurídico must define that matrix** before this worker can be honest.
Note: this compile step SUBSUMES the "compile a package" behaviour the code currently misplaces in
`ExecuteExportWorker` (O2, which compiles *after* approval — a model mismatch; see R-E).

### R-D · `execute_request` (T4 vs O2/O3/O4) — FIX CODE · **DPO/SME SIGN-OFF (L0)** — **METADE DE TOPOLOGIA APLICADA 2026-09-06**

> **APLICADO — SÓ A TOPOLOGIA** (decisão do dono **R-181**, `UNLOCK-LEDGER.yaml`, classe A:
> "Colapsar os três `Execute*Worker` num único worker no tópico modelado
> `operadora.lgpd.execute_request` ... mantendo cada caminho de execução fail-closed; o DPO
> ratifica depois apenas QUANDO a execução real é habilitada, não a topologia").
>
> O que pousou: `ExecuteExportWorker`/`ExecuteRectificationWorker`/`ExecuteErasureWorker` foram
> APAGADOS e substituídos por `ExecuteRequestWorker` em `operadora.lgpd.execute_request`, que
> despacha pelo `tipo_requisicao` declarado (contrato + `bpmn:documentation`) e **recusa em todo
> caminho** com `WorkerFailureError(retries_left=0)`. As três condições que esta seção exige que
> o colapso preserve estão preservadas e ENDURECIDAS: (a) nenhuma execução sem aprovação humana,
> (b) nenhuma execução sem `EXECUTAR_E_ENVIAR` explícito, (c) `NEGAR_FUNDAMENTADO` nunca executa
> — agora todas as três, mais qualquer outra entrada, terminam em incidente.
>
> **Rota escolhida entre as duas que esta seção enumera: (ii).** Nenhum `ERR_DSR_ERASURE_*` é
> levantado — eles seguem DECLARADOS e NÃO-VINCULADOS, e levantá-los reprovaria
> `scripts/ci/check_bpmn_error_allowlist.py`. A rota (i) — adicionar boundary catches em
> `ST_ExecutarRequisicao` — é mudança de spec e **continua DPO/SME-gated**.
>
> **Descoberta registrada durante a aplicação:** devolver um dict de guard (o que os três workers
> aposentados faziam) seria FAIL-OPEN no tópico modelado, porque `Flow_Executar_Enviar` não tem
> `conditionExpression` — qualquer `complete` avança o token para `ST_EnviarResposta` →
> `event_desfecho=atendida`. Além disso `human_approved`, o sinal dos guards antigos, **não
> existe no BPMN nem no contrato** (grep = 0 nos dois): nenhuma instância real o semeia. Por isso
> a recusa é total e a mensagem de todo incidente lidera com o fato invariante (nada implementado)
> antes de nomear o guard.
>
> **NÃO é uma ratificação:** este documento continua `DRAFT` e não pode ser citado como sign-off.
> R-C segue DPO/SME-gated; a habilitação da execução REAL segue gated no DPO (F-2 + matriz AF-07);
> nenhum marcador `DRAFT`/`verify` foi removido; nenhum campo de signoff foi tocado.
The BPMN has ONE generic execute task `operadora.lgpd.execute_request`, reached ONLY on the
`EXECUTAR_E_ENVIAR` path (`bpmn:270-275,367-369`). The code splits execution into THREE topics
(`execute_export`/`execute_rectification`/`execute_erasure`). These three carry the L0-hard guards:
`ERR_DENIAL_NOT_HUMAN` + fail-closed `decisao_dsr == 'EXECUTAR_E_ENVIAR'` + `NEGAR_FUNDAMENTADO`
handling (`lgpd.py:311-363`). Collapsing to the single BPMN topic is the correct direction (spec SoT),
but is **load-bearing** and needs sign-off because:

  - **Error-code divergence (secondary finding):** the BPMN declares `ERR_DSR_ERASURE_FAILED` and
    `ERR_DSR_ERASURE_NOT_HUMAN` (GAP-LGPD-2 defence-in-depth, `bpmn:19,22`) that **no worker emits
    today** — the code returns the generic `ERR_DENIAL_NOT_HUMAN` (`lgpd.py:319,346`). Precision on
    the BPMN side: these two error definitions are **declared but UNBOUND** — no `errorRef` /
    boundary event anywhere in the file references them (the only error bindings are the
    `Error_LgpdIdentidade` boundary at `bpmn:105-108` and the error END events at `bpmn:251,266`),
    and `ST_ExecutarRequisicao` has **no boundary event at all**. So mapping the collapsed
    `execute_request` worker's guards to the `ERR_DSR_ERASURE_*` codes requires EITHER (i) ALSO
    adding boundary catches on `ST_ExecutarRequisicao` in the BPMN — a spec-side change needing SME
    sign-off — OR (ii) relying on the T1.1 §9 harness behaviour: a `bpmnError` code not in the
    harness's `bpmn_error_allowlist` is demoted to `failure(retries=0)` → immediate engine incident,
    never silently dropped (`src/maezo/tools/workers/harness.py:44-49,132-133`). Either route is
    fail-closed (incident, not silent scope-kill — the GAP-LGPD-6 hazard class the BPMN's own
    comments warn about, `bpmn:15-18`); the choice between them is part of the R-D sign-off.
  - **Model mismatch (export path):** in the BPMN, the `APROVAR_ENVIO` (send-only) path goes STRAIGHT
    to `ST_EnviarResposta` with NO execute task (`bpmn:373-375`); only `EXECUTAR_E_ENVIAR` executes.
    The code's `ExecuteExportWorker` (a post-approval "compile package" task, O2) has no BPMN
    counterpart — its compilation belongs at `compile_data_package` (R-C, pre-human), and export
    delivery belongs at `send_response` (R-F). This is a genuine decomposition difference, not a rename.

    Because the erasure/denial guard surface is L0 (ADR-0005/ADR-0008), the DPO + the worker-owning
    lane must jointly confirm the guard mapping preserves: (a) no erasure without `human_approved`,
    (b) no erasure without explicit `EXECUTAR_E_ENVIAR`, (c) `NEGAR_FUNDAMENTADO` never erases.

### R-E · `assess_request` (O1) — FIX CODE (RETIRE) · **DPO/SME SIGN-OFF (routing)**
Retire `operadora.lgpd.assess_request`. In the BPMN, routing (fluxo / grupo_revisor / SLA) is a **DMN
decision** `lgpd_dsr_routing` evaluated engine-side by `BRT_RotearDsr` (`bpmn:172-179`; ADR-0028:
DMN evaluated engine-side, ratified). The code worker **duplicates** the DMN's logic (`lgpd.py:128-140`)
and is already **drifting**: it omits the DMN's `sla_resposta`/`sla_alerta` outputs (`dmn:42-43`) and
its health-data→jurídico branching does not mirror DMN rules r1–r7 exactly. This is precisely the
GAP-LGPD-5 drift risk the DMN `<description>` flags (`dmn:17-30`). **DPO/SME confirm the DMN is the
sole router**; then the worker is retired (no external task needed).

### R-F · `send_response` (T5) — FIX CODE · MECHANICAL (DPO content note)
Add a worker on the BPMN topic. This delivers the human-approved response: data package, execution
confirmation, OR *negativa fundamentada* with `fundamentacao_legal` (`bpmn:277-284`). LGPD Art. 18/19-II
response obligation. Topic wiring is mechanical; the **negativa-fundamentada wording is DPO-facing**
but is authored by the human reviewer in `UT_RevisaoDpo`, not by the worker — so no legal content is
hard-coded. Confirm with DPO that the response envelope is acceptable.

### R-G · `notify_sla_risk` (T6) — FIX CODE · MECHANICAL (DPO escalation note)
Add ONE worker on the BPMN topic serving BOTH service tasks. It must distinguish the P7D internal
alert (ack phase, `bpmn:226-227`) from the P15D legal-deadline breach (resolution phase,
`bpmn:331-332`) via the `sla_breach_task_name`/`sla_breach_phase` inputParameters (the ExternalTask
does not expose `activityId`, so these labels are the only discriminator — `bpmn:221-228`). The P15D
leg is the LGPD Art. 19-II legal deadline; **DPO/jurídico confirm the escalation target** for the
breach notification. Wiring is mechanical.

### R-H · `publish_completed` (O5) — FIX CODE (RETIRE) · MECHANICAL — **APLICADO 2026-09-04**
Retire `operadora.lgpd.publish_completed`. The BPMN publishes completion via the shared
`operadora.events.publish` generic publisher, with `event_topic` / `event_desfecho` computed in-engine
(GAP-LGPD-7 JUEL, `bpmn:290-296`). The dedicated code worker is redundant with the shared publisher
(T7), which is out-of-scope per ADR-0026 §2b. Mechanical.

> **APLICADO** (gap `LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC`, decisão do dono R-103, 2026-09-04).
> `PublishCompletedWorker`, o seu registro em `register_lgpd_workers`, os **3** testes unitários
> que o pinavam (`test_publish_completed_topic` / `_publishes_event` / `_includes_desfecho`) e a
> linha de docstring que reivindicava o tópico foram removidos de
> `src/maezo/tools/workers/lgpd.py` / `tests/unit/tools/workers/test_lgpd_erasure.py`.
> **Base da remoção:** o FATO de engenharia — `grep -rn "operadora.lgpd.publish_completed" spec/`
> = 0 ocorrências, e `ST_PublishCompleted` (`bpmn:286-297`) publica a conclusão pelo publicador
> genérico T7 — **mais** a classificação `Fix code → spec · mechanical` da tabela de direções
> abaixo. **NÃO** uma ratificação: este documento continua `DRAFT` (linha 3) e continua
> READ-ONLY sobre contratos/spec/código; nenhum PR pode citá-lo como sign-off. R-C/R-D/R-E do
> mesmo documento seguem **DPO/SME sign-off** e continuam bloqueados; nenhum marcador
> `DRAFT`/`verify` foi removido. As referências `path:line` deste documento a `lgpd.py` são
> anteriores à remoção e ficaram deslocadas — cite símbolos, não linhas.

### Reconciliation-direction summary

| Direction | Count | Items |
|---|---|---|
| No action (aligned) | 1 | T1 verify_identity |
| No action (shared/out-of-scope) | 1 | T7 operadora.events.publish |
| Fix code → spec · **mechanical** | 4 | R-B request_additional_proof, R-F send_response, R-G notify_sla_risk, R-H retire publish_completed |
| Fix code → spec · **DPO/SME sign-off** | 3 | R-C compile_data_package (legal-bases/retention map), R-D execute_request (L0 guard collapse + ERR_DSR_ERASURE_* mapping), R-E retire assess_request (DMN is sole router) |

**Every actionable divergence reconciles toward the BPMN/contract (spec/ SoT). Zero cases recommend
changing the contract or BPMN topics.**

## 4. Which side the tests expect (load-bearing for T3.1's donor port)

Two test surfaces disagree, and the disagreement *confirms* the code→spec direction:

- **This-repo UNIT tests** (`tests/unit/tools/workers/test_lgpd_erasure.py:29,99,157,241,284,312`)
  assert the **code** topic names (`assess_request`, `execute_export/rectification/erasure`,
  `publish_completed`). These are v2/current-repo tests, editable by the worker-owning lane —
  reconciling code→spec requires updating these asserts. **They are NOT donor fixtures.**
  *(Atualização 2026-09-04: os asserts de `assess_request` já haviam saído com R-E, e os de
  `publish_completed` saíram com R-H — os 3 testes foram REMOVIDOS junto com o worker, não
  reescritos. Restam os asserts de `execute_export/rectification/erasure`, cujo destino é R-D,
  DPO/SME-gated.)* *(Atualização 2026-09-06: também saíram — R-181 migrou os asserts para
  `execute_request`, com o mapa old→new no comentário `MAPA DE MIGRACAO` do próprio arquivo de
  teste. **Nenhum** assert de tópico de código órfão resta.)*
- **Integration test-spec** (`docs/processes/test-specs/SP-OP-LGPD-DSR-001.md:18,48,56`), which drives
  the **T3.1 donor port** from `Maezo-Healthcare-Plan/tests/integration/`, references the **BPMN/contract**
  topic names: `operadora.lgpd.execute_request` (`:18`), `compile_data_package` (`:48,56`),
  `send_response`, `request_additional_proof`, `notify_sla_risk`. **The donor integration side already
  expects the BPMN topics.**

**Consequence:** the code→spec direction is consistent with the donor. T3.1 needs NO donor edits (v1
READ-ONLY preserved); only the current-repo unit-test topic asserts move, done by the worker lane when
the workers are re-decomposed. Fixture topic names in the donor are load-bearing and already match the
recommended target vocabulary.

## 5. Handoff / ownership

- **Worker-owning lane (not compliance-analyst):** applies R-B..R-H in `lgpd.py` + updates
  `test_lgpd_erasure.py`. Compliance does not edit workers or tests (charter constraint).
- **DPO + jurídico (external, blocked):** sign off R-C (legal-bases/retention matrix content), R-D
  (erasure/denial L0 guard mapping to `ERR_DSR_ERASURE_*`), R-E (DMN as sole router). Tracked in
  `docs/review-queue.md:22-23`.
- **Orchestrator:** sequences the code re-decomposition after DPO sign-off on the three gated items;
  the 4 mechanical items can proceed in parallel.

**No topic was changed by this document. This is analysis + recommended direction only, pending R1
verification and DPO/SME sign-off.**
