"""SP-OP-CONTAS-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 2 (13-family
process-suite port). contas.py is one of the 9 ADR-0028/T1.5-touched modules (glosa_reason_
normalization/glosa_classification/glosa_triage/contas_sla are all evaluated via the real
engine DMN transport, not a Python if/elif fork) AND one of the 6 typed-I/O modules with
dict-boundary entry functions (ADR-0026 §2b) — mirrors `test_sp_op_cancel_001.py`'s
dict-boundary/typed-I/O pattern (self-contained fixtures, drift-guard).

Implementa o test-spec do W5 contra o engine real (ADR-0011: SEM mock de engine). Cada teste:

1. inicia a instancia via REST com business key `CONTAS-amh-{numero_lote_tiss}`;
2. drena as external tasks com o `contas_probe` (workers reais + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que o aceite passa pela UT humana).

Dados sinteticos obvios: lote `LOTE-TESTE-NNNN`, prestador `PRESTADOR-TESTE-001`, beneficiario
pseudonimizado `bnf-teste-0001`, tenant `amh`. Business key `CONTAS-amh-{lote}`.
Process key: SP-OP-CONTAS-001 (exato — nao alterar).

## Invariante L0 (DoD deliverable)

test_nenhum_caminho_automatizado_glosa:
  Varredura de TODAS as combinacoes de input da DMN glosa_triage. A instancia NUNCA atinge
  End_GlosaAplicadaHumano nem End_PagamentoParcialHumano sem que UT_AnalistaContas /
  UT_CoordenacaoContasAssume tenha sido
  completada por humano com decisao_contas in {GLOSAR, PAGAR_PARCIAL}. Prova via
  history/activity-instance.

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor where
the underlying mechanism is unchanged; see FINDINGS for the cases where it is NOT unchanged):

  1. **`EngineRest._to_camunda_vars` wire-encoding gap (LOAD-BEARING — verify-before-citing).**
     `tests/integration/processes/engine_rest.py:129-146` (shared infra, reuse-not-duplicate)
     only special-cases `dict-with-"value"` / `bool` / `int` — everything else (`float`, `list`,
     `dict` without `"value"`) falls through to `{"value": str(v), "type": "String"}`. This is
     UNLIKE `harness.py`'s own `_to_camunda_var` / `dmn_transport.py`'s `_to_camunda_vars`, which
     both correctly type `float -> Double` and `list|dict -> Json`. For AUTH/CANCEL this never
     surfaced (their money fields are seeded as opaque strings never arithmetic'd by the ported
     scenarios; their list fields are never parsed downstream). For CONTAS it IS load-bearing:
     `identify_glosa()` (`contas.py:193`) computes `total_apresentado_int =
     int(input_data.valor_apresentado_brl * 100)` unconditionally whenever `linhas_conta_refs` is
     non-empty — if `valor_apresentado_brl` arrives as the STRING `"1800.00"` (naively
     stringified), `"1800.00" * 100` (str-repeat) then `int(...)` raises `ValueError` ->
     `FunctionWorker.execute`'s `_HARNESS_CLASSIFIED` re-raises it unchanged -> harness `_handle`
     (`harness.py:952-954`) reports an IMMEDIATE incident at `ST_IdentifyGlosa`, the FIRST
     worker in the entire flow — blocking every single non-empty-`linhas_conta_refs` scenario.
     Live-verified via direct `identify_glosa()` invocation (not just static reading) before
     writing this fixture. WORKED AROUND here (not an xfail — this is entirely a test-fixture
     concern, not a src/ gap): `_double_var()`/`_json_var()` helpers below wrap
     `valor_apresentado_brl` (`Double`) and `linhas_conta_refs`/`reason_codes_tiss` (`Json`) as
     engine-shaped `{"value": ..., "type": ...}` dicts BEFORE handing them to
     `engine.start_by_key` — `EngineRest._to_camunda_vars`'s `isinstance(v, dict) and "value" in
     v` branch passes these straight through unchanged. Once each worker's OWN `complete()` call
     re-emits these variables (via `harness.py`'s correctly-typed `_to_camunda_var`), they
     self-heal downstream regardless — the gap only bites at the very first, TEST-seeded hop.
     Worth fixing in the shared `engine_rest.py` for future family ports that seed money/list
     process variables at START (not done here — shared infra, reuse-not-duplicate per charter).

  2. **`categoria_normalizada` is fully RECOMPUTED downstream — donor's direct seed is inert.**
     Donor seeds `categoria_normalizada` directly as a scenario control at START. In v2,
     `ST_AnalyzeReason` (`analyze_reason_entry` -> `analyze_reason`, `contas.py:214-252`) ALWAYS
     overwrites `categoria_normalizada` as a process-variable output when it completes (its
     return dict includes the key unconditionally) — donor's directly-seeded value is dead by the
     time `BRT_Classification` reads it. Adapted: `start_contas` translates a `categoria_
     normalizada` scenario-control kwarg into a representative TISS `reason_codes_tiss` code
     (`_CATEGORIA_REASON_CODE` below, cross-checked against `glosa_reason_normalization.dmn`'s
     own rules) so the REAL `analyze_reason` -> DMN pipeline produces the intended category,
     rather than seeding a variable that gets silently discarded. `denial_ratio`/`divergencia_
     valor`/`glosa_count` are likewise never seeded directly (GAP-CONTAS-2, donor's own
     convention, preserved): they are DATA-DRIVEN via `linhas_conta_refs` shape
     (`_build_glosa_scenario` below), verified against a direct `identify_glosa()` invocation
     (see FINDINGS) rather than assumed from the donor's per-line shape (which does NOT reproduce
     `divergencia_valor=False` under v2's current `identify_glosa` — see FINDING 2 below).
  - import paths -> v2 `maezo.tools.workers.contas`/`harness`/`dmn_transport`/`events`.
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5).
  - `drain()` uses the shared `drain_topics()` helper (v2 transport signature adaptation).
  - `contas_probe` wires `dmn=CibSevenDmnTransport(CIBSEVEN_BASE_URL)` into `register_contas_
    workers` (ADR-0028 §1 seam) — `analyze_reason_entry`/`prepare_triage_dossier_entry` both
    `require_dmn(...)` and raise `DmnEvaluationError` (transient) without it.
  - `test_worker_registrar_glosa_recusa_sem_humano`: donor's `make_register_glosa_accept_
    handler(kafka) -> Callable[[ExternalTask], ...]` + `WorkerBpmnError` do not exist on v2 main
    (ADR-0026 §2b replaced the class/factory-per-topic shape with dict-boundary entry functions
    for this module). Adapted to call `registrar_glosa_entry(variables: dict, *,
    kafka=None) -> dict` directly; the guard raises `ContasGlosaNotHumanError` (a `PermissionError`
    subclass), not a modeled `WorkerBpmnError`. The 4 guard scenarios are preserved verbatim
    (decision missing / wrong decision / missing analista_id / success); the "success" shape
    changed to check the RETURNED dict (v2's entry function does not itself call `kafka.publish`
    — see FINDING 1 below, same systemic gap).

FINDINGS (root-cause, file:line evidence; see PR body / evidence-ledger for full detail):

  1. **Kafka-publish gap (systemic, T3.1 events.publish-fix ledger row).** `contas.py`'s 9
     dict-boundary entry functions (`identify_glosa_entry` contas.py:549, `analyze_reason_entry`
     :557, `calculate_impact_entry`:569, `prepare_triage_dossier_entry`:579,
     `registrar_glosa_entry`, `emitir_demonstrativo_entry`, `devolver_conta_entry`,
     `notify_sla_risk_entry`, `handoff_pagamento_entry`, `publish_entry`) EVERY one does
     `del kafka  # unused` —
     none calls `kafka.publish`. `contas_probe.notifications_of_type("contas.<fn>")` can
     therefore never observe any of these workers' executions. Distinct from the GENERIC
     `operadora.events.publish` path (events.py:247, already fixed/merged, registered here too)
     — `contas_probe.has_event(...)` assertions over `agents.events.contas.*` topics DO work
     (they flow through the generic handler, not contas.py's own functions) and are asserted
     green below. Only the tests whose blocking assertion is specifically `notifications_of_type
     ("contas.<fn>")` are marked `_WORKER_KAFKA_GAP_REASON`.
  1a. **t3.1-event-gap-a-contas batch (test-only, zero src/BPMN/DMN/contract edits).** The 5
     `notifications_of_type("contas.<fn>")` checks that were the SOLE blocking assertion in their
     xfail test (`test_happy_path_recorrer_handoff_recurso` x3, `test_happy_path_aceitar_glosa_
     pelo_analista`, `test_happy_path_reenviar`, `test_timer_alerta_sla_nao_interruptivo`,
     `test_sla_ancora_em_data_recebimento_lote_nao_em_attach_da_ut`) were re-expressed against the
     strongest available engine-side equivalent: `has_event(_CONTAS_COMPLETED, ...)` on the real
     `operadora.events.publish` path where a downstream `ST_Publish*` task exists
     (registrar_glosa/emitir_demonstrativo/devolver_conta/handoff_pagamento all feed one), or
     `engine.get_variable`/`activity_instances_ended` where none does (analyze_reason's
     `categoria_normalizada`, prepare_triage_dossier's execution, and BOTH `notify_sla_risk`
     non-interruptive alert tests — `ST_NotificarRiscoSla` routes straight to
     `End_RiscoSlaNotificado`, no publish task on that branch at all). xfail/strict marks are
     UNCHANGED on purpose (see `_WORKER_KAFKA_GAP_REASON`'s UPDATE paragraph): the underlying
     kafka-publish gap is not touched by this batch, and whether the adapted asserts flip these
     tests fully green has not been re-verified against a live engine here — deferred to the
     live-validation step. `test_aceitar_glosa_exige_campos` (below) ALSO calls
     `notifications_of_type` but is NOT xfail-marked and asserts emptiness (`assert not aceites`)
     — that assert is structurally vacuous (always true; the channel is always empty regardless of
     whether the worker guard fired) and was deliberately left UNCHANGED here per this batch's
     policy against silently altering a passing test's semantics; the test's real coverage of the
     guard already comes from the `_ENDS_ADVERSOS` check / `_assert_no_glosa_without_
     human_task` checks alongside it.
  2. **Registry drift (mild, opposite direction from cancel's): 1 orphan registration.**
     `register_contas_workers` (contas.py:661-719) registers `operadora.contas.publish` (line
     717) — contas.py's OWN module comment (contas.py:530-533) says this "has no distinct spec
     topic ... registered under a function-derived topic for registry completeness": no BPMN
     `camunda:topic` in `SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn` ever references
     `operadora.contas.publish` (grep confirms every `ST_Publish*` service task in the BPMN uses
     `camunda:topic="operadora.events.publish"`, the GENERIC topic, exclusively). Harmless (the
     orphan topic is simply never dispatched to), but `_CONTAS_WORKER_TOPICS` below includes it
     for the drift-guard to pass naturally, and this is called out rather than silently ignored.
  3. **Guard errors never reach a BPMN boundary catch (both declared boundaries are DEAD code).**
     `ContasGlosaNotHumanError` (a `PermissionError` subclass) and
     `ContasLoteInvalidoError` (contas.py:50, `ValueError` subclass) are BOTH in `base.py`'s
     `_HARNESS_CLASSIFIED` tuple -> re-raised unchanged by `FunctionWorker.execute` ->
     `harness._handle` (harness.py:948-954) routes `PermissionError`/`ValueError` STRAIGHT to
     `_report_failure(..., retries_override=0)` — an immediate engine incident via
     `handle_failure`, NEVER via `handle_bpmn_error`/the `bpmn_error_allowlist` path (that path
     is reserved for `WorkerBpmnError`, which neither of these classes subclasses). Consequently
     the BPMN's own `Error_ContasGlosaNotHuman`/`Error_ContasLoteInvalido` declarations
     (`SP-OP-CONTAS-001_...bpmn:16`) have NO matching `boundaryEvent`/`errorEventDefinition`
     ANYWHERE in the process (grep confirms only 3 boundary events total: `BT_AlertaSlaContas`,
     `BT_SlaAnaliseContas`, `BME_LinhasAtualizadas` — a timer/timer/message trio, none an error
     boundary) — even if one existed, it could never fire for these exception types. Guard
     failures therefore surface as an OPEN ENGINE INCIDENT at `ST_RegistrarGlosa`, not a
     graceful boundary-catch terminal (unlike cancel's `End_ManterNaoConfirmado` /
     auth's `End_FundamentacaoIncompletaBloqueada`). This does NOT block
     `test_aceitar_glosa_exige_campos` below (the donor's own assertions there are compatible
     with an open-incident outcome — verified: it only asserts the adverse end is NOT reached and
     no acceptance was registered, both hold regardless of incident vs boundary-catch), so no
     xfail is attached to that test; this finding is informational/structural, flagged for the
     record per the "root-cause, never fabricated" discipline.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.gateway.audit_postgres import FreshSinkAuditEmitter
from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport
from maezo.tools.workers.contas import (
    ContasGlosaNotHumanError,
    register_contas_workers,
    registrar_glosa_entry,
)
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness
from maezo.tools.workers.pagto import register_pagto_workers

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest, assert_definition_provenance

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn"
_DMN_REASON = _REPO / "spec/processes/dmn/glosa_reason_normalization.dmn"
_DMN_CLASS = _REPO / "spec/processes/dmn/glosa_classification.dmn"
_DMN_TRIAGE = _REPO / "spec/processes/dmn/glosa_triage.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/contas_sla.dmn"

# SP-OP-PAGTO-001 — o destino do handoff de TODA perna que paga (ADR-0040 §3.1).
_BPMN_PAGTO = _REPO / "spec/processes/bpmn/SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn"
_DMN_PAGTO_ADMISSIBILITY = _REPO / "spec/processes/dmn/pagto_admissibility.dmn"
_DMN_PAGTO_ALCADA = _REPO / "spec/processes/dmn/pagto_alcada.dmn"

# External task topics do contrato SP-OP-CONTAS-001.
_PROCESS_KEY = "SP-OP-CONTAS-001"

#: Marcadores que SO existem na definicao desta arvore (SP-OP-CONTAS-001 reconstruido na
#: perspectiva do pagador) e marcadores que SO existem na definicao da `main`. Verificados
#: 11/11 discriminantes contra `git show main:<bpmn>`.
#:
#: Por que este guard existe (licao do 2o reparo do PR-3, e visivel nesta suite tambem): o engine
#: do dev-stack e COMPARTILHADO e `POST /deployment/create` cria uma VERSAO NOVA da mesma
#: process-definition-key. Um `make deploy-artifacts` — ou um `make release-floor-check`, que roda
#: `pytest tests/` INTEIRO — disparado de OUTRO checkout contra o mesmo `ENGINE_REST_URL` vira a
#: `latestVersion` no meio da sessao, e `start_by_key` passa a instanciar a definicao ALHEIA. As
#: falhas resultantes se parecem exatamente com defeitos do PR: terminais que nao sao alcancados,
#: eventos de dominio que nao aparecem. Verificar o deploy UMA VEZ antes do pytest nao basta — a
#: contaminacao chega depois. Aqui ela falha ALTO, com o marcador que divergiu.
_MARCADORES_DO_CHECKOUT: tuple[str, ...] = (
    "Start_LoteTissRecebido",
    "ST_ApurarDivergencias",
    "ST_DevolverConta",
    "End_ContaAprovadaIntegral",
)
_MARCADORES_DE_OUTRA_DEFINICAO: tuple[str, ...] = (
    "Start_LoteRecebido",
    "ST_IdentifyGlosa",
    "ST_StartRecurso",
    "End_SemGlosa",
    "End_GlosaAceitaHumano",
    "ACEITAR_GLOSA",
    "ST_ReconcilePayment",
)

#: Ids de process-definition ja verificados nesta sessao (uma leitura de XML por VERSAO). Uma
#: versao nova — i.e. um deploy no meio da sessao — nunca esta neste conjunto, entao e sempre
#: verificada; o cache so evita re-ler a MESMA versao 26 vezes.
_definicoes_verificadas: set[str] = set()

_PUBLISH_TOPIC = "operadora.events.publish"
_IDENTIFY_TOPIC = "operadora.contas.identify_glosa"
_ANALYZE_TOPIC = "operadora.contas.analyze_reason"
_IMPACT_TOPIC = "operadora.contas.calculate_impact"
_DOSSIER_TOPIC = "operadora.contas.prepare_triage_dossier"
_NOTIFY_SLA_TOPIC = "operadora.contas.notify_sla_risk"
_REGISTRAR_GLOSA_TOPIC = "operadora.contas.registrar_glosa"
_EMITIR_DEMONSTRATIVO_TOPIC = "operadora.contas.emitir_demonstrativo"
_DEVOLVER_CONTA_TOPIC = "operadora.contas.devolver_conta"
_HANDOFF_PAGAMENTO_TOPIC = "operadora.contas.handoff_pagamento"
_START_FRAUDE_TOPIC = "operadora.contas.start_fraude"  # T4 Phase-3 CONTAS→FRAUDE in-flow worker.
_ORPHAN_PUBLISH_TOPIC = "operadora.contas.publish"  # FINDING 2: registered, no BPMN consumer.

# Topicos servidos pelos workers REAIS registrados no harness (drain generico). Inclui o orfao
# (FINDING 2) para que o drift-guard do `contas_probe` passe naturalmente.
_CONTAS_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _IDENTIFY_TOPIC,
    _ANALYZE_TOPIC,
    _IMPACT_TOPIC,
    _DOSSIER_TOPIC,
    _NOTIFY_SLA_TOPIC,
    _REGISTRAR_GLOSA_TOPIC,
    _EMITIR_DEMONSTRATIVO_TOPIC,
    _DEVOLVER_CONTA_TOPIC,
    _HANDOFF_PAGAMENTO_TOPIC,
    _START_FRAUDE_TOPIC,
    _ORPHAN_PUBLISH_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_CONTAS_RECEIVED = "agents.events.contas.received"
_CONTAS_GLOSA_IDENTIFIED = "agents.events.contas.glosa_identified"
_CONTAS_SLA_BREACHED = "agents.events.contas.sla_breached"
_CONTAS_COMPLETED = "agents.events.contas.completed"

_UT_ANALISTA = "UT_AnalistaContas"
_UT_COORDENACAO = "UT_CoordenacaoContasAssume"

_END_APROVADA_INTEGRAL = "End_ContaAprovadaIntegral"
_END_APROVADA_HUMANO = "End_ContaAprovadaHumano"
_END_GLOSA_APLICADA = "End_GlosaAplicadaHumano"
_END_PAGAMENTO_PARCIAL = "End_PagamentoParcialHumano"
_END_CONTA_DEVOLVIDA = "End_ContaDevolvidaPrestador"
_END_ENCAMINHADA_FRAUDE = "End_EncaminhadaFraude"  # T4 Phase-3 CONTAS→FRAUDE leg terminal.
_END_ERR_DECISAO = "End_ErrContasDecisaoInvalida"

#: The TWO adverse terminals (ADR-0040): applying a glosa in full, and reducing the payment.
#: Neither is reachable without a completed human User Task — that is the L0 invariant.
_ENDS_ADVERSOS = frozenset({_END_GLOSA_APLICADA, _END_PAGAMENTO_PARCIAL})

_UT_HUMANAS_ACEITE = frozenset({_UT_ANALISTA, _UT_COORDENACAO})

#: CONTAS-DATA-VENCIMENTO-FAILCLOSED-DOWNSTREAM (R-084): as tarefas que materializam um efeito
#: EXTERNO ao prestador na adjudicacao — os tres emissores de demonstrativo (mais o da glosa
#: integral) e os dois registros de glosa. `GW_VencimentoConta` fica ANTES de todas elas; numa
#: conta sem `data_vencimento` nenhuma pode aparecer na historia de atividades do engine.
#: A dominancia ESTATICA da mesma lista e provada sem engine em
#: `tests/unit/spec/test_sp_op_contas_001_artefatos.py::
#: test_gate_de_vencimento_domina_todo_efeito_externo_da_adjudicacao`.
_EFEITOS_EXTERNOS_DA_ADJUDICACAO = frozenset(
    {
        "ST_EmitirDemonstrativoIntegral",
        "ST_EmitirDemonstrativoAprovado",
        "ST_EmitirDemonstrativoParcial",
        "ST_EmitirDemonstrativo",
        "ST_RegistrarGlosa",
        "ST_RegistrarGlosaParcial",
    }
)

# FINDING 1 (module docstring): contas.py's 9 dict-boundary entry functions never call
# kafka.publish (each does `del kafka  # unused`) — the generic operadora.events.publish path
# (events.py, T3.1 R2 fix, registered below via register_events_workers) is UNAFFECTED and
# asserted green throughout. Only tests whose blocking assertion is a `notifications_of_type
# ("contas.<fn>")` call are marked with this reason.
_WORKER_KAFKA_GAP_REASON = (
    "v2 systemic drift (T3.1 finding 1, same class as escalation/auth/cancel's Kafka-gap "
    "residuals — ledger row 'T3.1 (events.publish fix)'): contas.py's dict-boundary entry "
    "functions (identify_glosa_entry/analyze_reason_entry/calculate_impact_entry/"
    "prepare_triage_dossier_entry/registrar_glosa_entry/emitir_demonstrativo_entry/"
    "devolver_conta_entry/notify_sla_risk_entry/handoff_pagamento_entry) all accept a `kafka` "
    "kwarg but `del kafka` it "
    "unused — none calls kafka.publish. contas_probe.notifications_of_type('contas.<fn>') can "
    "therefore never observe any of these workers' executions, even though the worker itself "
    "runs and completes against the live engine (verified: the instance reaches its correct "
    "flow point / end event and passes every other flow assertion first). Distinct from the "
    "GENERIC operadora.events.publish path (events.py:247, already fixed) which DOES work and is "
    "asserted via has_event(...) elsewhere in this file. Fix belongs to the Kafka-producer wiring "
    "task, not this port."
    "\n\nUPDATE (t3.1-event-gap-a-contas, test-only batch, zero src/BPMN/DMN/contract edits): "
    "assert adapted to engine-side has_event, pending live-proof flip. The dead "
    "`notifications_of_type(...)` checks below have been replaced with the strongest available "
    "engine-side equivalent per assert — `has_event(_CONTAS_COMPLETED, ...)` on the real "
    "`operadora.events.publish` path where a matching ST_Publish* task exists downstream "
    "(registrar_glosa/emitir_demonstrativo/devolver_conta/handoff_pagamento), `engine.get_variable(...)`/"
    "`activity_instances_ended(...)` where no domain event carries the fact at all "
    "(analyze_reason's categoria_normalizada value, prepare_triage_dossier's execution, "
    "notify_sla_risk's non-interruptive alert branch — none has a downstream ST_Publish task in "
    "SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn). This mark/strict is left UNCHANGED "
    "on purpose: the underlying kafka-publish gap in contas.py's own entry functions is NOT "
    "touched by this batch (no src/ edit), so whether the adapted assertions now cause the test "
    "to run clean end-to-end against a live engine (as the docstring above already claims for "
    "the OTHER, unrelated assertions in each of these tests) has not been re-verified live here — "
    "that confirmation, and the consequent xfail removal, is deferred to the live-validation step "
    "(see PR body / t3.1-event-gap-a-contas report). "
    "LIVE-FLIPPED (wave2b2 R1 live-validation, cibseven 2.1.0): all 5 adapted tests ran green "
    "end-to-end against a real engine + real PostgresAuditSink and their xfail marks were removed "
    "in-step. Right-reason evidence (engine /history, independent of the tests' own asserts): "
    "ST_PublishEncaminhadaRecurso/ST_PublishGlosaAceita/ST_PublishReenviada completed in the "
    "respective instances (has_event matched the REAL publish-task payloads: desfecho + "
    "glosa_id/codigo_glosa_tiss+analista_id/prestador_id), ST_NotificarRiscoSla completed in "
    "both SLA-alert instances, and the recorrer instance's engine-recorded variables were "
    "categoria_normalizada=valor + glosa_id=GLOSA-TESTE-001. This constant is retained (no test "
    "references it anymore) purely because the module docstring/FINDING prose above cites it; the "
    "underlying per-worker kafka-publish gap in contas.py itself remains open and out of scope."
)


def _json_var(value: Any) -> dict[str, Any]:
    """Engine-shaped `Json`-typed variable (PORT NOTE 1: `EngineRest._to_camunda_vars` gap)."""
    return {"value": json.dumps(value), "type": "Json"}


def _double_var(value: float) -> dict[str, Any]:
    """Engine-shaped `Double`-typed variable (PORT NOTE 1: `EngineRest._to_camunda_vars` gap)."""
    return {"value": float(value), "type": "Double"}


# TISS reason codes representative of each glosa_reason_normalization.dmn category (verified
# against the deployed table's own rules, not assumed) — drives `categoria_normalizada` via the
# REAL analyze_reason -> DMN pipeline (PORT NOTE 2), replacing the donor's dead direct seed.
_CATEGORIA_REASON_CODE: dict[str, str] = {
    "administrativa": "GUIA_INCOMPLETA",
    "tecnica": "PROCEDIMENTO_NAO_INDICADO",
    "valor": "VALOR_ACIMA_TABELA",
    "documental": "SEM_AUTORIZACAO",
    "clinica": "CARENCIA",
    "desconhecida": "COD_NAO_MAPEADO_TESTE_9999",  # not in any DMN rule -> catch-all "desconhecida"
}


def _build_glosa_scenario(
    *,
    has_glosas: bool,
    divergencia_valor: bool,
    categoria_normalizada: str | None,
    reason_codes_tiss: list[str] | None,
    linhas_conta_refs: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Translate donor-style scenario-control booleans into REAL `linhas_conta_refs`/
    `reason_codes_tiss` data (GAP-CONTAS-2 convention, preserved: `identify_glosa`/`calculate_
    impact` COMPUTE the facts, they are never seeded directly).

    Live-verified against a direct `identify_glosa()` invocation (not assumed): `has_glosas=True,
    divergencia_valor=False` requires a LOTE-LEVEL `reason_codes_tiss` entry (which independently
    triggers `has_glosas` via `identify_glosa`'s `len(reason_codes) > 0` check) over otherwise
    CLEAN lines — donor's original per-line `reason_code_tiss`-without-`valor_glosado` shape does
    NOT reproduce `divergencia_valor=False` under v2's current `identify_glosa` (that shape
    causes the whole line's `valor_apresentado` to be folded into `total_glosado`, since v2's
    branch order is `if valor_glosado>0: ... elif valor_pago: ... else: total_glosado +=
    valor_apresentado` whenever ANY reason code is present on the line) — a genuine golden-parity
    behavior divergence from the donor's assumed shape, not a fixture bug.
    """
    if linhas_conta_refs is not None:
        linhas = linhas_conta_refs
    elif not has_glosas:
        linhas = [{"valor_apresentado_centavos": 90000}, {"valor_apresentado_centavos": 90000}]
    elif divergencia_valor:
        linhas = [
            {"valor_apresentado_centavos": 90000, "valor_glosado_centavos": 90000},
            {"valor_apresentado_centavos": 90000},
        ]
    else:
        linhas = [{"valor_apresentado_centavos": 90000}, {"valor_apresentado_centavos": 90000}]

    if reason_codes_tiss is not None:
        codes = reason_codes_tiss
    elif not has_glosas:
        codes = []
    elif categoria_normalizada is not None:
        codes = [_CATEGORIA_REASON_CODE[categoria_normalizada]]
    else:
        codes = ["VALOR_ACIMA_TABELA"]

    return linhas, codes


def _data_recebimento_futura(days: int = 45) -> str:
    """Ancora DINAMICA no futuro (GAP-CONTAS-4 — precedente GAP-NIP-1/#111).

    Timers timeDate absoluto = data_recebimento_lote + duracao (DMN contas_sla). Uma data FIXA no
    passado ficaria obsoleta assim que o relogio real a ultrapassasse. +45 dias da folga acima do
    maior prazo (P30D). Testes que precisam disparar o timer usam execute_job (nunca relogio).
    """
    return (datetime.now(UTC) + timedelta(days=days)).date().isoformat()


# ---------------------------------------------------------------------------
# EngineProbe para contas
# ---------------------------------------------------------------------------


@dataclass
class ContasEngineProbe:
    """Driva os workers reais de contas contra o engine CIB Seven."""

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
        await drain_topics(self.transport, self.harness, self.worker_id, _CONTAS_WORKER_TOPICS, rounds=rounds)


async def _assert_definicao_latest_e_do_checkout(
    engine: EngineRest, *, contexto: str, exigir_deployada: bool = True
) -> None:
    """A versao LATEST de SP-OP-CONTAS-001 no engine e a desta arvore (ver `_MARCADORES_*`).

    `exigir_deployada=False` para os pontos de checagem que NAO acabaram de deployar CONTAS: se a
    key nem existe neste engine (404) nao ha definicao alheia para contaminar nada. Onde CONTAS
    ESTA deployada, a checagem e a mesma — e todo start passa pelo guard POR INSTANCIA de
    `start_contas`, que nao depende deste.
    """
    xml = await engine.latest_definition_xml_or_none(_PROCESS_KEY)
    if xml is None:
        if exigir_deployada:
            raise AssertionError(
                f"{contexto}: {_PROCESS_KEY} nao esta deployada logo apos o proprio deploy — "
                "o engine aceitou o POST e nao registrou a definicao"
            )
        return
    assert_definition_provenance(
        xml,
        must_contain=_MARCADORES_DO_CHECKOUT,
        must_not_contain=_MARCADORES_DE_OUTRA_DEFINICAO,
        context=contexto,
    )


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 4 DMN de contas/glosa da arvore no engine real, e PROVA a proveniencia."""
    deployment_id = await engine.deploy(
        _BPMN, _DMN_REASON, _DMN_CLASS, _DMN_TRIAGE, _DMN_SLA, name="SP-OP-CONTAS-001-qa"
    )
    await _assert_definicao_latest_e_do_checkout(engine, contexto="setup de deploy_artifacts")
    return deployment_id


@pytest_asyncio.fixture
async def contas_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str, audit_pg: tuple[str, str]
) -> AsyncIterator[ContasEngineProbe]:
    """Probe que serve as external tasks com os workers reais de contas."""
    worker_id = f"qa-contas-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # Nenhum WorkerBpmnError e lancado por contas.py (FINDING 3: os guards sao PermissionError/
    # ValueError, sempre incident, nunca bpmnError) — bpmn_error_allowlist deliberadamente OMITIDO.
    # T1.10 wave: emit-before-complete is FAIL-CLOSED (harness.py _emit_audit) — a real
    # PostgresAuditSink (lane PG, migrations 0001->0005) is REQUIRED or the harness refuses
    # to complete. `tenant` scopes the durable audit chain / dedup key to the per-run schema.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        audit_sink=audit_sink,
    )
    kafka = FakeKafkaPublisher()
    dmn = CibSevenDmnTransport(CIBSEVEN_BASE_URL, timeout=30.0)
    # EB-4/ADR-0040: handoff_pagamento runs the fenced `start_process_idempotent` chokepoint
    # against SP-OP-PAGTO-001 (the STRICT dedup family), so it REQUIRES the engine seam
    # (`engine=`) + a loop-agnostic durable audit
    # sink (`audit_sink=`, FreshSinkAuditEmitter — the sync worker emits on its own asyncio.run loop,
    # NOT the harness's pooled sink). Same split the live worker-daemon + the inadimplencia probe use.
    engine_seam = FreshClientCibSevenTransport(CIBSEVEN_BASE_URL)
    handoff_audit_sink = FreshSinkAuditEmitter(audit_pg[0], audit_tenant)
    register_contas_workers(harness, kafka, dmn=dmn, engine=engine_seam, audit_sink=handoff_audit_sink)
    # T3.1 R2: o worker generico operadora.events.publish que todo ST_Publish* deste BPMN usa.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (mirrors cancel's — verbatim style): todo topico contas.* registrado no harness
    # DEVE estar na lista de drain — falha AQUI, explicita, se um worker novo ficar fora.
    contas_registered = {t for t in harness.registered_topics if t.startswith("operadora.contas.")}
    missing_from_drain = contas_registered - set(_CONTAS_WORKER_TOPICS)
    assert not missing_from_drain, (
        f"_CONTAS_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = ContasEngineProbe(
        engine=engine, harness=harness, transport=transport, kafka=kafka, worker_id=worker_id
    )
    try:
        yield probe
    finally:
        await transport.close()
        await dmn.close()


#: Topicos que `register_pagto_workers` REALMENTE registra (espelha `_PAGTO_WORKER_TOPICS` de
#: `test_sp_op_recurso_001.py` — o irmao de origem-RECURSO desta mesma prova). Sem drenar os
#: workers de PAGTO a instancia de destino ESTACIONA na sua primeira external task e toda
#: assercao negativa sobre a liberacao automatica vale por CONSTRUCAO — vacuidade, nao prova.
_PAGTO_WORKER_TOPICS = [
    "operadora.events.publish",
    "operadora.pagto.validate_payment_data",
    "operadora.pagto.assess_admissibility",
    "operadora.pagto.calculate_facts",
    "operadora.pagto.release_low_value_payment",
    "operadora.pagto.release_high_value_payment",
    "operadora.pagto.notify_sla_risk",
    "operadora.pagto.register_payment_refusal",
    "operadora.pagto.prepare_approval_dossier",
]

_UT_PAGTO_ADMISSIBILIDADE = "UT_AnaliseAdmissibilidade"
_BRT_PAGTO_ADMISSIBILIDADE = "BRT_PagtoAdmissibility"


@dataclass
class PagtoDrainProbe:
    """Serve as external tasks de SP-OP-PAGTO-001 com os workers REAIS de pagto.

    Existe apenas para tornar DISCRIMINANTES as assercoes de I-PAGTO-1 do lado do destino: a
    ordem so alcanca (ou deixa de alcancar) `BRT_PagtoAdmissibility` se alguem servir as tarefas
    que a antecedem.
    """

    harness: WorkerHarness
    transport: CibSevenWorkerTransport
    worker_id: str

    async def drain(self, *, rounds: int = 30) -> None:
        await drain_topics(self.transport, self.harness, self.worker_id, _PAGTO_WORKER_TOPICS, rounds=rounds)


@pytest_asyncio.fixture
async def pagto_drain(audit_sink: Any, audit_tenant: str) -> AsyncIterator[PagtoDrainProbe]:
    """Workers reais de pagto (com transporte DMN real — ADR-0028), sem seams de handoff."""
    worker_id = f"qa-pagto-drain-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        audit_sink=audit_sink,
    )
    kafka = FakeKafkaPublisher()
    register_pagto_workers(harness, kafka, dmn=CibSevenDmnTransport(CIBSEVEN_BASE_URL))
    register_events_workers(harness, kafka)
    try:
        yield PagtoDrainProbe(harness=harness, transport=transport, worker_id=worker_id)
    finally:
        await transport.close()


def _data_vencimento_futura(days: int = 60) -> str:
    """Vencimento sintetico no futuro (ISO `YYYY-MM-DD`), coerente com a ancora de recebimento.

    `handoff_pagamento` so exige que NAO esteja em branco (nunca a defaulta); a data e dinamica
    para que a suite nao dependa de um calendario fixo.
    """
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d")


def _unique_lote(prefix: str = "LOTE-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_contas(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key CONTAS-amh-{lote} e payload canonico.

    Overrides via kwargs. Dados sinteticos obvios (LOTE-TESTE-NNNN, tenant amh). `has_glosas`/
    `divergencia_valor`/`categoria_normalizada` sao CONTROLES DE CENARIO (nao seeded diretamente
    — GAP-CONTAS-2/PORT NOTE 2): traduzidos para `linhas_conta_refs`/`reason_codes_tiss` REAIS via
    `_build_glosa_scenario`. `valor_apresentado_brl`/`linhas_conta_refs`/`reason_codes_tiss` sao
    wrapped como variaveis engine-shaped (PORT NOTE 1) para sobreviver o round-trip do wire.
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        lote = overrides.pop("numero_lote_tiss", _unique_lote())
        has_glosas = bool(overrides.pop("has_glosas", True))
        divergencia_valor = bool(overrides.pop("divergencia_valor", True))
        categoria_normalizada = overrides.pop("categoria_normalizada", None)
        reason_codes_tiss = overrides.pop("reason_codes_tiss", None)
        linhas_conta_refs = overrides.pop("linhas_conta_refs", None)
        valor_apresentado_brl = float(overrides.pop("valor_apresentado_brl", 1800.00))

        linhas, codes = _build_glosa_scenario(
            has_glosas=has_glosas,
            divergencia_valor=divergencia_valor,
            categoria_normalizada=categoria_normalizada,
            reason_codes_tiss=reason_codes_tiss,
            linhas_conta_refs=linhas_conta_refs,
        )

        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "numero_lote_tiss": lote,
            "numero_guia_tiss": "GUIA-TESTE-0001",
            "numero_conta": "CONTA-TESTE-0001",
            "prestador_id": "PRESTADOR-TESTE-001",
            "beneficiario_pseudo_id": "bnf-teste-0001",
            "competencia": "2026-05",
            # GAP-CONTAS-4: ancora dinamica no futuro.
            "data_recebimento_lote": _data_recebimento_futura(),
            # MAJOR-1 (VERIFY-PR4-CONTAS): o vencimento da obrigacao de pagamento, vindo do
            # lote/termo contratual (contrato `:69`, "sim*"; origem DRAFT/verify — ADR-0040 OQ-2).
            # SEM ela, TODA perna que chega a `operadora.contas.handoff_pagamento` morria com
            # `ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO` — a recusa fail-closed do worker esta certa,
            # faltava o dado no lote sintetico. Dinamica no futuro pelo mesmo motivo da ancora:
            # uma data fixa envelhece e o teste passa a provar outra coisa.
            "data_vencimento": _data_vencimento_futura(),
            "valor_apresentado_brl": _double_var(valor_apresentado_brl),
            "tipo_lote": "sadt",
            "linhas_conta_refs": _json_var(linhas),
            "reason_codes_tiss": _json_var(codes),
            # fatos de conferencia de OUTROS workers (seeded — fora do escopo de GAP-CONTAS-2)
            "item_conforme_tabela": True,
            "documentacao_anexa": True,
            "indicio_fraude_sinalizado": False,
        }
        variables.update(overrides)
        business_key = f"CONTAS-amh-{lote}"
        inst = await engine.start_by_key(_PROCESS_KEY, business_key, variables)
        # Proveniencia da definicao que ESTA instancia carrega (nao a latest do momento): imune a
        # um deploy alheio que chegue depois do start, e o unico ponto onde a versao executada e
        # observavel. Cache por definitionId — uma leitura de XML por versao, nao por teste.
        definition_id = str(inst["definitionId"])
        if definition_id not in _definicoes_verificadas:
            assert_definition_provenance(
                await engine.definition_xml(definition_id),
                must_contain=_MARCADORES_DO_CHECKOUT,
                must_not_contain=_MARCADORES_DE_OUTRA_DEFINICAO,
                context=f"instancia {inst['id']} iniciada na definicao {definition_id}",
            )
            _definicoes_verificadas.add(definition_id)
        return inst

    return _start


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _await_end(engine: EngineRest, iid: str, *, attempts: int = 60, delay: float = 0.25) -> set[str]:
    """Espera a instancia terminar e retorna os end events atingidos."""
    for _ in range(attempts):
        state = await engine.history_state(iid)
        if state == "COMPLETED":
            return await engine.activity_instances_ended(iid)
        await asyncio.sleep(delay)
    return await engine.activity_instances_ended(iid)


async def _assert_no_glosa_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0: prova que NENHUM dos dois terminais adversos existe sem UT humana.

    ADR-0040: sao DOIS agora — `End_GlosaAplicadaHumano` (glosa integral) e
    `End_PagamentoParcialHumano` (reducao). Ambos so nascem de `decisao_contas ∈
    {GLOSAR, PAGAR_PARCIAL}` numa User Task humana.
    """
    ended = await engine.activity_instances_ended(iid)
    reached = ended & _ENDS_ADVERSOS
    if reached:
        human_tasks_in_history = ended & _UT_HUMANAS_ACEITE
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA: terminal adverso {sorted(reached)} atingido para instancia "
            f"{iid} SEM nenhuma User Task humana no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_ACEITE}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado que glosa — violacao do L0 hard (ADR-0005)."
        )


async def _drive_to_analista(engine: EngineRest, probe: ContasEngineProbe, iid: str) -> Any:
    """Drena ate UT_AnalistaContas surgir (dossie preparado pelo worker real)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_ANALISTA)


# ===========================================================================
# INVARIANTE L0 — DoD deliverable (varredura de inputs da DMN glosa_triage)
# ===========================================================================


async def test_nenhum_caminho_automatizado_glosa(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """INVARIANTE L0: NENHUM caminho automatizado APLICA glosa substantiva (ADR-0040).

    RENOMEADO de `test_nenhum_caminho_automatizado_aceita_glosa`. Mesma varredura, objeto
    invertido: o ato adverso do pagador e GLOSAR/PAGAR_PARCIAL, e os DOIS terminais adversos
    (`End_GlosaAplicadaHumano`, `End_PagamentoParcialHumano`) sao verificados.
    E a prova das 5 partes de ADR-0018 e o item que ADR-0018 cita nominalmente.

    Varredura de TODAS as combinacoes de input da DMN glosa_triage:
      categoria_normalizada in {tecnica, administrativa, clinica, valor, documental, desconhecida}
      item_conforme_tabela / divergencia_valor / documentacao_anexa in {true, false}
    (6 * 2 * 2 * 2 = 48 combinacoes; tipo_item sintetico fixo).

    Nao depende de `contas_probe.notifications_of_type(...)` (sobrevive ao gap da FINDING 1
    acima) — so consulta o historico do engine (history/activity-instance).
    """
    categorias = ["tecnica", "administrativa", "clinica", "valor", "documental", "desconhecida"]
    bools = [True, False]
    checked = 0

    for categoria, conforme, divergencia, docs in itertools.product(categorias, bools, bools, bools):
        inst = await start_contas(
            categoria_normalizada=categoria,
            item_conforme_tabela=conforme,
            divergencia_valor=divergencia,
            documentacao_anexa=docs,
            has_glosas=True,
        )
        iid = inst["id"]
        await contas_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        assert not (ended & _ENDS_ADVERSOS), (
            f"L0 VIOLADO: categoria={categoria} conforme={conforme} divergencia={divergencia} "
            f"docs={docs} atingiu um terminal adverso automaticamente. ended={ended}"
        )
        await _assert_no_glosa_without_human_task(engine, iid)
        checked += 1

    assert checked == 48, f"Esperava 48 combinacoes varridas; varri {checked}"


async def test_inelegibilidade_roteia_para_humano_nao_glosa(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """Item nao conforme + sem documentacao => glosa_triage catch-all -> ANALISE_HUMANA."""
    inst = await start_contas(
        categoria_normalizada="documental",
        item_conforme_tabela=False,
        documentacao_anexa=False,
        divergencia_valor=False,
        has_glosas=True,
    )
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Inelegibilidade nao deve produzir glosa automatica (L0)"
    await _assert_no_glosa_without_human_task(engine, iid)


async def test_glosa_tecnica_roteia_para_humano_nao_glosa(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """categoria_normalizada=tecnica => glosa_triage -> ANALISE_HUMANA (default conservador R4)."""
    inst = await start_contas(categoria_normalizada="tecnica", has_glosas=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Glosa tecnica nao deve produzir glosa automatica (L0)"
    await _assert_no_glosa_without_human_task(engine, iid)


async def test_indicio_fraude_nunca_auto_flag(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """indicio_fraude_sinalizado=true => nenhum branch auto-acusa fraude."""
    inst = await start_contas(
        indicio_fraude_sinalizado=True, categoria_normalizada="tecnica", has_glosas=True
    )
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS)
    await _assert_no_glosa_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


async def test_happy_path_pagamento_integral(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """RESCRITO de `test_happy_path_sem_glosa`. `has_glosas=false` => a conta e adjudicada
    integralmente: demonstrativo emitido ao prestador + handoff a SP-OP-PAGTO-001 +
    `contas.completed` com `desfecho=pagar_integral` + `End_ContaAprovadaIntegral`.

    O que mudou em relacao ao teste que substitui: antes esta perna terminava sem efeito nenhum.
    Agora ela COMUNICA e ENCAMINHA — e continua sem criar User Task NESTE processo, porque a
    humana esta em PAGTO (I-PAGTO-1).
    """
    await _deploy_pagto(engine)
    inst = await start_contas(has_glosas=False)
    iid = inst["id"]

    await contas_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_APROVADA_INTEGRAL in ended, f"Deve atingir End_ContaAprovadaIntegral. ended={ended}"
    assert "ST_EmitirDemonstrativoIntegral" in ended, "M6: o prestador tem de ser comunicado"
    assert "ST_HandoffPagamentoAuto" in ended, "a conta adjudicada gera a ordem de pagamento"
    assert not await engine.list_user_tasks(iid), "conta sem divergencia nao cria User Task em CONTAS"
    assert contas_probe.has_event(_CONTAS_RECEIVED)
    assert contas_probe.has_event(_CONTAS_COMPLETED, desfecho="pagar_integral")


async def test_lote_sem_data_vencimento_roteia_a_analise_humana_no_intake(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """GATE DE INTAKE de `data_vencimento` (`CONTAS-DATA-VENCIMENTO-FAILCLOSED-DOWNSTREAM`,
    decisao do dono R-084: *"validar agora"*), provado ponta-a-ponta contra o engine.

    REESCRITO de `test_lote_sem_data_vencimento_nao_gera_ordem_de_pagamento`, que provava a postura
    ANTERIOR: a recusa vivia em `operadora.contas.handoff_pagamento`, A JUSANTE dos tres
    `ST_EmitirDemonstrativo*` — o teste antigo exigia (corretamente, para o desenho de entao) que a
    instancia CHEGASSE a `ST_HandoffPagamentoAuto`, isto e, que o demonstrativo ja tivesse sido
    emitido ao prestador. Era exatamente o defeito: a operadora comunicava uma adjudicacao cuja
    ordem nunca nasceria.

    Agora `GW_VencimentoConta` fica antes de tudo isso. A perna AUTOMATICA (`has_glosas=false`) e a
    prova mais forte porque nela nao ha humano nenhum: sem o gate ela emitiria o demonstrativo
    sozinha.

    A assercao e sobre a HISTORIA DE ATIVIDADES do engine, nao sobre eco de kafka: o que importa e
    que as tarefas de efeito externo NUNCA foram executadas, e so o historico do engine sustenta
    isso (um evento ausente pode ser um publicador quebrado).
    """
    await _deploy_pagto(engine)
    lote = _unique_lote("LOTE-SEM-VENC")
    inst = await start_contas(numero_lote_tiss=lote, has_glosas=False, data_vencimento="")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups, (
        "a conta sem vencimento tem de parar na fila HUMANA de auditoria de contas"
    )

    ended = await engine.activity_instances_ended(iid)
    assert "GW_VencimentoConta" in ended, (
        f"o gate de intake tem de aparecer na historia — senao a rota provada e outra. ended={ended}"
    )
    for efeito in _EFEITOS_EXTERNOS_DA_ADJUDICACAO:
        assert efeito not in ended, (
            f"{efeito} executou numa conta SEM vencimento — o gate de intake tem de vir ANTES de "
            f"qualquer demonstrativo ao prestador e de qualquer registro de glosa. ended={ended}"
        )
    assert "GW_HasGlosas" not in ended, (
        "a perna automatica nem chega a ser avaliada quando o vencimento falta (o gate desvia "
        f"antes de GW_HasGlosas). ended={ended}"
    )
    assert _END_APROVADA_INTEGRAL not in ended
    assert not (ended & _ENDS_ADVERSOS)
    assert not await engine.find_active_instances(f"PAGTO-amh-{lote}-PRESTADOR-TESTE-001"), (
        "nenhuma ordem de pagamento pode nascer de um lote sem vencimento"
    )
    assert not await engine.incidents(iid), (
        "o desvio a humano NAO e um incidente: a instancia fica viva, na fila do analista"
    )


async def test_controle_negativo_com_vencimento_o_gate_libera_a_perna_automatica(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """Controle de NAO-VACUIDADE do teste acima: o mesmo lote COM vencimento passa direto pelo gate.

    Sem este par, um gate que roteasse TUDO a humano (ou uma fixture que nunca alcancasse a perna
    automatica) satisfaria as assercoes negativas acima por construcao.
    """
    await _deploy_pagto(engine)
    lote = _unique_lote("LOTE-COM-VENC")
    inst = await start_contas(numero_lote_tiss=lote, has_glosas=False)
    iid = inst["id"]

    await contas_probe.drain()
    ended = await _await_end(engine, iid)

    assert "GW_VencimentoConta" in ended, f"o gate roda em TODO caminho. ended={ended}"
    assert "GW_HasGlosas" in ended, f"com vencimento o token segue para GW_HasGlosas. ended={ended}"
    assert "ST_EmitirDemonstrativoIntegral" in ended
    assert _END_APROVADA_INTEGRAL in ended, f"a perna automatica continua fechando. ended={ended}"
    assert not await engine.list_user_tasks(iid), "com vencimento o gate NAO cria User Task"


async def test_sem_data_vencimento_recusa_no_handoff_segue_como_defesa_em_profundidade(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """A recusa de `handoff_pagamento` NAO foi removida — e ainda e alcancavel, pelo unico caminho
    que restou: o analista decide `PAGAR` numa conta cujo vencimento nunca apareceu.

    Esta e a metade que impede a leitura de que o gate de intake "substituiu" o fail-closed a
    jusante. O gate de intake muda o ROTEAMENTO; a recusa de `ERR_CONTAS_HANDOFF_PAGAMENTO_INVALIDO`
    continua sendo o que impede uma ordem de pagamento com prazo inventado quando um humano manda
    pagar assim mesmo. Aqui o demonstrativo E emitido — e correto: houve decisao humana, e o
    prestador tem de ser comunicado dela.
    """
    await _deploy_pagto(engine)
    lote = _unique_lote("LOTE-SEM-VENC-HUMANO")
    inst = await start_contas(numero_lote_tiss=lote, has_glosas=False, data_vencimento="")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_contas": "PAGAR",
            "valor_liberado_brl": _double_var(1800.00),
            "analista_id": "analista-sintetico-001",
        },
    )
    await contas_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert "ST_HandoffPagamentoHumano" in ended, (
        f"o teste tem de CHEGAR ao handoff para ser discriminante. ended={ended}"
    )
    assert _END_APROVADA_HUMANO not in ended, (
        "sem vencimento contratual a conta NAO pode ser dada por aprovada e encaminhada"
    )
    assert not await engine.find_active_instances(f"PAGTO-amh-{lote}-PRESTADOR-TESTE-001"), (
        "nenhuma ordem de pagamento pode nascer de um lote sem vencimento — um prazo inventado "
        "e um prazo falso (ADR-0040 OQ-2)"
    )
    assert not contas_probe.has_event(_CONTAS_COMPLETED, desfecho="pagamento_aprovado_humano")
    incidents = await engine.incidents(iid)
    handoff_incidents = [i for i in incidents if i.get("activityId") == "ST_HandoffPagamentoHumano"]
    assert handoff_incidents, (
        f"a recusa tem de ser VISIVEL: um incidente aberto em ST_HandoffPagamentoHumano, nunca um "
        f"no-op silencioso. Incidentes encontrados: {incidents}"
    )
    assert any("data_vencimento" in (i.get("incidentMessage") or "") for i in handoff_incidents), (
        f"o incidente deve NOMEAR o campo ausente: {handoff_incidents}"
    )


async def test_sem_detalhe_de_linha_fail_closed_roteia_a_humano(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """FAIL-CLOSED (GAP-CONTAS-2): lote SEM detalhe de linha NUNCA auto-clear."""
    inst = await start_contas(linhas_conta_refs=[], categoria_normalizada="valor")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_APROVADA_INTEGRAL not in ended, "Sem detalhe de linha NUNCA pode auto-clear (fail-closed)"
    assert not (ended & _ENDS_ADVERSOS)
    await _assert_no_glosa_without_human_task(engine, iid)


async def _deploy_pagto(engine: EngineRest) -> str:
    """Deploy SP-OP-PAGTO-001 — the handoff target of every paying leg of CONTAS.

    `handoff_pagamento` runs the fenced `start_process_idempotent` chokepoint against
    SP-OP-PAGTO-001, so its BPMN + DMNs MUST be deployed for the handoff to complete and the
    instance to reach its terminal (mirrors the inadimplencia->CANCEL handoff test's own
    downstream deploy; do NOT rely on cross-test engine leakage).
    """
    deployment_id = await engine.deploy(
        _BPMN_PAGTO,
        _DMN_PAGTO_ADMISSIBILITY,
        _DMN_PAGTO_ALCADA,
        name="SP-OP-PAGTO-001-qa-contas-handoff",
    )
    # Ponto de sincronizacao barato no meio do teste: se outra definicao de CONTAS tiver sido
    # deployada depois do setup, a divergencia e apanhada AQUI, antes de a instancia ser iniciada,
    # em vez de virar uma assercao de terminal que nao fecha.
    await _assert_definicao_latest_e_do_checkout(
        engine, contexto="deploy de SP-OP-PAGTO-001", exigir_deployada=False
    )
    return deployment_id


async def test_happy_path_glosar_pelo_analista(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """SUBSTITUI `test_happy_path_recorrer_handoff_recurso`, que MORRE: a operadora nao recorre da
    propria glosa (a aresta CONTAS->RECURSO foi deletada por ADR-0040).

    O analista completa `GLOSAR` com os campos obrigatorios => `registrar_glosa` (guard satisfeito,
    `glosa_id` cunhado) -> `emitir_demonstrativo` (M6) -> `contas.completed` com
    `desfecho=glosa_aplicada_humano` -> `End_GlosaAplicadaHumano`.
    """
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    ended_pre_ut = await engine.activity_instances_ended(iid)
    assert "ST_AnalyzeReason" in ended_pre_ut, (
        f"ST_AnalyzeReason deve aparecer na historia. ended={ended_pre_ut}"
    )
    # categoria_normalizada is a REAL engine process variable (module docstring PORT NOTE 2:
    # ST_AnalyzeReason's return dict unconditionally overwrites it) — reading it directly from the
    # live engine is strictly stronger than a worker-side echo.
    categoria_var = await engine.get_variable(iid, "categoria_normalizada")
    assert categoria_var == "valor", (
        f"categoria_normalizada (variavel de processo, engine-side) deve ser 'valor'; veio {categoria_var!r}"
    )
    # `ended_pre_ut` is captured AFTER `_drive_to_analista` — ST_PrepareTriageDossier is the UT's
    # ONLY predecessor on Flow_Dossie_UTAnalista, so it must already be in the engine history.
    assert "ST_PrepareTriageDossier" in ended_pre_ut, (
        f"ST_PrepareTriageDossier (Marina) deve aparecer na historia. ended={ended_pre_ut}"
    )

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_contas": "GLOSAR",
            "justificativa_glosa": "Item sintetico glosado por divergencia de valor (teste L0)",
            "codigo_glosa_tiss": "VALOR_ACIMA_TABELA",
            "valor_glosado_brl": _double_var(900.00),
            "analista_id": "analista-sintetico-001",
        },
    )
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_glosa_without_human_task(engine, iid)
    assert _END_GLOSA_APLICADA in ended, f"GLOSAR => End_GlosaAplicadaHumano. ended={ended}"
    assert "ST_RegistrarGlosa" in ended, "o efeito adverso passa pelo worker gated"
    assert "ST_EmitirDemonstrativo" in ended, "M6: a glosa e comunicada ao prestador"
    assert _END_PAGAMENTO_PARCIAL not in ended
    # `Flow_GWDec_Glosar`'s own condition (`${decisao_contas == 'GLOSAR'}`) is the ONLY route to
    # ST_RegistrarGlosa/End_GlosaAplicadaHumano — reaching this end event already proves the
    # decision engine-side (gateway-gated, not worker-echoed). The completed event additionally
    # carries the glosa identity the RECURSO intake needs.
    assert contas_probe.has_event(
        _CONTAS_COMPLETED,
        desfecho="glosa_aplicada_humano",
        codigo_glosa_tiss="VALOR_ACIMA_TABELA",
        analista_id="analista-sintetico-001",
    ), (
        "ST_PublishGlosaAplicada deve emitir completed(desfecho=glosa_aplicada_humano) com "
        "codigo_glosa_tiss/analista_id da decisao humana"
    )


# ===========================================================================
# I-PAGTO-1 — a segregacao de funcoes do lado do destino (M1)
# ===========================================================================


async def test_conta_originada_em_contas_nunca_alcanca_liberacao_automatica_sem_ut(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    pagto_drain: PagtoDrainProbe,
    start_contas: Callable[..., Any],
) -> None:
    """M1 / I-PAGTO-1, IRMAO DE ORIGEM-CONTAS de
    `test_sp_op_recurso_001.py::test_glosa_revertida_nunca_alcanca_liberacao_automatica_sem_ut`.

    A ordem originada na perna AUTOMATICA (`has_glosas=false`, nenhum humano em CONTAS) PARA em
    `UT_AnaliseAdmissibilidade` e NAO alcanca `End_PagamentoLiberadoAutomatico`. Sem a invariante,
    a origem semearia `lastro_confirmado=true` — o proprio contrato de PAGTO convidava a isso,
    definindo o booleano como "conta adjudicada" — e a ordem cairia direto na faixa clerical
    `DENTRO_TETO_L2` (`ST_ReleaseLowValue`, sem User Task nenhuma), que NAO e coberta por
    `ERR_PAYMENT_RELEASE_NOT_HUMAN`. Com ela, `pagto_admissibility` le o lastro ausente =>
    `ANALISE_HUMANA` => a ordem PARA na fila de `coordenacao-financeira`.

    Este e o caso MAIS FORTE dos dois: aqui nao houve humano NENHUM do lado da origem, entao a
    User Task de admissibilidade e o UNICO humano no caminho inteiro do dinheiro.

    O dreno de PAGTO nao e decoracao: sem ele a instancia de destino estaciona na sua primeira
    external task, `BRT_PagtoAdmissibility` nunca e avaliada, e as assercoes negativas abaixo
    valeriam por construcao — valeriam tambem com `lastro_confirmado=true` semeado. O controle
    negativo que prova que elas DISCRIMINAM ja existe no irmao de RECURSO
    (`test_controle_negativo_lastro_confirmado_libera_pela_faixa_clerical`) e nao e duplicado.
    """
    await _deploy_pagto(engine)
    inst = await start_contas(has_glosas=False)
    iid = inst["id"]

    await contas_probe.drain()
    ended = await _await_end(engine, iid)
    assert _END_APROVADA_INTEGRAL in ended
    assert not await engine.list_user_tasks(iid), "nenhum humano do lado da ORIGEM nesta perna"

    # `get_history_variable`, nao `get_variable`: a instancia de CONTAS JA TERMINOU
    # (`End_ContaAprovadaIntegral` acima) e o endpoint de runtime responde
    # `500 ... execution is null` para uma instancia concluida. Antes do reparo de
    # `data_vencimento` esta linha nunca era alcancada porque a instancia travava no handoff e
    # continuava viva — o defeito de harness so aparece quando o processo passa a FUNCIONAR.
    lote = await engine.get_history_variable(iid, "numero_lote_tiss")
    pagto_bk = f"PAGTO-amh-{lote}-PRESTADOR-TESTE-001"
    pagto = await engine.find_active_instances(pagto_bk)
    assert len(pagto) == 1, f"o handoff deve ter criado UMA ordem sob {pagto_bk}; veio {pagto}"
    pagto_iid = pagto[0]["id"]

    # AGORA a instancia de destino anda: sem isto tudo abaixo seria vacuo.
    await pagto_drain.drain()

    pagto_ut = await engine.await_user_task(pagto_iid, _UT_PAGTO_ADMISSIBILIDADE)
    assert "coordenacao-financeira" in pagto_ut.candidate_groups, (
        "a ordem tem de parar na fila HUMANA de admissibilidade — a adjudicacao automatica da "
        "conta nao confirma o lastro da ordem que ela produziu (I-PAGTO-1)"
    )

    # `get_variable_or_none`, nao `get_variable`: a AUSENCIA e que E a prova de I-PAGTO-1, e o
    # engine responde `404 ... does not exist` para uma variavel nunca setada — com
    # `get_variable` a resposta que prova o invariante virava `EngineRestError` e o ramo `None`
    # destas duas assercoes era INALCANCAVEL (mesmo defeito de harness que o 2o reparo do PR-3
    # corrigiu em `test_sp_op_recurso_001.py`). 500/503 continuam levantando.
    lastro = await engine.get_variable_or_none(pagto_iid, "lastro_confirmado")
    assert lastro in (False, None), (
        f"`lastro_confirmado` tem de chegar ausente/false a pagto_admissibility; veio {lastro!r}"
    )
    origem = await engine.get_variable(pagto_iid, "lastro_origem")
    assert origem == "contas_adjudicacao_automatica"
    decisor = await engine.get_variable_or_none(pagto_iid, "lastro_decisor_id")
    assert decisor in ("", None), "a perna automatica nao tem decisor humano — e nao inventa um"

    pagto_ended = await engine.activity_instances_ended(pagto_iid)
    assert _BRT_PAGTO_ADMISSIBILIDADE in pagto_ended, (
        f"o gate de admissibilidade TEM de ter sido avaliado; ended={pagto_ended}"
    )
    assert "End_PagamentoLiberadoAutomatico" not in pagto_ended, (
        "uma conta adjudicada automaticamente NUNCA pode ser liberada automaticamente"
    )
    assert "ST_ReleaseLowValue" not in pagto_ended
    assert "ST_ReleaseHighValue" not in pagto_ended
    assert "BRT_AlcadaRouting" not in pagto_ended, (
        "sem lastro confirmado a ordem nem chega a escada de alcada"
    )


async def test_m4_categoria_desconhecida_para_em_admissibilidade_humana(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    pagto_drain: PagtoDrainProbe,
    start_contas: Callable[..., Any],
) -> None:
    """OQ-10 / M-4: a DIVULGACAO OBRIGATORIA de ADR-0040, tornada um fato verificavel.

    O achado M-4 NAO foi fechado por este redesenho — a row permissiva da `glosa_triage` continua
    com o conjunto negativo que admite `categoria_normalizada="desconhecida"`, e fecha-lo e ato do
    dono da tabela. O que MUDOU e a CONSEQUENCIA, e ela tem de ser dita nos dois sentidos:

      ANTES: o input defeituoso terminava num terminal sem efeito e sem revisor.
      DEPOIS: ele emite um demonstrativo ao prestador E cria uma ordem em SP-OP-PAGTO-001 —
              que PARA na fila humana de `UT_AnaliseAdmissibilidade`.

    Ou seja o raio vai de zero-efeito-e-invisivel para ordem-visivel-e-humano-gated: MAIS
    exposicao de artefato, MAIS visibilidade humana, NENHUM dinheiro automatico. O que NAO melhora
    e o lado da operadora — a conta e adjudicada "pagar integralmente" sem que um analista de
    contas a veja. Esse residuo e OQ-10, e este teste o deixa medido em vez de argumentado.
    """
    await _deploy_pagto(engine)
    inst = await start_contas(
        categoria_normalizada="desconhecida",
        item_conforme_tabela=True,
        divergencia_valor=False,
        documentacao_anexa=True,
        has_glosas=True,
    )
    iid = inst["id"]

    await contas_probe.drain()
    ended = await _await_end(engine, iid)

    # O RESIDUO, medido: nenhum analista de contas viu esta conta.
    assert _END_APROVADA_INTEGRAL in ended, (
        f"M-4: a categoria fora do dominio declarado ainda e adjudicada como PAGAR. ended={ended}"
    )
    assert not await engine.list_user_tasks(iid), (
        "M-4 (OQ-10): o residuo E este — nenhuma User Task de contas foi criada"
    )

    # O QUE MELHOROU: existe artefato, e ele para num humano.
    assert "ST_EmitirDemonstrativoIntegral" in ended, "o prestador passa a ser comunicado"
    # `get_history_variable` pelo mesmo motivo do teste anterior: a instancia de CONTAS ja
    # terminou em `End_ContaAprovadaIntegral` e o endpoint de runtime devolve 500.
    lote = await engine.get_history_variable(iid, "numero_lote_tiss")
    pagto = await engine.find_active_instances(f"PAGTO-amh-{lote}-PRESTADOR-TESTE-001")
    assert len(pagto) == 1
    await pagto_drain.drain()
    pagto_ut = await engine.await_user_task(pagto[0]["id"], _UT_PAGTO_ADMISSIBILIDADE)
    assert "coordenacao-financeira" in pagto_ut.candidate_groups
    pagto_ended = await engine.activity_instances_ended(pagto[0]["id"])
    assert "End_PagamentoLiberadoAutomatico" not in pagto_ended, (
        "o input defeituoso de M-4 produz uma ordem PENDENTE de humano, nunca uma liberacao"
    )


async def test_happy_path_encaminhar_fraude_handoff_fraude(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """T4 Phase-3 leg: analista humano decide ENCAMINHAR_FRAUDE => start_fraude (handoff neutro);
    End_EncaminhadaFraude.

    Prova end-to-end (engine cibseven real, lean stack):
      - o branch humano decisao_contas==ENCAMINHAR_FRAUDE (GW_DecisaoContas -> ST_StartFraude ->
        ST_PublishEncaminhadaFraude) alcanca End_EncaminhadaFraude;
      - ST_PublishEncaminhadaFraude emite completed(desfecho=encaminhada_fraude, prestador_id=...)
        (o event_payload_vars enriquecido que ARMA a regra CONTAS→FRAUDE do notification_bridge);
      - o worker in-flow start_fraude iniciou SP-OP-FRAUDE-001 sob a business key CONVERGENTE
        FRAUDE-amh-PRESTADOR-TESTE-001 (== a que a regra do bridge deriva) — prova de convergencia;
      - IDEMPOTENCIA: uma segunda instancia CONTAS que encaminha o MESMO prestador converge na
        MESMA instancia FRAUDE-001 ativa (already_existed) — ZERO double-start (requisito L0).
    """
    # start_fraude runs the fenced start_process_idempotent chokepoint against SP-OP-FRAUDE-001,
    # so its BPMN + DMNs MUST be deployed for the handoff to reach End_EncaminhadaFraude (mirrors
    # the RECORRER->RECURSO downstream deploy above).
    await engine.deploy(
        _REPO / "spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn",
        _REPO / "spec/processes/dmn/fraude_indicadores.dmn",
        _REPO / "spec/processes/dmn/fraude_routing.dmn",
        _REPO / "spec/processes/dmn/fraude_sla.dmn",
        name="SP-OP-FRAUDE-001-qa-contas-handoff",
    )
    # Unique prestador per run: the cibseven container persists instances across the whole test
    # session, so a fixed business key would collide with a prior run's still-active FRAUDE-001.
    prestador = f"PRESTADOR-FRAUDE-{uuid.uuid4().hex[:8]}"
    expected_fraude_bk = f"FRAUDE-amh-{prestador}"

    # No FRAUDE-001 instance for this prestador yet.
    assert await engine.find_active_instances(expected_fraude_bk) == []

    inst = await start_contas(categoria_normalizada="tecnica", has_glosas=True, prestador_id=prestador)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    # HUMAN decision — never auto-flagged (L0 hard): the analyst sets ENCAMINHAR_FRAUDE.
    await engine.complete_task_as_human(ut.id, {"decisao_contas": "ENCAMINHAR_FRAUDE"})
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENCAMINHADA_FRAUDE in ended, f"ENCAMINHAR_FRAUDE => End_EncaminhadaFraude. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)  # never an adverse terminal
    assert "ST_StartFraude" in ended, "start_fraude deve ter executado nesta instancia"
    # ST_PublishEncaminhadaFraude (the only task downstream of ST_StartFraude) emits the enriched
    # completed event that ARMS the bridge's CONTAS→FRAUDE rule.
    assert contas_probe.has_event(_CONTAS_COMPLETED, desfecho="encaminhada_fraude", prestador_id=prestador), (
        "ST_PublishEncaminhadaFraude deve emitir completed(desfecho=encaminhada_fraude, prestador_id=...)"
    )

    # CONVERGENCE: start_fraude started SP-OP-FRAUDE-001 under the SAME business key the bridge
    # rule derives (FRAUDE-{tenant}-{prestador_id}).
    fraude_instances = await engine.find_active_instances(expected_fraude_bk)
    assert len(fraude_instances) == 1, (
        f"start_fraude deve ter iniciado exatamente 1 SP-OP-FRAUDE-001 sob {expected_fraude_bk}; "
        f"veio {fraude_instances}"
    )
    assert fraude_instances[0]["definitionId"].startswith("SP-OP-FRAUDE-001")

    # IDEMPOTENCY / no double-start: a SECOND CONTAS instance referring the SAME prestador to fraud
    # converges on the SAME active FRAUDE-001 instance (business-key idempotent chokepoint).
    inst2 = await start_contas(categoria_normalizada="tecnica", has_glosas=True, prestador_id=prestador)
    iid2 = inst2["id"]
    ut2 = await _drive_to_analista(engine, contas_probe, iid2)
    await engine.complete_task_as_human(ut2.id, {"decisao_contas": "ENCAMINHAR_FRAUDE"})
    await contas_probe.drain()
    ended2 = await _await_end(engine, iid2)
    assert _END_ENCAMINHADA_FRAUDE in ended2
    still_one = await engine.find_active_instances(expected_fraude_bk)
    assert len(still_one) == 1 and still_one[0]["id"] == fraude_instances[0]["id"], (
        "re-encaminhamento do MESMO prestador NAO pode criar uma segunda instancia FRAUDE-001 "
        f"(convergencia idempotente): antes={fraude_instances}, depois={still_one}"
    )


async def test_happy_path_pagar_parcial_pelo_analista(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """SUBSTITUI `test_happy_path_aceitar_glosa_pelo_analista`, que MORRE: aceitar uma glosa e o
    ato de quem a SOFRE.

    `PAGAR_PARCIAL` e o SEGUNDO terminal adverso (uma reducao): `registrar_glosa` ->
    `emitir_demonstrativo` (misto) -> `handoff_pagamento` da parcela LIBERADA ->
    `End_PagamentoParcialHumano`. O valor encaminhado e `valor_liberado_brl`, nunca o apresentado
    (que ainda inclui a parte glosada) — `fonte_valor=liberado`, declarado pelo elemento chamador.

    PORT FIX (T3.1 phase-2, test-harness-only, PORT NOTE 1 class): os campos monetarios usam
    `_double_var()`, nao literais — um literal cai no catch-all `String` de
    `EngineRest._to_camunda_vars` e o guard monetario do worker teria de lidar com uma `str`
    (`_parse_valor_monetario` lida, mas o teste ficaria testando a coercao, nao o caminho).
    """
    await _deploy_pagto(engine)
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_contas": "PAGAR_PARCIAL",
            "justificativa_glosa": "Duas linhas fora da tabela contratada (teste L0)",
            "codigo_glosa_tiss": "VALOR_ACIMA_TABELA",
            "valor_glosado_brl": _double_var(900.00),
            "valor_liberado_brl": _double_var(900.00),
            "analista_id": "analista-sintetico-001",
        },
    )
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_glosa_without_human_task(engine, iid)
    assert _END_PAGAMENTO_PARCIAL in ended, f"PAGAR_PARCIAL => End_PagamentoParcialHumano. ended={ended}"
    assert "ST_RegistrarGlosaParcial" in ended
    assert "ST_EmitirDemonstrativoParcial" in ended, "M6: o demonstrativo misto e emitido"
    assert "ST_HandoffPagamentoParcial" in ended, "a parcela liberada vira ordem de pagamento"
    assert _END_GLOSA_APLICADA not in ended
    assert contas_probe.has_event(
        _CONTAS_COMPLETED,
        desfecho="pagamento_parcial_humano",
        codigo_glosa_tiss="VALOR_ACIMA_TABELA",
        analista_id="analista-sintetico-001",
    )


async def test_happy_path_pagar_pelo_analista(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """A decisao humana FAVORAVEL: `PAGAR` -> demonstrativo -> handoff (`fonte_valor=liberado`)
    -> `End_ContaAprovadaHumano`. Nao passa pelo worker adverso em momento algum."""
    await _deploy_pagto(engine)
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_contas": "PAGAR",
            "valor_liberado_brl": _double_var(1800.00),
            "analista_id": "analista-sintetico-001",
        },
    )
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_APROVADA_HUMANO in ended, f"PAGAR => End_ContaAprovadaHumano. ended={ended}"
    assert "ST_RegistrarGlosa" not in ended, "uma decisao favoravel nunca toca o worker adverso"
    assert not (ended & _ENDS_ADVERSOS)
    assert contas_probe.has_event(_CONTAS_COMPLETED, desfecho="pagamento_aprovado_humano")


async def test_happy_path_devolver_conta(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """SUBSTITUI `test_happy_path_reenviar`, que MORRE: reapresentar a conta e ato do prestador.

    `DEVOLVER` => `devolver_conta` (sem efeito adverso L0) -> `ST_ComunicarDevolucao`
    (`tipo_comunicacao=devolucao_para_correcao`, M6) -> `End_ContaDevolvidaPrestador`. Sem a
    comunicacao o prestador nunca saberia que a conta voltou, e `msg.contas.linhas_atualizadas`
    ficaria sem gatilho.
    """
    inst = await start_contas(
        categoria_normalizada="administrativa", documentacao_anexa=False, has_glosas=True
    )
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_contas": "DEVOLVER",
            "justificativa_devolucao": "Anexos das linhas 3 e 4 ausentes (sintetico)",
            "analista_id": "analista-sintetico-001",
        },
    )
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_CONTA_DEVOLVIDA in ended, f"DEVOLVER => End_ContaDevolvidaPrestador. ended={ended}"
    assert "ST_ComunicarDevolucao" in ended, "M6: a devolucao TEM de ser comunicada ao prestador"
    assert not (ended & _ENDS_ADVERSOS)
    assert contas_probe.has_event(
        _CONTAS_COMPLETED, desfecho="conta_devolvida_humano", prestador_id="PRESTADOR-TESTE-001"
    ), "ST_PublishContaDevolvida deve emitir completed(desfecho=conta_devolvida_humano, prestador_id=...)"


async def test_decisao_invalida_termina_em_erro_sem_efeito(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """O default de `GW_DecisaoContas` deixou de ser uma ACAO (ADR-0040).

    Uma decisao ausente ou fora do dominio cai em `End_ErrContasDecisaoInvalida` e NENHUM efeito e
    materializado — nem glosa, nem comunicacao, nem ordem de pagamento. Antes, uma variavel
    ausente PRODUZIA um ato.
    """
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_contas": "VALOR_INEXISTENTE"})
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ERR_DECISAO in ended, f"decisao invalida => End_ErrContasDecisaoInvalida. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    for efeito in (
        "ST_RegistrarGlosa",
        "ST_RegistrarGlosaParcial",
        "ST_EmitirDemonstrativo",
        "ST_EmitirDemonstrativoAprovado",
        "ST_DevolverConta",
        "ST_HandoffPagamentoHumano",
    ):
        assert efeito not in ended, f"{efeito} nao pode executar sob decisao invalida"


# ===========================================================================
# Aceite exige campos / worker guard
# ===========================================================================


async def test_glosar_exige_campos(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """RENOMEADO de `test_aceitar_glosa_exige_campos`. `GLOSAR` sem
    justificativa/codigo/valor/analista_id => o worker guard recusa.

    O engine despacha `registrar_glosa`; o worker lanca `ContasGlosaNotHumanError`. FINDING 3
    (module docstring): isso vira um INCIDENTE aberto no engine (nao um boundary-catch limpo —
    `Error_ContasGlosaNotHuman` e declarado no BPMN mas SEM boundaryEvent correspondente, e mesmo
    se houvesse, `PermissionError` nunca dispara o caminho `bpmnError` — ADR-0030 §5). As
    assercoes abaixo NAO dependem de qual mecanismo produz o bloqueio — apenas que o terminal
    adverso nao e atingido e que nada foi registrado, ambos verdadeiros sob incidente OU
    boundary-catch.
    """
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_contas": "GLOSAR"})
    await contas_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_GLOSA_APLICADA not in ended, (
        "GLOSAR sem campos obrigatorios NAO pode atingir End_GlosaAplicadaHumano (guard do worker)"
    )
    incidents = await engine.incidents(iid)
    guard_incidents = [i for i in incidents if i.get("activityId") == "ST_RegistrarGlosa"]
    assert guard_incidents, (
        "registrar_glosa sem campos obrigatorios deve abrir um INCIDENTE real em "
        f"ST_RegistrarGlosa (FINDING 3 — sem boundary catch para PermissionError). "
        f"incidentes encontrados: {incidents}"
    )
    assert any("ERR_CONTAS_GLOSA_NOT_HUMAN" in (i.get("incidentMessage") or "") for i in guard_incidents), (
        f"incidente deve carregar a mensagem do guard ERR_CONTAS_GLOSA_NOT_HUMAN: {guard_incidents}"
    )
    await _assert_no_glosa_without_human_task(engine, iid)


async def test_pagar_parcial_exige_valor_liberado(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """`PAGAR_PARCIAL` com todos os campos de glosa mas SEM `valor_liberado_brl` => guard recusa.

    Uma reducao declara as DUAS metades. Sem o valor liberado o demonstrativo misto nao fecha
    `apresentado = liberado + glosa` e o handoff nao tem valor a encaminhar.
    """
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_contas": "PAGAR_PARCIAL",
            "justificativa_glosa": "Duas linhas fora da tabela (sintetico)",
            "codigo_glosa_tiss": "VALOR_ACIMA_TABELA",
            "valor_glosado_brl": _double_var(900.00),
            "analista_id": "analista-sintetico-001",
        },
    )
    await contas_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_PAGAMENTO_PARCIAL not in ended
    incidents = await engine.incidents(iid)
    guard_incidents = [i for i in incidents if i.get("activityId") == "ST_RegistrarGlosaParcial"]
    assert guard_incidents, f"incidentes encontrados: {incidents}"
    assert any("valor_liberado_brl" in (i.get("incidentMessage") or "") for i in guard_incidents), (
        f"o incidente deve nomear o campo faltante: {guard_incidents}"
    )
    await _assert_no_glosa_without_human_task(engine, iid)


async def test_worker_registrar_glosa_recusa_sem_humano() -> None:
    """Invocacao direta de `registrar_glosa_entry` sem decisao humana => `ContasGlosaNotHumanError`.

    RENOMEADO do teste de guard anterior. O guard mudou de OBJETO,
    nao de mecanismo: `PermissionError` subclass, nao `WorkerBpmnError` — caminho de incidente
    auditado (ADR-0030 §5). Os quatro casos abaixo sao os quatro modos de recusa.
    """
    kafka = FakeKafkaPublisher()

    # (a) sem decisao nenhuma -> recusa
    with pytest.raises(ContasGlosaNotHumanError) as exc_a:
        registrar_glosa_entry({}, kafka=kafka)
    assert "decisao_contas" in str(exc_a.value)

    # (b) decisao FAVORAVEL (PAGAR) -> recusa: uma decisao favoravel nunca toca o worker adverso
    with pytest.raises(ContasGlosaNotHumanError) as exc_b:
        registrar_glosa_entry({"decisao_contas": "PAGAR"}, kafka=kafka)
    assert "decisao_contas" in str(exc_b.value)

    # (c) GLOSAR mas faltando analista_id -> recusa
    with pytest.raises(ContasGlosaNotHumanError) as exc_c:
        registrar_glosa_entry(
            {
                "decisao_contas": "GLOSAR",
                "justificativa_glosa": "Divergencia de valor (sintetico)",
                "codigo_glosa_tiss": "VALOR_ACIMA_TABELA",
                "valor_glosado_brl": 900.00,
            },
            kafka=kafka,
        )
    assert "analista_id" in str(exc_c.value)

    # (d) PAGAR_PARCIAL sem valor_liberado_brl -> recusa
    with pytest.raises(ContasGlosaNotHumanError) as exc_d:
        registrar_glosa_entry(
            {
                "decisao_contas": "PAGAR_PARCIAL",
                "justificativa_glosa": "Duas linhas fora da tabela (sintetico)",
                "codigo_glosa_tiss": "VALOR_ACIMA_TABELA",
                "valor_glosado_brl": 900.00,
                "analista_id": "analista-sintetico-001",
            },
            kafka=kafka,
        )
    assert "valor_liberado_brl" in str(exc_d.value)

    # (e) decisao humana completa -> registra, com glosa_id DETERMINISTICO
    result = registrar_glosa_entry(
        {
            "decisao_contas": "GLOSAR",
            "justificativa_glosa": "Divergencia de valor (sintetico)",
            "codigo_glosa_tiss": "VALOR_ACIMA_TABELA",
            "valor_glosado_brl": 900.00,
            "analista_id": "analista-sintetico-001",
            "tenant_id": "amh",
            "numero_lote_tiss": "LOTE-TESTE-0001",
        },
        kafka=kafka,
    )
    assert result["registered"] is True
    assert result["glosa_id"].startswith("GLOSA-analista-sintetico-001-")


# ===========================================================================
# Reavaliacao por correcao de linhas (mensagem)
# ===========================================================================


async def test_linhas_atualizadas_reavalia(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """Instancia aguardando na UT (docs ausentes) => msg.contas.linhas_atualizadas reavalia triagem."""
    inst = await start_contas(
        categoria_normalizada="documental",
        item_conforme_tabela=False,
        documentacao_anexa=False,
        has_glosas=True,
    )
    iid = inst["id"]

    await _drive_to_analista(engine, contas_probe, iid)

    business_key = inst["businessKey"]
    correlate_payload = {
        "messageName": "msg.contas.linhas_atualizadas",
        "businessKey": business_key,
        "processVariables": {
            "documentacao_anexa": {"value": True, "type": "Boolean"},
            "item_conforme_tabela": {"value": True, "type": "Boolean"},
        },
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.contas.linhas_atualizadas falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    await contas_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Reavaliacao por mensagem nunca glosa sozinha (L0)"
    await _assert_no_glosa_without_human_task(engine, iid)
    assert await engine.instance_is_active(iid) or _END_APROVADA_INTEGRAL in ended, (
        "Apos a mensagem, a instancia deve reavaliar a triagem (ativa) ou chegar a fim neutro"
    )


# ===========================================================================
# Timers de SLA
# ===========================================================================


async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """Timer BT_AlertaSlaContas (nao-interruptivo): notify_sla_risk recebe task; UT segue aberta.

    ADAPTED (t3.1-event-gap-a-contas, test-only, see `_WORKER_KAFKA_GAP_REASON` UPDATE): the
    `notifications_of_type("contas.notify_sla_risk")` check was dead (FINDING 1). Unlike the
    other adaptations in this file, this one has NO has_event equivalent at all — bpmn:216-223
    confirms `ST_NotificarRiscoSla` routes DIRECTLY to `End_RiscoSlaNotificado` (a plain end
    event), with no `ST_Publish*` task anywhere on this non-interruptive alert branch. Re-expressed
    against engine activity-history instead (the same technique the design doc itself proposes for
    inadimplencia's structurally-identical `notify_sla_risk` alert case).
    """
    inst = await start_contas(categoria_normalizada="tecnica", has_glosas=True)
    iid = inst["id"]

    await _drive_to_analista(engine, contas_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSlaContas")
    await engine.execute_job(job.id)
    await contas_probe.drain()

    ended_after_alerta = await engine.activity_instances_ended(iid)
    assert "ST_NotificarRiscoSla" in ended_after_alerta, (
        "Worker notify_sla_risk (ST_NotificarRiscoSla) deve aparecer na historia apos o alerta de SLA"
    )

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """Timer BT_SlaAnaliseContas (interruptivo): UT_AnalistaContas cancelada;
    UT_CoordenacaoContasAssume criada."""
    inst = await start_contas(categoria_normalizada="tecnica", has_glosas=True)
    iid = inst["id"]

    await _drive_to_analista(engine, contas_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaAnaliseContas")
    await engine.execute_job(job.id)
    await contas_probe.drain()

    assert contas_probe.has_event(_CONTAS_SLA_BREACHED), "contas.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-contas" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA not in open_keys, "UT_AnalistaContas deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro de SLA nunca glosa sozinho (L0)"
    await _assert_no_glosa_without_human_task(engine, iid)


async def test_coordenacao_assume_e_glosa(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """RENOMEADO de `test_coordenacao_assume_e_aceita`. SLA estourado; a coordenacao assume e
    decide `GLOSAR` => `End_GlosaAplicadaHumano`, com User Task humana na historia.

    E o SEGUNDO canal humano do MESMO guard: `registrar_glosa` le a decisao, nao o elemento que a
    produziu. Ainda assim NAO ha desfecho automatico por timeout — o timeout troca de humano, nunca
    dispensa um.

    PORT FIX (T3.1 phase-2, test-harness-only, PORT NOTE 1 class): `valor_glosado_brl` MUST go
    through `_double_var()`, not a bare Python literal. `EngineRest._to_camunda_vars` (this
    package's OWN test-only helper, not src/) has branches for dict-passthrough/bool/int only; a
    bare float falls into its catch-all `else` and is sent as a Camunda **String**. O worker lida
    com isso hoje (`_parse_valor_monetario` aceita a string), mas ai o teste estaria exercitando a
    coercao em vez do caminho — `_double_var()` mantem o tipo real no wire.
    """
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    await _drive_to_analista(engine, contas_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaAnaliseContas")
    await engine.execute_job(job.id)
    await contas_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_contas": "GLOSAR",
            "justificativa_glosa": "Prazo esgotado — coordenacao glosa (sintetico)",
            "codigo_glosa_tiss": "VALOR_ACIMA_TABELA",
            "valor_glosado_brl": _double_var(900.00),
            "analista_id": "coordenacao-sintetica-001",
        },
    )
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_GLOSA_APLICADA in ended
    await _assert_no_glosa_without_human_task(engine, iid)
    assert contas_probe.has_event(_CONTAS_COMPLETED, desfecho="glosa_aplicada_humano")


async def test_dmn_contas_sla_internacao(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """tipo_lote=internacao => DMN contas_sla resolve os deadlines absolutos; timers existem."""
    inst = await start_contas(tipo_lote="internacao", categoria_normalizada="tecnica", has_glosas=True)
    iid = inst["id"]

    await _drive_to_analista(engine, contas_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSlaContas")
    assert job.activity_id == "BT_AlertaSlaContas"
    job_sla = await engine.await_timer_job(iid, "BT_SlaAnaliseContas")
    assert job_sla.activity_id == "BT_SlaAnaliseContas"


async def test_sla_ancora_em_data_recebimento_lote_nao_em_attach_da_ut(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """GAP-CONTAS-4: os boundary timers de SLA ancoram em `data_recebimento_lote`, NAO no attach.

    Tecnica (output-vars + execute_job, sem sleep — precedente GAP-NIP-1/#111): `data_recebimento_
    lote` e seedada com um valor no FUTURO distante. Consultamos o `dueDate` real do job (SEM
    dispara-lo) e comparamos com a ancora correta vs a legada (attach da UT).

    ADAPTED (t3.1-event-gap-a-contas, test-only, see `_WORKER_KAFKA_GAP_REASON` UPDATE): the
    `notifications_of_type("contas.notify_sla_risk")` check after the non-interruptive alert was
    dead (FINDING 1) and has no has_event equivalent (bpmn:216-223: ST_NotificarRiscoSla routes
    directly to End_RiscoSlaNotificado, no ST_Publish* task on that branch) — re-expressed against
    engine activity-history, same technique as `test_timer_alerta_sla_nao_interruptivo` above.
    """
    anchor_iso = _data_recebimento_futura(days=30)
    anchor_midnight = datetime.combine(datetime.fromisoformat(anchor_iso).date(), datetime.min.time())

    inst = await start_contas(
        categoria_normalizada="tecnica", has_glosas=True, data_recebimento_lote=anchor_iso
    )
    iid = inst["id"]

    await _drive_to_analista(engine, contas_probe, iid)

    job_alerta = await engine.await_timer_job(iid, "BT_AlertaSlaContas")
    job_sla = await engine.await_timer_job(iid, "BT_SlaAnaliseContas")
    assert job_alerta.due_date, "dueDate do job de alerta deve estar presente (timeDate absoluto)"
    assert job_sla.due_date, "dueDate do job de SLA deve estar presente (timeDate absoluto)"

    due_alerta = datetime.fromisoformat(job_alerta.due_date).replace(tzinfo=None)
    due_sla = datetime.fromisoformat(job_sla.due_date).replace(tzinfo=None)

    # tipo_lote=sadt => contas_sla r_padrao: sla_analise=P30D, sla_alerta=P20D.
    expected_sla_anchor_based = anchor_midnight + timedelta(days=30)
    expected_alerta_anchor_based = anchor_midnight + timedelta(days=20)
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    attach_based_sla = now_naive + timedelta(days=30)
    attach_based_alerta = now_naive + timedelta(days=20)

    tolerance = timedelta(hours=6)
    far_enough = timedelta(days=20)

    assert abs(due_sla - expected_sla_anchor_based) < tolerance, (
        f"BT_SlaAnaliseContas.dueDate={due_sla} nao bate com a ancora "
        f"data_recebimento_lote+P30D={expected_sla_anchor_based} (GAP-CONTAS-4)"
    )
    assert abs(due_alerta - expected_alerta_anchor_based) < tolerance, (
        f"BT_AlertaSlaContas.dueDate={due_alerta} nao bate com a ancora "
        f"data_recebimento_lote+P20D={expected_alerta_anchor_based} (GAP-CONTAS-4)"
    )
    assert abs(due_sla - attach_based_sla) > far_enough, (
        "BT_SlaAnaliseContas.dueDate esta perto do attach da UT — regressao GAP-CONTAS-4"
    )
    assert abs(due_alerta - attach_based_alerta) > far_enough, (
        "BT_AlertaSlaContas.dueDate esta perto do attach da UT — regressao GAP-CONTAS-4"
    )

    await engine.execute_job(job_alerta.id)
    await contas_probe.drain()
    ended_after_alerta = await engine.activity_instances_ended(iid)
    assert "ST_NotificarRiscoSla" in ended_after_alerta, (
        "notify_sla_risk (ST_NotificarRiscoSla) deve aparecer na historia apos o alerta (nao-interruptivo)"
    )
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA in open_keys, "Timer nao-interruptivo nao deve cancelar UT_AnalistaContas"

    await engine.execute_job(job_sla.id)
    await contas_probe.drain()
    assert contas_probe.has_event(_CONTAS_SLA_BREACHED), "contas.sla_breached deve publicar"
    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-contas" in ut_coord.candidate_groups
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA not in open_keys, "Timer interruptivo deve cancelar UT_AnalistaContas"
    await _assert_no_glosa_without_human_task(engine, iid)


# ===========================================================================
# DMN/BPMN — asserções ESTÁTICAS: movidas para o gate unitário
# ===========================================================================
# `test_glosa_triage_sem_saida_de_glosa` (era `…_sem_saida_de_aceite`) e
# `test_dmn_typeref_allowlist` NAO precisam de engine e viviam aqui sob o
# `pytestmark = pytest.mark.integration` do modulo — logo, DESELECIONADAS de `make test`. Ficaram
# meses afirmando o dominio ANTIGO da `glosa_triage` sem ninguem ver (VERIFY-PR4-CONTAS MAJOR-2).
# Vivem agora em `tests/unit/spec/test_sp_op_contas_001_artefatos.py`, com as tres provas novas de
# inicializacao das variaveis lidas por `GW_DecisaoContas`. Nada foi afrouxado na mudanca de casa:
# o dominio foi corrigido para o que o desenho de registro prescreve, e a lista de vocabulario
# proibido CRESCEU (agora tambem `GLOSAR`, `RECORRER` e `SEM_GLOSA`).


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


async def test_business_key_uma_instancia_por_lote(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa."""
    lote = "LOTE-TESTE-IDEM-001"
    business_key = f"CONTAS-amh-{lote}"

    first = await start_contas(numero_lote_tiss=lote, categoria_normalizada="tecnica", has_glosas=True)
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1

    active_before = await engine.find_active_instances(business_key)
    assert len(active_before) == 1
    assert active_before[0]["id"] == first["id"]
