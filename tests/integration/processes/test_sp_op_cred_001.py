"""SP-OP-CRED-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 2 (13-family
process-suite port). Implementa a parte 5 de 5 (ADR-0018) contra o engine real (ADR-0011: SEM
mock de engine), para as DUAS direcoes adversas do contrato docs/processes/contracts/
SP-OP-CRED-001.md. Cada teste:

1. inicia a instancia via REST com business key `CRED-amh-{prestador_id}`;
2. drena as external tasks com o `cred_probe` (workers reais + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que descredenciamento E negativa de
   credenciamento passam por User Task humana — no-adverse / provider_decredentialing L1).

Dados sinteticos obvios: prestador `PREST-TESTE-NNNN` (cadastral pseudonimo — NUNCA CPF/nome
real), tenant `amh`, origem `TESTE`. Business key `CRED-amh-{prestador}`. Process key:
SP-OP-CRED-001 (exato — nao alterar).

credenciamento.py e um modulo "dict-first FunctionWorker-wrapped" (bootstrap.py linha 11) — NAO
o padrao WorkerBase-per-topic de auth.py. Mirrors test_sp_op_cancel_001.py's dict-boundary
pattern (constraint: credenciamento.py e "dict-first FunctionWorker-wrapped module").

## Invariante L1 (DoD deliverable) — provider_decredentialing / no-adverse (DUAS direcoes)

test_nenhum_caminho_automatizado_descredencia_ou_nega:
  Varredura de combinacoes de input das DMN cred_admissibility / cred_route (96 combinacoes). A
  instancia NUNCA atinge End_PrestadorDescredenciado / End_CredenciamentoNegado sem que
  UT_AnaliseDescredenciamento / UT_AnaliseCredenciamento (ou as UTs de coordenacao) tenham sido
  completadas por humano. A prova e feita consultando a historia do engine
  (history/activity-instance). Assertion strength preserved BYTE-IDENTICAL from the donor (never
  weakened) — this test holds VACUOUSLY under every finding below (an instance stuck waiting on
  an unregistered topic never reaches an adverse terminal either), so it is left UNMARKED.

WorkerHarness NAO retorna output variables ao engine da MESMA forma que o BPMN nativo consome:
os workers observam/ecoam/publicam via `kafka`, mas a DECISAO de roteamento (BRT_Admissibilidade
/ BRT_Route/BRT_CredSla/BRT_PriorNotice, todas `businessRuleTask` NATIVAS do engine) le variaveis
de PROCESSO diretas (documentacao_completa, licenca_valida, dentro_criterios_rede, direcao,
tipo_prestador, indicio_irregularidade_sinalizado etc.) — nao um valor computado por um
`FunctionWorker` sob um nome diferente (`admissibilidade.roteamento`/`rota.roteamento`, sempre
setados pela PROPRIA businessRuleTask nativa, nunca por um worker). Por isso o TESTE seed os
fatos clericais como variaveis de START (o teste e o agente de origem; mesma tecnica de
test_sp_op_cancel_001.py) — EXCETO que, ao contrario do donor, `verify_credentials`
(`operadora.cred.verify_credentials`) AQUI *sobrescreve* `licenca_valida`/`documentacao_completa`
apos rodar (ver FINDING 2 abaixo — `CibSevenWorkerTransport.complete()` FORWARDS o dict de saida
do worker como variaveis de processo reais; isto NAO era verdade no donor v1).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor except
where explicitly ADAPTED and called out below):
  - import paths -> v2 `maezo.tools.workers.credenciamento`/`harness`/`dmn_transport`/`events`.
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5), NOT the donor's
    `src/maezo/processes/{bpmn,dmn}/**`.
  - `engine` fixture: REUSED from the shared `conftest.py` (v2 hoists exactly `engine` +
    `drain_topics()` — conftest.py module docstring "port rule 3"), NOT redefined locally,
    UNLIKE the donor's own docstring ("redefine `engine`, `deploy_artifacts`, `cred_probe`,
    `start_cred` localmente") — that quote is v1-era (v1 had no shared conftest.py at all); v2's
    `test_sp_op_cancel_001.py` (the exemplary self-contained-fixture template this file mirrors)
    does the SAME thing (imports `CIBSEVEN_BASE_URL`/`drain_topics` from `.conftest`, never
    redefines `engine`). `deploy_artifacts`, `CredEngineProbe`, `cred_probe`, `start_cred` stay
    LOCAL (self-contained, matching cancel's convention — the probe construction contract is
    family-specific).
  - `drain()` uses the shared `drain_topics()` helper (v2 `WorkerTransport.fetch_and_lock`
    signature adaptation — see `conftest.py`), instead of the donor's own inline fetch loop.
  - NEW seam not present in the donor: `assess_admissibility` (the `operadora.cred.
    check_network_criteria` worker fn) takes a `dmn=` keyword (ADR-0028 §1 DMN-evaluation seam,
    `dmn_transport.py`) — `require_dmn` raises `DmnEvaluationError` if unwired. `cred_probe` wires
    a REAL `CibSevenDmnTransport(CIBSEVEN_BASE_URL, timeout=30.0)` (mirrors
    `tests/integration/agents/test_rafael_auth_dossier.py`'s / `test_helena_escalation.py`'s own
    `CibSevenDmnTransport(engine_base_url, timeout=30.0)` convention) so this worker executes
    cleanly against the SAME deployed `cred_admissibility`/`cred_route` tables the BPMN's own
    native `BRT_Admissibilidade`/`BRT_Route` businessRuleTasks evaluate — this is fixture
    plumbing, NOT a v2 gap (leaving it unwired would raise `DmnEvaluationError` on EVERY
    `check_network_criteria` task, an artifact of an incomplete probe, not a genuine finding).
  - `register_credenciamento_workers(harness, kafka, dmn=dmn)`; also registers
    `maezo.tools.workers.events.register_events_workers` (T3.1 R2, already merged — mirrors the
    donor's own `register_phase0_workers` composition; every `ST_Publish*` service task in this
    BPMN routes through `operadora.events.publish`).
  - `test_register_descredenciamento_recusa_sem_humano` / `test_register_cred_denial_recusa_sem_
    humano` (no engine — unit-style over the real functions): v2's dict-boundary entry points are
    `register_descredenciamento(variables: dict) -> dict` / `register_cred_denial(variables:
    dict) -> dict` (plain dict-first functions, ADR-0026 Decisao §2a — NOT donor's
    `make_register_descredenciamento_handler(kafka) -> Callable[[ExternalTask], ...]`, which does
    not exist on v2 main). The guard ALSO changed shape (verify each, port rule 1): v2's guard
    raises `CredError(code, message)` (credenciamento.py:206/256) — a bespoke `.code`/`.message`
    exception (ADR-0026 census: one of 6 "coded exception" modules), NOT donor's
    `WorkerBpmnError(error_code=...)`. `register_credenciamento_workers` explicitly discards its
    `kafka` argument (`del kafka  # unused`, credenciamento.py:312) — THIS module has ZERO Kafka
    dependency (unlike cancel.py's `send_cancellation_notice_entry`, which retains a `kafka=`
    kwarg for shape compatibility even though it doesn't call it either) — so the adapted tests
    below call the functions with no `kafka` argument at all. The 6 guard scenarios (a-f) are
    preserved verbatim in INTENT (missing decision / neutral decision / all-required-fields-
    present); the SUCCESS-case assertions are adapted to what v2's functions actually return
    (`descredenciamento_registrado`/`network_changed`/`data_efeito_iso` and
    `credenciamento_negado` respectively) — v2 does NOT echo `responsavel_id`/`tier` back nor
    generate a `descredenciamento_id`/`negativa_id` tracking value the donor's handler did; this
    is a data-contract simplification (the GUARD logic — which fields are REQUIRED to avoid
    `CredError` — is preserved byte-for-byte), not a security weakening.
  - Synthetic data mirrors the donor's `PREST-TESTE-NNNN` / `CONTRATO`-style convention exactly
    (`_unique_prestador` -> `PREST-TESTE-{8 hex}`).

FINDINGS (root-cause, file:line evidence — this port's own discoveries; NOT re-litigating
already-fixed cross-family facts unless they apply):

  1. REGISTRY DRIFT (dominant finding — blocks the MAJORITY of the donor's HITL/happy-path
     tests): the BPMN (`spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn`) declares 9
     distinct `operadora.cred.*` external-task topics (grepped: `verify_credentials`,
     `check_network_criteria`, `check_prior_notice`, `notify_doc_pendente`, `prepare_dossier`
     [x2 — descred AND cred branches, SAME topic], `register_credenciamento`,
     `register_cred_denial`, `register_descredenciamento`, `notify_sla_risk`).
     **UPDATE (T2.5-p2b, this task's own follow-up build):** `register_credenciamento_workers`
     (credenciamento.py) now registers 8 of these 9 — the original port snapshot's 5
     (`verify_credentials` -> `validate_cred`, `check_network_criteria` -> `assess_admissibility`,
     `check_prior_notice` -> `notify_prestador`, `register_descredenciamento`, `register_cred_
     denial`) PLUS 3 newly-built neutral/notify-only workers: `notify_doc_pendente` (fail-safe,
     never denies — PENDENTE_DOCUMENTACAO branch), `register_credenciamento` (EXCECAO CLERICAL —
     the favorable/neutral direction, no human-gate by BPMN design), `notify_sla_risk`
     (informational, non-interruptive, shared by both directions' boundary timers). Mechanically
     built + wired (`_CRED_WORKER_TOPICS` synced below, mirrors the #93/#94 inadimplencia
     precedent — `d27264e`); **NOT live-proven** — no docker/live-engine run was performed to
     build this (HARD CONSTRAINT). Only `operadora.cred.prepare_dossier` remains genuinely
     unimplemented (Carolina A2A integration, separately gated, explicitly out of scope for
     T2.5-p2b) — `ST_PrepareDossierDescred` AND `ST_PrepareDossierCred` (BPMN lines 303-309,
     498-503) BOTH route through this SAME unimplemented topic, and BOTH sit immediately upstream
     of `UT_AnaliseDescredenciamento`/`UT_AnaliseCredenciamento` (the ONLY two entry points into
     the human-decision branches) — so every test that needs to reach either User Task via the
     descredenciamento branch (which ALWAYS passes through `ST_PrepareDossierDescred`) remains
     blocked on this ONE residual topic; the instance still stalls at the dossier step (not in
     `_CRED_WORKER_TOPICS`'s drain subscription by design — see the module-level topic
     constants). `_CRED_MISSING_WORKERS_REASON` below reflects this narrowed, single-topic gap.
     A SEPARATE, PRE-EXISTING gap (finding 5 below, unaffected by this task): NONE of the 8 now-
     registered `operadora.cred.*` functions (the original 5 OR the 3 built here) calls
     `kafka.publish` for the `operadora.notifications.internal` "type"-tagged notification
     convention several assertions in this file rely on (e.g.
     `cred_probe.notifications_of_type("cred.register_credenciamento")`,
     `cred_probe.notifications_of_type("cred.notify_doc_pendente")`) — this module's dict-first,
     synchronous functions mirror the SAME "not fabricated here" convention documented across
     `ans_submit.py`/`cancel.py`/`contas.py`/`reembolso.py`/`nip.py`/`recurso.py`/
     `inadimplencia.py` (a genuine `kafka.publish` fan-out needs an async seam distinct from these
     sync entry points). Tests that assert on `notifications_of_type(...)` therefore remain
     blocked even where the underlying worker-registration gap is now closed — see the
     per-test notes below and the flip-candidate disclosure in the companion commit.
  2. `documentacao_completa`/`licenca_valida` OVERWRITE BUG (credenciamento-direction-specific):
     `validate_cred` (credenciamento.py:43-65, the `operadora.cred.verify_credentials` worker fn)
     computes `licenca_valida = True` UNCONDITIONALLY (line 52, "placeholder — real: query
     CRM/CNES") and `documentacao_completa = isinstance(documentos, dict) and len(documentos) > 0`
     (line 53) from `documentos_refs`. This suite (mirroring every donor/sibling-family
     convention, e.g. `start_cred`'s own `documentos_refs: "[]"`) sends `documentos_refs` as a
     JSON-encoded STRING, which the engine round-trips as Camunda type `String` (never `Json`) —
     `_from_camunda_var` (harness.py:199-224) only JSON-decodes `Json`-typed variables, so the
     worker receives the Python `str` `"[]"`, and `isinstance("[]", dict)` is always `False`.
     `CibSevenWorkerTransport.complete()` (harness.py:404-410) then forwards the worker's RETURN
     dict as real process variables on `/external-task/{id}/complete` — overwriting whatever
     `documentacao_completa`/`licenca_valida` this suite seeded as START variables, regardless of
     the test's intended scenario. `cred_admissibility.dmn`'s `hitPolicy="FIRST"` table (line 37)
     checks `r_cred_doc_pendente` (direcao=credenciamento AND documentacao_completa=false,
     lines 80-90) BEFORE `r_cred_clerical`/`r_cred_humano` — so EVERY credenciamento-direction,
     non-indicio test (which seeds `documentacao_completa=True`/`licenca_valida={True,False}` to
     reach `CLERICAL_CREDENCIAR`/`ANALISE_HUMANA`) instead ALWAYS reroutes to
     `PENDENTE_DOCUMENTACAO` once `verify_credentials` completes. **UPDATE (T2.5-p2b):**
     `notify_doc_pendente` is now registered (finding 1 closed for this topic specifically) — the
     instance no longer stalls unserved at `ST_NotifyDocPendente`, but this finding's OWN root
     cause (the overwrite bug itself) is untouched and remains the independent blocker for every
     test tagged `_CRED_DOC_COMPLETA_OVERWRITE_REASON` below: the rerouted instance now correctly
     reaches `ICE_AguardarInfoDoc` and waits there for `msg.cred.info_received` (never sent by
     these tests), so it still never reaches the `ANALISE_HUMANA`/`CLERICAL_CREDENCIAR` branch the
     test expects. (Descredenciamento-direction tests are UNAFFECTED: `r_descred_segue`, line
     69-79, matches on `direcao` alone.)
  3. `ERR_CRED_INVALID_PRESTADOR` NEVER RAISED: the constant is declared
     (credenciamento.py:29) and the BPMN declares a matching boundary catch
     (`BE_PrestadorInvalido` / `Error_CredPrestadorInvalido`, BPMN lines 129-136), but
     `grep -n "ERR_CRED_INVALID_PRESTADOR" src/maezo/tools/workers/credenciamento.py` returns
     ONLY the constant declaration — zero raise-sites. `validate_cred` never inspects
     `prestador_id` at all. `End_CredPrestadorInvalido` is therefore categorically unreachable
     via any worker path. `_CRED_INVALID_PRESTADOR_NOT_RAISED_REASON` below.
  4. `CredError` GUARD-SHAPE MISMATCH (defense-in-depth terminal unreachable, guard itself still
     enforced): `register_descredenciamento`/`register_cred_denial` (credenciamento.py:175-221,
     229-266) raise `CredError(code, message)` on a missing-human-decision guard failure — a
     bespoke `.code`/`.message` exception, ADR-0026's documented "coded exception" convention
     (`FunctionWorker.execute()`, base.py:284-293: any exception NOT already `_HARNESS_CLASSIFIED`
     but duck-typing `.code`/`.message` strings is reclassified into a bare `ValueError`).
     `harness._handle`'s `except ValueError` branch (harness.py:952) routes this to
     `_report_failure` (a generic, retries=0 incident) — NEVER to `except WorkerBpmnError`
     (harness.py:916-917, which dispatches `handle_bpmn_error` only for an ACTUAL
     `WorkerBpmnError`). Consequently `BE_DecredNaoHumano`/`BE_CredDenialNaoHumano` (the BPMN's
     own boundary catches for `Error_DecredNotHuman`/`Error_CredDenialNotHuman`) NEVER fire — the
     clean terminals `End_DecredBloqueadoNaoHumano`/`End_CredGuardBloqueadoNaoHumano` the donor
     asserts are unreachable; the instance is left with an open incident instead. The GUARD
     itself is unaffected (no adverse registration ever occurs — `CredError` still blocks the
     write), so this is NOT an L1 regression, only an unreachable "clean fail-safe terminal"
     regression. In THIS suite both affected donor tests are ALSO blocked earlier by finding 1
     (`prepare_dossier` missing, so the instance never even reaches the UT to submit the
     incomplete decision) — finding 4 is the SECOND, independent blocker that would remain even
     after finding 1 is fixed. `_CRED_GUARD_SHAPE_MISMATCH_REASON` below.
  5. Kafka-publish gap (Step 3 systemic fact #1) — was VERIFIED N/A as an INDEPENDENT blocker at
     port-authoring time: `grep -rn "kafka[.]publish(" src/maezo/tools/workers/credenciamento.py`
     returned zero hits (consistent with `register_credenciamento_workers`'s own `del kafka  #
     unused — no credenciamento.py worker declares a Kafka dependency`), and every test that would
     exercise a credenciamento-specific `notifications_of_type(...)` assertion was already blocked
     earlier by findings 1-4. **UPDATE (T2.5-p2b):** this gap is now the FIRST-ORDER blocker for
     `test_documentacao_incompleta_pendente_nunca_nega` specifically (finding 1's
     `notify_doc_pendente` registration gap is closed AND finding 2's overwrite bug happens to be
     a no-op for that one test's exact seed values — see its own docstring update below): the
     assertion `cred_probe.notifications_of_type("cred.notify_doc_pendente")` still returns `[]`
     because `notify_doc_pendente` (like every other function in this dict-first, synchronous
     module — the original 5 AND the 3 built by T2.5-p2b) does not itself call `kafka.publish`
     (mirrors the SAME documented "not fabricated here" convention as `ans_submit.py`/`cancel.py`/
     `contas.py`/`reembolso.py`/`nip.py`/`recurso.py`/`inadimplencia.py` — a genuine fan-out needs
     an async seam distinct from these sync entry points; not fabricated here either). This gap
     remains latent (not newly introduced) for every OTHER `notifications_of_type(...)` assertion
     in this file (`cred.register_credenciamento`, `cred.register_descredenciamento`,
     `cred.register_cred_denial`) — those tests remain blocked by findings 1/2/4 first regardless,
     so this gap stays undetectable for them until those are ALSO fixed.
  6. D-07 (`CeilingResolver`) — VERIFIED N/A: `grep -n "CeilingResolver" src/maezo/tools/workers/
     credenciamento.py` returns zero hits. Only auth.py/pagto.py/reembolso.py import it.
  7. `notification_bridge.py`'s FRAUDE->CRED rule — VERIFIED N/A for this file: the donor's own
     tests (read in full) all start `SP-OP-CRED-001` directly via REST (`start_cred`, business
     key `CRED-amh-{prestador}`) — the test IS the origin agent for every scenario in this suite,
     including `test_indicio_irregularidade_roteia_para_humano_nunca_auto_acusa` (which asserts
     the human decides whether to hand off to SP-OP-FRAUDE-001, never asserts an INCOMING
     auto-start FROM fraude). No test here depends on the bridge being live.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers.credenciamento import (
    CRED_BPMN_ERROR_ALLOWLIST,
    ERR_CRED_DENIAL_NOT_HUMAN,
    ERR_DECRED_NOT_HUMAN,
    register_cred_denial,
    register_credenciamento_workers,
    register_descredenciamento,
)
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    FakeKafkaPublisher,
    WorkerBpmnError,
    WorkerHarness,
)

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn"
_DMN_ADMISSIBILITY = _REPO / "spec/processes/dmn/cred_admissibility.dmn"
_DMN_ROUTE = _REPO / "spec/processes/dmn/cred_route.dmn"
_DMN_PRIOR_NOTICE = _REPO / "spec/processes/dmn/cred_prior_notice.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/cred_sla.dmn"

# External task topics do contrato SP-OP-CRED-001 — 9 declarados no BPMN (grep
# camunda:topic="operadora.cred.*"). T2.5-p2b (this port's follow-up task) BUILT 3 of the
# original 4 missing workers (notify_doc_pendente / register_credenciamento / notify_sla_risk) —
# SOMENTE operadora.cred.prepare_dossier permanece sem worker registrado (Carolina A2A
# integration, separately gated, genuinely out of scope for T2.5-p2b — module bootstrap
# docstring documents this explicitly; NOT fabricated here either).
_PUBLISH_TOPIC = "operadora.events.publish"
_VERIFY_CRED_TOPIC = "operadora.cred.verify_credentials"
_CHECK_NETWORK_TOPIC = "operadora.cred.check_network_criteria"
_CHECK_PRIOR_NOTICE_TOPIC = "operadora.cred.check_prior_notice"
_NOTIFY_DOC_TOPIC = "operadora.cred.notify_doc_pendente"  # registrado desde T2.5-p2b
_REGISTER_DESCRED_TOPIC = "operadora.cred.register_descredenciamento"
_REGISTER_DENIAL_TOPIC = "operadora.cred.register_cred_denial"
_REGISTER_CREDENCIAMENTO_TOPIC = "operadora.cred.register_credenciamento"  # registrado desde T2.5-p2b
_NOTIFY_SLA_TOPIC = "operadora.cred.notify_sla_risk"  # registrado desde T2.5-p2b

# Declarado no BPMN, SEM worker registrado em `register_credenciamento_workers`
# (credenciamento.py bootstrap docstring, "Carolina A2A integration, separately gated") — NAO
# incluido no drain: o drain so deve poll topicos com handler real (poll-lo sem handler so
# mudaria o SINTOMA do stall, nao o resultado). Mantido aqui, documentado, para o finding 1
# residual e para os textos de xfail abaixo.
_PREPARE_DOSSIER_TOPIC = "operadora.cred.prepare_dossier"  # DL-0033 stub (registered; not drained here)

# Topicos servidos pelos workers REAIS registrados no harness (drain generico) — espelha 1:1 o
# que `register_credenciamento_workers` de fato registra (credenciamento.py, T2.5-p2b) + o
# `operadora.events.publish` generico (register_events_workers).
_CRED_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _VERIFY_CRED_TOPIC,
    _CHECK_NETWORK_TOPIC,
    _CHECK_PRIOR_NOTICE_TOPIC,
    _NOTIFY_DOC_TOPIC,  # T2.5-p2b sync
    _REGISTER_DESCRED_TOPIC,
    _REGISTER_DENIAL_TOPIC,
    _REGISTER_CREDENCIAMENTO_TOPIC,  # T2.5-p2b sync
    _NOTIFY_SLA_TOPIC,  # T2.5-p2b sync
    _PREPARE_DOSSIER_TOPIC,  # t2-dossier-a2a #178: real worker (Carolina A2A / DL-0037 gap) drains
]

# Topico interno de notificacoes (harness.py FakeKafkaPublisher convention)
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato SP-OP-CRED-001.md)
_CRED_RECEIVED = "agents.events.cred.received"
_CRED_PENDED = "agents.events.cred.pended"
_CRED_SLA_BREACHED = "agents.events.cred.sla_breached"
_CRED_NETWORK_CHANGED = "agents.events.cred.network_changed"
_CRED_COMPLETED = "agents.events.cred.completed"

# User Task definition keys (BPMN)
_UT_DESCRED = "UT_AnaliseDescredenciamento"
_UT_CRED = "UT_AnaliseCredenciamento"
_UT_COORD_DESCRED = "UT_CoordenacaoRedeDescred"
_UT_COORD_CRED = "UT_CoordenacaoRedeCred"

# End events do processo
_END_DESCREDENCIADO = "End_PrestadorDescredenciado"
_END_CRED_NEGADO = "End_CredenciamentoNegado"
_END_CREDENCIADO = "End_PrestadorCredenciado"
_END_VINCULO_MANTIDO = "End_VinculoMantido"
_END_SUBSTITUICAO = "End_SubstituicaoRegistrada"

# Terminais de boundary-error catch (guards TECNICOS/fail-safe, NAO decisoes adversas
# automatizadas) — ver findings 3/4: hoje inalcancaveis via qualquer caminho de worker.
_END_CRED_PRESTADOR_INVALIDO = "End_CredPrestadorInvalido"
_END_DECRED_BLOQUEADO_NAO_HUMANO = "End_DecredBloqueadoNaoHumano"
_END_CRED_GUARD_BLOQUEADO_NAO_HUMANO = "End_CredGuardBloqueadoNaoHumano"

# User Tasks humanas que podem produzir os desfechos adversos (L1 guard)
_UT_HUMANAS = frozenset({_UT_DESCRED, _UT_CRED, _UT_COORD_DESCRED, _UT_COORD_CRED})

# Os dois terminais ADVERSOS (so via UT humana — provider_decredentialing L1)
_ENDS_ADVERSOS = frozenset({_END_DESCREDENCIADO, _END_CRED_NEGADO})

# Campos obrigatorios de cada decisao adversa humana.
_CAMPOS_DESCRED = {
    "fundamentacao": "Fundamentacao sintetica do descredenciamento (teste L1)",
    "referencia_regulatoria": "RN 567 — DRAFT/verify (teste)",
    "comprovacao_notificacao_previa": "ref-comprovante-notificacao-teste-0001",
    "responsavel_id": "juridico-rede-sintetico-001",
    "tier": "senior",
}
_CAMPOS_DENIAL = {
    "fundamentacao": "Fundamentacao sintetica da negativa (teste L1)",
    "referencia_regulatoria": "RN 566 — DRAFT/verify (teste)",
    "responsavel_id": "gestao-rede-sintetico-001",
    "tier": "pleno",
}

# ---------------------------------------------------------------------------
# _REASON constants (prefixed `_CRED_` — distinct from the parallel programa-family port)
# ---------------------------------------------------------------------------

_CRED_MISSING_WORKERS_REASON = (
    "PARTIAL (t2-dossier-a2a #178): `prepare_dossier` is now a REAL worker (Carolina A2A / DL-0037 "
    "gap marker) — ST_PrepareDossierDescred/ST_PrepareDossierCred drain, so UT_AnaliseDescredenciamento "
    "is reachable and 9 of the 12 former bucket-1 cred tests flipped to real passes (live-proven, CIB "
    "Seven 2.1.0). This test is one of the 3 DESCREDENCIAMENTO happy-paths that NOW correctly progress "
    "PAST the dossier to the RN-567 prior-notice cure-window (GW_AguardarNotificacao / ICE_PrazoNotificacao) "
    "and WAIT there — ST_RegisterDescredenciamento runs but End_PrestadorDescredenciado / "
    "End_SubstituicaoRegistrada is never reached because the test does not drive the notification-ack / "
    "advance the cure-window timer to the terminal (a test-completion follow-up: complete the "
    "prior-notice period). A SECOND residual class (test_documentacao_incompleta / test_direcao_ambigua) "
    "now reaches its notify/check activity (adapted to engine-side) but still asserts "
    "has_event(_CRED_PENDED) — RESOLVED (item-9 w5+assembly, live-proven): "
    "ST_PublishCredPendedDoc/ST_PublishCredPendedNotice (the T3.1 remedy-B splice cred never got) "
    "now publish agents.events.cred.pended and both tests flipped to real passes. The ONLY "
    "residual this constant still covers is the cure-window class above (3 tests). "
    '**DIAGNOSTICO CORRIGIDO (2026-08-06) — a frase anterior ("test-completion follow-up: o teste '
    'nao dirige o ack/timer") era FALSA, e estava repetida em 3 lugares (esta razao, PLANS.md e a '
    "memoria de handoff do orquestrador).** Os 3 testes JA disparam o timer (`_drive_to_descred` "
    'chama `await_timer_job("ICE_PrazoNotificacao")` + `execute_job`), e um irmao NAO-xfail que '
    "passa (`test_happy_path_descredenciamento_manter_vinculo`) usa o MESMO helper e atinge seu "
    "terminal — logo o cure-window nunca foi o bloqueio. O defeito REAL era de src: o gateway "
    "`GW_Substituicao` roteia por `${tem_plano_substituicao == true}`, variavel que aparecia "
    "exatamente 2x no repo inteiro (ambas DENTRO do BPMN — a condicao e um comentario) e que "
    "NENHUM worker setava; o proprio comentario do BPMN especifica que `register_descredenciamento` "
    'deve ecoa-la como booleano FLAT e avisa que sem ela a instancia trava no gateway ("Cannot '
    'resolve identifier"). CORRIGIDO no worker; os 3 marcadores foram removidos e live-provados. '
    "grep-confirmed: zero pytest.mark.xfail call sites reference this constant anymore."
)

_CRED_DOC_COMPLETA_OVERWRITE_REASON = (
    "documentacao_completa/licenca_valida OVERWRITE BUG (finding 2, T3.1 phase-2 port): "
    "validate_cred (credenciamento.py:43-65, the operadora.cred.verify_credentials worker fn) "
    "unconditionally sets licenca_valida=True (line 52, hardcoded placeholder) and computes "
    "documentacao_completa = isinstance(documentos_refs, dict) (line 53) — this suite sends "
    "documentos_refs as a Camunda String (mirrors every sibling family's own convention), so "
    "documentacao_completa is ALWAYS False regardless of the seeded start variable. "
    "CibSevenWorkerTransport.complete() (harness.py:404-410) forwards the worker's return dict "
    "as real process variables, overwriting the test's seeded documentacao_completa/"
    "licenca_valida before the native BRT_Admissibilidade businessRuleTask evaluates. "
    "cred_admissibility.dmn's hitPolicy=FIRST table checks r_cred_doc_pendente (documentacao_"
    "completa=false, cred_admissibility.dmn lines 80-90) BEFORE r_cred_clerical/r_cred_humano, so "
    "every credenciamento-direction (non-indicio) scenario in this test reroutes to "
    "PENDENTE_DOCUMENTACAO instead of the intended branch. UPDATE (T2.5-p2b): notify_doc_pendente "
    "is now BUILT and registered — LIVE-PROVEN (wave2a, CIB Seven 2.1.0): a live-engine run "
    "CONFIRMED this test still XFAILs (NOT flippable) — so the rerouted instance no longer "
    "stalls unserved; instead it correctly reaches ICE_AguardarInfoDoc and waits there for "
    "msg.cred.info_received (never sent by this test), so THIS finding (the overwrite bug itself) "
    "remains the independent, still-unfixed blocker regardless (NOT fixed here per charter — "
    "separate task). Descredenciamento-direction tests "
    "are unaffected (r_descred_segue matches on direcao alone). src/** fix (either typing "
    "documentos_refs as Json, or having validate_cred stop clobbering an already-resolved fact) "
    "is out of scope for this port. "
    "RETIRED (item-9 w4+assembly, live-proven): validate_cred now RESPECTS explicit engine "
    "booleans (fact-preservation fix, credenciamento.py) — the seeded facts survive to "
    "BRT_Admissibilidade and all 6 tests flipped to real passes on a fresh engine; "
    "grep-confirmed: zero pytest.mark.xfail call sites reference this constant anymore."
)

_CRED_INVALID_PRESTADOR_NOT_RAISED_REASON = (
    "ERR_CRED_INVALID_PRESTADOR NEVER RAISED (finding 3, T3.1 phase-2 port): the constant is "
    "declared (credenciamento.py:29) and the BPMN declares a matching boundary catch "
    "(BE_PrestadorInvalido / Error_CredPrestadorInvalido, BPMN lines 129-136), but grep across "
    "credenciamento.py for this code returns only the declaration — zero raise-sites. "
    "validate_cred never inspects prestador_id at all. End_CredPrestadorInvalido is categorically "
    "unreachable via any worker path; this is independent of and precedes every other finding in "
    "this suite (it would apply even with prepare_dossier implemented — notify_doc_pendente IS "
    "implemented as of T2.5-p2b, which does not change this finding: this test never reaches "
    "notify_doc_pendente at all, since ST_VerifyCredentials — the very first service task after "
    "start — is where the missing raise-site would need to be). "
    "src/** fix (adding the presence check + raise) is out of scope for this port. "
    "RETIRED (item-9 w4+assembly, live-proven): validate_cred now raises "
    "WorkerBpmnError(ERR_CRED_INVALID_PRESTADOR) on absent/blank prestador_id (ADR-0030 G2-val "
    "migration, production allowlist = 7 codes) and this suite's probe mirrors "
    "credenciamento.CRED_BPMN_ERROR_ALLOWLIST, so BE_PrestadorInvalido fires and the test passes "
    "for real; zero xfail call sites reference this constant anymore."
)

_CRED_GUARD_SHAPE_MISMATCH_REASON = (
    "GUARD-SHAPE finding 4 is RESOLVED in src (t5-workers-f2): register_descredenciamento/"
    "register_cred_denial now raise WorkerBpmnError(ERR_DECRED_NOT_HUMAN)/"
    "WorkerBpmnError(ERR_CRED_DENIAL_NOT_HUMAN) — the MODELED bpmn errors — instead of a bespoke "
    "CredError (which FunctionWorker.execute reclassified to a bare ValueError -> generic "
    "failure(retries=0) incident, so BE_DecredNaoHumano/BE_CredDenialNaoHumano could never fire). "
    "The boundary is now REACHABLE — gate-proven/consumption-covered by "
    "scripts/ci/check_bpmn_error_allowlist.py and proven at the harness layer by "
    "tests/unit/tools/workers/test_credenciamento.py::test_decred_guard_reaches_boundary_when_"
    "allowlisted (mirrors ERR_CANCEL_MANTER_NOT_HUMAN). This ENGINE test nonetheless stays xfail on "
    "the STILL-open finding 1 (_CRED_MISSING_WORKERS_REASON): operadora.cred.prepare_dossier is not "
    "in this probe's _CRED_WORKER_TOPICS drain (a local stub worker now exists in the production "
    "bootstrap per DL-0033/t5, but wiring it into THIS probe's drain + flipping this engine xfail "
    "needs a dedicated live-engine proof, out of scope here), so the instance still stalls at "
    "ST_PrepareDossier before ever reaching the UT to submit the incomplete decision. (The "
    "credenciamento-direction sibling is additionally blocked by finding 2, the documentos_refs "
    "overwrite.) The two adverse guard codes stay T-E-deferred out of PRODUCTION_BPMN_ERROR_"
    "ALLOWLIST until T-E, so on a live engine today the guard still fails closed to an audited "
    "incident — identical runtime effect, boundary now reachable the moment T-E flips the allowlist."
)


# ---------------------------------------------------------------------------
# EngineProbe para cred (espelha CancelEngineProbe) — self-contained, NAO edita conftest.py.
# ---------------------------------------------------------------------------


@dataclass
class CredEngineProbe:
    """Driva os workers reais de credenciamento contra o engine CIB Seven."""

    engine: EngineRest
    harness: WorkerHarness
    transport: CibSevenWorkerTransport
    kafka: FakeKafkaPublisher
    worker_id: str

    @property
    def _captured(self) -> list[tuple[str, dict[str, Any], str | None]]:
        return self.kafka.published

    def _domain_events(self) -> list[dict[str, Any]]:
        out = []
        for topic, payload, _key in self._captured:
            if topic == _NOTIFICATIONS_TOPIC:
                continue
            out.append({"topic": topic, "payload": payload})
        return out

    def events_on(self, topic: str) -> list[dict[str, Any]]:
        return [e for e in self._domain_events() if e["topic"] == topic]

    def has_event(self, topic: str, **match: Any) -> bool:
        return any(all(e["payload"].get(k) == v for k, v in match.items()) for e in self.events_on(topic))

    def notifications_of_type(self, ntype: str) -> list[dict[str, Any]]:
        return [v for (_t, v, _k) in self._captured if v.get("type") == ntype]

    async def drain(self, *, rounds: int = 30) -> None:
        await drain_topics(self.transport, self.harness, self.worker_id, _CRED_WORKER_TOPICS, rounds=rounds)


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 4 DMN de (des)credenciamento da arvore no engine real."""
    return await engine.deploy(
        _BPMN, _DMN_ADMISSIBILITY, _DMN_ROUTE, _DMN_PRIOR_NOTICE, _DMN_SLA, name="SP-OP-CRED-001-qa"
    )


@pytest_asyncio.fixture
async def cred_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[CredEngineProbe]:
    """Probe que serve as external tasks com os workers reais de credenciamento."""
    worker_id = f"qa-cred-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # T1.10 wave: emit-before-complete is FAIL-CLOSED (harness.py _emit_audit) — a real
    # PostgresAuditSink (lane PG, migrations 0001->0005) is REQUIRED or the harness refuses
    # to complete. `tenant` scopes the durable audit chain / dedup key to the per-run schema.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        audit_sink=audit_sink,
        # ADR-0030: mirror the production allowlist (credenciamento.CRED_BPMN_ERROR_ALLOWLIST ->
        # service.py's PRODUCTION_BPMN_ERROR_ALLOWLIST) so ERR_CRED_INVALID_PRESTADOR fires the
        # modeled BE_PrestadorInvalido boundary instead of demoting to an incident.
        bpmn_error_allowlist=CRED_BPMN_ERROR_ALLOWLIST,
    )
    kafka = FakeKafkaPublisher()
    # ADR-0028 §1 DMN-evaluation seam: assess_admissibility (operadora.cred.check_network_
    # criteria) requires a live dmn transport (require_dmn raises DmnEvaluationError otherwise).
    # Points at the SAME engine the BPMN's own native BRT_Admissibilidade/BRT_Route
    # businessRuleTasks evaluate cred_admissibility/cred_route against (mirrors
    # test_rafael_auth_dossier.py's / test_helena_escalation.py's own
    # CibSevenDmnTransport(engine_base_url, timeout=30.0) convention) — fixture plumbing, not a
    # v2 gap (see module docstring PORT NOTES).
    dmn = CibSevenDmnTransport(CIBSEVEN_BASE_URL, timeout=30.0)
    register_credenciamento_workers(harness, kafka, dmn=dmn)
    # T3.1 R2: the generic operadora.events.publish worker every ST_Publish* service task in this
    # BPMN routes through — mirrors the donor's own register_phase0_workers composition.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (mirrors test_sp_op_cancel_001.py's cancel_probe fixture, verbatim technique):
    # todo topico operadora.cred.* registrado no harness DEVE estar na lista de drain — falha
    # AQUI, explicita, se um worker novo ficar fora. Passa NATURALMENTE hoje (1:1 com os 8
    # topicos que register_credenciamento_workers de fato registra desde T2.5-p2b — finding 1
    # narrowed to the single remaining operadora.cred.prepare_dossier gap).
    cred_registered = {t for t in harness.registered_topics if t.startswith("operadora.cred.")}
    # DL-0033 (t5): operadora.cred.prepare_dossier is now a REGISTERED local stub, but this probe
    # deliberately does NOT drain it (keeps the guard-shape/HITL engine xfails valid pending a
    # dedicated live-engine proof that would flip them). Exclude it explicitly from the drift check;
    # the guard still catches any OTHER registered-but-undrained worker.
    missing_from_drain = cred_registered - set(_CRED_WORKER_TOPICS) - {_PREPARE_DOSSIER_TOPIC}
    assert not missing_from_drain, (
        f"_CRED_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = CredEngineProbe(
        engine=engine,
        harness=harness,
        transport=transport,
        kafka=kafka,
        worker_id=worker_id,
    )
    try:
        yield probe
    finally:
        await transport.close()


def _unique_prestador(prefix: str = "PREST-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_cred(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key CRED-amh-{prestador} e payload canonico.

    Os fatos pre-resolvidos por worker (licenca_valida, documentacao_completa,
    dentro_criterios_rede, notificacao_previa_feita, etc.) sao seeded como variaveis de start: as
    businessRuleTasks nativas do BPMN leem variaveis de processo diretas, nao um valor computado
    por um FunctionWorker sob um nome diferente. Mesma tecnica de test_sp_op_cancel_001.py — ver
    module docstring FINDING 2 para a ressalva especifica de credenciamento (verify_credentials
    sobrescreve documentacao_completa/licenca_valida apos rodar).

    Default = descredenciamento de hospital com beneficiarios vinculados -> SEGUE_ANALISE
    (cure-window + UT humana de descredenciamento).
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        prestador = overrides.pop("prestador_id", _unique_prestador())
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "prestador_id": prestador,
            "protocolo_cred": "PROTO-TESTE-0001",
            "direcao": "descredenciamento",
            "tipo_prestador": "hospital",
            "origem_solicitacao": "operadora",
            "motivo_informado": "Motivo sintetico pseudonimizado (teste)",
            "data_solicitacao_iso": "2026-06-01",
            "data_efeito_iso": "2026-07-15",
            "documentos_refs": "[]",
            # fatos pre-resolvidos (seeded — workers os observam, nao os decidem)
            "licenca_valida": True,
            "documentacao_completa": True,
            "dentro_criterios_rede": True,
            "notificacao_previa_feita": True,
            "tem_beneficiarios_vinculados": True,
            "substituto_equivalente_identificado": False,
            "indicio_irregularidade_sinalizado": False,
        }
        variables.update(overrides)
        business_key = f"CRED-amh-{prestador}"
        return await engine.start_by_key("SP-OP-CRED-001", business_key, variables)

    return _start


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _await_end(engine: EngineRest, iid: str, *, attempts: int = 60, delay: float = 0.25) -> set[str]:
    for _ in range(attempts):
        state = await engine.history_state(iid)
        if state == "COMPLETED":
            return await engine.activity_instances_ended(iid)
        await asyncio.sleep(delay)
    return await engine.activity_instances_ended(iid)


async def _assert_no_adverse_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L1 (provider_decredentialing / no-adverse): nenhum terminal adverso sem UT humana.

    Para CADA terminal adverso presente no historico (End_PrestadorDescredenciado /
    End_CredenciamentoNegado), exige pelo menos uma User Task humana (_UT_HUMANAS) tambem no
    historico. Prova negativa: se nenhum terminal adverso esta no historico -> ok por vacuidade.
    """
    ended = await engine.activity_instances_ended(iid)
    adversos_atingidos = ended & _ENDS_ADVERSOS
    if adversos_atingidos:
        human_tasks_in_history = ended & _UT_HUMANAS
        assert human_tasks_in_history, (
            f"INVARIANTE L1 VIOLADA (provider_decredentialing): terminal(is) adverso(s) "
            f"{adversos_atingidos} atingido(s) para instancia {iid} SEM nenhuma User Task humana no "
            f"historico. User Tasks esperadas (qualquer uma de): {_UT_HUMANAS}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de descredenciamento/negativa — violacao do L1."
        )


async def _drive_to_descred(engine: EngineRest, probe: CredEngineProbe, iid: str) -> Any:
    """Drena (passando pelo cure-window via timer) ate UT_AnaliseDescredenciamento surgir."""
    await probe.drain()
    # O descredenciamento passa pelo event gateway de notificacao previa; dispara o timer para
    # alcancar o dossie + UT (NUNCA auto-descredencia ao expirar — vai para a UT humana).
    try:
        job = await engine.await_timer_job(iid, "ICE_PrazoNotificacao")
        await engine.execute_job(job.id)
        await probe.drain()
    except Exception:
        # Se ja avancou via ack/outra rota, segue para a UT diretamente.
        pass
    return await engine.await_user_task(iid, _UT_DESCRED)


async def _drive_to_cred(engine: EngineRest, probe: CredEngineProbe, iid: str) -> Any:
    """Drena ate UT_AnaliseCredenciamento surgir (sem cure-window — credenciamento)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_CRED)


# ===========================================================================
# INVARIANTE L1 — DoD deliverable (varredura de inputs das DMN)
# ===========================================================================


async def test_nenhum_caminho_automatizado_descredencia_ou_nega(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """INVARIANTE L1 (provider_decredentialing): NENHUM caminho automatizado descredencia/nega.

    Varredura de combinacoes de input das DMN cred_admissibility / cred_route:
      direcao in {credenciamento, descredenciamento}
      x tipo_prestador in {hospital, clinica, pessoa_fisica}
      x licenca_valida in {true, false}
      x documentacao_completa in {true, false}
      x dentro_criterios_rede in {true, false}
      x indicio_irregularidade_sinalizado in {true, false}
    (2 * 3 * 2 * 2 * 2 * 2 = 96 combinacoes).

    Para cada instancia que percorre ate estabilizar (sem completar nenhuma User Task), NENHUMA atinge
    End_PrestadorDescredenciado / End_CredenciamentoNegado. Prova formal via historia do engine.

    UNMARKED: holds VACUOUSLY under every module-docstring finding (an instance stuck on an
    unregistered/rerouted topic never reaches an adverse terminal either) — assertion strength
    preserved BYTE-IDENTICAL from the donor (constraint: never weaken the L1 sweep).
    """
    direcoes = ["credenciamento", "descredenciamento"]
    tipos = ["hospital", "clinica", "pessoa_fisica"]
    bools = [True, False]
    checked = 0

    for direcao, tipo, lic, doc, rede, indicio in itertools.product(
        direcoes, tipos, bools, bools, bools, bools
    ):
        inst = await start_cred(
            direcao=direcao,
            tipo_prestador=tipo,
            licenca_valida=lic,
            documentacao_completa=doc,
            dentro_criterios_rede=rede,
            indicio_irregularidade_sinalizado=indicio,
        )
        iid = inst["id"]
        await cred_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        adversos = ended & _ENDS_ADVERSOS
        assert not adversos, (
            f"L1 VIOLADO (provider_decredentialing): direcao={direcao} tipo={tipo} licenca={lic} "
            f"doc={doc} rede={rede} indicio={indicio} atingiu terminal(is) adverso(s) {adversos} "
            f"automaticamente. ended={ended}"
        )
        await _assert_no_adverse_without_human_task(engine, iid)
        checked += 1

    assert checked == 96, f"Esperava 96 combinacoes varridas; varri {checked}"


async def test_credenciamento_licenca_irregular_roteia_para_humano_nunca_auto_nega(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Credenciamento com licenca aparentemente irregular => ANALISE_HUMANA (nunca auto-nega).

    Fluxo chega a UT_AnaliseCredenciamento (gestao-rede), NUNCA a End_CredenciamentoNegado. A negativa
    so existe via decisao humana NEGAR_CREDENCIAMENTO.
    """
    inst = await start_cred(
        direcao="credenciamento",
        tipo_prestador="clinica",
        documentacao_completa=True,
        licenca_valida=False,
        dentro_criterios_rede=True,
    )
    iid = inst["id"]

    ut = await _drive_to_cred(engine, cred_probe, iid)
    assert "gestao-rede" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_CRED_NEGADO not in ended, "Licenca irregular nao deve auto-negar credenciamento (L1)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_indicio_irregularidade_roteia_para_humano_nunca_auto_acusa(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """indicio_irregularidade_sinalizado=true => ANALISE_DESCREDENCIAMENTO (nunca auto-acusa fraude).

    Nenhum caminho automatizado registra acusacao de fraude (fraud_accusation L0 hard); o fluxo
    aguarda decisao humana de descredenciamento (que decide encaminhar a SP-OP-FRAUDE-001).
    """
    inst = await start_cred(
        direcao="credenciamento",
        tipo_prestador="hospital",
        indicio_irregularidade_sinalizado=True,
        licenca_valida=True,
        documentacao_completa=True,
        dentro_criterios_rede=True,
    )
    iid = inst["id"]

    ut = await _drive_to_descred(engine, cred_probe, iid)
    assert "juridico-rede" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Indicio de irregularidade nunca produz terminal adverso automatico"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


async def test_happy_path_credenciamento_clerical_neutro(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Credenciamento favoravel (doc completa, licenca valida, dentro criterios) => CLERICAL_CREDENCIAR.

    register_credenciamento executado; cred.network_changed (credenciado) + cred.completed
    (credenciado); fim End_PrestadorCredenciado; NENHUMA User Task adversa criada (direcao favoravel).
    register_cred_denial / register_descredenciamento NUNCA invocados.

    UPDATE (T2.5-p2b): operadora.cred.register_credenciamento is now BUILT and registered — the
    registry-drift half of this test's original "doubly-blocked" framing is closed. The
    documentacao_completa overwrite bug (finding 2) remains the PRIMARY, still-unfixed blocker
    (reroutes this test to PENDENTE_DOCUMENTACAO before it can ever reach CLERICAL_CREDENCIAR).
    FLIPPED (item-9 assembly, live-proven): the w4 fact-preservation fix
    unblocked CLERICAL_CREDENCIAR and the final dead kafka echo
    (register_credenciamento publishes no notification of its own) was adapted to engine-side
    activity-history evidence; the has_event(...) assertions ride the generic, already-wired
    operadora.events.publish worker.
    """
    inst = await start_cred(
        direcao="credenciamento",
        tipo_prestador="clinica",
        documentacao_completa=True,
        licenca_valida=True,
        dentro_criterios_rede=True,
    )
    iid = inst["id"]

    await cred_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_CREDENCIADO in ended, f"Deve atingir End_PrestadorCredenciado. ended={ended}"
    assert not await engine.list_user_tasks(iid), "Credenciamento clerical nao cria User Task adversa"
    assert cred_probe.has_event(_CRED_RECEIVED)
    assert cred_probe.has_event(_CRED_NETWORK_CHANGED, tipo_mudanca="prestador_credenciado")
    assert cred_probe.has_event(_CRED_COMPLETED, desfecho="credenciado")
    # Engine-side (kafka-echo morto: register_credenciamento nao publica notificacao propria;
    # os has_event acima ja provam os eventos de dominio via operadora.events.publish):
    assert "ST_RegisterCredenciamento" in ended, "register_credenciamento deve executar no caminho clerical"
    assert not ({"ST_RegisterCredDenial", "ST_RegisterDescredenciamento"} & ended), (
        "nenhum worker adverso executa no caminho clerical favoravel"
    )


async def test_happy_path_descredenciamento_humano(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Descredenciamento => SEGUE_ANALISE; humano DESCREDENCIAR => End_PrestadorDescredenciado.

    register_descredenciamento executado (guard satisfeito); cred.network_changed (descredenciado);
    cred.completed desfecho=descredenciado; responsavel_id+tier na trilha de auditoria.
    """
    inst = await start_cred(direcao="descredenciamento", tem_beneficiarios_vinculados=False)
    iid = inst["id"]

    ut = await _drive_to_descred(engine, cred_probe, iid)
    assert "gestao-rede" in ut.candidate_groups or "juridico-rede" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, {"decisao_cred": "DESCREDENCIAR", **_CAMPOS_DESCRED})
    await cred_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_DESCREDENCIADO in ended, (
        f"Descred. humano deve atingir End_PrestadorDescredenciado. ended={ended}"
    )
    assert cred_probe.has_event(_CRED_NETWORK_CHANGED, tipo_mudanca="prestador_descredenciado")
    assert cred_probe.has_event(_CRED_COMPLETED, desfecho="descredenciado")

    # Engine-side (eco kafka morto: credenciamento.py nao chama kafka.publish em NENHUM worker —
    # `notifications_of_type` e estruturalmente sempre []). ST_RegisterDescredenciamento executou;
    # e como register_descredenciamento carrega o guard ERR_DECRED_NOT_HUMAN (exige decisao_cred +
    # responsavel_id + fundamentacao + referencia_regulatoria), atingir End_PrestadorDescredenciado
    # SEM o guard bloquear ja prova que os campos humanos chegaram ao worker. Os valores exatos sao
    # asseridos a partir do historico do engine.
    assert "ST_RegisterDescredenciamento" in ended, (
        "register_descredenciamento deve executar apos a decisao humana"
    )
    assert await engine.get_history_variable(iid, "decisao_cred") == "DESCREDENCIAR"
    assert await engine.get_history_variable(iid, "responsavel_id") == "juridico-rede-sintetico-001"
    assert await engine.get_history_variable(iid, "tier") == "senior"
    assert await engine.get_history_variable(iid, "descredenciamento_registrado") is True


async def test_happy_path_descredenciamento_com_substituicao(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Descredenciamento de hospital com plano de substituicao => End_SubstituicaoRegistrada.

    A saida do descredenciado E adverso (passa pela UT humana), mas com plano_substituicao o desfecho
    de dominio e substituicao_registrada (RN 567).
    """
    inst = await start_cred(
        direcao="descredenciamento", tipo_prestador="hospital", tem_beneficiarios_vinculados=True
    )
    iid = inst["id"]

    ut = await _drive_to_descred(engine, cred_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_cred": "DESCREDENCIAR",
            "plano_substituicao": "Substituto equivalente HOSP-EQUIV-0002 (sintetico)",
            **_CAMPOS_DESCRED,
        },
    )
    await cred_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_SUBSTITUICAO in ended, (
        f"DESCREDENCIAR + plano_substituicao => End_SubstituicaoRegistrada. ended={ended}"
    )
    assert cred_probe.has_event(_CRED_NETWORK_CHANGED, tipo_mudanca="substituicao_registrada")
    assert cred_probe.has_event(_CRED_COMPLETED, desfecho="substituicao_registrada")


async def test_happy_path_descredenciamento_manter_vinculo(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Descredenciamento => SEGUE_ANALISE; humano MANTER => End_VinculoMantido (nao prossegue)."""
    inst = await start_cred(direcao="descredenciamento", tem_beneficiarios_vinculados=False)
    iid = inst["id"]

    ut = await _drive_to_descred(engine, cred_probe, iid)
    await engine.complete_task_as_human(
        ut.id, {"decisao_cred": "MANTER", "fundamentacao": "Vinculo mantido (teste)", "responsavel_id": "r-1"}
    )
    await cred_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_VINCULO_MANTIDO in ended, f"MANTER => End_VinculoMantido. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert cred_probe.has_event(_CRED_COMPLETED, desfecho="vinculo_mantido")
    assert not cred_probe.notifications_of_type("cred.register_descredenciamento")


async def test_happy_path_credenciamento_negado_humano(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Credenciamento com licenca irregular => ANALISE_HUMANA; humano NEGAR => End_CredenciamentoNegado.

    A negativa (End_CredenciamentoNegado) SO existe por esta via (decisao humana NEGAR_CREDENCIAMENTO).
    register_cred_denial executado (guard satisfeito); cred.completed desfecho=credenciamento_negado.
    """
    inst = await start_cred(
        direcao="credenciamento",
        tipo_prestador="clinica",
        documentacao_completa=True,
        licenca_valida=False,
        dentro_criterios_rede=True,
    )
    iid = inst["id"]

    ut = await _drive_to_cred(engine, cred_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_cred": "NEGAR_CREDENCIAMENTO", **_CAMPOS_DENIAL})
    await cred_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_CRED_NEGADO in ended, f"NEGAR humano => End_CredenciamentoNegado. ended={ended}"
    assert cred_probe.has_event(_CRED_COMPLETED, desfecho="credenciamento_negado")

    # Engine-side (kafka-echo morto): ST_RegisterCredDenial executou — e como register_cred_denial
    # carrega o guard t5-workers-f2 (WorkerBpmnError ERR_CRED_DENIAL_NOT_HUMAN quando faltam os
    # campos humanos), atingir End_CredenciamentoNegado SEM o boundary disparar prova que
    # decisao_cred/responsavel_id/tier chegaram ao worker.
    assert "ST_RegisterCredDenial" in ended, (
        "register_cred_denial (ST_RegisterCredDenial) deve executar apos NEGAR humano"
    )
    assert await engine.get_history_variable(iid, "decisao_cred") == "NEGAR_CREDENCIAMENTO"


async def test_happy_path_credenciamento_aprovado_humano(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Credenciamento (licenca irregular) => ANALISE_HUMANA; humano APROVAR => End_PrestadorCredenciado."""
    inst = await start_cred(
        direcao="credenciamento",
        tipo_prestador="clinica",
        documentacao_completa=True,
        licenca_valida=False,
        dentro_criterios_rede=True,
    )
    iid = inst["id"]

    ut = await _drive_to_cred(engine, cred_probe, iid)
    await engine.complete_task_as_human(
        ut.id, {"decisao_cred": "APROVAR_CREDENCIAMENTO", "responsavel_id": "gestao-rede-sintetico-001"}
    )
    await cred_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_CREDENCIADO in ended, f"APROVAR humano => End_PrestadorCredenciado. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert cred_probe.has_event(_CRED_COMPLETED, desfecho="credenciado")
    assert not cred_probe.notifications_of_type("cred.register_cred_denial")


# ===========================================================================
# Aceite exige campos / worker guard (defesa em profundidade)
# ===========================================================================


@pytest.mark.xfail(reason=_CRED_GUARD_SHAPE_MISMATCH_REASON, strict=True)
async def test_descredenciar_exige_campos_worker_guard(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """DESCREDENCIAR sem campos obrigatorios => worker guard recusa (ERR_DECRED_NOT_HUMAN).

    A instancia NAO atinge End_PrestadorDescredenciado; o worker nao registra. Regressao GAP-CRED-1
    (#96): o boundary catch BE_DecredNaoHumano captura ERR_DECRED_NOT_HUMAN e termina a instancia de
    forma LIMPA em End_DecredBloqueadoNaoHumano (guard/fail-safe NEUTRO) — antes do fix, o mesmo
    cenario deixava a instancia com incidente TRAVADO (sem nenhum end event atingido).
    """
    inst = await start_cred(direcao="descredenciamento", tem_beneficiarios_vinculados=False)
    iid = inst["id"]

    ut = await _drive_to_descred(engine, cred_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_cred": "DESCREDENCIAR"})
    await cred_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_DESCREDENCIADO not in ended, (
        "Descred. sem campos obrigatorios NAO pode atingir End_PrestadorDescredenciado (guard)"
    )
    assert _END_DECRED_BLOQUEADO_NAO_HUMANO in ended, (
        f"GAP-CRED-1 (#96): guard sem campos deve terminar LIMPO em {_END_DECRED_BLOQUEADO_NAO_HUMANO} "
        f"(boundary catch BE_DecredNaoHumano), nao travar como incidente. ended={ended}"
    )
    assert not cred_probe.notifications_of_type("cred.register_descredenciamento")
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_CRED_GUARD_SHAPE_MISMATCH_REASON, strict=True)
async def test_negar_exige_campos_worker_guard(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """NEGAR_CREDENCIAMENTO sem campos obrigatorios => worker guard recusa (ERR_CRED_DENIAL_NOT_HUMAN).

    Regressao GAP-CRED-1 (#96): o boundary catch BE_CredDenialNaoHumano captura ERR_CRED_DENIAL_NOT_HUMAN
    e termina a instancia de forma LIMPA em End_CredGuardBloqueadoNaoHumano (guard/fail-safe NEUTRO).
    """
    inst = await start_cred(
        direcao="credenciamento",
        tipo_prestador="clinica",
        documentacao_completa=True,
        licenca_valida=False,
        dentro_criterios_rede=True,
    )
    iid = inst["id"]

    ut = await _drive_to_cred(engine, cred_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_cred": "NEGAR_CREDENCIAMENTO"})
    await cred_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_CRED_NEGADO not in ended, (
        "Negativa sem campos obrigatorios NAO pode atingir End_CredenciamentoNegado (guard)"
    )
    assert _END_CRED_GUARD_BLOQUEADO_NAO_HUMANO in ended, (
        f"GAP-CRED-1 (#96): guard sem campos deve terminar LIMPO em "
        f"{_END_CRED_GUARD_BLOQUEADO_NAO_HUMANO} (boundary catch BE_CredDenialNaoHumano), nao travar "
        f"como incidente. ended={ended}"
    )
    assert not cred_probe.notifications_of_type("cred.register_cred_denial")
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Boundary-error catches (GAP-CRED-1, #96) — guards TECNICOS terminam LIMPO, nunca travam
# ===========================================================================


async def test_prestador_id_ausente_termina_limpo_sem_incidente_travado(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    deploy_artifacts: str,
) -> None:
    """prestador_id ausente => ST_VerifyCredentials lanca ERR_CRED_INVALID_PRESTADOR (validacao de
    origem — FATO, nunca decisao). Regressao GAP-CRED-1 (#96): o boundary catch BE_PrestadorInvalido
    captura o erro e termina a instancia LIMPO em End_CredPrestadorInvalido (fail-safe NEUTRO,
    primeiro service task da mainline) — antes do fix, o mesmo cenario travava incidente sem nenhum
    end event atingido. Nenhum efeito adverso; nenhum worker adverso invocado.
    """
    business_key = f"CRED-amh-INVALIDO-{uuid.uuid4().hex[:8]}"
    variables: dict[str, Any] = {
        "tenant_id": "amh",
        "prestador_id": "",
        "protocolo_cred": "PROTO-TESTE-INVALIDO-0001",
        "direcao": "credenciamento",
        "tipo_prestador": "clinica",
        "origem_solicitacao": "operadora",
        "motivo_informado": "",
        "data_solicitacao_iso": "2026-06-01",
        "data_efeito_iso": "2026-07-15",
        "documentos_refs": "[]",
    }
    inst = await engine.start_by_key("SP-OP-CRED-001", business_key, variables)
    iid = inst["id"]

    await cred_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_CRED_PRESTADOR_INVALIDO in ended, (
        f"GAP-CRED-1 (#96): prestador_id ausente deve terminar LIMPO em "
        f"{_END_CRED_PRESTADOR_INVALIDO} (boundary catch BE_PrestadorInvalido), nao travar como "
        f"incidente. ended={ended}"
    )
    assert not (ended & _ENDS_ADVERSOS), "Validacao de origem nunca produz terminal adverso"
    assert not cred_probe.notifications_of_type("cred.register_descredenciamento")
    assert not cred_probe.notifications_of_type("cred.register_cred_denial")
    assert not cred_probe.notifications_of_type("cred.register_credenciamento")
    await _assert_no_adverse_without_human_task(engine, iid)


def test_register_descredenciamento_recusa_sem_humano() -> None:
    """Invocacao direta do dict-first fn register_descredenciamento sem decisao humana =>
    WorkerBpmnError(ERR_DECRED_NOT_HUMAN). Unit-style sobre a funcao real (SEM engine — roda mesmo sem
    o dev-stack).

    ADAPTED (port rule 1, verify each register_*-shape on v2 main): v2's
    register_descredenciamento(variables: dict) -> dict (dict-first, ADR-0026 Decisao Sec2a)
    replaces donor's make_register_descredenciamento_handler(kafka) ->
    Callable[[ExternalTask], ...], which does not exist on v2 main. UPDATE (t5-workers-f2, finding 4
    fix): the adverse guard now raises `WorkerBpmnError(ERR_DECRED_NOT_HUMAN)` — the MODELED bpmn
    error the donor used — restoring the byte-for-byte donor shape, so the boundary catch
    BE_DecredNaoHumano is reachable (checked via `.error_code`; was a bespoke CredError -> ValueError
    -> incident before). v2's function takes NO kafka argument at all
    (register_credenciamento_workers discards its own kafka param, `del kafka  # unused`); this module
    has zero Kafka dependency. The 3 guard scenarios (empty / neutral MANTER decision /
    all-required-fields-present) are preserved verbatim in intent; the success-case assertions check
    v2's ACTUAL return shape (descredenciamento_registrado / network_changed / data_efeito_iso).
    """
    with pytest.raises(WorkerBpmnError) as exc_a:
        register_descredenciamento({})
    assert exc_a.value.error_code == ERR_DECRED_NOT_HUMAN

    with pytest.raises(WorkerBpmnError) as exc_b:
        register_descredenciamento({"decisao_cred": "MANTER"})
    assert exc_b.value.error_code == ERR_DECRED_NOT_HUMAN

    result = register_descredenciamento(
        {
            "decisao_cred": "DESCREDENCIAR",
            "fundamentacao": "Descred. fundamentado (teste)",
            "referencia_regulatoria": "RN 567 — DRAFT/verify",
            "comprovacao_notificacao_previa": "ref-comprovante-0001",
            "responsavel_id": "juridico-rede-sintetico-001",
            "tier": "senior",
            "tenant_id": "amh",
            "prestador_id": "PREST-TESTE-GUARD",
            "data_efeito_iso": "2026-07-15",
        }
    )
    assert result["descredenciamento_registrado"] is True
    assert result["network_changed"] is True
    assert result["data_efeito_iso"] == "2026-07-15"


def test_register_cred_denial_recusa_sem_humano() -> None:
    """Invocacao direta do dict-first fn register_cred_denial sem decisao humana =>
    WorkerBpmnError(ERR_CRED_DENIAL_NOT_HUMAN). Unit-style sobre a funcao real (SEM engine).

    Same adaptation rationale as test_register_descredenciamento_recusa_sem_humano above
    (register_cred_denial(variables: dict) -> dict; no kafka param). UPDATE (t5-workers-f2, finding 4
    fix): now raises WorkerBpmnError so BE_CredDenialNaoHumano is reachable (checked via `.error_code`).
    """
    with pytest.raises(WorkerBpmnError) as exc_a:
        register_cred_denial({})
    assert exc_a.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN

    # APROVAR_CREDENCIAMENTO (neutro) tampouco passa pelo worker adverso.
    with pytest.raises(WorkerBpmnError) as exc_b:
        register_cred_denial({"decisao_cred": "APROVAR_CREDENCIAMENTO"})
    assert exc_b.value.error_code == ERR_CRED_DENIAL_NOT_HUMAN

    result = register_cred_denial(
        {
            "decisao_cred": "NEGAR_CREDENCIAMENTO",
            "fundamentacao": "Negativa fundamentada (teste)",
            "referencia_regulatoria": "RN 566 — DRAFT/verify",
            "responsavel_id": "gestao-rede-sintetico-001",
            "tier": "pleno",
            "tenant_id": "amh",
            "prestador_id": "PREST-TESTE-GUARD",
        }
    )
    assert result["credenciamento_negado"] is True


# ===========================================================================
# Documentacao pendente (nunca negativa)
# ===========================================================================


async def test_documentacao_incompleta_pendente_nunca_nega(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """credenciamento + documentacao_completa=false => PENDENTE_DOCUMENTACAO; notify_doc_pendente; pended.

    A documentacao incompleta NUNCA produz negativa automatica: a instancia aguarda a documentacao.

    UPDATE (T2.5-p2b): notify_doc_pendente is now BUILT and registered, and this test's seeded
    documentacao_completa=False survives the finding-2 overwrite bug unchanged (it does not need
    ANY UT, so it is the CLOSEST of this file's xfails to a genuine live flip). It remains xfail
    ONLY on finding 5 (Kafka-publish gap, module docstring): notify_doc_pendente does not itself
    kafka.publish an `agents.events.cred.pended` domain event or a `cred.notify_doc_pendente`-
    typed internal notification (mirrors the SAME "not fabricated here" convention documented for
    the other 5 dict-first cred functions and 7+ sibling modules) — both
    `cred_probe.notifications_of_type("cred.notify_doc_pendente")` and
    `cred_probe.has_event(_CRED_PENDED)` below will still return empty. LIVE-PROVEN (wave2a): a
    live-engine run confirmed both assertions return empty (the systemic kafka gap) — this test
    still XFAILs and was NOT flipped. Resolving finding 5 (a genuine kafka.publish fan-out, out of
    scope for T2.5-p2b's mechanical worker build) or adjusting these two assertions is the remedy.
    """
    inst = await start_cred(
        direcao="credenciamento",
        tipo_prestador="clinica",
        documentacao_completa=False,
        licenca_valida=True,
        dentro_criterios_rede=True,
    )
    iid = inst["id"]

    await cred_probe.drain()

    # Engine-side (kafka-echo `notifications_of_type` morto pos-#178): o serviceTask
    # notify_doc_pendente executou. (has_event(_CRED_PENDED) abaixo continua sendo o evento real.)
    assert "ST_NotifyDocPendente" in await engine.activity_instances_ended(iid), (
        "Worker notify_doc_pendente (ST_NotifyDocPendente) deve executar em PENDENTE_DOCUMENTACAO"
    )
    assert cred_probe.has_event(_CRED_PENDED), "cred.pended deve ser publicado"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Documentacao incompleta nunca nega automaticamente"
    assert await engine.instance_is_active(iid), "Instancia deve aguardar a documentacao"


# ===========================================================================
# Notificacao previa (cure-window — INVERTE auto-descredenciamento por timeout)
# ===========================================================================


async def test_prazo_notificacao_expira_vai_para_humano_nao_descredencia(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Aguardando no cure-window, sem ack => timer prazo_notificacao => UT_AnaliseDescredenciamento.

    A expiracao do prazo NUNCA alcanca End_PrestadorDescredenciado automaticamente (INVERTE o
    anti-pattern Boundary_LicenseTimeout->Task_ExpireRequest do reference SP-PS-002).
    """
    inst = await start_cred(direcao="descredenciamento", tem_beneficiarios_vinculados=True)
    iid = inst["id"]

    await cred_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_PrazoNotificacao")
    await engine.execute_job(job.id)
    await cred_probe.drain()

    ut = await engine.await_user_task(iid, _UT_DESCRED)
    assert "gestao-rede" in ut.candidate_groups or "juridico-rede" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Expiracao do prazo NUNCA auto-descredencia (L1)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_notificacao_ack_destrava_analise(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Aguardando no cure-window => msg.cred.notification_ack => segue para UT_AnaliseDescredenciamento."""
    inst = await start_cred(direcao="descredenciamento", tem_beneficiarios_vinculados=True)
    iid = inst["id"]
    business_key = inst["businessKey"]

    await cred_probe.drain()

    correlate_payload = {
        "messageName": "msg.cred.notification_ack",
        "businessKey": business_key,
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.cred.notification_ack falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await cred_probe.drain()
    ut = await engine.await_user_task(iid, _UT_DESCRED)
    assert "gestao-rede" in ut.candidate_groups or "juridico-rede" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Ack de notificacao nunca auto-descredencia (L1)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Timers de SLA
# ===========================================================================


async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Timer BT_SlaDescred (interruptivo): UT_AnaliseDescredenciamento cancelada; coordenacao assume.

    cred.sla_breached publicado. NAO ha auto-descredenciamento por timeout — a decisao continua humana.
    """
    inst = await start_cred(direcao="descredenciamento", tem_beneficiarios_vinculados=False)
    iid = inst["id"]

    await _drive_to_descred(engine, cred_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaDescred")
    await engine.execute_job(job.id)
    await cred_probe.drain()

    assert cred_probe.has_event(_CRED_SLA_BREACHED), "cred.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORD_DESCRED)
    assert "coordenacao-rede" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_DESCRED not in open_keys, "UT_AnaliseDescredenciamento deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro de SLA nunca auto-descredencia (L1)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_coordenacao_assume_e_descredencia(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e DESCREDENCIAR => End_PrestadorDescredenciado com UT humana."""
    inst = await start_cred(direcao="descredenciamento", tem_beneficiarios_vinculados=False)
    iid = inst["id"]

    await _drive_to_descred(engine, cred_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaDescred")
    await engine.execute_job(job.id)
    await cred_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORD_DESCRED)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_cred": "DESCREDENCIAR",
            "fundamentacao": "Prazo esgotado — coordenacao descredencia (sintetico)",
            "referencia_regulatoria": "RN 567 — DRAFT/verify",
            "comprovacao_notificacao_previa": "ref-comprovante-coord-0001",
            "responsavel_id": "coordenacao-rede-sintetica-001",
            "tier": "coordenador",
        },
    )
    await cred_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_DESCREDENCIADO in ended
    await _assert_no_adverse_without_human_task(engine, iid)
    assert cred_probe.has_event(_CRED_COMPLETED, desfecho="descredenciado")


async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Timer BT_AlertaSlaDescred (nao-interruptivo): notify_sla_risk recebe task; UT segue aberta.

    UPDATE (T2.5-p2b): notify_sla_risk is now BUILT and registered, but this test still never
    reaches it — `_drive_to_descred` stalls at `ST_PrepareDossierDescred` first (prepare_dossier,
    out of scope). Once that is fixed, the `notifications_of_type("cred.notify_sla_risk")`
    assertion below would ALSO need finding 5 (Kafka-publish gap) resolved — notify_sla_risk does
    not itself kafka.publish a "cred.notify_sla_risk"-typed internal notification either (same
    "not fabricated here" convention as every other function in this module).
    """
    inst = await start_cred(direcao="descredenciamento", tem_beneficiarios_vinculados=False)
    iid = inst["id"]

    await _drive_to_descred(engine, cred_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSlaDescred")
    await engine.execute_job(job.id)
    await cred_probe.drain()

    # Engine-side (kafka-echo morto pos-#178): o serviceTask notify_sla_risk executou.
    assert "ST_NotifySlaRisk" in await engine.activity_instances_ended(iid), (
        "Worker notify_sla_risk (ST_NotifySlaRisk) deve executar no alerta de SLA"
    )

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_DESCRED in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def test_cred_admissibility_sem_saida_adversa() -> None:
    """O dominio de roteamento da cred_admissibility e EXATAMENTE
    {CLERICAL_CREDENCIAR, SEGUE_ANALISE, PENDENTE_DOCUMENTACAO, ANALISE_HUMANA}.

    Nenhum valor NEGAR/DESCREDENCIAR; e existe row catch-all -> ANALISE_HUMANA.
    """
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ADMISSIBILITY)
    root = tree.getroot()

    roteamentos: set[str] = set()
    last_rule_first_output: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert outputs, "cada rule deve ter outputEntry"
        text_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        assert text_el is not None and text_el.text
        val = text_el.text.strip().strip('"')
        roteamentos.add(val)
        last_rule_first_output = val

    esperado = {"CLERICAL_CREDENCIAR", "SEGUE_ANALISE", "PENDENTE_DOCUMENTACAO", "ANALISE_HUMANA"}
    assert roteamentos == esperado, (
        f"dominio de roteamento inesperado: {roteamentos} — NAO pode conter NEGAR/DESCREDENCIAR (L1)"
    )
    blob = " ".join(roteamentos)
    for proibido in ("NEGAR", "DESCREDENCIAR", "RECUSAR", "DESLISTAR"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na cred_admissibility (L1)"
    assert last_rule_first_output == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_cred_route_sem_saida_adversa() -> None:
    """O dominio de roteamento da cred_route e EXATAMENTE
    {ANALISE_CREDENCIAMENTO, ANALISE_DESCREDENCIAMENTO, ANALISE_HUMANA} — nenhuma saida adversa."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ROUTE)
    root = tree.getroot()

    roteamentos: set[str] = set()
    last_rule_first_output: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert outputs, "cada rule deve ter outputEntry"
        text_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        assert text_el is not None and text_el.text
        val = text_el.text.strip().strip('"')
        roteamentos.add(val)
        last_rule_first_output = val

    assert roteamentos == {"ANALISE_CREDENCIAMENTO", "ANALISE_DESCREDENCIAMENTO", "ANALISE_HUMANA"}, (
        f"dominio de roteamento inesperado: {roteamentos} — NAO pode conter NEGAR/DESCREDENCIAR (L1)"
    )
    blob = " ".join(roteamentos)
    for proibido in ("NEGAR", "DESCREDENCIAR", "AUTO_APROVAR_ADVERSO", "FRAUD_DETECTED"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na cred_route (L1)"
    assert last_rule_first_output == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}.

    Nenhuma coluna usa "number". Varredura estatica das 4 DMNs.
    """
    from xml.etree import ElementTree as ET

    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_ADMISSIBILITY, _DMN_ROUTE, _DMN_PRIOR_NOTICE, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double)"


def test_dmn_history_ttl_e_hitpolicy() -> None:
    """Toda <decision> tem camunda:historyTimeToLive e toda <decisionTable> tem hitPolicy."""
    from xml.etree import ElementTree as ET

    camunda_ns = "{http://camunda.org/schema/1.0/dmn}historyTimeToLive"
    for dmn_path in (_DMN_ADMISSIBILITY, _DMN_ROUTE, _DMN_PRIOR_NOTICE, _DMN_SLA):
        tree = ET.parse(dmn_path)
        root = tree.getroot()
        decisions = [e for e in root.iter() if _local(e.tag) == "decision"]
        assert decisions, f"{dmn_path.name}: deve ter pelo menos uma <decision>"
        for dec in decisions:
            assert dec.get(camunda_ns), f"{dmn_path.name}: <decision> sem camunda:historyTimeToLive"
        for dt in (e for e in root.iter() if _local(e.tag) == "decisionTable"):
            assert dt.get("hitPolicy"), f"{dmn_path.name}: <decisionTable> sem hitPolicy"


def test_bpmn_user_tasks_tem_candidate_groups() -> None:
    """Toda <bpmn:userTask> traz camunda:candidateGroups nao-vazio (gate D1)."""
    from xml.etree import ElementTree as ET

    camunda_cg = "{http://camunda.org/schema/1.0/bpmn}candidateGroups"
    tree = ET.parse(_BPMN)
    user_tasks = [e for e in tree.getroot().iter() if _local(e.tag) == "userTask"]
    assert len(user_tasks) == 4, f"esperava 4 userTasks (2 analise + 2 coordenacao); achei {len(user_tasks)}"
    for ut in user_tasks:
        cg = ut.get(camunda_cg)
        assert cg, f"userTask {ut.get('id')} sem camunda:candidateGroups"


def test_bpmn_xml_ids_unicos() -> None:
    """Todos os ids do BPMN sao unicos (ENGINE-22004)."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_BPMN)
    ids: list[str] = [el.get("id") for el in tree.getroot().iter() if el.get("id")]
    assert len(ids) == len(set(ids)), (
        f"ids duplicados no BPMN: {sorted(i for i in set(ids) if ids.count(i) > 1)}"
    )


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_prestador(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa.

    Nao depende de `cred_probe.drain()` progredir alem do primeiro publish — sobrevive a todos os
    findings acima (nao marcado xfail, ao contrario dos demais; mesma postura de
    test_business_key_uma_instancia_por_guia em test_sp_op_auth_001.py).
    """
    prestador = _unique_prestador("PREST-TESTE-IDEM")
    business_key = f"CRED-amh-{prestador}"

    first = await start_cred(prestador_id=prestador, direcao="descredenciamento")
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1
    assert existing[0]["id"] == first["id"]


# ===========================================================================
# GAP-CRED-4 / GAP-CRED-5 / GAP-CRED-6 — regressao (T2, orchestrator-wave3, donor)
# ===========================================================================


async def test_direcao_ambigua_roteia_para_co_review_nao_credenciamento_only(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """GAP-CRED-4: cred_route catch-all ANALISE_HUMANA (direcao nao mapeada/ambigua) deve rotear para
    o ramo de CO-REVIEW (UT_AnaliseDescredenciamento — candidate groups gestao-rede+juridico-rede),
    NUNCA para o ramo credenciamento-only (UT_AnaliseCredenciamento — so gestao-rede). A ambiguidade
    tambem deve passar pela obrigacao de notificacao previa RN 567 (cure-window), nunca pula-la
    (regressao conjunta com GAP-CRED-6).
    """
    inst = await start_cred(
        direcao="direcao-nao-mapeada-teste",
        tipo_prestador="hospital",
        tem_beneficiarios_vinculados=True,
    )
    iid = inst["id"]

    ut = await _drive_to_descred(engine, cred_probe, iid)
    assert {"gestao-rede", "juridico-rede"}.issubset(ut.candidate_groups), (
        f"GAP-CRED-4: catch-all ANALISE_HUMANA deve cair no ramo de co-review (ambos os grupos). "
        f"groups={ut.candidate_groups}"
    )
    # Engine-side (kafka-echo morto pos-#178): o serviceTask check_prior_notice executou.
    assert "ST_CheckPriorNotice" in await engine.activity_instances_ended(iid), (
        "GAP-CRED-6: catch-all ANALISE_HUMANA deve passar pela cure-window RN 567 "
        "(ST_CheckPriorNotice), nunca pular"
    )
    assert cred_probe.has_event(_CRED_PENDED)

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS)
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_credenciamento_puro_nunca_roda_check_prior_notice(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """GAP-CRED-6: BRT_PriorNotice/ST_CheckPriorNotice (RN 567, so descredenciamento) NUNCA rodam
    para direcao=credenciamento pura (sem indicio de irregularidade). cred_prior_notice nao tem
    input de direcao — antes do fix rodava (e publicava cred.pended) tambem para credenciamento,
    onde a obrigacao regulatoria e irrelevante.
    """
    inst = await start_cred(
        direcao="credenciamento",
        tipo_prestador="clinica",
        documentacao_completa=True,
        licenca_valida=False,  # forca ANALISE_HUMANA na admissibilidade -> segue para BRT_Route -> UT
        dentro_criterios_rede=True,
    )
    iid = inst["id"]

    ut = await _drive_to_cred(engine, cred_probe, iid)
    assert "gestao-rede" in ut.candidate_groups
    assert "juridico-rede" not in ut.candidate_groups

    assert not cred_probe.notifications_of_type("cred.check_prior_notice"), (
        "GAP-CRED-6: check_prior_notice NAO deve rodar para credenciamento puro"
    )
    assert not cred_probe.has_event(_CRED_PENDED), (
        "GAP-CRED-6: cred.pended (cure-window RN 567) NAO deve ser publicado para credenciamento puro"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_solicitar_info_descredenciamento_aguarda_e_retoma_ut(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """GAP-CRED-5: decisao_cred=SOLICITAR_INFO (descred.) NAO tinha ramo dedicado e caia no default
    (vinculo_mantido — desfecho ERRADO: SOLICITAR_INFO significa que falta informacao, nao que o
    vinculo foi mantido). Agora aguarda msg.cred.info_received e retoma UT_AnaliseDescredenciamento
    para nova decisao.
    """
    inst = await start_cred(direcao="descredenciamento", tem_beneficiarios_vinculados=False)
    iid = inst["id"]
    business_key = inst["businessKey"]

    ut = await _drive_to_descred(engine, cred_probe, iid)
    await engine.complete_task_as_human(
        ut.id, {"decisao_cred": "SOLICITAR_INFO", "responsavel_id": "juridico-rede-sintetico-001"}
    )
    await cred_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_VINCULO_MANTIDO not in ended, (
        "GAP-CRED-5: SOLICITAR_INFO NAO deve produzir End_VinculoMantido (desfecho errado)"
    )
    assert not (ended & _ENDS_ADVERSOS)
    assert await engine.instance_is_active(iid), "Instancia deve aguardar a informacao adicional"

    correlate_payload = {"messageName": "msg.cred.info_received", "businessKey": business_key}
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.cred.info_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )
    await cred_probe.drain()

    ut2 = await engine.await_user_task(iid, _UT_DESCRED)
    assert "gestao-rede" in ut2.candidate_groups or "juridico-rede" in ut2.candidate_groups

    await engine.complete_task_as_human(
        ut2.id,
        {
            "decisao_cred": "MANTER",
            "fundamentacao": "Vinculo mantido apos info adicional (teste)",
            "responsavel_id": "juridico-rede-sintetico-001",
        },
    )
    await cred_probe.drain()

    ended2 = await _await_end(engine, iid)
    assert _END_VINCULO_MANTIDO in ended2
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_solicitar_info_credenciamento_aguarda_e_retoma_ut(
    engine: EngineRest,
    cred_probe: CredEngineProbe,
    start_cred: Callable[..., Any],
) -> None:
    """GAP-CRED-5: decisao_cred=SOLICITAR_INFO (cred.) simetrico ao caso de descredenciamento — aguarda
    msg.cred.info_received e retoma UT_AnaliseCredenciamento, em vez de encerrar como vinculo_mantido.
    """
    inst = await start_cred(
        direcao="credenciamento",
        tipo_prestador="clinica",
        documentacao_completa=True,
        licenca_valida=False,
        dentro_criterios_rede=True,
    )
    iid = inst["id"]
    business_key = inst["businessKey"]

    ut = await _drive_to_cred(engine, cred_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_cred": "SOLICITAR_INFO"})
    await cred_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_VINCULO_MANTIDO not in ended, (
        "GAP-CRED-5: SOLICITAR_INFO NAO deve produzir End_VinculoMantido (desfecho errado)"
    )
    assert not (ended & _ENDS_ADVERSOS)
    assert await engine.instance_is_active(iid), "Instancia deve aguardar a informacao adicional"

    correlate_payload = {"messageName": "msg.cred.info_received", "businessKey": business_key}
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.cred.info_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )
    await cred_probe.drain()

    ut2 = await engine.await_user_task(iid, _UT_CRED)
    assert "gestao-rede" in ut2.candidate_groups

    await engine.complete_task_as_human(
        ut2.id, {"decisao_cred": "APROVAR_CREDENCIAMENTO", "responsavel_id": "gestao-rede-sintetico-001"}
    )
    await cred_probe.drain()

    ended2 = await _await_end(engine, iid)
    assert _END_CREDENCIADO in ended2
    assert not (ended2 & _ENDS_ADVERSOS)
