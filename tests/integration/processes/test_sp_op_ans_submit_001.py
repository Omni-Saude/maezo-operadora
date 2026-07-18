"""SP-OP-ANS-SUBMIT-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`,
`tests/integration/processes/test_sp_op_ans_submit_001.py`, 1557 lines) — T3.1 phase 2
(13-family process-suite port). The donor file covers TWO distinct process families in one
module: SP-OP-ANS-SUBMIT-001 proper (lines ~1-1397) and a "GAP-ANS-1 seam cron" section
(lines ~1400-1557, `SP-OP-ANS-CRON-001 -> fato ans.cron_due -> bridge -> SP-OP-ANS-SUBMIT-001`).
This file ports ONLY the SUBMIT-001 half; the CRON seam lives in the sibling
`test_sp_op_ans_cron_001.py` (own module docstring cross-references back here) — the split
mirrors the v2 family boundary (`ans_submit.py` vs `ans_cron.py`, two distinct worker modules).

Implementa o test-spec do W5 (docs/processes/test-specs/SP-OP-ANS-SUBMIT-001.md) contra o engine
real (ADR-0011: SEM mock de engine). Cada teste:

1. inicia a instancia via REST com business key `ANSSUB-amh-{report_type}-{competencia}`;
2. drena as external tasks com o `ans_probe` (workers reais + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que o filing passa pela UT humana).

Dados sinteticos obvios: tenant `amh`, `report_type=RN_124_SIP`, `competencia=2026-01`, dataset
`DATASET-TESTE-0001`. Business key `ANSSUB-amh-RN_124_SIP-2026-01`.
Process key: SP-OP-ANS-SUBMIT-001 (exato — nao alterar).

## NAO negativa-like

Este processo nao tem padrao no-denial — nao ha decisao adversa, logo nao ha invariante de negativa
por DMN. A salvaguarda equivalente, testada como PRIORITARIA, e o HITL PRE-FILING
(`test_submit_exige_user_task_humana`): NENHUM caminho automatizado transmite a ANS. A prova e
feita consultando a historia do engine: o submit/terminal de envio so co-ocorre com a User Task de
aprovacao concluida por humano.

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor unless a
FINDING below documents a genuine v2 behavioral gap):
  - import paths -> v2 `maezo.tools.workers.ans_submit`/`events`/`harness`; the shared `engine`
    fixture + `drain_topics()` helper come from `.conftest` (v2 convention established by the
    already-merged `test_sp_op_auth_001.py`/`test_sp_op_cancel_001.py` templates) — UNLIKE the
    donor, which self-contains `engine`/`_engine_available` locally. `deploy_artifacts` /
    `AnsEngineProbe` / `start_ans` stay self-contained (family-specific probe construction, per
    `conftest.py`'s own port-rule-3 note).
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5). SUBMIT's BPMN references ALL
    FOUR DMNs under `spec/processes/dmn/`: `ans_calendar` (BRT_Calendario), `ans_sla`
    (BRT_AnsSla), `ans_submission_admissibility` (BRT_Admissibilidade), `ans_retry_policy`
    (BRT_RetryPolicy, inside `SUB_RetryEnvio`) — verified by
    `grep -o 'camunda:decisionRef="[^"]*"' spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_*.bpmn`,
    all 4 present; `deploy_artifacts` deploys all 4 alongside the BPMN.
  - v1's `register_phase0_workers`/`FakeKafkaPublisher` (from v1's `phase0.py`) -> v2's
    `maezo.tools.workers.events.register_events_workers` + `maezo.tools.workers.harness
    .FakeKafkaPublisher` (T1.1/T3.1 R2 spine).
  - v1's `make_submit_handler(kafka) -> Callable[[ExternalTask], ...]` / `make_assemble_handler`
    factories do NOT exist on v2 main (grep confirms zero hits) — v2 replaced the class/factory-
    per-topic shape for this module with ADR-0026 SS2b's typed dict-boundary entry functions
    (`submit_entry`/`assemble_entry(variables: dict, *, kafka=None) -> dict`). The 2 donor
    unit-style tests that construct a handler directly are adapted to call the entry functions
    with a plain dict (mirrors `test_sp_op_cancel_001.py`'s adaptation of
    `send_cancellation_notice_entry`); `test_err_ans_submit_not_human_recusa_sem_aprovacao`'s guard
    genuinely still works end-to-end (port rule 1 verified) and is kept as a REAL, unmarked test —
    `AnsSubmitNotHumanError` (a `PermissionError` subclass) replaces the donor's
    `WorkerBpmnError(ERR_ANS_SUBMIT_NOT_HUMAN)` (guard shape changed the SAME way cancel.py's
    `CancellationNotHumanError` did: v2 routes `PermissionError` guards straight to an engine
    incident, `harness.py`'s `_handle`, never through the `bpmn_error_allowlist`).
    `test_assemble_dataset_incompleto_roteia_a_humano` does NOT survive adaptation this way — see
    FINDING D below (the guard it exercises does not exist in v2 at all).
  - `ans_probe`'s harness wires `dmn=CibSevenDmnTransport(CIBSEVEN_BASE_URL)` into
    `register_ans_submit_workers` (the ONLY entry function that evaluates a DMN table directly,
    `retransmit_entry` -> `ans_retry_policy`, ADR-0028 T1.5 cutover) — inert in practice today
    (FINDING B: the retry subprocess this seam feeds is unreachable), kept for fidelity /
    future-proofing once FINDING B is fixed.
  - `bpmn_error_allowlist={"ERR_ANS_PROTOCOLO_NACK", "ERR_ANS_RETRY_ESGOTADO"}` mirrors the two
    `bpmn:error` codes SUBMIT's BPMN declares a matching boundary catch for (`BE_SubmitNack` /
    `BE_RetryEsgotado`) — SAME precedent as the auth/cancel probes' allowlists. Currently INERT
    (FINDING B): no code path in `ans_submit.py` raises either as a `WorkerBpmnError` today.

FINDINGS (grep-confirmed on this v2 main; see PR body / evidence-ledger for full detail). Two are
genuinely NEW (not previously documented in the auth/cancel/escalation port ledger) and are the
dominant reason most of this suite's tests are blocked:

  FINDING A (NEW — registration gap, blocks the MAJORITY of this file's tests): the BPMN topic
  `regulatorio.anssubmit.notify_regulatorio` is declared by TWO service tasks —
  `ST_PrepararDossie` (spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn, ~line
  258-263, "Preparar dossie de envio (agente Gustavo)") and `ST_NotificarDeadlineRisk` (~line
  294-306, shared by ALL THREE deadline-risk boundary timers: `BT_DeadlineRisk` on
  `UT_RevisarEnvio`, `BT_DeadlineRiskPendencia` on `UT_CorrigirPendenciaEnvio`,
  `BT_DeadlineRiskJuridico` on `UT_RevisarEnvioJuridico`). `ans_submit.py`'s
  `register_ans_submit_workers` (lines ~444-487) registers EXACTLY 6 topics — assemble, validate,
  submit, track_protocol, retransmit, publish_completed — NOT notify_regulatorio. The module's own
  docstring (lines ~344-356) documents this as a known, undisguised gap ("Spec topic with NO
  implementing function today (gap, not fabricated here): notify_regulatorio"). This is a genuine
  v1->v2 REGRESSION, not a fixture-serving choice like auth's `_AnalyzeRequestStub`: the v1 donor
  itself registers a REAL worker for this topic (donor file's own dataclass docstring: "sem stub —
  todos os topicos teem worker real"; donor's `test_happy_path_envio_aprovado_e_acked` asserts
  `ans_probe.notifications_of_type("anssubmit.notify_regulatorio")` is non-empty).
  Consequence: `harness.py`'s `_handle` reports a task on an unregistered topic as an immediate
  incident (`"no handler registered for topic %r"`, `retries_override=0`) — the external task
  itself never completes, so the process instance stalls BEFORE `ST_PrepararDossie` /
  `ST_NotificarDeadlineRisk` ever advance. `_ANS_SUBMIT_WORKER_TOPICS` below still LISTS
  `_NOTIFY_TOPIC` (mirrors the donor's 1:1 topic/worker expectation and the BPMN's own
  declaration) so any task landing there surfaces as a LOUD incident rather than silently
  starving un-polled forever (`harness.py` design: "a task with no handler must not vanish").
  Blocks EVERY test whose flow reaches `ST_PrepararDossie` (the DEFAULT `Flow_GW_Revisao` branch
  out of `GW_Admissibilidade` — i.e. any `SEGUE_ENVIO`/`REVISAO_HUMANA` routing, NOT `PENDENTE` or
  `nip_filing`, which bypass it) or fires ANY of the three shared deadline-risk boundary timers.
  Does NOT block: the `nip_filing` route (`Flow_GW_NipFiling` goes DIRECTLY to
  `UT_RevisarEnvioJuridico`, skipping `ST_PrepararDossie`) nor the two INTERRUPTIVE due-date
  boundary timers (`BT_DueDateJuridico`/`BT_DueDatePendencia` route DIRECTLY to
  `UT_CoordenacaoEnvioAssume`, never through `ST_NotificarDeadlineRisk`) — these paths are left
  UNMARKED below (genuinely fine, verified by tracing the BPMN's own sequence flows).

  FINDING B (NEW — the entire NACK/retry subprocess is unreachable): `transmit_to_ans`
  (ans_submit.py) never raises anything representing `ERR_ANS_PROTOCOLO_NACK` — it has NO
  `status_envio` parameter and NO branch producing a NACK outcome; once the human-approval guard
  passes it UNCONDITIONALLY returns `status_envio="enviado"`. The donor's seeded
  `status_envio="nack"` override (silently dropped by `pick_fields` — not a field of
  `AnsSubmissionData`/`AnsSubmitDecision`) can therefore never influence v2's worker. Consequence:
  `BE_SubmitNack` (boundary on `ST_SubmeterEnvio`, catches `Error_AnsProtocoloNack`) NEVER fires,
  so `SUB_RetryEnvio` (the whole retry/backoff subprocess: `ST_RetransmitirEnvio`,
  `BRT_RetryPolicy`/`ans_retry_policy` DMN, `ICE_Backoff`, `End_RetryOk`/`End_RetryEsgotado`,
  `BE_RetryEsgotado`, `ST_PublishFailed`, `UT_TratarNack`) is dead code from a live-engine
  perspective. Independently, EVEN IF that boundary somehow fired: `AnsRetryEsgotadoError`
  (ans_submit.py, a bare `RuntimeError` subclass) is NOT a `harness.WorkerBpmnError` — the harness
  classifies bare `RuntimeError` as TRANSIENT (`_handle`'s `_transient_types`) and engine-retries
  it instead of reporting a modeled `bpmnError`, so `BE_RetryEsgotado` (catches
  `Error_AnsRetryEsgotado`) could not fire either. Two independent breaks in the same subprocess.

  FINDING C (systemic, same class as auth/cancel/escalation's residual — Step-3 fact #1): none of
  `ans_submit.py`'s dict-boundary entry functions (`assemble_entry`/`validate_entry`/
  `submit_entry`/`track_protocol_entry`/`retransmit_entry`/`publish_completed_entry`) call
  `kafka.publish` themselves (`kafka` is threaded through for signature parity only, `del kafka  #
  unused` in every one). The donor's PER-WORKER notification pattern
  (`ans_probe.notifications_of_type("anssubmit.submit")` etc.) can therefore never observe an
  execution — DISTINCT from the domain-event assertions (`has_event`/`events_on`,
  `agents.events.anssubmit.*`), which DO work: those flow through the GENERIC
  `operadora.events.publish` handler (`maezo.tools.workers.events.make_publish_event_handler`),
  which DOES call `kafka.publish` (Step-3 fact #1: the ONE call site,
  `src/maezo/tools/workers/events.py:247`). Every xfail below whose test would ALSO hit this gap
  (even after FINDING A is fixed) notes it explicitly; no test in this file fails SOLELY on this
  finding (FINDING A always blocks first), so it does not get its own top-level xfail marker.

  FINDING D (NEW — a guard the donor tests for does not exist in v2 at all): the donor's
  `make_assemble_handler` raises `ERR_ANS_DATASET_INCOMPLETO` when `dataset_assembly_failed=True`
  (montagem falhou na origem). v2's `AnsSubmitInput` dataclass (ans_submit.py) has NO
  `dataset_assembly_failed` field at all, and `prepare_submission`/`assemble_entry` never raise
  `AnsDatasetIncompletoError` for ANY input shape — that exception class is raised ONLY by
  `track_protocol_entry`/`retransmit_entry`, for a completely different condition (blank
  `protocolo_ans`). The assembly-failure guard itself is simply MISSING, not merely
  unreachable/misrouted like A/B.

  D-07 / ceilings (Step-3 fact #2, confirmed inapplicable): `CeilingResolver` is used only by
  auth.py/pagto.py/reembolso.py — ans_submit.py has no ceiling dependency, so D-07 is not cited
  here.

  ans_calendar DMN taxonomy mismatch (Step-3 fact #4) does NOT apply to this file: that mismatch is
  specific to `ans_cron.py`'s own Python re-implementation of `check_calendar`
  (`_REPORT_PERIODICIDADE`'s literals vs the DMN's RN-citation literals) — a function this file
  never calls. SUBMIT's OWN `BRT_Calendario` business-rule task evaluates `ans_calendar` NATIVELY
  via the engine using the SAME RN-citation literals (`RN_124_SIP`/`RN_209_UTILIZACAO`/
  `RN_388_QUALIDADE`/`RN_424_TISS_MONITORAMENTO`/`DIOPS_TRIMESTRAL`) this file's own `start_ans`
  fixture and `test_golden_por_report_type`/`test_catch_all_calendar_roteia_para_humano` already
  use — NO taxonomy mismatch for SUBMIT. Do not conflate the two; see the sibling
  `test_sp_op_ans_cron_001.py` for the ans_cron-specific finding.

Synthetic data mirrors the donor's obviously-fake identifiers exactly (`revisor-sintetico-001`,
`DATASET-TESTE-0001`, `NIP-TESTE-0001`, etc.).
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

from maezo.tools.workers.ans_submit import (
    AnsDatasetIncompletoError,
    AnsSubmitNotHumanError,
    assemble_entry,
    register_ans_submit_workers,
    submit_entry,
)
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn"
_DMN_CALENDAR = _REPO / "spec/processes/dmn/ans_calendar.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/ans_sla.dmn"
_DMN_ADMISS = _REPO / "spec/processes/dmn/ans_submission_admissibility.dmn"
_DMN_RETRY = _REPO / "spec/processes/dmn/ans_retry_policy.dmn"

# External task topics do contrato SP-OP-ANS-SUBMIT-001.
_PUBLISH_TOPIC = "operadora.events.publish"
_ASSEMBLE_TOPIC = "regulatorio.anssubmit.assemble"
_VALIDATE_TOPIC = "regulatorio.anssubmit.validate"
_SUBMIT_TOPIC = "regulatorio.anssubmit.submit"
_RETRANSMIT_TOPIC = "regulatorio.anssubmit.retransmit"
_TRACK_TOPIC = "regulatorio.anssubmit.track_protocol"
_NOTIFY_TOPIC = "regulatorio.anssubmit.notify_regulatorio"

# Topicos "servidos" pelo drain generico. `_NOTIFY_TOPIC` NAO tem worker real registrado
# (FINDING A no docstring do modulo) — listado aqui mesmo assim (mirrors a declaracao do BPMN e a
# expectativa 1:1 do donor) para que qualquer task que caia nele produza um INCIDENTE alto-falante
# (harness.py: "no handler registered for topic") em vez de ficar presa sem nunca ser buscada.
_ANS_SUBMIT_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _ASSEMBLE_TOPIC,
    _VALIDATE_TOPIC,
    _SUBMIT_TOPIC,
    _RETRANSMIT_TOPIC,
    _TRACK_TOPIC,
    _NOTIFY_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

# Eventos de dominio (contrato SP-OP-ANS-SUBMIT-001.md) — publicados via o handler GENERICO
# `operadora.events.publish` (funciona — FINDING C no docstring do modulo).
_ANS_SUBMITTED = "agents.events.anssubmit.submitted"
_ANS_ACKED = "agents.events.anssubmit.acked"
_ANS_DEADLINE_RISK = "agents.events.anssubmit.deadline_risk"
_ANS_FAILED = "agents.events.anssubmit.failed"
_ANS_COMPLETED = "agents.events.anssubmit.completed"

# User Task definition keys (BPMN)
_UT_REVISAR = "UT_RevisarEnvio"
_UT_REVISAR_JURIDICO = "UT_RevisarEnvioJuridico"
_UT_CORRIGIR = "UT_CorrigirPendenciaEnvio"
_UT_COORDENACAO = "UT_CoordenacaoEnvioAssume"
_UT_TRATAR_NACK = "UT_TratarNack"

# End events de envio efetivo (so alcancaveis apos UT humana de aprovacao)
_END_ENVIADO_ACK = "End_EnviadoAck"
_END_ENVIADO_PENDENTE_ACK = "End_EnviadoPendenteAck"
_END_ADIADO = "End_AdiadoHumano"

# Service task que materializa o ato vinculante (so co-ocorre com UT humana de aprovacao).
_ST_SUBMETER = "ST_SubmeterEnvio"

_SUB_RETRY = "SUB_RetryEnvio"
_ST_RETRANSMITIR = "ST_RetransmitirEnvio"
_ICE_BACKOFF = "ICE_Backoff"
_END_RETRY_ESGOTADO = "End_RetryEsgotado"

# User Tasks humanas que podem produzir a aprovacao do envio (invariante HITL pre-filing).
_UT_HUMANAS_APROVACAO = frozenset({_UT_REVISAR, _UT_REVISAR_JURIDICO, _UT_COORDENACAO})

# Terminais de envio efetivo (ato vinculante consumado).
_ENDS_ENVIO = frozenset({_END_ENVIADO_ACK, _END_ENVIADO_PENDENTE_ACK})

# FINDING A (registration gap — see module docstring for full grep evidence). Also notes FINDING C
# (systemic per-worker-notification kafka gap) as a compounding cause for any assertion this test
# would ALSO hit on `notifications_of_type(...)` even if A were fixed.
_NOTIFY_REGULATORIO_GAP_REASON = (
    "T3.1 phase-2 FINDING A (new, registration gap): BPMN topic "
    "regulatorio.anssubmit.notify_regulatorio (ST_PrepararDossie / ST_NotificarDeadlineRisk, "
    "spec/processes/bpmn/SP-OP-ANS-SUBMIT-001_Envios_Periodicos_ANS.bpmn) has NO worker registered "
    "in register_ans_submit_workers (ans_submit.py registers exactly 6 topics — assemble/validate/"
    "submit/track_protocol/retransmit/publish_completed — confirmed by reading the function; its "
    "own docstring documents the gap: 'Spec topic with NO implementing function today'). "
    "harness.py's _handle reports an immediate incident for any task on an unregistered topic, so "
    "the instance never advances past ST_PrepararDossie (default Flow_GW_Revisao branch) or past "
    "a shared deadline-risk boundary timer. This is a genuine v1->v2 regression: the donor's own "
    "worker for this topic is real ('sem stub — todos os topicos teem worker real'). Where this "
    "test would ALSO assert a per-worker notification (notifications_of_type('anssubmit."
    "notify_regulatorio'/'anssubmit.submit'/etc.)) even after A is fixed, that assertion hits the "
    "SEPARATE systemic FINDING C (no ans_submit.py entry function calls kafka.publish; only the "
    "generic operadora.events.publish handler does) — noted here, not double-marked."
)

# FINDING B (new — NACK/retry subprocess entirely unreachable). See module docstring for the full
# two-part evidence (no NACK branch in transmit_to_ans; AnsRetryEsgotadoError is a bare
# RuntimeError, not a WorkerBpmnError, so BE_RetryEsgotado could not fire even hypothetically).
_SUBMIT_NACK_UNREACHABLE_REASON = (
    "T3.1 phase-2 FINDING B (new): ans_submit.py's transmit_to_ans has no status_envio parameter "
    "and no NACK-producing branch — once the human-approval guard passes it unconditionally "
    "returns status_envio='enviado'. The donor's seeded status_envio='nack' override is silently "
    "dropped by pick_fields (not a field of AnsSubmissionData/AnsSubmitDecision). Consequence: "
    "BE_SubmitNack (boundary on ST_SubmeterEnvio, errorRef Error_AnsProtocoloNack) never fires, so "
    "the entire SUB_RetryEnvio subprocess (ST_RetransmitirEnvio/BRT_RetryPolicy/ICE_Backoff/"
    "End_RetryOk/End_RetryEsgotado/BE_RetryEsgotado/ST_PublishFailed/UT_TratarNack) is dead code "
    "from a live-engine perspective. Independently: AnsRetryEsgotadoError is a bare RuntimeError, "
    "not a harness.WorkerBpmnError, so even a hypothetical NACK entry into the retry subprocess "
    "could not route ERR_ANS_RETRY_ESGOTADO through BE_RetryEsgotado's boundary catch (the harness "
    "classifies bare RuntimeError as transient/engine-retried, never a modeled bpmnError). This "
    "test is ALSO blocked earlier by FINDING A (_drive_to_revisar never reaches UT_RevisarEnvio) — "
    "cited together since B is the independent, deeper root cause that would remain even if A were "
    "fixed."
)

# FINDING D (new — guard genuinely missing in v2, not merely misrouted).
_ASSEMBLE_FAILURE_GUARD_MISSING_REASON = (
    "T3.1 phase-2 FINDING D (new): the donor's assemble worker raises ERR_ANS_DATASET_INCOMPLETO "
    "when dataset_assembly_failed=True (montagem falhou na origem). v2's AnsSubmitInput dataclass "
    "(ans_submit.py) has NO dataset_assembly_failed field at all, and prepare_submission/"
    "assemble_entry never raise AnsDatasetIncompletoError for ANY input shape — that exception is "
    "raised ONLY by track_protocol_entry/retransmit_entry, for an unrelated condition (blank "
    "protocolo_ans). The assembly-failure guard itself is MISSING in v2, not misrouted like "
    "FINDING A/B; src/** fix (adding the field + the raise) is out of scope for this porting task."
)


@dataclass
class AnsEngineProbe:
    """Driva os workers reais de ans_submit contra o engine CIB Seven."""

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
            self.transport, self.harness, self.worker_id, _ANS_SUBMIT_WORKER_TOPICS, rounds=rounds
        )


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 4 DMN de envio ANS da arvore no engine real (todos os 4 referenciados por
    decisionRef — verificado por grep, module docstring)."""
    return await engine.deploy(
        _BPMN, _DMN_CALENDAR, _DMN_SLA, _DMN_ADMISS, _DMN_RETRY, name="SP-OP-ANS-SUBMIT-001-qa"
    )


@pytest_asyncio.fixture
async def ans_probe(engine: EngineRest) -> AsyncIterator[AnsEngineProbe]:
    """Probe que serve as external tasks com os workers reais de envio ANS."""
    worker_id = f"qa-anssubmit-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # bpmn_error_allowlist mirrors the 2 bpmn:error codes SUBMIT's BPMN declares a boundary catch
    # for (BE_SubmitNack / BE_RetryEsgotado) — currently INERT, see FINDING B in the module
    # docstring (no code path raises either as a WorkerBpmnError today); kept for fidelity.
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        lock_duration_ms=10_000,
        bpmn_error_allowlist=frozenset({"ERR_ANS_PROTOCOLO_NACK", "ERR_ANS_RETRY_ESGOTADO"}),
    )
    kafka = FakeKafkaPublisher()
    dmn = CibSevenDmnTransport(CIBSEVEN_BASE_URL)
    register_ans_submit_workers(harness, kafka, dmn=dmn)
    # The generic operadora.events.publish worker every ST_Publish* service task in this BPMN
    # routes through — mirrors the donor's own register_phase0_workers composition.
    register_events_workers(harness, kafka)
    probe = AnsEngineProbe(
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


@pytest_asyncio.fixture
async def start_ans(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key ANSSUB-amh-{report_type}-{competencia} e payload
    canonico. Overrides via kwargs. Dados sinteticos obvios (tenant amh, RN_124_SIP, 2026-01).
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        report_type = overrides.pop("report_type", "RN_124_SIP")
        competencia = overrides.pop("competencia", "2026-01")
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "report_type": report_type,
            "competencia": competencia,
            "periodicidade": "mensal",
            "origem_envio": "calendario",
            "dataset_ref": "DATASET-TESTE-0001",
            "dataset_complete": True,
            "schema_valid": True,
            "lgpd_anonimizado": True,
        }
        variables.update(overrides)
        business_key = f"ANSSUB-amh-{report_type}-{competencia}"
        return await engine.start_by_key("SP-OP-ANS-SUBMIT-001", business_key, variables)

    return _start


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _await_end(engine: EngineRest, iid: str, *, attempts: int = 60, delay: float = 0.25) -> set[str]:
    """Espera a instancia terminar e retorna as atividades historicas atingidas."""
    for _ in range(attempts):
        state = await engine.history_state(iid)
        if state == "COMPLETED":
            return await engine.activity_instances_ended(iid)
        await asyncio.sleep(delay)
    return await engine.activity_instances_ended(iid)


async def _assert_no_filing_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE HITL pre-filing: prova que o ato vinculante (submit / terminal de envio) nao
    existe sem uma User Task humana de aprovacao."""
    ended = await engine.activity_instances_ended(iid)
    filing_in_history = (_ST_SUBMETER in ended) or bool(ended & _ENDS_ENVIO)
    if filing_in_history:
        human_tasks_in_history = ended & _UT_HUMANAS_APROVACAO
        assert human_tasks_in_history, (
            f"INVARIANTE HITL VIOLADA: filing ANS (submit/terminal de envio) atingido para "
            f"instancia {iid} SEM nenhuma User Task humana de aprovacao no historico. "
            f"User Tasks esperadas (qualquer uma de): {_UT_HUMANAS_APROVACAO}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de envio — violacao do HITL pre-filing (ADR-0007)."
        )


async def _drive_to_revisar(engine: EngineRest, probe: AnsEngineProbe, iid: str) -> Any:
    """Drena ate UT_RevisarEnvio surgir (dossie preparado pelo worker real)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_REVISAR)


async def _aprovar_envio(
    engine: EngineRest, ut_id: str, *, revisor_id: str = "revisor-sintetico-001"
) -> None:
    """Completa a User Task de revisao como humano com APROVAR_ENVIO + revisor_id."""
    await engine.complete_task_as_human(ut_id, {"decisao_envio": "APROVAR_ENVIO", "revisor_id": revisor_id})


async def _correlate_ack(business_key: str, protocolo_ans: str = "ANS-TESTE-0001") -> None:
    """Correlaciona msg.anssubmit.ack_received (ACK assincrono da ANS) por business key."""
    correlate_payload = {
        "messageName": "msg.anssubmit.ack_received",
        "businessKey": business_key,
        "processVariables": {
            "status_envio": {"value": "ack", "type": "String"},
            "protocolo_ans": {"value": protocolo_ans, "type": "String"},
        },
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.anssubmit.ack_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )


# ===========================================================================
# INVARIANTE HITL pre-filing — DoD deliverable (varredura de inputs da DMN admissibilidade)
# ===========================================================================


async def test_submit_exige_user_task_humana(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """INVARIANTE HITL pre-filing: NENHUM caminho automatizado transmite a ANS.

    Varredura de TODAS as combinacoes de input da DMN ans_submission_admissibility (2*2*2 = 8).
    Sobrevive a FINDING A (module docstring): as 3 combinacoes que roteiam SEGUE_ENVIO/
    REVISAO_HUMANA ficam presas em ST_PrepararDossie (nunca alcancam ST_SubmeterEnvio de qualquer
    forma); as 5 que roteiam PENDENTE alcancam UT_CorrigirPendenciaEnvio sem depender de
    notify_regulatorio (Flow_GW_Pendente sai direto de GW_Admissibilidade). Em ambos os casos a
    invariante (nunca transmite) se mantem — nao depende de notifications_of_type().
    """
    bools = [True, False]
    checked = 0

    for complete, schema, lgpd in itertools.product(bools, bools, bools):
        inst = await start_ans(
            competencia=f"2026-SCAN-{checked:02d}",
            dataset_complete=complete,
            schema_valid=schema,
            lgpd_anonimizado=lgpd,
        )
        iid = inst["id"]
        await ans_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        assert _ST_SUBMETER not in ended, (
            f"HITL VIOLADO: complete={complete} schema={schema} lgpd={lgpd} executou "
            f"ST_SubmeterEnvio automaticamente. ended={ended}"
        )
        assert not (ended & _ENDS_ENVIO), (
            f"HITL VIOLADO: complete={complete} schema={schema} lgpd={lgpd} atingiu terminal de "
            f"envio automaticamente. ended={ended}"
        )
        await _assert_no_filing_without_human_task(engine, iid)
        checked += 1

    assert checked == 8, f"Esperava 8 combinacoes varridas; varri {checked}"


async def test_dados_faltantes_roteiam_para_humano_nunca_auto_rejeita(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """dataset_complete=false => admissibilidade -> PENDENTE -> UT_CorrigirPendenciaEnvio.

    Nao depende de notify_regulatorio (Flow_GW_Pendente bypassa ST_PrepararDossie) — nao afetado
    por FINDING A.

    PORT FIX (T3.1 phase-2, test-only bug — src/** untouched): `dataset_ref` MUST also be
    cleared. `start_ans`'s default payload seeds `dataset_ref="DATASET-TESTE-0001"` (a truthy
    stub); `ans_submit.py::prepare_submission` (line ~133) computes
    `dataset_complete = input_data.dataset_complete or bool(input_data.dataset_ref)` — i.e. it
    RECOMPUTES dataset_complete, and a non-empty dataset_ref forces it back to True regardless of
    the seeded `dataset_complete=False` override, so GW_Admissibilidade never sees a false
    `dataset_complete` and routes SEGUE_ENVIO/REVISAO_HUMANA (-> ST_PrepararDossie, topic
    `regulatorio.anssubmit.notify_regulatorio`, unregistered per FINDING A) instead of PENDENTE.
    Confirmed live: without this fix the instance stalls, `UT_CorrigirPendenciaEnvio` never
    appears. Clearing `dataset_ref` here is a straight port/fixture-usage fix (mirrors the
    `_MEASURE_GAP_OVERRIDES_SEEDED_FACTS_REASON` pattern already documented in adequacao), not a
    weakening of this test's intent (still exercises the PENDENTE/dados-faltantes path).
    """
    inst = await start_ans(competencia="2026-PEND", dataset_complete=False, dataset_ref="")
    iid = inst["id"]

    await ans_probe.drain()
    ut = await engine.await_user_task(iid, _UT_CORRIGIR)
    assert "regulatorio-ans" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Dado faltante nao deve transmitir (HITL)"
    assert not (ended & _ENDS_ENVIO), "Dado faltante nao deve atingir terminal de envio"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_aprovar_envio_exige_revisor_id(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """APROVAR_ENVIO sem revisor_id => worker submit recusa (ERR_ANS_SUBMIT_NOT_HUMAN)."""
    inst = await start_ans(competencia="2026-NOREV")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_envio": "APROVAR_ENVIO"})
    await ans_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ENVIO), "Aprovacao sem revisor_id NAO pode atingir terminal de envio"
    submits = ans_probe.notifications_of_type("anssubmit.submit")
    assert not submits, "submit NAO deve transmitir sem revisor_id"
    await _assert_no_filing_without_human_task(engine, iid)


async def test_nip_filing_exige_revisao_juridica(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """origem_envio=nip_filing => revisao roteada a juridico-regulatorio (texto legal sempre humano).

    Flow_GW_NipFiling vai DIRETO a UT_RevisarEnvioJuridico (bypassa ST_PrepararDossie) — nao
    afetado por FINDING A.
    """
    inst = await start_ans(
        competencia="2026-NIP", origem_envio="nip_filing", nip_protocolo_origem="NIP-TESTE-0001"
    )
    iid = inst["id"]

    await ans_probe.drain()
    ut = await engine.await_user_task(iid, _UT_REVISAR_JURIDICO)
    assert "juridico-regulatorio" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "nip_filing nao transmite antes da revisao juridica"
    await _assert_no_filing_without_human_task(engine, iid)

    await _aprovar_envio(engine, ut.id, revisor_id="juridico-sintetico-001")
    await ans_probe.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_ACK in ended, f"nip_filing aprovado juridicamente => enviado_ack. ended={ended}"
    await _assert_no_filing_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_happy_path_envio_aprovado_e_acked(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Humano aprova o envio (APROVAR_ENVIO + revisor_id); ACK correlacionado => End_EnviadoAck."""
    inst = await start_ans(competencia="2026-HAPPY")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    assert "regulatorio-ans" in ut.candidate_groups

    dossiers = ans_probe.notifications_of_type("anssubmit.notify_regulatorio")
    assert dossiers, "Worker notify_regulatorio (dossie) deve ter sido executado"

    await _aprovar_envio(engine, ut.id)
    await ans_probe.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_filing_without_human_task(engine, iid)
    assert _END_ENVIADO_ACK in ended, f"Envio aprovado+ACK deve atingir End_EnviadoAck. ended={ended}"
    assert ans_probe.has_event(_ANS_SUBMITTED)
    assert ans_probe.has_event(_ANS_ACKED)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="enviado_ack")

    submits = ans_probe.notifications_of_type("anssubmit.submit")
    assert submits, "Worker submit deve ser executado apos aprovacao humana"
    s = submits[0]
    assert s["decisao_envio"] == "APROVAR_ENVIO"
    assert s["revisor_id"] == "revisor-sintetico-001"
    assert s["protocolo_ans"].startswith("ANS")


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
@pytest.mark.parametrize(
    ("report_type", "competencia", "periodicidade"),
    [
        ("RN_124_SIP", "2026-01", "mensal"),
        ("RN_209_UTILIZACAO", "2026-01", "mensal"),
        ("RN_388_QUALIDADE", "2026-01", "anual"),
        ("RN_424_TISS_MONITORAMENTO", "2026-01", "mensal"),
        ("DIOPS_TRIMESTRAL", "2026-Q1", "trimestral"),
    ],
)
async def test_golden_por_report_type(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
    report_type: str,
    competencia: str,
    periodicidade: str,
) -> None:
    """Golden por report_type: ans_calendar resolve prazos coerentes; happy path ate enviado_ack."""
    inst = await start_ans(report_type=report_type, competencia=competencia, periodicidade=periodicidade)
    iid = inst["id"]
    assert inst["businessKey"] == f"ANSSUB-amh-{report_type}-{competencia}"

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    assert "regulatorio-ans" in ut.candidate_groups

    await _aprovar_envio(engine, ut.id)
    await ans_probe.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_ACK in ended, f"{report_type} {competencia} => enviado_ack. ended={ended}"
    await _assert_no_filing_without_human_task(engine, iid)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="enviado_ack")


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_ack_pendente_completa_como_enviado_pendente_ack(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Envio transmitido mas ACK nao chegou; ICE_AguardarAck (P1D) expira => enviado_pendente_ack."""
    inst = await start_ans(competencia="2026-PENDACK")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    await _aprovar_envio(engine, ut.id)
    await ans_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_AguardarAck")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_PENDENTE_ACK in ended, (
        f"ACK ausente no prazo => End_EnviadoPendenteAck. ended={ended}"
    )
    await _assert_no_filing_without_human_task(engine, iid)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="enviado_pendente_ack")


# ===========================================================================
# Pendencia de dados / adiamento
# ===========================================================================


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_corrigir_pendencia_reavalia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """dataset_complete=false => PENDENTE -> UT_CorrigirPendenciaEnvio; corrigido => reavalia.

    Alcanca UT_CorrigirPendenciaEnvio (nao bloqueado), mas a reavaliacao pos-correcao roteia
    SEGUE_ENVIO/REVISAO_HUMANA -> ST_PrepararDossie -> UT_RevisarEnvio, BLOQUEADO por FINDING A.
    """
    inst = await start_ans(competencia="2026-CORR", dataset_complete=False)
    iid = inst["id"]

    await ans_probe.drain()
    ut_corrigir = await engine.await_user_task(iid, _UT_CORRIGIR)
    assert "regulatorio-ans" in ut_corrigir.candidate_groups

    await engine.complete_task_as_human(ut_corrigir.id, {"dataset_complete": True})
    await ans_probe.drain()

    ut_revisar = await engine.await_user_task(iid, _UT_REVISAR)
    assert "regulatorio-ans" in ut_revisar.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Correcao nao transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_lgpd_nao_anonimizado_roteia_direto_a_revisao_humana(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """lgpd_anonimizado=false (com dataset_complete/schema_valid=true) => REVISAO_HUMANA (rule
    r_revisao_lgpd) => Flow_GW_Revisao (default) => ST_PrepararDossie => UT_RevisarEnvio DIRETO —
    BLOQUEADO por FINDING A (mesma rota default de ST_PrepararDossie que o happy path).
    """
    inst = await start_ans(competencia="2026-LGPDREV", lgpd_anonimizado=False)
    iid = inst["id"]

    await ans_probe.drain()
    ut_revisar = await engine.await_user_task(iid, _UT_REVISAR)
    assert "regulatorio-ans" in ut_revisar.candidate_groups
    assert await engine.get_variable(iid, "lgpd_anonimizado") is False

    ended = await engine.activity_instances_ended(iid)
    assert _UT_CORRIGIR not in ended, (
        "lgpd_anonimizado=false sozinho nao deve rotear a UT_CorrigirPendenciaEnvio"
    )
    assert _ST_SUBMETER not in ended, "Sem decisao humana, nao deve transmitir (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_corrigir_pendencia_lgpd_anonimizado_reavalia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """GAP-ANS-5: lgpd_anonimizado=false => REVISAO_HUMANA => UT_RevisarEnvio (BLOQUEADO por
    FINDING A antes mesmo da primeira UT_RevisarEnvio aparecer)."""
    inst = await start_ans(competencia="2026-CORRLGPD", lgpd_anonimizado=False)
    iid = inst["id"]

    await ans_probe.drain()
    ut_revisar_1 = await engine.await_user_task(iid, _UT_REVISAR)
    assert "regulatorio-ans" in ut_revisar_1.candidate_groups

    await engine.complete_task_as_human(ut_revisar_1.id, {"decisao_envio": "CORRIGIR_PENDENCIA"})
    await ans_probe.drain()

    ut_corrigir = await engine.await_user_task(iid, _UT_CORRIGIR)
    assert "regulatorio-ans" in ut_corrigir.candidate_groups

    await engine.complete_task_as_human(ut_corrigir.id, {"lgpd_anonimizado": True})
    await ans_probe.drain()

    ut_revisar_2 = await engine.await_user_task(iid, _UT_REVISAR)
    assert "regulatorio-ans" in ut_revisar_2.candidate_groups
    assert await engine.get_variable(iid, "lgpd_anonimizado") is True

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Correcao nao transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_adiar_envio_registra_justificativa(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Humano completa ADIAR_ENVIO => End_AdiadoHumano; submit NAO executado."""
    inst = await start_ans(competencia="2026-ADIAR")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_envio": "ADIAR_ENVIO",
            "justificativa_adiamento": "Aguardando reconciliacao de competencia (teste)",
            "revisor_id": "revisor-sintetico-001",
        },
    )
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ADIADO in ended, f"ADIAR_ENVIO => End_AdiadoHumano. ended={ended}"
    assert _ST_SUBMETER not in ended, "Adiamento nao transmite a ANS"
    assert not (ended & _ENDS_ENVIO)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="adiado_humano")
    await _assert_no_filing_without_human_task(engine, iid)


# ===========================================================================
# Timers de calendario / SLA
# ===========================================================================


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_dmn_ans_calendar_resolve_due_date(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """report_type=RN_124_SIP => ans_calendar resolve due_date; ans_sla resolve duracoes; timers
    existem. NAO relacionado a taxonomia (module docstring) — bloqueado por FINDING A."""
    inst = await start_ans(competencia="2026-CAL")
    iid = inst["id"]

    await _drive_to_revisar(engine, ans_probe, iid)

    job_alerta = await engine.await_timer_job(iid, "BT_DeadlineRisk")
    assert job_alerta.activity_id == "BT_DeadlineRisk"
    job_due = await engine.await_timer_job(iid, "BT_DueDate")
    assert job_due.activity_id == "BT_DueDate"


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_timer_deadline_risk_nao_interruptivo(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DeadlineRisk (nao-interruptivo): notify_regulatorio recebe task; UT segue aberta."""
    inst = await start_ans(competencia="2026-DLRISK")
    iid = inst["id"]

    await _drive_to_revisar(engine, ans_probe, iid)

    job = await engine.await_timer_job(iid, "BT_DeadlineRisk")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    assert ans_probe.has_event(_ANS_DEADLINE_RISK), "anssubmit.deadline_risk deve ser publicado"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_timer_due_date_coordenacao_assume(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DueDate (interruptivo): UT_RevisarEnvio cancelada; UT_CoordenacaoEnvioAssume criada."""
    inst = await start_ans(competencia="2026-DUEDATE")
    iid = inst["id"]

    await _drive_to_revisar(engine, ans_probe, iid)

    job = await engine.await_timer_job(iid, "BT_DueDate")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-regulatorio" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR not in open_keys, "UT_RevisarEnvio deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Estouro de prazo nunca transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_coordenacao_assume_e_aprova(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Prazo estourado; coordenacao assume e APROVAR_ENVIO => filing apos UT humana (invariante)."""
    inst = await start_ans(competencia="2026-COORD")
    iid = inst["id"]

    await _drive_to_revisar(engine, ans_probe, iid)
    job = await engine.await_timer_job(iid, "BT_DueDate")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await _aprovar_envio(engine, ut_coord.id, revisor_id="coordenacao-sintetica-001")
    await ans_probe.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_ACK in ended
    await _assert_no_filing_without_human_task(engine, iid)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="enviado_ack")


# ===========================================================================
# GAP-ANS-4: UT_RevisarEnvioJuridico / UT_CorrigirPendenciaEnvio boundary SLA timers (mirror dos
# timers de UT_RevisarEnvio). O timer NAO-INTERRUPTIVO converge no MESMO ST_NotificarDeadlineRisk
# (bloqueado por FINDING A); o timer INTERRUPTIVO vai DIRETO a UT_CoordenacaoEnvioAssume (nao
# bloqueado).
# ===========================================================================


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_timer_deadline_risk_nao_interruptivo_juridico(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DeadlineRiskJuridico (nao-interruptivo): converge em ST_NotificarDeadlineRisk
    (topico compartilhado regulatorio.anssubmit.notify_regulatorio) — BLOQUEADO por FINDING A,
    mesmo a UT_RevisarEnvioJuridico em si NAO precisando de notify_regulatorio para ser criada.
    """
    inst = await start_ans(
        competencia="2026-DLRJUR", origem_envio="nip_filing", nip_protocolo_origem="NIP-TESTE-DLR"
    )
    iid = inst["id"]

    await ans_probe.drain()
    await engine.await_user_task(iid, _UT_REVISAR_JURIDICO)

    job = await engine.await_timer_job(iid, "BT_DeadlineRiskJuridico")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    assert ans_probe.has_event(_ANS_DEADLINE_RISK), "anssubmit.deadline_risk deve ser publicado"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR_JURIDICO in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_due_date_coordenacao_assume_juridico(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DueDateJuridico (interruptivo): vai DIRETO a UT_CoordenacaoEnvioAssume
    (Flow_DueDateJuridico_UTCoord) — NAO passa por ST_NotificarDeadlineRisk, logo NAO bloqueado por
    FINDING A (verificado tracando o BPMN)."""
    inst = await start_ans(
        competencia="2026-DDJUR", origem_envio="nip_filing", nip_protocolo_origem="NIP-TESTE-DD"
    )
    iid = inst["id"]

    await ans_probe.drain()
    await engine.await_user_task(iid, _UT_REVISAR_JURIDICO)

    job = await engine.await_timer_job(iid, "BT_DueDateJuridico")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-regulatorio" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_REVISAR_JURIDICO not in open_keys, "UT_RevisarEnvioJuridico deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Estouro de prazo nunca transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)

    await _aprovar_envio(engine, ut_coord.id, revisor_id="coordenacao-sintetica-002")
    await ans_probe.drain()
    await _correlate_ack(inst["businessKey"])
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_ENVIADO_ACK in ended
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_timer_deadline_risk_nao_interruptivo_pendencia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DeadlineRiskPendencia (nao-interruptivo): converge em ST_NotificarDeadlineRisk —
    BLOQUEADO por FINDING A, mesmo UT_CorrigirPendenciaEnvio em si sendo alcancavel."""
    inst = await start_ans(competencia="2026-DLRPEND", dataset_complete=False)
    iid = inst["id"]

    await ans_probe.drain()
    await engine.await_user_task(iid, _UT_CORRIGIR)

    job = await engine.await_timer_job(iid, "BT_DeadlineRiskPendencia")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    assert ans_probe.has_event(_ANS_DEADLINE_RISK), "anssubmit.deadline_risk deve ser publicado"

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_CORRIGIR in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_due_date_coordenacao_assume_pendencia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Timer BT_DueDatePendencia (interruptivo): vai DIRETO a UT_CoordenacaoEnvioAssume
    (Flow_DueDatePendencia_UTCoord) — NAO passa por ST_NotificarDeadlineRisk, logo NAO bloqueado
    por FINDING A (verificado tracando o BPMN).

    PORT FIX (T3.1 phase-2, test-only bug — src/** untouched): same `dataset_ref` override
    documented in `test_dados_faltantes_roteiam_para_humano_nunca_auto_rejeita` above
    (`ans_submit.py::prepare_submission` recomputes `dataset_complete = dataset_complete or
    bool(dataset_ref)`, and `start_ans`'s default `dataset_ref` is a truthy stub) — clear it so
    the seeded `dataset_complete=False` actually reaches GW_Admissibilidade and the instance
    reaches UT_CorrigirPendenciaEnvio in the first place.
    """
    inst = await start_ans(competencia="2026-DDPEND", dataset_complete=False, dataset_ref="")
    iid = inst["id"]

    await ans_probe.drain()
    await engine.await_user_task(iid, _UT_CORRIGIR)

    job = await engine.await_timer_job(iid, "BT_DueDatePendencia")
    await engine.execute_job(job.id)
    await ans_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-regulatorio" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_CORRIGIR not in open_keys, "UT_CorrigirPendenciaEnvio deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Estouro de prazo nunca transmite automaticamente (HITL)"
    await _assert_no_filing_without_human_task(engine, iid)


@pytest.mark.xfail(reason=_NOTIFY_REGULATORIO_GAP_REASON, strict=True)
async def test_catch_all_calendar_roteia_para_humano(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """report_type desconhecido => ans_calendar catch-all (fonte_regulatoria=REVISAO_HUMANA);
    admissibilidade roteia SEGUE_ENVIO (facts default true) => ST_PrepararDossie => BLOQUEADO por
    FINDING A."""
    inst = await start_ans(report_type="RELATORIO_TESTE_INVALIDO", competencia="2026-CATCH")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    assert "regulatorio-ans" in ut.candidate_groups

    job = await engine.await_timer_job(iid, "BT_DueDate")
    assert job.activity_id == "BT_DueDate"

    ended = await engine.activity_instances_ended(iid)
    assert _ST_SUBMETER not in ended, "Tipo desconhecido nunca transmite automaticamente"
    await _assert_no_filing_without_human_task(engine, iid)


# ===========================================================================
# Retry / NACK — FINDING B (subprocess inteiro inalcancavel, ver docstring do modulo)
# ===========================================================================


@pytest.mark.xfail(reason=_SUBMIT_NACK_UNREACHABLE_REASON, strict=True)
async def test_nack_entra_em_retry_e_retransmite_sucesso(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """submit -> ERR_ANS_PROTOCOLO_NACK (transitorio) => SUB_RetryEnvio; retransmissao OK =>
    enviado_ack. Bloqueado tanto por FINDING A (UT_RevisarEnvio) quanto por FINDING B (NACK nunca
    dispara)."""
    inst = await start_ans(competencia="2026-NACK-OK", status_envio="nack")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    await _aprovar_envio(engine, ut.id)
    await ans_probe.drain()

    ended_mid = await engine.activity_instances_ended(iid)
    assert _ST_RETRANSMITIR in ended_mid, (
        f"ST_RetransmitirEnvio devia ter executado apos o NACK transitorio. ended={ended_mid}"
    )
    retransmits = ans_probe.notifications_of_type("anssubmit.retransmit")
    assert retransmits, "worker de retransmissao deve ter sido executado no subprocess"
    assert retransmits[0]["status_envio"] == "retransmitido"
    assert retransmits[0]["revisor_id"] == "revisor-sintetico-001"
    assert "decisao_envio" in retransmits[0]

    await _correlate_ack(inst["businessKey"])
    await ans_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_filing_without_human_task(engine, iid)
    assert _SUB_RETRY in ended, f"SUB_RetryEnvio devia constar no historico. ended={ended}"
    assert _END_ENVIADO_ACK in ended, f"retransmissao OK + ACK => End_EnviadoAck. ended={ended}"
    assert ans_probe.has_event(_ANS_SUBMITTED)
    assert ans_probe.has_event(_ANS_COMPLETED, desfecho="enviado_ack")


@pytest.mark.xfail(reason=_SUBMIT_NACK_UNREACHABLE_REASON, strict=True)
async def test_retry_esgotado_roteia_para_humano(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Retransmissao sempre NACK => DMN ans_retry_policy esgota o retry => ERR_ANS_RETRY_ESGOTADO
    => UT_TratarNack (humano) + anssubmit.failed. Bloqueado por FINDING A + FINDING B."""
    inst = await start_ans(competencia="2026-NACK-ESG", status_envio="nack", retransmit_outcome="nack")
    iid = inst["id"]

    ut = await _drive_to_revisar(engine, ans_probe, iid)
    await _aprovar_envio(engine, ut.id)
    await ans_probe.drain()

    backoffs = 0
    for _ in range(6):
        open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
        if _UT_TRATAR_NACK in open_keys:
            break
        job = await engine.await_timer_job(iid, _ICE_BACKOFF, attempts=8, delay=0.25)
        await engine.execute_job(job.id)
        backoffs += 1
        await ans_probe.drain()

    ut_nack = await engine.await_user_task(iid, _UT_TRATAR_NACK)
    assert "regulatorio-ans" in ut_nack.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _ST_RETRANSMITIR in ended, f"ST_RetransmitirEnvio devia ter executado. ended={ended}"
    assert _END_RETRY_ESGOTADO in ended, f"retry esgotado deve atingir End_RetryEsgotado. ended={ended}"
    assert 1 <= backoffs <= 3, f"loop de retry deve ser bounded (esperado ~3 backoffs); foi {backoffs}"

    retransmits = ans_probe.notifications_of_type("anssubmit.retransmit")
    attempts = sorted(int(r["retry_attempt"]) for r in retransmits)
    assert len(retransmits) >= 2, f"o loop deve retransmitir mais de uma vez; foi {len(retransmits)}"
    assert attempts == list(range(1, len(attempts) + 1)), (
        f"retry_attempt deve incrementar 1..N; foi {attempts}"
    )

    assert ans_probe.has_event(_ANS_FAILED), "anssubmit.failed deve ser publicado no esgotamento do retry"
    await _assert_no_filing_without_human_task(engine, iid)


# ===========================================================================
# Guards (unit-style, SEM engine) — port rule 1: v2 usa entry functions dict-boundary, nao
# handler factories (make_submit_handler/make_assemble_handler nao existem em v2 main).
# ===========================================================================


def test_err_ans_submit_not_human_recusa_sem_aprovacao() -> None:
    """Invocacao direta de submit_entry sem decisao humana => AnsSubmitNotHumanError.

    Unit-style sobre o handler real (SEM engine). ADAPTED (port rule 1): v2's
    `submit_entry(variables: dict, *, kafka=None) -> dict` (dict-boundary) replaces donor's
    `make_submit_handler(kafka) -> Callable[[ExternalTask], ...]`; the guard raises
    `AnsSubmitNotHumanError` (a `PermissionError` subclass, routed straight to an engine incident
    by harness.py's `_handle`) rather than donor's `WorkerBpmnError(ERR_ANS_SUBMIT_NOT_HUMAN)`. The
    guard's 4 scenarios (a-d) are preserved verbatim — this guard genuinely works end-to-end in v2.
    """
    kafka = FakeKafkaPublisher()

    # (a) decisao_envio ausente -> recusa
    with pytest.raises(AnsSubmitNotHumanError) as exc_a:
        submit_entry({}, kafka=kafka)
    assert "ERR_ANS_SUBMIT_NOT_HUMAN" in str(exc_a.value)

    # (b) decisao_envio != APROVAR_ENVIO -> recusa
    with pytest.raises(AnsSubmitNotHumanError) as exc_b:
        submit_entry({"decisao_envio": "ADIAR_ENVIO"}, kafka=kafka)
    assert "ERR_ANS_SUBMIT_NOT_HUMAN" in str(exc_b.value)

    # (c) APROVAR_ENVIO mas faltando revisor_id -> recusa
    with pytest.raises(AnsSubmitNotHumanError) as exc_c:
        submit_entry({"decisao_envio": "APROVAR_ENVIO"}, kafka=kafka)
    assert "ERR_ANS_SUBMIT_NOT_HUMAN" in str(exc_c.value)
    assert not kafka.published, "Nenhum filing deve ser publicado quando o guard recusa"

    # (d) decisao humana completa -> transmite e carrega revisor_id
    result = submit_entry(
        {
            "decisao_envio": "APROVAR_ENVIO",
            "revisor_id": "revisor-sintetico-001",
            "tenant_id": "amh",
            "report_type": "RN_124_SIP",
            "competencia": "2026-GUARD",
        },
        kafka=kafka,
    )
    assert result["submitted"] is True
    assert result["revisor_id"] == "revisor-sintetico-001"
    assert result["protocolo_ans"].startswith("ANSPROTO-")


@pytest.mark.xfail(reason=_ASSEMBLE_FAILURE_GUARD_MISSING_REASON, strict=True)
def test_assemble_dataset_incompleto_roteia_a_humano() -> None:
    """Worker assemble com dataset_assembly_failed=true => ERR_ANS_DATASET_INCOMPLETO.

    FINDING D (module docstring): v2's `assemble_entry`/`prepare_submission` has NO
    `dataset_assembly_failed` field/check at all — this raise never happens in v2, unlike the
    donor's `make_assemble_handler`. Kept as an honest xfail (never raises -> `pytest.raises`
    itself fails -> expected xfail) rather than deleted, per port rule 2.
    """
    kafka = FakeKafkaPublisher()

    with pytest.raises(AnsDatasetIncompletoError):
        assemble_entry({"dataset_assembly_failed": True}, kafka=kafka)


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def test_ans_admissibility_sem_saida_de_reject() -> None:
    """O dominio de roteamento da ans_submission_admissibility e EXATAMENTE
    {SEGUE_ENVIO, PENDENTE, REVISAO_HUMANA}. NAO existe valor reject/deny (nao-negativa)."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ADMISS)
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

    assert roteamentos == {"SEGUE_ENVIO", "PENDENTE", "REVISAO_HUMANA"}, (
        f"dominio de roteamento inesperado: {roteamentos} — NAO pode conter REJECT/DENY (nao-negativa)"
    )
    joined = " ".join(roteamentos)
    assert "REJECT" not in joined.upper()
    assert "DENY" not in joined.upper()
    assert "NEGAR" not in joined.upper()
    assert last_rule_first_output == "REVISAO_HUMANA", "row catch-all deve rotear a REVISAO_HUMANA"


def test_ans_calendar_catch_all_revisao_humana() -> None:
    """ans_calendar: a row catch-all (ultima) tem fonte_regulatoria=REVISAO_HUMANA com prazos
    conservadores (nunca prazo infinito)."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_CALENDAR)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    rules = [e for e in root.iter() if _local(e.tag) == "rule"]
    assert rules, "ans_calendar deve ter rules"
    last_rule = rules[-1]
    outputs = [c for c in last_rule if _local(c.tag) == "outputEntry"]
    assert len(outputs) == 4, "ans_calendar deve ter 4 outputs (due_date/sla_alerta/periodicidade/fonte)"
    due_date_el = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
    fonte_el = next((c for c in outputs[3] if _local(c.tag) == "text"), None)
    assert due_date_el is not None and due_date_el.text and due_date_el.text.strip().strip('"'), (
        "catch-all deve ter due_date conservador (nunca prazo infinito/vazio)"
    )
    assert fonte_el is not None and fonte_el.text
    # v2's ans_calendar.dmn embeds a full descriptive sentence in fonte_regulatoria (not a bare
    # enum token like the donor's DMN) — adapted (port rule 1, verified against v2 main) to check
    # the semantic prefix rather than exact string equality.
    assert fonte_el.text.strip().strip('"').startswith("REVISAO_HUMANA"), (
        "catch-all do ans_calendar deve rotear a REVISAO_HUMANA"
    )


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}."""
    from xml.etree import ElementTree as ET

    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_CALENDAR, _DMN_SLA, _DMN_ADMISS, _DMN_RETRY):
        tree = ET.parse(dmn_path)
        for el in tree.getroot().iter():
            type_ref = el.get("typeRef")
            if type_ref is not None:
                assert type_ref in allowed, (
                    f"{dmn_path.name}: typeRef invalido '{type_ref}' (allowlist={sorted(allowed)})"
                )
                assert type_ref != "number", f"{dmn_path.name}: 'number' e proibido (use double)"


# ===========================================================================
# Idempotencia (por competencia)
# ===========================================================================


async def test_business_key_uma_instancia_por_competencia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Mesmo business key (report_type+competencia): consultar antes de iniciar; uma instancia
    ativa. Competencia com sufixo unico por run (evita leftovers)."""
    report_type = "RN_124_SIP"
    competencia = f"2026-IDEM-{uuid.uuid4().hex[:4]}"
    business_key = f"ANSSUB-amh-{report_type}-{competencia}"

    first = await start_ans(report_type=report_type, competencia=competencia)
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1
    assert existing[0]["id"] == first["id"]


async def test_competencia_diferente_cria_nova_instancia(
    engine: EngineRest,
    ans_probe: AnsEngineProbe,
    start_ans: Callable[..., Any],
) -> None:
    """Competencia diferente => nova instancia (idempotencia e por periodo, nao por tipo)."""
    report_type = "RN_124_SIP"
    run_id = uuid.uuid4().hex[:4]
    comp_a = f"2026-IDA-{run_id}"
    comp_b = f"2026-IDB-{run_id}"
    bk_a = f"ANSSUB-amh-{report_type}-{comp_a}"
    bk_b = f"ANSSUB-amh-{report_type}-{comp_b}"

    first = await start_ans(report_type=report_type, competencia=comp_a)
    second = await start_ans(report_type=report_type, competencia=comp_b)

    assert (await engine.find_active_instances(bk_a))[0]["id"] == first["id"]
    assert (await engine.find_active_instances(bk_b))[0]["id"] == second["id"]
    assert first["id"] != second["id"]
