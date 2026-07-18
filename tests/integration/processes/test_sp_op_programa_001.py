"""SP-OP-PROGRAMA-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 2 (13-family
process-suite port). programa.py is a "dict-first `FunctionWorker`-wrapped module" (bootstrap.py
categorization; ADR-0026 Decisao §1/§2) — mirrors `test_sp_op_cancel_001.py`'s dict-boundary,
self-contained-fixture pattern rather than `test_sp_op_auth_001.py`'s typed-I/O `WorkerBase`-class
pattern.

Implementa o contrato W-B Phase 3 (docs/processes/contracts/SP-OP-PROGRAMA-001.md) contra o engine
real (ADR-0011: SEM mock de engine). Cada teste:

1. inicia a instancia via REST com business key `PROG-amh-{programa}-{benef}-{ciclo}`;
2. drena as external tasks com o `programa_probe` (workers reais de programa + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers/correlaciona mensagens via job/message API (NUNCA sleep);
5. verifica invariantes via historia do engine:
   - (A) CHOKEPOINT: NENHUM processamento de PHI (stratify/build/contact) sem consentimento;
   - (B) revogacao INTERROMPE e para (End_ProcessamentoInterrompidoRevogacao; fail-safe LGPD);
   - (C) NO-ADVERSE clinico: nenhuma End_DesligamentoClinicoHumano sem User Task clinica concluida.

Dados sinteticos obvios (mirrored EXACTLY from the donor): programa `cronicos`, beneficiario
pseudonimizado `bnf-teste-0001` (ADR-0006 — NUNCA CPF/nome), tenant `amh`, ciclo `2026-Q2`.
Business key `PROG-amh-{programa}-{benef}-{ciclo}`. Process key: SP-OP-PROGRAMA-001 (exato — nao
alterar).

Este modulo SELF-CONTAINS suas fixtures (NAO edita o conftest.py compartilhado): define localmente
`deploy_artifacts`, `programa_probe` (`ProgramaEngineProbe`), `start_programa` — mesmo padrao de
`test_sp_op_cancel_001.py`. Diferente do donor (que tambem redefinia `engine`/`_engine_available`
localmente), este port REUSA o `engine` fixture ja hoisted em `conftest.py` (port rule 3 do
charter: a construcao do fixture `engine` e IDENTICA entre familias, entao so vive uma vez).

## Invariantes (DoD deliverable)

test_chokepoint_consentimento_nenhum_phi_sem_consentimento:
  Sem consentimento ativo, a instancia atinge End_SemConsentimento e NENHUM worker de PHI
  (stratify_risk/build_care_plan/proactive_contact) e executado. Prova via historia + notificacoes.

test_revogacao_interrompe_processamento:
  Com consentimento, no subprocesso de cuidado, a correlacao msg.programa.consent_revoked
  INTERROMPE o subprocesso e leva a End_ProcessamentoInterrompidoRevogacao (stop_processing
  executado). Fail-safe.

test_nenhum_caminho_automatizado_desliga_clinicamente:
  Varredura das combinacoes de input da DMN programa_routing. A instancia NUNCA atinge
  End_DesligamentoClinicoHumano sem que UT_DecisaoClinica / UT_CoordenacaoDecisao tenha sido
  completada por humano com decisao_programa=DESLIGAR_CLINICO. Prova via historia do engine.

WorkerHarness PROPAGA as variaveis de saida dos handlers ao completar a external task. Ainda
assim, os FATOS DE ROTEAMENTO que as DMNs leem (`risco_estratificado`,
`elegibilidade_criterios_atendidos`) sao SEEDED como variaveis de START — o teste e o agente de
origem (mesma tecnica de `test_sp_op_cancel_001.py`, verificado no donor e preservado aqui): as
DMNs business-rule rodam ANTES do worker de estratificacao completar, entao os fatos precisam
estar no escopo do processo desde o start (in-zone, APOS o gate; o teste seed).

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor):
  - import paths -> v2 `maezo.tools.workers.programa`/`events`/`harness`; donor's
    `maezo.tools.workers.phase0.register_phase0_workers` -> v2
    `maezo.tools.workers.events.register_events_workers` (T3.1 R2, already merged — see finding 3).
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5; donor used
    `src/maezo/processes/{bpmn,dmn}/**`).
  - `engine` fixture no longer redefined locally (donor did) — reused from the shared
    `conftest.py` (port rule 3: identical construction contract across every family).
  - `drain()` uses the shared `drain_topics()` helper (v2 `WorkerTransport.fetch_and_lock`
    signature adaptation — see `conftest.py`) instead of the donor's own inline fetch loop.
  - `WorkerHarness(...)` is constructed WITHOUT a `bpmn_error_allowlist` here (unlike
    `test_sp_op_auth_001.py`/`test_sp_op_cancel_001.py`, which gate a family-specific modeled
    `WorkerBpmnError` code into their harness's allowlist): programa.py has NO worker path that
    ever raises a `WorkerBpmnError` in the first place — see finding 2 below. There is nothing to
    gate into an allowlist.
  - `_PROGRAMA_WORKER_TOPICS` is built from what `register_programa_workers` ACTUALLY registers
    (programa.py:227-241), cross-checked against the BPMN's declared `operadora.programa.*`
    topics via a drift-guard assertion in the `programa_probe` fixture (mirrors
    `test_sp_op_cancel_001.py`'s `assert not missing_from_drain` pattern) — see finding 1: the
    cross-check reveals 3 BPMN-declared topics with NO registered worker at all (a registry-drift
    finding in the OPPOSITE direction from cancel's case, where extra *registered* workers lacked
    drain coverage; here, workers are simply MISSING).

FINDINGS (see PR body / evidence-ledger for full detail):

  1. ROOT CAUSE (registry drift — programa.py itself, NOT this port): `register_programa_workers`
     (programa.py:227-241) registers handlers for only 5 of the 7 `operadora.programa.*` topics
     the BPMN declares (spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn):
     `operadora.programa.stratify_risk` (`ST_StratifyRisk`, BPMN:153 — the FIRST external task
     inside `SUB_Cuidado`, immediately after the consent gate), `operadora.programa.
     proactive_contact` (`ST_ProactiveContact`, BPMN:191), and `operadora.programa.
     notify_sla_risk` (`ST_NotifySlaRisk`, BPMN:263) have NO implementing function/registration at
     all. programa.py's OWN bootstrap docstring (lines 220-223) already self-documents this as a
     gap ("Spec topics with NO implementing function today (gap, not fabricated here):
     stratify_risk, proactive_contact, notify_sla_risk") — not fabricated by this port. Because
     `ST_StratifyRisk` is the very FIRST task any consented instance hits inside `SUB_Cuidado`,
     this single gap blocks EVERY test that needs the process to progress past the consent gate:
     the external task is created by the engine but never fetched by `drain()` (deliberately
     excluded from `_PROGRAMA_WORKER_TOPICS` — subscribing to an unregistered topic would only
     route it through `harness._handle`'s "no handler registered for topic" branch, harness.py:
     888-900, which demotes it straight to an incident; leaving it un-subscribed instead leaves
     the instance genuinely, silently PENDING at `ST_StratifyRisk` forever). `monitor_programa` IS
     registered (programa.py:236) under `operadora.programa.monitor_programa`, but NO BPMN service
     task in this process declares that topic either — an intentional orphan registration per
     programa.py's own docstring ("registered under a function-derived topic for registry
     completeness"), included in `_PROGRAMA_WORKER_TOPICS` so the drift-guard assertion passes
     naturally (mirrors cancel's now-fixed drift-guard, just in the opposite direction: here the
     guard proves no *extra* worker is undrained, while the 3 *missing* workers are a distinct,
     separately-documented gap the guard does not and should not catch). `src/**` fix
     (implementing/registering `stratify_risk`/`proactive_contact`/`notify_sla_risk` workers) is
     out of scope for this port.

  2. ROOT CAUSE (programa-specific, distinct from auth/cancel's `WorkerBpmnError` pattern):
     programa.py's guard failures can NEVER be reported to the engine as a `bpmnError` — they
     always demote straight to an engine incident, bypassing the BPMN's OWN declared boundary
     catch. Evidence chain: `check_consent`/`_register_program_discharge` raise `ProgramaError`
     (programa.py:200-206), a bespoke `Exception` subclass with `.code`/`.message` (NOT a
     `WorkerBpmnError`, NOT one of `_HARNESS_CLASSIFIED`). `FunctionWorker.execute()`
     (base.py:284-294) catches exactly this shape and re-raises it as a plain `ValueError(f"{code}:
     {message}")` (base.py:293) — a DELIBERATE reclassification (base.py's own `FunctionWorker`
     docstring, lines 255-270) so an unclassified coded exception doesn't fall into the engine's
     retry-on-failure default. `WorkerBase.run()` (base.py:123-201) then re-raises this SAME
     `ValueError` instance unchanged after exhausting its single attempt (`max_retries=1`
     default). By the time `WorkerHarness._handle` (harness.py:883) sees the exception, it is
     ALREADY a `ValueError` — so the `except WorkerBpmnError` branch (harness.py:916-917, the ONE
     branch that checks `bpmn_error_allowlist` and can dispatch `handle_bpmn_error` to the engine)
     NEVER matches; the generic `except ValueError` branch (harness.py:952-954) fires instead,
     calling `_report_failure(..., retries_override=0)` -> an engine INCIDENT. Consequence: the
     BPMN's own `BE_SemConsentimento` boundary event (BPMN:120-123, `errorRef="Error_
     ProgramaNoConsent"` / `errorCode="ERR_PROGRAMA_NO_CONSENT"`) can NEVER fire for
     `check_consent`'s guard failure — `End_SemConsentimento` (the documented LGPD fail-safe
     terminal, invariant A) is therefore UNREACHABLE via the current v2 dispatch wiring; the
     consent-gate-fail path produces an open incident instead. (The SAME misrouting applies to
     `register_program_discharge`'s `ERR_PROGRAM_DISCHARGE_NOT_HUMAN` — moot for every donor test
     that exercises it, since all of them are blocked earlier by finding 1 before ever reaching
     `ST_RegisterDischarge`.) This is orthogonal to auth.py/cancel.py, whose GATED action workers
     raise `WorkerBpmnError(error_code=...)` directly and so DO honor `bpmn_error_allowlist`; this
     is the reason this suite's `programa_probe` harness is built WITHOUT an allowlist (see PORT
     NOTES) — there is no `WorkerBpmnError` code programa.py's OWN workers could ever emit to gate.
     `src/**` fix (giving `FunctionWorker` a path to report a modeled `WorkerBpmnError` for coded
     exceptions whose code has a matching BPMN boundary) is out of scope for this port.

  3. `operadora.events.publish` gap, FIXED (T3.1 R2, already on main): `programa_probe` registers
     `maezo.tools.workers.events.register_events_workers` — mirrors the donor's own
     `register_phase0_workers` composition. This unblocks the GENERIC domain events
     (`agents.events.programa.{received,consent_blocked,sla_breached,completed}`, all published by
     dedicated `ST_Publish*` service tasks with `camunda:topic="operadora.events.publish"`) for any
     test that manages to progress far enough (findings 1/2 permitting).

  4. Kafka-publish gap, systemic (D-flagged in the T3.1 R2 ledger; distinct from findings 1/2 —
     even a fully-fixed registry would still hit this): `grep -rn "kafka.publish(" src/maezo/
     tools/workers/*.py` returns exactly one call site (events.py:247, the generic publish handler
     finding 3 wires). `register_programa_workers` (programa.py:233) explicitly discards its
     `kafka`/`seams` arguments (`del kafka, seams`) — none of programa.py's dict-first functions
     (`check_consent`/`enroll_beneficiario`/`monitor_programa`/`register_program_discharge`/
     `stop_processing`) accept or close over a `kafka` publisher at all, so NONE of them can ever
     call `kafka.publish` directly. Distinct from finding 3 (generic events.publish topic, WORKS):
     this is the family-specific direct-notification class (does NOT work) — e.g. `ST_StopProcessing`
     (BPMN:387-397, topic `operadora.programa.stop_processing`) carries an `event_topic_stopped`
     input parameter (BPMN:392, literal `agents.events.programa.processing_stopped`) that
     `stop_processing()` never reads or acts on, and there is NO separate `ST_Publish*` task
     between `ST_StopProcessing` and `End_ProcessamentoInterrompidoRevogacao` (BPMN:396-401) — so
     `agents.events.programa.processing_stopped` can NEVER be published by the current wiring,
     independent of findings 1/2. Every donor test that would assert this (`test_revogacao_
     interrompe_processamento` and its 2 `correlation_keys`/fan-out siblings) is ALREADY blocked
     earlier by finding 1 (`_drive_to_decisao` needs `stratify_risk`), so this does not change
     their xfail classification, but it WOULD independently block them even if finding 1 were
     fixed — tracked here for when that day comes. `src/**` fix (Kafka-producer wiring) is out of
     scope for this port.

  5. SECONDARY (would ALSO block, even with findings 1+3 fixed): `ST_PublishCompleted`'s
     `event_payload_vars` (BPMN:410) is `tenant_id,programa_id,beneficiario_pseudo_id,ciclo,
     decisao_programa,elegivel_programa` — it does NOT include `responsavel_clinico_id`. Even once
     the generic publish path fires with real data, `test_happy_path_desligamento_clinico_humano`'s
     assertion that the `programa.completed` fact carries `responsavel_clinico_id=
     "clinico-sintetico-001"` (ADR-0007 audit trail) would still fail — the field is simply never
     copied into the payload (events.py:216-218 only copies vars listed in `event_payload_vars`).
     Does not change this test's xfail reason (already blocked by finding 1) but is tracked here
     for when finding 1 is fixed.

  6. D-07 (`CeilingResolver`) — VERIFIED N/A for programa: `grep -nE "CeilingResolver|ceilings"
     src/maezo/tools/workers/programa.py` returns zero hits. Not cited as a blocker anywhere below.

  7. `notification_bridge.py` (src/maezo/platform/notification_bridge.py) — VERIFIED N/A for
     programa: read in full; it registers exactly 5 rules (CONTAS->RECURSO, CONTAS->FRAUDE,
     FRAUDE->CRED, FRAUDE->CANCEL, FRAUDE->INADIMPLENCIA). programa is not the source or target of
     any of the 5 — confirmed, no discrepancy to flag.

  8. `ERR_PROGRAMA_INSTANCIA_INVALIDA` (programa.py:27, BPMN:17 `Error_ProgramaInstanciaInvalida`)
     is DEAD: declared as a constant and as a BPMN `bpmn:error`, but never raised by any function
     in programa.py and never caught by any boundary event in the BPMN (`grep
     errorEventDefinition` against the BPMN shows only `EED_SemConsentimento`, matching
     `Error_ProgramaNoConsent`). The donor test suite never references this code either. Noted for
     the record only — no test depends on it, so it needs no xfail.

  9. Cross-process handoff-seam tests (test_cross_process_handoff_seam.py, a separate donor file)
     are OUT OF SCOPE per the port charter — the donor `test_sp_op_programa_001.py` itself
     contains no such tests, so there is nothing to exclude here.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness
from maezo.tools.workers.programa import register_programa_workers

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-PROGRAMA-001_Programas_Cuidado.bpmn"
_DMN_ROUTING = _REPO / "spec/processes/dmn/programa_routing.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/programa_sla.dmn"

# External task topics do contrato SP-OP-PROGRAMA-001, REGISTRADOS de fato por
# `register_programa_workers` (programa.py:227-241) + o publisher generico (finding 3).
_PUBLISH_TOPIC = "operadora.events.publish"
_CHECK_CONSENT_TOPIC = "operadora.programa.check_consent"
_BUILD_CARE_PLAN_TOPIC = "operadora.programa.build_care_plan"
_MONITOR_PROGRAMA_TOPIC = "operadora.programa.monitor_programa"  # orfao (sem topico BPMN correspondente)
_REGISTER_DISCHARGE_TOPIC = "operadora.programa.register_program_discharge"
_STOP_PROCESSING_TOPIC = "operadora.programa.stop_processing"

_PROGRAMA_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _CHECK_CONSENT_TOPIC,
    _BUILD_CARE_PLAN_TOPIC,
    _MONITOR_PROGRAMA_TOPIC,
    _REGISTER_DISCHARGE_TOPIC,
    _STOP_PROCESSING_TOPIC,
]

# BPMN-declared `operadora.programa.*` topicos SEM worker registrado (finding 1 — registry drift em
# programa.py, nao neste port). Deliberadamente FORA de `_PROGRAMA_WORKER_TOPICS`/drain(): ver
# module docstring. Mantidos aqui so para documentar os nomes exatos citados nos xfail reasons.
_STRATIFY_TOPIC_UNREGISTERED = "operadora.programa.stratify_risk"  # ST_StratifyRisk, BPMN:153
# ST_ProactiveContact, BPMN:191
_PROACTIVE_CONTACT_TOPIC_UNREGISTERED = "operadora.programa.proactive_contact"
_NOTIFY_SLA_TOPIC_UNREGISTERED = "operadora.programa.notify_sla_risk"  # ST_NotifySlaRisk, BPMN:263

# Topico interno de notificacoes.
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato SP-OP-PROGRAMA-001.md §Topicos) — publicados via o worker generico
# operadora.events.publish (finding 3, FIXED).
_PROGRAMA_RECEIVED = "agents.events.programa.received"
_PROGRAMA_CONSENT_BLOCKED = "agents.events.programa.consent_blocked"
_PROGRAMA_PROCESSING_STOPPED = "agents.events.programa.processing_stopped"
_PROGRAMA_SLA_BREACHED = "agents.events.programa.sla_breached"
_PROGRAMA_COMPLETED = "agents.events.programa.completed"

# User Task definition keys (BPMN).
_UT_DECISAO = "UT_DecisaoClinica"
_UT_COORDENACAO = "UT_CoordenacaoDecisao"

# End events do processo.
_END_DESLIGAMENTO = "End_DesligamentoClinicoHumano"
_END_REVOGACAO = "End_ProcessamentoInterrompidoRevogacao"
_END_SEM_CONSENTIMENTO = "End_SemConsentimento"
_END_ENROLLMENT = "End_EnrollmentRealizado"
_END_ENROLLMENT_HUMANO = "End_EnrollmentRealizadoHumano"
_END_NAO_ELEGIVEL = "End_NaoElegivel"
_END_ACOMPANHAMENTO = "End_AcompanhamentoConcluido"

# Mensagens BPMN.
_MSG_CONSENT_REVOKED = "msg.programa.consent_revoked"
_MSG_INFO_RECEIVED = "msg.programa.info_received"

# Workers de PHI que NUNCA podem executar sem consentimento (chokepoint A).
_PHI_NOTIFICATION_TYPES = frozenset(
    {"programa.stratify_risk", "programa.build_care_plan", "programa.proactive_contact"}
)

# User Tasks humanas que podem produzir o desfecho adverso clinico (L0 hard guard).
_UT_HUMANAS = frozenset({_UT_DECISAO, _UT_COORDENACAO})

# Terminal ADVERSO clinico (so via UT humana — clinical_decision L0 hard).
_ENDS_ADVERSOS = frozenset({_END_DESLIGAMENTO})

# Valor de decisao humana que autoriza o efeito adverso clinico (setado SO na User Task — L0 hard).
_DECISAO_DESLIGAR = "DESLIGAR_CLINICO"

# Campos obrigatorios de um desligamento clinico humano (DESLIGAR_CLINICO — defesa em profundidade).
_CAMPOS_DESLIGAMENTO = {
    "motivo_desligamento_clinico": "Alta de ciclo terapeutico — fundamentacao sintetica (teste L0)",
    "referencia_clinica": "Protocolo clinico de alta de ciclo — DRAFT/verify (teste)",
    "responsavel_clinico_id": "clinico-sintetico-001",
}

# ---------------------------------------------------------------------------
# xfail reason constants (distinct per finding — prefixed `_PROGRAMA_*`; a separate agent ports
# credenciamento in parallel to a different file with its own `_REASON` naming).
# ---------------------------------------------------------------------------

_PROGRAMA_MISSING_WORKER_REASON = (
    "T3.1 phase-2 finding 1 (registry drift in programa.py itself, NOT this port): "
    "register_programa_workers (programa.py:227-241) registers handlers for only 5 of the 7 "
    "operadora.programa.* topics the BPMN declares. operadora.programa.stratify_risk "
    "(ST_StratifyRisk, BPMN:153) has NO registered worker at all, and it is the FIRST external "
    "task any consented instance hits inside SUB_Cuidado (immediately after the consent gate) — "
    "so the instance stalls there, genuinely pending (not an incident: the topic is deliberately "
    "excluded from this suite's drain() rather than routed through harness._handle's "
    "'no handler registered' -> incident branch). Every assertion downstream of that point "
    "(BRT_Routing's DMN evaluation, UT_DecisaoClinica, any SLA timer attached to it, any human "
    "decision, any programa.completed fact) is therefore unreachable. operadora.programa."
    "proactive_contact (ST_ProactiveContact, BPMN:191) and operadora.programa.notify_sla_risk "
    "(ST_NotifySlaRisk, BPMN:263) are separately unregistered too (module docstring finding 1), "
    "compounding the same class of gap further downstream. programa.py's OWN bootstrap docstring "
    "(lines 220-223) already self-documents this as a known gap, not fabricated here. src/** fix "
    "(implementing/registering stratify_risk/proactive_contact/notify_sla_risk workers) is out of "
    "scope for this port."
)

_PROGRAMA_CONSENT_GUARD_NOT_BPMN_ERROR_REASON = (
    "T3.1 phase-2 finding 2 (programa-specific dispatch gap, distinct from auth/cancel's "
    "WorkerBpmnError-gated guards): check_consent raises ProgramaError(ERR_PROGRAMA_NO_CONSENT, "
    "...) (programa.py:55-58) on a failed/revoked consent check. FunctionWorker.execute() "
    "(base.py:284-294) catches this bespoke .code/.message exception (deliberately, per its own "
    "class docstring) and re-raises it as a plain ValueError — by the time WorkerHarness._handle "
    "(harness.py:883) sees it, it is no longer a WorkerBpmnError, so the ONE branch that checks "
    "bpmn_error_allowlist and can dispatch handle_bpmn_error to the engine (harness.py:916-917) "
    "never matches; the generic 'except ValueError' branch (harness.py:952-954) fires instead, "
    "reporting a straight engine incident (retries_override=0). Consequence: the BPMN's own "
    "BE_SemConsentimento boundary event (BPMN:120-123, errorRef=Error_ProgramaNoConsent / "
    "errorCode=ERR_PROGRAMA_NO_CONSENT) can NEVER fire for this guard failure under the current "
    "wiring — End_SemConsentimento (the documented LGPD fail-safe terminal, invariant A) is "
    "unreachable via the automated gate-fail path; an open incident is produced instead. src/** "
    "fix (giving FunctionWorker/the harness a path to report a modeled WorkerBpmnError for coded "
    "exceptions with a matching BPMN boundary) is out of scope for this port."
)


# ---------------------------------------------------------------------------
# ProgramaEngineProbe (espelha CancelEngineProbe) — self-contained fixtures (NAO edita conftest.py)
# ---------------------------------------------------------------------------


@dataclass
class ProgramaEngineProbe:
    """Driva os workers reais de programa contra o engine CIB Seven."""

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
        await drain_topics(
            self.transport, self.harness, self.worker_id, _PROGRAMA_WORKER_TOPICS, rounds=rounds
        )


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + as 2 DMN de processo de programa da arvore no engine real."""
    return await engine.deploy(_BPMN, _DMN_ROUTING, _DMN_SLA, name="SP-OP-PROGRAMA-001-qa")


@pytest_asyncio.fixture
async def programa_probe(engine: EngineRest) -> AsyncIterator[ProgramaEngineProbe]:
    """Probe que serve as external tasks com os workers reais de programa."""
    worker_id = f"qa-programa-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # SEM bpmn_error_allowlist (diferente de auth/cancel): nenhum worker de programa.py chega a
    # levantar um WorkerBpmnError (finding 2 — ProgramaError vira ValueError antes do harness ver
    # a excecao), entao nao ha codigo nenhum para gatear num allowlist.
    harness = WorkerHarness(transport, worker_id=worker_id, lock_duration_ms=10_000)
    kafka = FakeKafkaPublisher()
    register_programa_workers(harness, kafka)
    # T3.1 R2: o worker generico operadora.events.publish que toda ST_Publish* deste BPMN usa —
    # mirrors a composicao register_phase0_workers do donor.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (mirrors test_sp_op_cancel_001.py's cancel_probe, mesmo padrao): todo topico
    # operadora.programa.* registrado no harness DEVE estar na lista de drain — falha AQUI,
    # explicita, se um worker novo (ex.: stratify_risk, quando finding 1 for corrigido) ficar fora.
    programa_registered = {t for t in harness.registered_topics if t.startswith("operadora.programa.")}
    missing_from_drain = programa_registered - set(_PROGRAMA_WORKER_TOPICS)
    assert not missing_from_drain, (
        f"_PROGRAMA_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = ProgramaEngineProbe(
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


def _unique_benef(prefix: str = "bnf-teste") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest_asyncio.fixture
async def start_programa(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key PROG-amh-{programa}-{benef}-{ciclo} e payload canonico.

    Overrides via kwargs. Dados sinteticos obvios (cronicos / bnf-teste-NNNN / 2026-Q2, tenant amh).
    Os FATOS de estratificacao (risco_estratificado, elegibilidade_criterios_atendidos) sao seeded
    como variaveis de start — as DMNs business-rule (BRT_Routing) rodam ANTES do worker de
    estratificacao completar, entao o teste (agente de origem) seed os fatos que a DMN le.
    In-zone, APOS o gate.

    Default = consentimento ativo, risco moderado + criterios atendidos -> ELEGIVEL (caminho L3
    consentido). Override consentimento_ativo=False para exercitar o chokepoint.
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        programa = overrides.pop("programa_id", "cronicos")
        benef = overrides.pop("beneficiario_pseudo_id", _unique_benef())
        ciclo = overrides.pop("ciclo", "2026-Q2")
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "programa_id": programa,
            "beneficiario_pseudo_id": benef,
            "ciclo": ciclo,
            "gatilho": "indicacao_clinica",
            "consent_scope": "programa_cuidado",
            # consentimento resolvido no gate (seeded — o chokepoint o re-verifica)
            "consentimento_ativo": True,
            "consent_checked": True,
            # FATOS de estratificacao pre-resolvidos in-zone (seeded — banda, NUNCA PHI clinico cru)
            "risco_estratificado": "moderado",
            "elegibilidade_criterios_atendidos": True,
        }
        variables.update(overrides)
        business_key = f"PROG-amh-{programa}-{benef}-{ciclo}"
        return await engine.start_by_key("SP-OP-PROGRAMA-001", business_key, variables)

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


async def _assert_no_adverse_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE (C) clinical_decision / no-adverse: prova que nenhum terminal adverso clinico
    existe sem UT humana.

    Consulta a historia do engine: para o terminal adverso clinico (End_DesligamentoClinicoHumano)
    presente no historico, existe pelo menos uma User Task humana (_UT_HUMANAS) tambem no
    historico. Prova negativa: se nenhum terminal adverso esta no historico -> ok por vacuidade.

    Esta e a prova de que NAO existe caminho automatizado que desligue clinicamente o beneficiario
    do programa. Score/estratificacao alta NUNCA desliga — so roteia ao clinico humano.
    """
    ended = await engine.activity_instances_ended(iid)
    adversos_atingidos = ended & _ENDS_ADVERSOS
    if adversos_atingidos:
        human_tasks_in_history = ended & _UT_HUMANAS
        assert human_tasks_in_history, (
            f"INVARIANTE (C) VIOLADA (clinical_decision): terminal(is) adverso(s) "
            f"{adversos_atingidos} atingido(s) para instancia {iid} SEM nenhuma User Task humana no "
            f"historico. User Tasks esperadas (qualquer uma de): {_UT_HUMANAS}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de desligamento clinico — violacao do L0 hard."
        )


async def _drive_to_decisao(engine: EngineRest, probe: ProgramaEngineProbe, iid: str) -> Any:
    """Drena ate UT_DecisaoClinica surgir (dossie montado pelos workers reais; APOS o gate)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_DECISAO)


async def _correlate_message(
    business_key: str, message_name: str, variables: dict[str, Any] | None = None
) -> None:
    """Correlaciona uma mensagem BPMN por business key (revogacao / info recebida)."""
    payload: dict[str, Any] = {"messageName": message_name, "businessKey": business_key}
    if variables:
        payload["processVariables"] = {
            k: ({"value": v, "type": "Boolean"} if isinstance(v, bool) else {"value": v, "type": "String"})
            for k, v in variables.items()
        }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao {message_name} falhou [{resp.status_code}]: {resp.text[:200]}"
        )


async def _correlate_message_by_keys(
    message_name: str, correlation_keys: dict[str, str], *, all_matching: bool, variables: dict[str, Any]
) -> None:
    """Correlaciona uma mensagem BPMN por VARIAVEIS de processo (nao business key) — mesma forma
    que `mcp_cibseven.CibSevenHttpTransport.correlate_message` usa com `correlation_keys`/
    `all_matching` (GAP-PROG-4; POST /message `correlationKeys`+`all`, API padrao Camunda 7/CIB
    Seven). Chamada REST crua (mirror de `_correlate_message`) para provar a capacidade do ENGINE
    REAL diretamente, sem depender do loop Kafka da consent_revocation_bridge (Kafka nao esta sob
    teste aqui — so a correlacao engine-a-engine)."""
    payload: dict[str, Any] = {
        "messageName": message_name,
        "correlationKeys": {k: {"value": v, "type": "String"} for k, v in correlation_keys.items()},
        "all": all_matching,
        "processVariables": {k: {"value": v, "type": "String"} for k, v in variables.items()},
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao por correlationKeys {message_name} falhou [{resp.status_code}]: {resp.text[:200]}"
        )


# ===========================================================================
# (A) CHOKEPOINT de consentimento — gateia TODO processamento de PHI
# ===========================================================================


@pytest.mark.xfail(reason=_PROGRAMA_CONSENT_GUARD_NOT_BPMN_ERROR_REASON, strict=True)
async def test_chokepoint_consentimento_nenhum_phi_sem_consentimento(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Sem consentimento ativo => End_SemConsentimento; NENHUM worker de PHI executado (chokepoint A).

    Prova: o gate check_consent lanca ERR_PROGRAMA_NO_CONSENT (boundary error) -> consent_blocked ->
    End_SemConsentimento. stratify_risk/build_care_plan/proactive_contact NUNCA sao executados
    (nenhum PHI de programa processado). NENHUM terminal adverso clinico.
    """
    inst = await start_programa(consentimento_ativo=False)
    iid = inst["id"]

    await programa_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_SEM_CONSENTIMENTO in ended, f"Sem consentimento => End_SemConsentimento. ended={ended}"
    assert programa_probe.has_event(_PROGRAMA_CONSENT_BLOCKED), "programa.consent_blocked deve ser publicado"

    # Nenhum worker de PHI executado (chokepoint fail-closed).
    for ntype in _PHI_NOTIFICATION_TYPES:
        assert not programa_probe.notifications_of_type(ntype), (
            f"Worker de PHI '{ntype}' NUNCA executa sem consentimento (chokepoint A) — LGPD violado"
        )
    # O subprocesso de cuidado nunca entrou.
    assert "ST_StratifyRisk" not in ended, "Estratificacao NUNCA roda sem consentimento (in-zone APOS gate)"
    assert not (ended & _ENDS_ADVERSOS)
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PROGRAMA_CONSENT_GUARD_NOT_BPMN_ERROR_REASON, strict=True)
async def test_canal_proativo_sem_consent_checked_barra(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Canal proativo (D9) com consentimento_ativo=true mas consent_checked=false => barra no chokepoint.

    O canal proativo exige consent_checked==true antes de qualquer contato/PHI (D9). Sem ele, o gate
    fail-closed -> End_SemConsentimento; nenhum PHI processado.
    """
    inst = await start_programa(
        gatilho="canal_proativo",
        consentimento_ativo=True,
        consent_checked=False,
    )
    iid = inst["id"]

    await programa_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_SEM_CONSENTIMENTO in ended, (
        f"Canal proativo sem consent_checked => End_SemConsentimento. ended={ended}"
    )
    for ntype in _PHI_NOTIFICATION_TYPES:
        assert not programa_probe.notifications_of_type(ntype), (
            f"Worker de PHI '{ntype}' NUNCA executa sem consent_checked no canal proativo (D9)"
        )


# ===========================================================================
# (B) REVOGACAO = boundary interruptivo que PARA o processamento
# ===========================================================================


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_revogacao_interrompe_processamento(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Com consentimento, no subprocesso de cuidado, a revogacao INTERROMPE e para (End_...Revogacao).

    Roteia a ANALISE_HUMANA (estratificacao alta) para garantir que o subprocesso fica ativo (User
    Task aberta), depois correlaciona msg.programa.consent_revoked: o boundary interruptivo
    interrompe o subprocesso, dispara stop_processing e leva a End_ProcessamentoInterrompidoRevogacao.
    Fail-safe LGPD (NAO adverso) — NUNCA atinge End_DesligamentoClinicoHumano.
    """
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]
    business_key = inst["businessKey"]

    # Drena ate a User Task clinica abrir (subprocesso de cuidado ativo, in-zone, APOS o gate).
    await _drive_to_decisao(engine, programa_probe, iid)

    # Revogacao de consentimento (de SP-OP-LGPD-DSR-001 ou canal direto) -> boundary interruptivo.
    await _correlate_message(business_key, _MSG_CONSENT_REVOKED)
    await programa_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_REVOGACAO in ended, f"Revogacao => End_ProcessamentoInterrompidoRevogacao. ended={ended}"
    assert programa_probe.notifications_of_type("programa.stop_processing"), (
        "stop_processing deve ser executado na revogacao (para o tratamento de PHI)"
    )
    assert programa_probe.has_event(_PROGRAMA_PROCESSING_STOPPED), (
        "programa.processing_stopped deve ser publicado"
    )
    # Fail-safe, NAO adverso: a revogacao NUNCA atinge o terminal de desligamento clinico.
    assert _END_DESLIGAMENTO not in ended, "Revogacao (fail-safe LGPD) NUNCA desliga clinicamente"
    assert not (ended & _ENDS_ADVERSOS)
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# GAP-PROG-4: consent_revocation_bridge correlaciona msg.programa.consent_revoked por VARIAVEIS
# (tenant_id+beneficiario_pseudo_id, all_matching=True) — nao business_key unico. Prova contra o
# ENGINE REAL que (a) a engine aceita correlationKeys+all no shape que
# CibSevenHttpTransport.correlate_message envia, e (b) o fan-out atinge TODAS as instancias ativas
# do titular (um titular pode ter multiplas instancias PROGRAMA-001 ativas — uma por programa x
# ciclo), nao so uma.
# ===========================================================================


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_bridge_correlaciona_revogacao_por_correlation_keys_instancia_unica(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Correlacao por correlation_keys (tenant_id+beneficiario_pseudo_id) atinge a MESMA instancia
    que uma correlacao por business_key atingiria — mesmo desfecho de
    `test_revogacao_interrompe_processamento`, mas pelo mecanismo NOVO (GAP-PROG-4)."""
    benef = _unique_benef()
    inst = await start_programa(
        beneficiario_pseudo_id=benef, risco_estratificado="alto", elegibilidade_criterios_atendidos=True
    )
    iid = inst["id"]

    await _drive_to_decisao(engine, programa_probe, iid)

    consent_ref = f"DSR-amh-{benef}-revogacao_consentimento-2026-06-14"
    await _correlate_message_by_keys(
        _MSG_CONSENT_REVOKED,
        {"tenant_id": "amh", "beneficiario_pseudo_id": benef},
        all_matching=True,
        variables={"consent_event_ref": consent_ref},
    )
    await programa_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_REVOGACAO in ended, (
        f"Correlacao por correlation_keys => End_ProcessamentoInterrompidoRevogacao. ended={ended}"
    )
    assert programa_probe.has_event(_PROGRAMA_PROCESSING_STOPPED), (
        "programa.processing_stopped deve ser publicado"
    )
    # consent_event_ref (auditoria, GAP-PROG-4) chega ao worker stop_processing via a variavel
    # correlacionada e e ecoado no evento de dominio (contrato §Variaveis de saida).
    stopped = programa_probe.notifications_of_type("programa.stop_processing")
    assert stopped, "stop_processing deve ser executado na revogacao"
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_bridge_correlaciona_revogacao_fan_out_multiplas_instancias_do_titular(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """FAN-OUT (GAP-PROG-4): um titular com DUAS instancias PROGRAMA-001 ativas (programas/ciclos
    distintos) tem AMBAS interrompidas por UMA UNICA correlacao all_matching=True — a engine
    resolve o fan-out, nao a ponte. Prova a razao de ser de correlation_keys sobre business_key
    (revogacao de consentimento e TITULAR-WIDE para consent_scope=programa_cuidado, LGPD art. 8
    §5/art. 18 §2 — deve parar TODO processamento do beneficiario, nao so uma inscricao)."""
    benef = _unique_benef()
    inst_cronicos = await start_programa(
        beneficiario_pseudo_id=benef,
        programa_id="cronicos",
        ciclo="2026-Q2",
        risco_estratificado="alto",
        elegibilidade_criterios_atendidos=True,
    )
    inst_prenatal = await start_programa(
        beneficiario_pseudo_id=benef,
        programa_id="pre-natal",
        ciclo="2026-Q2",
        risco_estratificado="alto",
        elegibilidade_criterios_atendidos=True,
    )
    iid_cronicos = inst_cronicos["id"]
    iid_prenatal = inst_prenatal["id"]
    assert iid_cronicos != iid_prenatal

    await _drive_to_decisao(engine, programa_probe, iid_cronicos)
    await _drive_to_decisao(engine, programa_probe, iid_prenatal)

    # UMA correlacao, DUAS instancias ativas do MESMO titular — o fan-out e do engine.
    await _correlate_message_by_keys(
        _MSG_CONSENT_REVOKED,
        {"tenant_id": "amh", "beneficiario_pseudo_id": benef},
        all_matching=True,
        variables={"consent_event_ref": f"DSR-amh-{benef}-revogacao_consentimento-2026-06-14"},
    )
    await programa_probe.drain()

    ended_cronicos = await _await_end(engine, iid_cronicos)
    ended_prenatal = await _await_end(engine, iid_prenatal)
    assert _END_REVOGACAO in ended_cronicos, (
        f"fan-out: instancia cronicos deve ser interrompida. ended={ended_cronicos}"
    )
    assert _END_REVOGACAO in ended_prenatal, (
        f"fan-out: instancia pre-natal deve ser interrompida. ended={ended_prenatal}"
    )
    await _assert_no_adverse_without_human_task(engine, iid_cronicos)
    await _assert_no_adverse_without_human_task(engine, iid_prenatal)


# ===========================================================================
# (C) NO-ADVERSE clinico — desligamento so por humano (DoD invariante)
# ===========================================================================


async def test_nenhum_caminho_automatizado_desliga_clinicamente(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """INVARIANTE (C) clinical_decision: NENHUM caminho automatizado desliga clinicamente.

    Varredura das combinacoes de input da DMN programa_routing (com consentimento ativo):
      risco_estratificado in {baixo, moderado, alto, indeterminado}
      x elegibilidade_criterios_atendidos in {true, false}
    (4 * 2 = 8 combinacoes).

    Para cada instancia que percorre ate estabilizar (sem completar nenhuma User Task):
    - NENHUMA atinge End_DesligamentoClinicoHumano (nenhuma UT humana completada na varredura).
    - Estratificacao alta NAO desliga — so roteia para ANALISE_HUMANA (clinico humano).
    - Invariante de historia (prova formal via history/activity-instance).

    PORT NOTE (honesty disclosure, NOT a weakening — every assertion below is BYTE-IDENTICAL to
    the donor): left UNMARKED (no xfail) because it is predicted to PASS as written, but the proof
    it delivers right now is WEAKER than intended. `operadora.programa.stratify_risk` has no
    registered worker (`_PROGRAMA_MISSING_WORKER_REASON` above) — it is the FIRST task inside
    `SUB_Cuidado`, reached BEFORE `BRT_Routing` (the DMN business-rule task this sweep's
    `risco_estratificado`/`elegibilidade_criterios_atendidos` combinations are meant to drive) ever
    evaluates. Every one of the 8 instances this sweep starts therefore stalls at `ST_StratifyRisk`
    before `BRT_Routing` runs, so the invariant holds VACUOUSLY (no automated path reaches
    `End_DesligamentoClinicoHumano` because NOTHING reaches it, not because each DMN branch was
    exercised and proven safe). Flagged here so a verifier does not mistake a green run for genuine
    DMN-branch coverage until the `stratify_risk` gap (finding 1) is fixed.
    """
    riscos = ["baixo", "moderado", "alto", "indeterminado"]
    bools = [True, False]
    checked = 0

    for risco, criterios in itertools.product(riscos, bools):
        inst = await start_programa(
            risco_estratificado=risco,
            elegibilidade_criterios_atendidos=criterios,
        )
        iid = inst["id"]
        await programa_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        adversos = ended & _ENDS_ADVERSOS
        assert not adversos, (
            f"(C) VIOLADO (clinical_decision): risco={risco} criterios={criterios} atingiu "
            f"terminal(is) adverso(s) {adversos} automaticamente. ended={ended}"
        )
        await _assert_no_adverse_without_human_task(engine, iid)
        checked += 1

    assert checked == 8, f"Esperava 8 combinacoes varridas; varri {checked}"


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_estratificacao_alta_roteia_para_humano_nunca_desliga(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Risco alto => programa_routing=ANALISE_HUMANA; chega a UT_DecisaoClinica — NUNCA desliga sozinho."""
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, programa_probe, iid)
    assert "coordenacao-clinica" in ut.candidate_groups or "equipe-cuidado" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Risco alto NUNCA produz desligamento clinico automatico (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Happy paths (caminho L3 consentido + decisao humana)
# ===========================================================================


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_happy_path_enrollment_elegivel_l3(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Consentido + risco moderado + criterios atendidos => ELEGIVEL; contato proativo; enrollment.

    DMN programa_routing=ELEGIVEL; proactive_contact executado (consent_checked==true); fim
    End_EnrollmentRealizado; NENHUMA User Task adversa criada (caminho L3 consentido). discharge
    NUNCA invocado. GAP-PROG-2: programa.completed carrega desfecho=enrollment_realizado.
    """
    inst = await start_programa(risco_estratificado="moderado", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    await programa_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_ENROLLMENT in ended, f"Elegivel => End_EnrollmentRealizado. ended={ended}"
    assert not await engine.list_user_tasks(iid), "Caminho L3 elegivel nao cria User Task adversa"
    assert programa_probe.has_event(_PROGRAMA_RECEIVED)
    assert programa_probe.notifications_of_type("programa.stratify_risk"), "estratificacao roda APOS o gate"
    assert programa_probe.notifications_of_type("programa.proactive_contact"), (
        "contato proativo no caminho elegivel"
    )
    assert not programa_probe.notifications_of_type("programa.register_program_discharge"), (
        "Enrollment elegivel NUNCA registra desligamento clinico (L0)"
    )
    assert not (ended & _ENDS_ADVERSOS)
    assert programa_probe.has_event(_PROGRAMA_COMPLETED, desfecho="enrollment_realizado"), (
        "GAP-PROG-2: programa.completed deve carregar desfecho=enrollment_realizado"
    )


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_happy_path_nao_elegivel_neutro(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Risco baixo + criterios nao atendidos => NAO_ELEGIVEL; fim neutro (NAO e negativa de cobertura).

    GAP-PROG-2: programa.completed carrega desfecho=nao_elegivel.
    """
    inst = await start_programa(risco_estratificado="baixo", elegibilidade_criterios_atendidos=False)
    iid = inst["id"]

    await programa_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_NAO_ELEGIVEL in ended, f"Nao elegivel => End_NaoElegivel. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS), "Nao-elegibilidade e neutra (nao e negativa de cobertura)"
    # Estratificacao rodou (APOS o gate), mas nenhum desligamento clinico.
    assert programa_probe.notifications_of_type("programa.stratify_risk")
    assert not programa_probe.notifications_of_type("programa.register_program_discharge")
    assert programa_probe.has_event(_PROGRAMA_COMPLETED, desfecho="nao_elegivel"), (
        "GAP-PROG-2: programa.completed deve carregar desfecho=nao_elegivel"
    )


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_happy_path_desligamento_clinico_humano(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Clinico completa DESLIGAR_CLINICO (com campos) => register_program_discharge => End_...Humano.

    register_program_discharge executado (guard satisfeito); programa.completed
    desfecho=desligamento_clinico_humano carregando responsavel_clinico_id na trilha de auditoria.
    Este e o UNICO caminho ao terminal adverso clinico (so via humano).
    """
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, programa_probe, iid)
    await engine.complete_task_as_human(
        ut.id, {"decisao_programa": _DECISAO_DESLIGAR, **_CAMPOS_DESLIGAMENTO}
    )
    await programa_probe.drain()

    ended = await _await_end(engine, iid)
    # PROVA: End_DesligamentoClinicoHumano so existe porque a UT humana foi completada com
    # DESLIGAR_CLINICO.
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_DESLIGAMENTO in ended, f"Desligamento humano => End_DesligamentoClinicoHumano. ended={ended}"
    assert programa_probe.has_event(
        _PROGRAMA_COMPLETED,
        desfecho="desligamento_clinico_humano",
        responsavel_clinico_id="clinico-sintetico-001",
    ), "programa.completed adverso deve carregar responsavel_clinico_id (trilha de auditoria ADR-0007)"

    # register_program_discharge publica programa.completed direto (auditoria dupla engine+Kafka).
    assert programa_probe.has_event(_PROGRAMA_COMPLETED, desfecho="desligamento_clinico_humano")


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_happy_path_enroll_humano(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Clinico completa ENROLL (consentido, nao-adverso) => End_EnrollmentRealizadoHumano (neutro).

    GAP-PROG-2: programa.completed carrega desfecho=enrollment_realizado (mesmo desfecho do ramo L3).
    """
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, programa_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_programa": "ENROLL"})
    await programa_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENROLLMENT_HUMANO in ended, f"ENROLL humano => End_EnrollmentRealizadoHumano. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert not programa_probe.notifications_of_type("programa.register_program_discharge")
    assert programa_probe.has_event(_PROGRAMA_COMPLETED, desfecho="enrollment_realizado"), (
        "GAP-PROG-2: programa.completed deve carregar desfecho=enrollment_realizado"
    )


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_happy_path_manter_acompanhamento(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Clinico completa MANTER_ACOMPANHAMENTO (default) => End_AcompanhamentoConcluido (neutro).

    GAP-PROG-2: programa.completed carrega desfecho=acompanhamento_concluido.
    """
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, programa_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_programa": "MANTER_ACOMPANHAMENTO"})
    await programa_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ACOMPANHAMENTO in ended, f"MANTER => End_AcompanhamentoConcluido. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert programa_probe.has_event(_PROGRAMA_COMPLETED, desfecho="acompanhamento_concluido"), (
        "GAP-PROG-2: programa.completed deve carregar desfecho=acompanhamento_concluido"
    )


# ===========================================================================
# Desligamento exige campos / worker guard
# ===========================================================================


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_desligar_exige_campos_worker_guard(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """DESLIGAR_CLINICO sem motivo/referencia/responsavel => worker guard recusa.

    O engine despacha register_program_discharge; o worker lanca ERR_PROGRAM_DISCHARGE_NOT_HUMAN
    (defesa em profundidade); a instancia NAO atinge End_DesligamentoClinicoHumano.
    """
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    ut = await _drive_to_decisao(engine, programa_probe, iid)
    # Completa com DESLIGAR_CLINICO mas SEM os campos obrigatorios.
    await engine.complete_task_as_human(ut.id, {"decisao_programa": _DECISAO_DESLIGAR})
    await programa_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_DESLIGAMENTO not in ended, (
        "Desligamento sem campos obrigatorios NAO pode atingir End_DesligamentoClinicoHumano (guard)"
    )
    assert not programa_probe.has_event(_PROGRAMA_COMPLETED, desfecho="desligamento_clinico_humano"), (
        "programa.completed adverso NAO deve publicar sem campos obrigatorios"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Timers de SLA (decisao clinica)
# ===========================================================================


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Timer BT_AlertaSlaPrograma (nao-interruptivo): notify_sla_risk recebe task; UT segue aberta."""
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    await _drive_to_decisao(engine, programa_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSlaPrograma")
    await engine.execute_job(job.id)
    await programa_probe.drain()

    assert programa_probe.notifications_of_type("programa.notify_sla_risk"), (
        "Worker notify_sla_risk deve ser executado no alerta de SLA"
    )
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_DECISAO in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Timer BT_SlaDecisao (interruptivo): UT_DecisaoClinica cancelada; UT_CoordenacaoDecisao criada.

    programa.sla_breached publicado. NAO ha auto-desligamento por timeout — a decisao continua
    humana.
    """
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    await _drive_to_decisao(engine, programa_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaDecisao")
    await engine.execute_job(job.id)
    await programa_probe.drain()

    assert programa_probe.has_event(_PROGRAMA_SLA_BREACHED), "programa.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-clinica" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_DECISAO not in open_keys, "UT_DecisaoClinica deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro de SLA NUNCA auto-desliga (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_coordenacao_assume_e_desliga(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e DESLIGAR_CLINICO => End_DesligamentoClinicoHumano com UT humana.

    Prova que mesmo no caminho de escalonamento o desligamento passa por UT humana (invariante C).
    """
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    await _drive_to_decisao(engine, programa_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaDecisao")
    await engine.execute_job(job.id)
    await programa_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_programa": _DECISAO_DESLIGAR,
            "motivo_desligamento_clinico": "Alta de ciclo — coordenacao decide (sintetico)",
            "referencia_clinica": "Protocolo de alta de ciclo — DRAFT/verify",
            "responsavel_clinico_id": "coordenacao-clinica-sintetica-001",
        },
    )
    await programa_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_DESLIGAMENTO in ended
    await _assert_no_adverse_without_human_task(engine, iid)
    assert programa_probe.has_event(_PROGRAMA_COMPLETED, desfecho="desligamento_clinico_humano")


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_dmn_programa_sla_resolve_timers(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """programa_routing=ANALISE_HUMANA => DMN programa_sla resolve prazos (ISO); timers existem.

    Prova indireta: os timers BT_AlertaSlaPrograma / BT_SlaDecisao usam ${sla.sla_*} da DMN. Se os
    jobs de timer existem, a DMN resolveu as duracoes corretamente.
    """
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]

    await _drive_to_decisao(engine, programa_probe, iid)

    job_alerta = await engine.await_timer_job(iid, "BT_AlertaSlaPrograma")
    assert job_alerta.activity_id == "BT_AlertaSlaPrograma"
    job_sla = await engine.await_timer_job(iid, "BT_SlaDecisao")
    assert job_sla.activity_id == "BT_SlaDecisao"


# ===========================================================================
# Pendencia de informacao clinica (humano solicita)
# ===========================================================================


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_solicitar_info_aguarda_correlacao(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """UT_DecisaoClinica => humano SOLICITAR_INFO => aguarda msg.programa.info_received => reabre UT.

    Ao correlacionar (business key), reabre UT_DecisaoClinica para nova decisao humana. Nunca
    desliga automaticamente.
    """
    inst = await start_programa(risco_estratificado="alto", elegibilidade_criterios_atendidos=True)
    iid = inst["id"]
    business_key = inst["businessKey"]

    ut = await _drive_to_decisao(engine, programa_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_programa": "SOLICITAR_INFO"})
    await programa_probe.drain()

    await _correlate_message(business_key, _MSG_INFO_RECEIVED)

    ut2 = await engine.await_user_task(iid, _UT_DECISAO)
    assert "coordenacao-clinica" in ut2.candidate_groups or "equipe-cuidado" in ut2.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "SOLICITAR_INFO NUNCA auto-desliga (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def test_programa_routing_sem_saida_adversa() -> None:
    """O dominio de elegivel_programa da programa_routing e EXATAMENTE
    {ELEGIVEL, NAO_ELEGIVEL, ANALISE_HUMANA}.

    Nenhum valor DESLIGAR/ALTA/NEGAR; e existe row catch-all -> ANALISE_HUMANA. Varredura estatica
    do XML da DMN (nao precisa de engine).
    """
    tree = ET.parse(_DMN_ROUTING)
    root = tree.getroot()

    elegibilidades: set[str] = set()
    last_rule_first_output: str | None = None
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert outputs, "cada rule deve ter outputEntry"
        text_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        assert text_el is not None and text_el.text
        val = text_el.text.strip().strip('"')
        elegibilidades.add(val)
        last_rule_first_output = val

    assert elegibilidades == {"ELEGIVEL", "NAO_ELEGIVEL", "ANALISE_HUMANA"}, (
        f"dominio de elegibilidade inesperado: {elegibilidades} — NAO pode conter DESLIGAR/ALTA/NEGAR (L0)"
    )
    blob = " ".join(elegibilidades)
    for proibido in ("DESLIGAR", "ALTA", "NEGAR", "DISCHARGE", "DENY"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na programa_routing (L0)"
    assert last_rule_first_output == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_programa_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}.

    Nenhuma coluna usa "number". Varredura estatica das 2 DMNs.
    """
    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_ROUTING, _DMN_SLA):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double)"


def test_programa_sla_sem_saida_adversa() -> None:
    """programa_sla so produz prazos ISO + fonte — nenhuma saida adversa (sem desligamento/alta)."""
    tree = ET.parse(_DMN_SLA)
    root = tree.getroot()
    blob = " ".join((el.text or "") for el in root.iter() if _local(el.tag) == "text")
    for proibido in ("DESLIGAR", "ALTA_CLINICA", "NEGAR", "DISCHARGE"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na programa_sla (L0)"


def test_programa_bpmn_desligamento_so_apos_user_task() -> None:
    """Prova estatica (BPMN): o terminal End_DesligamentoClinicoHumano so e alcancado via o worker
    register_program_discharge, cujo unico predecessor e o gateway de decisao alimentado SO por User
    Tasks (UT_DecisaoClinica / UT_CoordenacaoDecisao).

    Nenhuma DMN/service-task automatica seta decisao_programa=DESLIGAR_CLINICO. Varredura estatica
    do XML do BPMN (nao precisa de engine).
    """
    tree = ET.parse(_BPMN)
    root = tree.getroot()

    # 1) O ramo DESLIGAR_CLINICO so sai do gateway de decisao (alimentado por User Tasks).
    flows = [e for e in root.iter() if _local(e.tag) == "sequenceFlow"]
    desligar_flows = [f for f in flows if f.get("targetRef") == "ST_RegisterDischarge"]
    assert desligar_flows, "deve haver um flow para ST_RegisterDischarge"
    for f in desligar_flows:
        assert f.get("sourceRef") == "GW_Decisao", (
            "o ramo de desligamento so pode sair do gateway de decisao humana (GW_Decisao)"
        )

    # 2) GW_Decisao so e alimentado por User Tasks (UT_DecisaoClinica / UT_CoordenacaoDecisao).
    into_gw = [f.get("sourceRef") for f in flows if f.get("targetRef") == "GW_Decisao"]
    assert set(into_gw) <= {"UT_DecisaoClinica", "UT_CoordenacaoDecisao"}, (
        f"GW_Decisao deve ser alimentado SO por User Tasks humanas; encontrou: {into_gw}"
    )

    # 3) End_DesligamentoClinicoHumano so e alcancado pelo worker register_program_discharge.
    into_end = [f.get("sourceRef") for f in flows if f.get("targetRef") == "End_DesligamentoClinicoHumano"]
    assert into_end == ["ST_RegisterDischarge"], (
        f"End_DesligamentoClinicoHumano so via register_program_discharge; encontrou: {into_end}"
    )


# ===========================================================================
# Idempotencia (business key)
# ===========================================================================


@pytest.mark.xfail(reason=_PROGRAMA_MISSING_WORKER_REASON, strict=True)
async def test_business_key_uma_instancia_por_ciclo(
    engine: EngineRest,
    programa_probe: ProgramaEngineProbe,
    start_programa: Callable[..., Any],
) -> None:
    """Mesmo business key (programa x beneficiario x ciclo): nao criar 2a instancia ativa.

    Roteia a ANALISE_HUMANA (risco alto) para manter a instancia ativa (User Task aberta) e provar
    a unicidade por business key.
    """
    benef = "bnf-teste-idem-0001"
    business_key = f"PROG-amh-cronicos-{benef}-2026-Q2"

    first = await start_programa(
        beneficiario_pseudo_id=benef,
        risco_estratificado="alto",
        elegibilidade_criterios_atendidos=True,
    )
    await _drive_to_decisao(engine, programa_probe, first["id"])

    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1
    assert existing[0]["id"] == first["id"]
