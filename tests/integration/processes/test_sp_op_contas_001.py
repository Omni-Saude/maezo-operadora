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

test_nenhum_caminho_automatizado_aceita_glosa:
  Varredura de TODAS as combinacoes de input da DMN glosa_triage. A instancia NUNCA atinge
  End_GlosaAceitaHumano sem que UT_AnalistaContas / UT_CoordenacaoContasAssume tenha sido
  completada por humano com decisao_contas=ACEITAR_GLOSA. Prova via history/activity-instance.

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
  - `test_worker_register_glosa_accept_recusa_sem_humano`: donor's `make_register_glosa_accept_
    handler(kafka) -> Callable[[ExternalTask], ...]` + `WorkerBpmnError` do not exist on v2 main
    (ADR-0026 §2b replaced the class/factory-per-topic shape with dict-boundary entry functions
    for this module). Adapted to call `register_glosa_accept_entry(variables: dict, *,
    kafka=None) -> dict` directly; the guard raises `GlosaAcceptNotHumanError` (a `PermissionError`
    subclass), not a modeled `WorkerBpmnError`. The 4 guard scenarios are preserved verbatim
    (decision missing / wrong decision / missing analista_id / success); the "success" shape
    changed to check the RETURNED dict (v2's entry function does not itself call `kafka.publish`
    — see FINDING 1 below, same systemic gap).

FINDINGS (root-cause, file:line evidence; see PR body / evidence-ledger for full detail):

  1. **Kafka-publish gap (systemic, T3.1 events.publish-fix ledger row).** `contas.py`'s 9
     dict-boundary entry functions (`identify_glosa_entry` contas.py:549, `analyze_reason_entry`
     :557, `calculate_impact_entry`:569, `prepare_triage_dossier_entry`:579,
     `register_glosa_accept_entry`:603, `notify_sla_risk_entry`:619, `start_recurso_entry`:631,
     `reconcile_payment_entry`:641, `publish_entry`:652) EVERY one does `del kafka  # unused` —
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
     (register_glosa_accept/start_recurso/reconcile_payment all feed one), or
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
     guard already comes from the `_END_GLOSA_ACEITA not in ended` / `_assert_no_accept_without_
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
     `GlosaAcceptNotHumanError` (contas.py:34, `PermissionError` subclass) and
     `ContasLoteInvalidoError` (contas.py:50, `ValueError` subclass) are BOTH in `base.py`'s
     `_HARNESS_CLASSIFIED` tuple -> re-raised unchanged by `FunctionWorker.execute` ->
     `harness._handle` (harness.py:948-954) routes `PermissionError`/`ValueError` STRAIGHT to
     `_report_failure(..., retries_override=0)` — an immediate engine incident via
     `handle_failure`, NEVER via `handle_bpmn_error`/the `bpmn_error_allowlist` path (that path
     is reserved for `WorkerBpmnError`, which neither of these classes subclasses). Consequently
     the BPMN's own `Error_GlosaAcceptNotHuman`/`Error_ContasLoteInvalido` declarations
     (`SP-OP-CONTAS-001_...bpmn:16`) have NO matching `boundaryEvent`/`errorEventDefinition`
     ANYWHERE in the process (grep confirms only 3 boundary events total: `BT_AlertaSlaContas`,
     `BT_SlaTriagem`, `BME_LinhasAtualizadas` — a timer/timer/message trio, none an error
     boundary) — even if one existed, it could never fire for these exception types. Guard
     failures therefore surface as an OPEN ENGINE INCIDENT at `ST_RegisterGlosaAccept`, not a
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
    GlosaAcceptNotHumanError,
    register_contas_workers,
    register_glosa_accept_entry,
)
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn"
_DMN_REASON = _REPO / "spec/processes/dmn/glosa_reason_normalization.dmn"
_DMN_CLASS = _REPO / "spec/processes/dmn/glosa_classification.dmn"
_DMN_TRIAGE = _REPO / "spec/processes/dmn/glosa_triage.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/contas_sla.dmn"

# External task topics do contrato SP-OP-CONTAS-001.
_PUBLISH_TOPIC = "operadora.events.publish"
_IDENTIFY_TOPIC = "operadora.contas.identify_glosa"
_ANALYZE_TOPIC = "operadora.contas.analyze_reason"
_IMPACT_TOPIC = "operadora.contas.calculate_impact"
_DOSSIER_TOPIC = "operadora.contas.prepare_triage_dossier"
_NOTIFY_SLA_TOPIC = "operadora.contas.notify_sla_risk"
_REGISTER_ACCEPT_TOPIC = "operadora.contas.register_glosa_accept"
_START_RECURSO_TOPIC = "operadora.contas.start_recurso"
_RECONCILE_TOPIC = "operadora.contas.reconcile_payment"
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
    _REGISTER_ACCEPT_TOPIC,
    _START_RECURSO_TOPIC,
    _RECONCILE_TOPIC,
    _ORPHAN_PUBLISH_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_CONTAS_RECEIVED = "agents.events.contas.received"
_CONTAS_GLOSA_IDENTIFIED = "agents.events.contas.glosa_identified"
_CONTAS_SLA_BREACHED = "agents.events.contas.sla_breached"
_CONTAS_COMPLETED = "agents.events.contas.completed"

_UT_ANALISTA = "UT_AnalistaContas"
_UT_COORDENACAO = "UT_CoordenacaoContasAssume"

_END_SEM_GLOSA = "End_SemGlosa"
_END_ENCAMINHADA_RECURSO = "End_EncaminhadaRecurso"
_END_GLOSA_ACEITA = "End_GlosaAceitaHumano"
_END_REENVIADA = "End_Reenviada"

_UT_HUMANAS_ACEITE = frozenset({_UT_ANALISTA, _UT_COORDENACAO})

# FINDING 1 (module docstring): contas.py's 9 dict-boundary entry functions never call
# kafka.publish (each does `del kafka  # unused`) — the generic operadora.events.publish path
# (events.py, T3.1 R2 fix, registered below via register_events_workers) is UNAFFECTED and
# asserted green throughout. Only tests whose blocking assertion is a `notifications_of_type
# ("contas.<fn>")` call are marked with this reason.
_WORKER_KAFKA_GAP_REASON = (
    "v2 systemic drift (T3.1 finding 1, same class as escalation/auth/cancel's Kafka-gap "
    "residuals — ledger row 'T3.1 (events.publish fix)'): contas.py's dict-boundary entry "
    "functions (identify_glosa_entry/analyze_reason_entry/calculate_impact_entry/"
    "prepare_triage_dossier_entry/register_glosa_accept_entry/notify_sla_risk_entry/"
    "start_recurso_entry/reconcile_payment_entry) all accept a `kafka` kwarg but `del kafka` it "
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
    "(register_glosa_accept/start_recurso/reconcile_payment), `engine.get_variable(...)`/"
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
    "glosa_id/codigo_glosa_aceito+analista_id/prestador_id), ST_NotificarRiscoSla completed in "
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


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 4 DMN de contas/glosa da arvore no engine real."""
    return await engine.deploy(
        _BPMN, _DMN_REASON, _DMN_CLASS, _DMN_TRIAGE, _DMN_SLA, name="SP-OP-CONTAS-001-qa"
    )


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
    # EB-4: start_recurso now runs the fenced `start_process_idempotent` chokepoint against
    # SP-OP-RECURSO-001, so it REQUIRES the engine seam (`engine=`) + a loop-agnostic durable audit
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
        return await engine.start_by_key("SP-OP-CONTAS-001", business_key, variables)

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


async def _assert_no_accept_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0: prova que End_GlosaAceitaHumano nao existe sem UT humana."""
    ended = await engine.activity_instances_ended(iid)
    if _END_GLOSA_ACEITA in ended:
        human_tasks_in_history = ended & _UT_HUMANAS_ACEITE
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA: End_GlosaAceitaHumano atingido para instancia {iid} "
            f"SEM nenhuma User Task humana no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_ACEITE}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de aceite de glosa — violacao do L0 hard (ADR-0005)."
        )


async def _drive_to_analista(engine: EngineRest, probe: ContasEngineProbe, iid: str) -> Any:
    """Drena ate UT_AnalistaContas surgir (dossie preparado pelo worker real)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_ANALISTA)


# ===========================================================================
# INVARIANTE L0 — DoD deliverable (varredura de inputs da DMN glosa_triage)
# ===========================================================================


async def test_nenhum_caminho_automatizado_aceita_glosa(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """INVARIANTE L0: NENHUM caminho automatizado aceita glosa substantiva.

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
        assert _END_GLOSA_ACEITA not in ended, (
            f"L0 VIOLADO: categoria={categoria} conforme={conforme} divergencia={divergencia} "
            f"docs={docs} atingiu End_GlosaAceitaHumano automaticamente. ended={ended}"
        )
        await _assert_no_accept_without_human_task(engine, iid)
        checked += 1

    assert checked == 48, f"Esperava 48 combinacoes varridas; varri {checked}"


async def test_inelegibilidade_roteia_para_humano_nao_aceita(
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
    assert _END_GLOSA_ACEITA not in ended, "Inelegibilidade nao deve produzir aceite automatico (L0)"
    await _assert_no_accept_without_human_task(engine, iid)


async def test_glosa_tecnica_roteia_para_humano_nao_aceita(
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
    assert _END_GLOSA_ACEITA not in ended, "Glosa tecnica nao deve produzir aceite automatico (L0)"
    await _assert_no_accept_without_human_task(engine, iid)


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
    assert _END_GLOSA_ACEITA not in ended
    await _assert_no_accept_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


async def test_happy_path_sem_glosa(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """has_glosas=false => fluxo chega a End_SemGlosa; contas.completed desfecho=sem_glosa.

    Via events.publish generico (FIXED, T3.1 R2) — nao depende de contas.py's proprios workers.
    """
    inst = await start_contas(has_glosas=False)
    iid = inst["id"]

    await contas_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_SEM_GLOSA in ended, f"Deve atingir End_SemGlosa. ended={ended}"
    assert not await engine.list_user_tasks(iid), "Sem glosa nao cria User Tasks"
    assert contas_probe.has_event(_CONTAS_RECEIVED)
    assert contas_probe.has_event(_CONTAS_COMPLETED, desfecho="sem_glosa")


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
    assert _END_SEM_GLOSA not in ended, "Sem detalhe de linha NUNCA pode auto-clear (fail-closed)"
    assert _END_GLOSA_ACEITA not in ended
    await _assert_no_accept_without_human_task(engine, iid)


async def test_happy_path_recorrer_handoff_recurso(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """Analista completa RECORRER => start_recurso (handoff); End_EncaminhadaRecurso.

    ADAPTED (t3.1-event-gap-a-contas, test-only, see `_WORKER_KAFKA_GAP_REASON` UPDATE): the 3
    `notifications_of_type(...)` checks below were dead (FINDING 1). `ST_AnalyzeReason` and
    `ST_PrepareTriageDossier` have NO downstream `ST_Publish*` task in the BPMN (they feed
    `BRT_Classification`/`UT_AnalistaContas` directly) — no `has_event(...)` equivalent exists for
    either; re-expressed via `engine.get_variable` (real process variable) and
    `activity_instances_ended` (real engine history) respectively. `ST_StartRecurso` IS
    immediately followed by `ST_PublishEncaminhadaRecurso` (bpmn:272-283), whose
    `event_payload_vars` (bpmn:278) carry `glosa_id` — folded into the existing `has_event` call.
    """
    # EB-4: start_recurso runs the fenced start_process_idempotent chokepoint against
    # SP-OP-RECURSO-001, so its BPMN + DMNs MUST be deployed for the handoff to reach
    # End_EncaminhadaRecurso (mirrors the inadimplencia->CANCEL handoff test's own downstream deploy;
    # do NOT rely on cross-test engine leakage).
    await engine.deploy(
        _REPO / "spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn",
        _REPO / "spec/processes/dmn/recurso_admissibility.dmn",
        _REPO / "spec/processes/dmn/recurso_eligibility.dmn",
        _REPO / "spec/processes/dmn/recurso_sla.dmn",
        name="SP-OP-RECURSO-001-qa-contas-handoff",
    )
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
    # live engine is strictly stronger than the dead notifications_of_type echo it replaces.
    categoria_var = await engine.get_variable(iid, "categoria_normalizada")
    assert categoria_var == "valor", (
        f"categoria_normalizada (variavel de processo, engine-side) deve ser 'valor'; veio {categoria_var!r}"
    )

    # `ended_pre_ut` is captured AFTER `_drive_to_analista` (i.e. after UT_AnalistaContas already
    # exists) — ST_PrepareTriageDossier is its ONLY predecessor on Flow_Dossie_UTAnalista, so it
    # must already be in the engine's activity history if the UT was reached at all.
    assert "ST_PrepareTriageDossier" in ended_pre_ut, (
        f"ST_PrepareTriageDossier (Marina) deve aparecer na historia. ended={ended_pre_ut}"
    )

    await engine.complete_task_as_human(ut.id, {"decisao_contas": "RECORRER", "glosa_id": "GLOSA-TESTE-001"})
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENCAMINHADA_RECURSO in ended, f"RECORRER => End_EncaminhadaRecurso. ended={ended}"
    assert _END_GLOSA_ACEITA not in ended
    # ST_PublishEncaminhadaRecurso (bpmn:272-283) is the ONLY task downstream of ST_StartRecurso on
    # Flow_Recurso_Pub; its event_payload_vars (bpmn:278) carry glosa_id — asserting it here proves
    # start_recurso ran AND correlates the handoff (replaces the dead `notifications_of_type
    # ("contas.start_recurso")` check that used to sit here).
    assert contas_probe.has_event(
        _CONTAS_COMPLETED, desfecho="encaminhada_recurso", glosa_id="GLOSA-TESTE-001"
    ), "ST_PublishEncaminhadaRecurso deve emitir completed(desfecho=encaminhada_recurso, glosa_id=...)"


async def test_happy_path_aceitar_glosa_pelo_analista(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """Analista completa ACEITAR_GLOSA com campos obrigatorios => End_GlosaAceitaHumano.

    Este e o UNICO caminho ao terminal adverso.

    PORT FIX (T3.1 phase-2, test-harness-only bug — src/** untouched, PORT NOTE 1 class):
    `valor_glosa_aceito_brl` uses `_double_var()`, not a bare string literal — a bare string (or
    a bare float) falls into `EngineRest._to_camunda_vars`'s catch-all `else` branch and is sent
    as a Camunda String, and `contas.py::register_glosa_accept`'s `<= 0` guard then raises
    `TypeError` on the str before this test's own blocking assertion (the Kafka-gap
    `_WORKER_KAFKA_GAP_REASON` above) is ever reached. See `test_coordenacao_assume_e_aceita`
    below for the full writeup (same bug, live-confirmed there as an unmarked failure). This test
    stays `xfail` either way (`contas.register_glosa_accept_entry` never calls `kafka.publish` —
    the reason above) — this fix only makes it fail for the REASON ACTUALLY DOCUMENTED instead of
    an earlier, unrelated TypeError.
    """
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_contas": "ACEITAR_GLOSA",
            "justificativa_glosa": "Item sintetico glosado por divergencia de valor (teste L0)",
            "codigo_glosa_aceito": "VALOR_ACIMA_TABELA",
            "valor_glosa_aceito_brl": _double_var(900.00),
            "analista_id": "analista-sintetico-001",
        },
    )
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_accept_without_human_task(engine, iid)
    assert _END_GLOSA_ACEITA in ended, f"Aceite humano deve atingir End_GlosaAceitaHumano. ended={ended}"
    # ADAPTED (t3.1-event-gap-a-contas, test-only, see `_WORKER_KAFKA_GAP_REASON` UPDATE): the
    # trailing `notifications_of_type("contas.register_glosa_accept")` check (+ its 3 field
    # asserts) was dead (FINDING 1: register_glosa_accept_entry does `del kafka`).
    # Flow_GWDec_Aceitar's own condition (bpmn:363, `${decisao_contas == 'ACEITAR_GLOSA'}`) is the
    # ONLY route to ST_RegisterGlosaAccept/End_GlosaAceitaHumano — reaching this end event already
    # proves decisao_contas=='ACEITAR_GLOSA' engine-side (gateway-gated, not worker-echoed). The
    # real _CONTAS_COMPLETED event (ST_PublishGlosaAceita, bpmn:295-306) carries codigo_glosa_
    # aceito/analista_id in its own event_payload_vars (bpmn:301) — asserting them here is
    # strictly stronger than the dead notification echo (same field names, real kafka.publish
    # call site instead of a fabricated worker-side capture).
    assert contas_probe.has_event(
        _CONTAS_COMPLETED,
        desfecho="glosa_aceita_humano",
        codigo_glosa_aceito="VALOR_ACIMA_TABELA",
        analista_id="analista-sintetico-001",
    ), (
        "ST_PublishGlosaAceita deve emitir completed(desfecho=glosa_aceita_humano) com "
        "codigo_glosa_aceito/analista_id do aceite humano"
    )


async def test_happy_path_reenviar(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """Analista completa REENVIAR => reconcile_payment (sem efeito adverso); End_Reenviada.

    ADAPTED (t3.1-event-gap-a-contas, test-only, see `_WORKER_KAFKA_GAP_REASON` UPDATE): the
    trailing `notifications_of_type("contas.reconcile_payment")` check was dead (FINDING 1).
    ST_PublishReenviada (bpmn:318-329) is the ONLY task downstream of ST_ReconcilePayment on
    Flow_Reenvio_Pub, gated by Flow_GWDec_Reenviar's condition (bpmn:365,
    `${decisao_contas == 'REENVIAR'}`) — the has_event assert below already proves
    reconcile_payment ran; strengthened with `prestador_id` (in ST_PublishReenviada's own
    event_payload_vars, bpmn:324) since no reconcile-specific payload field exists to
    distinguish further.
    """
    inst = await start_contas(
        categoria_normalizada="administrativa", documentacao_anexa=False, has_glosas=True
    )
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    assert "auditoria-contas" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, {"decisao_contas": "REENVIAR"})
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_REENVIADA in ended, f"REENVIAR => End_Reenviada. ended={ended}"
    assert _END_GLOSA_ACEITA not in ended
    assert contas_probe.has_event(
        _CONTAS_COMPLETED, desfecho="reenviada", prestador_id="PRESTADOR-TESTE-001"
    ), "ST_PublishReenviada deve emitir completed(desfecho=reenviada, prestador_id=...)"


# ===========================================================================
# Aceite exige campos / worker guard
# ===========================================================================


async def test_aceitar_glosa_exige_campos(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """ACEITAR_GLOSA sem justificativa/codigo/valor/analista_id => worker guard recusa.

    O engine despacha register_glosa_accept; o worker lanca GlosaAcceptNotHumanError. FINDING 3
    (module docstring): isso vira um INCIDENTE aberto no engine (nao um boundary-catch limpo —
    Error_GlosaAcceptNotHuman e declarado no BPMN mas SEM boundaryEvent correspondente, e mesmo
    se houvesse, PermissionError nunca dispara o caminho bpmnError). As assercoes abaixo NAO
    dependem de qual mecanismo produz o bloqueio — apenas que o terminal adverso nao e atingido e
    que nada foi registrado, ambos verdadeiros sob incidente OU boundary-catch.

    STRENGTHENED (t3.1-test-hygiene-batch): the original `assert not
    contas_probe.notifications_of_type("contas.register_glosa_accept")` was structurally vacuous —
    that channel is ALWAYS empty regardless of whether the guard fired (FINDING 1: the entry
    function never calls `kafka.publish` at all, guard-refused or not), so the assert could never
    fail either way; it proved nothing about the guard. Replaced with the REAL, positive observable
    FINDING 3 identifies: a guard refusal here has no boundary catch, so it surfaces as an OPEN
    ENGINE INCIDENT at `ST_RegisterGlosaAccept` carrying the guard's own `ERR_GLOSA_ACCEPT_NOT_HUMAN`
    message (`harness.py`'s `PermissionError` branch -> `_report_failure(..., retries_override=0)`
    -> immediate incident, `harness.py:1372-1383`) — this is the guard ACTUALLY REFUSING, not an
    absence of evidence. Mirrors how sibling guard tests elsewhere in this suite family assert
    refusals live (recurso/pagto precedent: observe the engine-side refusal artifact directly,
    never an always-empty proxy channel).
    """
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    ut = await _drive_to_analista(engine, contas_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_contas": "ACEITAR_GLOSA"})
    await contas_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_GLOSA_ACEITA not in ended, (
        "Aceite sem campos obrigatorios NAO pode atingir End_GlosaAceitaHumano (guard do worker)"
    )
    incidents = await engine.incidents(iid)
    guard_incidents = [i for i in incidents if i.get("activityId") == "ST_RegisterGlosaAccept"]
    assert guard_incidents, (
        "register_glosa_accept sem campos obrigatorios deve abrir um INCIDENTE real em "
        f"ST_RegisterGlosaAccept (FINDING 3 — sem boundary catch para PermissionError). "
        f"incidentes encontrados: {incidents}"
    )
    assert any("ERR_GLOSA_ACCEPT_NOT_HUMAN" in (i.get("incidentMessage") or "") for i in guard_incidents), (
        f"incidente deve carregar a mensagem do guard ERR_GLOSA_ACCEPT_NOT_HUMAN: {guard_incidents}"
    )
    await _assert_no_accept_without_human_task(engine, iid)


async def test_worker_register_glosa_accept_recusa_sem_humano() -> None:
    """Invocacao direta de register_glosa_accept_entry sem decisao humana => GlosaAcceptNotHumanError.

    Unit-style sobre o entry function real (SEM engine — nao depende do contas_probe/engine, roda
    mesmo sem o dev-stack). ADAPTADO (port rule 1): v2's dict-boundary
    `register_glosa_accept_entry(variables: dict, *, kafka=None) -> dict` (ADR-0026 §2b) substitui
    o donor's `make_register_glosa_accept_handler(kafka) -> Callable[[ExternalTask], ...]`; o
    guard lanca `GlosaAcceptNotHumanError` (`PermissionError` subclass), nao `WorkerBpmnError`. Os
    4 cenarios do guard sao preservados verbatim; a asserçao de "sucesso" verifica o dict
    RETORNADO (v2's entry function nao chama kafka.publish — FINDING 1, mesmo gap sistemico).
    """
    kafka = FakeKafkaPublisher()

    # (a) decisao_contas ausente -> recusa
    with pytest.raises(GlosaAcceptNotHumanError) as exc_a:
        register_glosa_accept_entry({}, kafka=kafka)
    assert "ERR_GLOSA_ACCEPT_NOT_HUMAN" in str(exc_a.value)

    # (b) decisao_contas != ACEITAR_GLOSA -> recusa
    with pytest.raises(GlosaAcceptNotHumanError) as exc_b:
        register_glosa_accept_entry({"decisao_contas": "RECORRER"}, kafka=kafka)
    assert "ERR_GLOSA_ACCEPT_NOT_HUMAN" in str(exc_b.value)

    # (c) ACEITAR_GLOSA mas faltando analista_id -> recusa
    with pytest.raises(GlosaAcceptNotHumanError) as exc_c:
        register_glosa_accept_entry(
            {
                "decisao_contas": "ACEITAR_GLOSA",
                "justificativa_glosa": "x",
                "codigo_glosa_aceito": "VALOR_ACIMA_TABELA",
                "valor_glosa_aceito_brl": 900.00,
            },
            kafka=kafka,
        )
    assert "ERR_GLOSA_ACCEPT_NOT_HUMAN" in str(exc_c.value)
    assert not kafka.published, "Nenhum registro deve ser publicado quando o guard recusa"

    # (d) decisao humana completa -> registra e carrega analista_id (no dict RETORNADO)
    result = register_glosa_accept_entry(
        {
            "decisao_contas": "ACEITAR_GLOSA",
            "justificativa_glosa": "Aceite fundamentado (teste)",
            "codigo_glosa_aceito": "VALOR_ACIMA_TABELA",
            "valor_glosa_aceito_brl": 900.00,
            "analista_id": "analista-sintetico-001",
            "tenant_id": "amh",
            "numero_lote_tiss": "LOTE-TESTE-GUARD",
        },
        kafka=kafka,
    )
    assert result["registered"] is True
    assert result["glosa_id"].startswith("GLOSA-")


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
    assert _END_GLOSA_ACEITA not in ended, "Reavaliacao por mensagem nunca auto-aceita glosa (L0)"
    await _assert_no_accept_without_human_task(engine, iid)
    assert await engine.instance_is_active(iid) or _END_SEM_GLOSA in ended, (
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
    """Timer BT_SlaTriagem (interruptivo): UT_AnalistaContas cancelada; UT_CoordenacaoContasAssume criada."""
    inst = await start_contas(categoria_normalizada="tecnica", has_glosas=True)
    iid = inst["id"]

    await _drive_to_analista(engine, contas_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaTriagem")
    await engine.execute_job(job.id)
    await contas_probe.drain()

    assert contas_probe.has_event(_CONTAS_SLA_BREACHED), "contas.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-contas" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA not in open_keys, "UT_AnalistaContas deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert _END_GLOSA_ACEITA not in ended, "Estouro de SLA nunca auto-aceita glosa (L0)"
    await _assert_no_accept_without_human_task(engine, iid)


async def test_coordenacao_assume_e_aceita(
    engine: EngineRest,
    contas_probe: ContasEngineProbe,
    start_contas: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e ACEITAR_GLOSA => End_GlosaAceitaHumano com UT humana.

    PORT FIX (T3.1 phase-2, test-harness-only bug — src/** untouched, PORT NOTE 1 class):
    `valor_glosa_aceito_brl` MUST go through `_double_var()`, not a bare Python literal.
    `EngineRest._to_camunda_vars` (this package's OWN test-only helper, not src/) has branches
    for dict-passthrough/bool/int only; a bare string OR a bare float both fall into its
    catch-all `else` branch and get sent to the engine as a Camunda **String** variable —
    `contas.py::register_glosa_accept`'s `if input_data.valor_glosa_aceito_brl <= 0:` guard then
    raises `TypeError: '<=' not supported between instances of 'str' and 'int'` (confirmed live:
    the original `"900.00"` string literal here reproduced this exactly). `_double_var()` is
    already the established fix for this exact class in this same file (see
    `valor_apresentado_brl` above) — it wraps the value as an explicit
    `{"value": ..., "type": "Double"}`, which `_to_camunda_vars`'s dict-passthrough branch sends
    verbatim, round-tripping as a real Python float on the worker side (`harness.py`'s own
    `_from_camunda_var`/`_to_camunda_var` fully support `Double`). Not a src/ change, not a
    weakening — same assertions, same intent, just correct engine-variable typing.
    """
    inst = await start_contas(categoria_normalizada="valor", has_glosas=True)
    iid = inst["id"]

    await _drive_to_analista(engine, contas_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaTriagem")
    await engine.execute_job(job.id)
    await contas_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_contas": "ACEITAR_GLOSA",
            "justificativa_glosa": "Prazo esgotado — coordenacao aceita (sintetico)",
            "codigo_glosa_aceito": "VALOR_ACIMA_TABELA",
            "valor_glosa_aceito_brl": _double_var(900.00),
            "analista_id": "coordenacao-sintetica-001",
        },
    )
    await contas_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_GLOSA_ACEITA in ended
    await _assert_no_accept_without_human_task(engine, iid)
    assert contas_probe.has_event(_CONTAS_COMPLETED, desfecho="glosa_aceita_humano")


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
    job_sla = await engine.await_timer_job(iid, "BT_SlaTriagem")
    assert job_sla.activity_id == "BT_SlaTriagem"


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
    job_sla = await engine.await_timer_job(iid, "BT_SlaTriagem")
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
        f"BT_SlaTriagem.dueDate={due_sla} nao bate com a ancora "
        f"data_recebimento_lote+P30D={expected_sla_anchor_based} (GAP-CONTAS-4)"
    )
    assert abs(due_alerta - expected_alerta_anchor_based) < tolerance, (
        f"BT_AlertaSlaContas.dueDate={due_alerta} nao bate com a ancora "
        f"data_recebimento_lote+P20D={expected_alerta_anchor_based} (GAP-CONTAS-4)"
    )
    assert abs(due_sla - attach_based_sla) > far_enough, (
        "BT_SlaTriagem.dueDate esta perto do attach da UT — regressao GAP-CONTAS-4"
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
    await _assert_no_accept_without_human_task(engine, iid)


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def test_glosa_triage_sem_saida_de_aceite() -> None:
    """O dominio de roteamento da glosa_triage e EXATAMENTE {SEM_GLOSA, RECORRER, ANALISE_HUMANA}."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_TRIAGE)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

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

    assert roteamentos == {"SEM_GLOSA", "RECORRER", "ANALISE_HUMANA"}, (
        f"dominio de roteamento inesperado: {roteamentos} — NAO pode conter ACEITAR/CONFIRMAR (L0)"
    )
    assert "ACEITAR" not in " ".join(roteamentos)
    assert "CONFIRMAR" not in " ".join(roteamentos)
    assert last_rule_first_output == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}."""
    from xml.etree import ElementTree as ET

    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_REASON, _DMN_CLASS, _DMN_TRIAGE, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double)"


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
