"""SP-OP-CANCEL-001 — suite de integracao executavel (engine CIB Seven REAL).

Ported from the v1 donor (READ-ONLY `Maezo-Healthcare-Plan`) — T3.1 phase 1
(V2-COMPLETION-PLAN §3). cancel.py is NOT one of ADR-0028's 9 migrated modules (no DMN-fork
Python re-implementation lives there) — no in-flight T1.5 collision.

Implementa o test-spec do W5 (docs/processes/test-specs/SP-OP-CANCEL-001.md) contra o engine
real (ADR-0011: SEM mock de engine). Cada teste:

1. inicia a instancia via REST com business key `CANCEL-amh-{numero_contrato}`;
2. drena as external tasks com o `cancel_probe` (workers reais Phase-2 + generic publish);
3. avanca o HITL completando User Tasks como humano sintetico (caminho HITL permitido);
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes via historia do engine (garantia de que rescisao/suspensao/negativa do
   pedido passa pela UT humana — no-denial / contract_termination L0 hard).

Dados sinteticos obvios: contrato `CONTRATO-TESTE-NNNN`, beneficiario `BENEF-TESTE-0001`
(pseudonimo — NUNCA CPF/nome real), tenant `amh`, origem `TESTE`. Business key
`CANCEL-amh-{contrato}`. Process key: SP-OP-CANCEL-001 (exato — nao alterar).

Este modulo SELF-CONTAINS suas fixtures (NAO edita o conftest.py compartilhado): redefine
`deploy_artifacts`, `cancel_probe`, `start_cancel` localmente (override de modulo), espelhando o
padrao do proprio donor (docstring original: "NAO edita o conftest.py compartilhado").

## Invariante L0 (DoD deliverable) — contract_termination / no-denial

test_nenhum_caminho_automatizado_rescinde_contrato:
  Varredura de TODAS as combinacoes de input da DMN cancel_admissibility. A instancia NUNCA
  atinge End_ContratoRescindido / End_ContratoSuspenso / End_PedidoCancelamentoNegado sem que
  UT_AnaliseRescisao / UT_CoordenacaoCancelamento tenha sido completada por humano.

PORT NOTES (fixture adaptation only — port rule 1; logic/assertions verbatim from donor):
  - import paths -> v2 `maezo.tools.workers.cancel`/`harness`.
  - artifact paths -> `spec/processes/{bpmn,dmn}/**` (constraint 5).
  - `drain()` uses the shared `drain_topics()` helper (v2 transport signature adaptation).
  - `test_send_cancellation_notice_recusa_sem_humano` (no engine — unit-style over the real
    handler): v2's dict-boundary entry function is `send_cancellation_notice_entry(variables:
    dict, *, kafka=None) -> dict` (NOT `make_send_cancellation_notice_handler(kafka) ->
    Callable[[ExternalTask], ...]` — that donor factory name does not exist on v2 main; v2
    replaced the class-per-topic `WorkerBase` + raw `ExternalTask` handler shape for this module
    with ADR-0026 §2b's typed dict-boundary entry functions). Adapted to call the entry function
    directly with a plain `variables` dict. The guard ALSO changed shape (verify each, port rule
    1): v2's guard raises `CancellationNotHumanError` (a `PermissionError` subclass — the harness
    routes any `PermissionError` straight to an engine incident, `harness.py` `_handle`), not
    donor's `WorkerBpmnError(error_code=...)` (a modeled BPMN error routed to a boundary catch).
    The 6 guard scenarios (a-f) are preserved verbatim; only the exception type asserted and the
    "success" shape changed to match what v2's function actually returns (a `registered=True`
    dict — v2's `send_cancellation_notice_entry`/`register_contract_termination` do not call
    `kafka.publish` themselves, unlike the donor's handler; that indirection is the SAME
    `operadora.events.publish` finding below, not re-litigated here since this test intentionally
    does not touch the engine).

FINDINGS (see PR body / evidence-ledger for full detail):
  1. `operadora.events.publish` gap, FIXED (T3.1 R2, PR #61): `cancel_probe` registers
     `maezo.tools.workers.events.register_events_workers` (mirrors the donor's own
     `register_phase0_workers` composition) — engine-routed `ST_Publish*` domain events
     (cancel.received/completed/sla_breached) flow and are asserted green below.
  2 + 2b. Registry drift, FIXED (T3.1 topic reconciliation — the PR that carries this note):
     `register_cancel_workers` now registers EXACTLY the 7 `operadora.cancel.*` topics the BPMN
     declares — the 3 previously-missing workers are implemented (`confirm_maintained_decision`:
     GATED, GAP-CANCEL-3, raises a MODELED `WorkerBpmnError(ERR_CANCEL_MANTER_NOT_HUMAN)` so
     `BE_ManterNaoConfirmado`'s boundary catch fires; `effectuate_member_request`: L2 clerical,
     NOT adverse, no guard; `notify_sla_risk`: informational) and the 2 orphan registrations with
     no BPMN topic (`process_cancel`, `publish_completed`) are REMOVED. The donor's drift-guard in
     `cancel_probe` (`assert not missing_from_drain`, ported verbatim, byte-untouched) now passes
     NATURALLY. Live-proven (fresh `docker compose -p cancelfix` stack, CIB Seven 2.1.0, single
     clean run, 2026-07-18): 20/28 pass including the 128-combination L0 sweep, both
     `End_ManterNaoConfirmado` guard-catch paths, and all five human-decision terminals.
  3. REMAINING (7 xfails below, `_WORKER_KAFKA_GAP_REASON`): v2's ADR-0026 dict-boundary
     entry functions never call `kafka.publish` — the donor's cancel workers published per-worker
     notifications (`operadora.notifications.internal`) and the `agents.events.cancel.pended`
     domain event from INSIDE the handler; v2's entry functions only RETURN output variables.
     Every one of these 7 fails ONLY on a `notifications_of_type(...)` assertion over those
     worker-emitted messages (live-verified: each reached its correct end event / passed its flow
     assertions first). Same systemic finding as escalation's NotifyTeamWorker and auth's
     action-worker residuals (T3.1 events.publish-fix ledger row). Fix belongs to the
     Kafka-producer wiring task, not this registry reconciliation.
  4. T3.1 Wave 1 remedy B (event-gap design doc §2.2, THIS PR): the 8th test that used to carry
     `_WORKER_KAFKA_GAP_REASON` (`test_notificacao_previa_pendente_publica_pended`) asserted BOTH
     `has_event(cancel.pended)` (C1 — no publish task existed) AND
     `notifications_of_type("cancel.request_notification")` (C2 — dead internal channel). BPMN now
     carries `ST_PublishCancelPended` (a boundary-free `operadora.events.publish` task on
     `Flow_Request_WaitNotif`, mirroring `ST_PublishSlaBreach`), closing the C1 half; the C2 half
     is adapted to `has_event(..., tipo_solicitacao=...)` per the #108/#123 precedent. Split into
     its own `_CANCEL_PENDED_PUBLISH_ADDED_REASON` xfail reason — kept xfail (not flipped) pending
     a live engine run (no docker/engine in this pass); see that constant's docstring for detail.
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

from maezo.tools.workers.cancel import register_cancel_workers, send_cancellation_notice_entry
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn"
_DMN_ADMISSIBILITY = _REPO / "spec/processes/dmn/cancel_admissibility.dmn"
_DMN_ROUTING = _REPO / "spec/processes/dmn/cancel_routing.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/cancel_sla.dmn"

_PUBLISH_TOPIC = "operadora.events.publish"
_RESOLVE_FACTS_TOPIC = "operadora.cancel.resolve_facts"
_PREPARE_DOSSIER_TOPIC = "operadora.cancel.prepare_dossier"
_REQUEST_NOTIFICATION_TOPIC = "operadora.cancel.request_notification"
_EFFECTUATE_TOPIC = "operadora.cancel.effectuate_member_request"
_SEND_NOTICE_TOPIC = "operadora.cancel.send_cancellation_notice"
_CONFIRM_MAINTAINED_TOPIC = "operadora.cancel.confirm_maintained_decision"
_NOTIFY_SLA_TOPIC = "operadora.cancel.notify_sla_risk"

# Topicos servidos pelos workers REAIS registrados no harness (drain generico). T3.1 topic
# reconciliation (findings 2+2b, FIXED): `register_cancel_workers` agora registra handler para
# TODOS os 7 topicos `operadora.cancel.*` abaixo — a lista de drain (fixture verbatim do donor)
# e o registro real coincidem 1:1, e o drift-guard do `cancel_probe` (compara
# `harness.registered_topics` contra esta lista) passa NATURALMENTE.
_CANCEL_WORKER_TOPICS = [
    _PUBLISH_TOPIC,
    _RESOLVE_FACTS_TOPIC,
    _PREPARE_DOSSIER_TOPIC,
    _REQUEST_NOTIFICATION_TOPIC,
    _EFFECTUATE_TOPIC,
    _SEND_NOTICE_TOPIC,
    _CONFIRM_MAINTAINED_TOPIC,
    _NOTIFY_SLA_TOPIC,
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"

_CANCEL_RECEIVED = "agents.events.cancel.received"
_CANCEL_PENDED = "agents.events.cancel.pended"
_CANCEL_SLA_BREACHED = "agents.events.cancel.sla_breached"
_CANCEL_COMPLETED = "agents.events.cancel.completed"

_UT_ANALISE = "UT_AnaliseRescisao"
_UT_COORDENACAO = "UT_CoordenacaoCancelamento"

_END_RESCINDIDO = "End_ContratoRescindido"
_END_SUSPENSO = "End_ContratoSuspenso"
_END_PEDIDO_NEGADO = "End_PedidoCancelamentoNegado"
_END_CANCELADO_BENEFICIARIO = "End_CanceladoBeneficiario"
_END_MANTIDO = "End_ContratoMantido"
_END_MANTER_NAO_CONFIRMADO = "End_ManterNaoConfirmado"

_UT_HUMANAS = frozenset({_UT_ANALISE, _UT_COORDENACAO})
_ENDS_ADVERSOS = frozenset({_END_RESCINDIDO, _END_SUSPENSO, _END_PEDIDO_NEGADO})

_CAMPOS_ADVERSOS = {
    "fundamentacao_contratual": "Fundamentacao sintetica da decisao (teste L0)",
    "referencia_regulatoria": "RN 593/2023 — DRAFT/verify (teste)",
    "comprovacao_notificacao_previa": "ref-comprovante-notificacao-teste-0001",
    "responsavel_id": "juridico-sintetico-001",
}

# T3.1 topic reconciliation: findings 2+2b (registry drift) are FIXED — 16 of the previous 24
# xfails flipped green on a single clean run against a fresh engine (docker compose -p cancelfix,
# CIB Seven 2.1.0, 2026-07-18: 20 passed / 8 failed under --runxfail; the 4 never-marked tests
# were already green). The 8 tests still marked below all failed ONLY on the same remaining
# assertion class — worker-emitted Kafka messages (finding 3 in the module docstring): each one
# reached its correct end event / passed every flow assertion first, then failed on an empty
# `notifications_of_type(...)` (or the worker-published `cancel.pended` `has_event`). That is the
# donor-vs-v2 behavioral drift ADR-0026 §2 documents for EVERY dict-boundary module ("no entry
# function calls kafka.publish today"): the donor's handlers published notifications from inside
# the worker; v2's entry functions only return output variables. Same systemic residual already
# on record for escalation (NotifyTeamWorker) and auth (action workers) in the T3.1
# events.publish-fix ledger row — kept as HONEST xfails until the Kafka-producer wiring task
# lands, rather than weakening the ported assertions (constraint 2).
_WORKER_KAFKA_GAP_REASON = (
    "v2 systemic drift (T3.1 finding 3 — same class as escalation/auth residuals, ledger row "
    "'T3.1 (events.publish fix)'): v2's ADR-0026 dict-boundary entry functions never call "
    "kafka.publish — the donor's cancel handlers published per-worker notifications "
    "(operadora.notifications.internal) and agents.events.cancel.pended from INSIDE the worker; "
    "v2's entry functions only RETURN output variables. This test's notifications_of_type()/"
    "pended has_event() assertions can therefore never observe an execution, even though the "
    "worker itself now runs and completes against the live engine (registry drift fixed by the "
    "T3.1 topic reconciliation — live-verified: this test's instance reached its correct end "
    "event and passed every flow assertion before failing here). Fix belongs to the "
    "Kafka-producer wiring task, not this registry reconciliation."
)

# T3.1 Wave 1 remedy B (event-gap design doc §2.2): CLOSES the C1 half of the gap this ONE test
# hit — `ST_PublishCancelPended` (a plain, boundary-free `operadora.events.publish` task mirroring
# `ST_PublishSlaBreach`) now sits on `Flow_Request_WaitNotif` (spec/processes/bpmn/SP-OP-CANCEL-001_
# Cancelamento_Contrato.bpmn), so `has_event(_CANCEL_PENDED)` is no longer structurally blocked by a
# missing publisher. Distinct from `_WORKER_KAFKA_GAP_REASON` (which still applies to the OTHER 7
# xfails in this module — those have no publish task and are out of scope for this batch). Publish
# task added, pending live-proof flip: this repo's gates run without a live CIB Seven engine (no
# docker/engine in this pass), so the marker is intentionally NOT removed here — flip only after a
# real `make deploy-artifacts` + `pytest tests/integration/processes/test_sp_op_cancel_001.py -k
# test_notificacao_previa_pendente_publica_pended` run confirms the event round-trips and no
# sibling assertion regresses (design doc §5). The `notifications_of_type("cancel.request_notification")`
# execution-proof half of the original assert pair was ADAPTED to `has_event(..., tipo_solicitacao=...)`
# per the #108/#123 precedent (that internal-notification channel is structurally always-empty in
# v2's dict-first workers, ADR-0026) — see the test body below.
_CANCEL_PENDED_PUBLISH_ADDED_REASON = (
    "T3.1 Wave 1 remedy B (event-gap design doc §2.2, contract SP-OP-CANCEL-001.md:91 'produz'): "
    "ST_PublishCancelPended now exists on Flow_Request_WaitNotif — the BPMN-side gap that blocked "
    "has_event(_CANCEL_PENDED) is closed (mirrors ST_PublishSlaBreach; boundary-free per the 15/16 "
    "convention, check-bpmn-error-allowlist unaffected). Publish task added, pending live-proof "
    "flip: requires a real CIB Seven engine run (make deploy-artifacts + this suite) to confirm the "
    "event round-trips and no sibling assertion regresses before the xfail marker is removed — not "
    "flipped in this PR (no docker/engine in this pass, per the design doc's live-validation "
    "requirement, §5). LIVE-FLIPPED (wave2b2 R1 live-validation, cibseven 2.1.0): the test XPASSed "
    "strict on a real engine and the xfail was removed in-step. Engine /history right-reason "
    "evidence: ST_RequestNotification -> ST_PublishCancelPended both ended in the for_cause "
    "instance (splice executed on the main token flow in EVERY Flow_Request_WaitNotif traversal; "
    "downstream GW_AguardarNotificacao event race + prazo-notificacao timer tests all still green "
    "— 0 sibling regressions). Constant retained for the docstring prose above."
)


@dataclass
class CancelEngineProbe:
    """Driva os workers reais de Phase-2 (cancel) contra o engine CIB Seven."""

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
        await drain_topics(self.transport, self.harness, self.worker_id, _CANCEL_WORKER_TOPICS, rounds=rounds)


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya BPMN + 3 DMN de cancelamento/rescisao da arvore no engine real."""
    return await engine.deploy(_BPMN, _DMN_ADMISSIBILITY, _DMN_ROUTING, _DMN_SLA, name="SP-OP-CANCEL-001-qa")


@pytest_asyncio.fixture
async def cancel_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[CancelEngineProbe]:
    """Probe que serve as external tasks com os workers reais Phase-2 de cancel.

    T1.10 wave: completions são emit-before-complete contra o sink durável REAL da lane
    (PostgresAuditSink, migrações aplicadas) — um harness sem sink agora recusa completar.
    """
    worker_id = f"qa-cancel-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    # T3.1 R2: ERR_CANCEL_MANTER_NOT_HUMAN (GAP-CANCEL-3) is catchable by SP-OP-CANCEL-001's own
    # boundary event (Error_CancelManterNotHuman / BE_ManterNaoConfirmado) ONLY when it's in the
    # harness's allowlist (harness.py `_bpmn_error_allowlist` — no code is gate-proven at the
    # PRODUCTION default, empty). This probe wires the ONE code this family's BPMN declares a
    # matching boundary for, mirroring what a gate-proven production allowlist for this family
    # would contain (same pattern as `test_sp_op_escalation_001.py`'s `ERR_EVENT_PUBLISH_FAILED`).
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        bpmn_error_allowlist=frozenset({"ERR_CANCEL_MANTER_NOT_HUMAN"}),
        audit_sink=audit_sink,
    )
    kafka = FakeKafkaPublisher()
    register_cancel_workers(harness, kafka)
    # T3.1 R2: the generic operadora.events.publish worker every ST_Publish* service task in
    # this BPMN routes through — mirrors the donor's own register_phase0_workers composition.
    register_events_workers(harness, kafka)
    # DRIFT GUARD (donor fixture, verbatim): todo topico de cancel registrado no harness DEVE
    # estar na lista de drain — falha AQUI, explicita, se um worker novo ficar fora.
    cancel_registered = {t for t in harness.registered_topics if t.startswith("operadora.cancel.")}
    missing_from_drain = cancel_registered - set(_CANCEL_WORKER_TOPICS)
    assert not missing_from_drain, (
        f"_CANCEL_WORKER_TOPICS desatualizada — topicos registrados fora do drain: {missing_from_drain}"
    )
    probe = CancelEngineProbe(
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


def _unique_contrato(prefix: str = "CONTRATO-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_cancel(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key CANCEL-amh-{contrato} e payload canonico."""

    async def _start(**overrides: Any) -> dict[str, Any]:
        contrato = overrides.pop("numero_contrato", _unique_contrato())
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "numero_contrato": contrato,
            "matricula_beneficiario": "BENEF-TESTE-0001",
            "tipo_solicitacao": "inadimplencia",
            "origem_solicitacao": "operadora",
            "tipo_plano": "individual",
            "motivo_informado": "Motivo sintetico pseudonimizado (teste)",
            "data_solicitacao_iso": "2026-06-01",
            "documentos_refs": "[]",
            "dentro_prazo": True,
            "notificacao_previa_feita": True,
            "titularidade_confirmada": True,
            "vinculo_ativo": True,
            "meses_inadimplencia": 3,
        }
        variables.update(overrides)
        business_key = f"CANCEL-amh-{contrato}"
        return await engine.start_by_key("SP-OP-CANCEL-001", business_key, variables)

    return _start


async def _await_end(engine: EngineRest, iid: str, *, attempts: int = 60, delay: float = 0.25) -> set[str]:
    """Espera a instancia terminar e retorna os end events atingidos."""
    for _ in range(attempts):
        state = await engine.history_state(iid)
        if state == "COMPLETED":
            return await engine.activity_instances_ended(iid)
        await asyncio.sleep(delay)
    return await engine.activity_instances_ended(iid)


async def _assert_no_adverse_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0 (contract_termination / no-denial)."""
    ended = await engine.activity_instances_ended(iid)
    adversos_atingidos = ended & _ENDS_ADVERSOS
    if adversos_atingidos:
        human_tasks_in_history = ended & _UT_HUMANAS
        assert human_tasks_in_history, (
            f"INVARIANTE L0 VIOLADA (contract_termination): terminal(is) adverso(s) "
            f"{adversos_atingidos} atingido(s) para instancia {iid} SEM nenhuma User Task humana no "
            f"historico. User Tasks esperadas (qualquer uma de): {_UT_HUMANAS}. "
            f"Atividades historicas encontradas: {ended}. "
            "Isto indica um caminho automatizado de rescisao/suspensao/negativa — violacao do L0 hard."
        )


async def _drive_to_analise(engine: EngineRest, probe: CancelEngineProbe, iid: str) -> Any:
    """Drena ate UT_AnaliseRescisao surgir (dossie preparado pelo worker real)."""
    await probe.drain()
    return await engine.await_user_task(iid, _UT_ANALISE)


# ===========================================================================
# INVARIANTE L0 — DoD deliverable (varredura de inputs da DMN cancel_admissibility)
# ===========================================================================


async def test_nenhum_caminho_automatizado_rescinde_contrato(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """INVARIANTE L0 (contract_termination): NENHUM caminho automatizado rescinde/suspende/nega.

    GREEN since the T3.1 topic reconciliation (findings 1 + 2 + 2b all FIXED — see module
    docstring): live-proven on a fresh engine (2026-07-18, single clean run), all 128
    combinations swept, zero adverse terminals reached without a human User Task. The assertion
    body is unchanged from the donor.
    """
    tipos_solicitacao = [
        "pedido_beneficiario",
        "inadimplencia",
        "for_cause_operadora",
        "fraude_referida",
    ]
    tipos_plano = ["individual", "familiar", "coletivo_empresarial", "coletivo_adesao"]
    bools = [True, False]
    checked = 0

    for tipo_sol, tipo_plano, prazo, notif, titular in itertools.product(
        tipos_solicitacao, tipos_plano, bools, bools, bools
    ):
        inst = await start_cancel(
            tipo_solicitacao=tipo_sol,
            tipo_plano=tipo_plano,
            dentro_prazo=prazo,
            notificacao_previa_feita=notif,
            titularidade_confirmada=titular,
        )
        iid = inst["id"]
        await cancel_probe.drain()

        ended = await engine.activity_instances_ended(iid)
        adversos = ended & _ENDS_ADVERSOS
        assert not adversos, (
            f"L0 VIOLADO (contract_termination): tipo_solicitacao={tipo_sol} tipo_plano={tipo_plano} "
            f"dentro_prazo={prazo} notificacao_previa_feita={notif} titularidade_confirmada={titular} "
            f"atingiu terminal(is) adverso(s) {adversos} automaticamente. ended={ended}"
        )
        await _assert_no_adverse_without_human_task(engine, iid)
        checked += 1

    assert checked == 128, f"Esperava 128 combinacoes varridas; varri {checked}"


async def test_inadimplencia_aparente_roteia_para_humano_nao_rescinde(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Inadimplencia com notificacao previa feita => cancel_admissibility=SEGUE_ANALISE."""
    inst = await start_cancel(
        tipo_solicitacao="inadimplencia",
        meses_inadimplencia=3,
        notificacao_previa_feita=True,
    )
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Inadimplencia nao deve produzir terminal adverso automatico (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_inelegibilidade_pedido_roteia_para_humano_nunca_auto_nega(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Pedido com titularidade nao comprovada => cancel_admissibility catch-all -> ANALISE_HUMANA."""
    inst = await start_cancel(
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="individual",
        titularidade_confirmada=False,
    )
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_PEDIDO_NEGADO not in ended, "Inelegibilidade nao deve auto-negar o pedido (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_plano_coletivo_pedido_roteia_para_humano(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Pedido em plano coletivo => cancel_admissibility -> ANALISE_HUMANA."""
    inst = await start_cancel(
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="coletivo_empresarial",
        titularidade_confirmada=True,
    )
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_CANCELADO_BENEFICIARIO not in ended, "Pedido coletivo nunca auto-efetiva"
    assert not (ended & _ENDS_ADVERSOS)
    assert not cancel_probe.notifications_of_type("cancel.effectuate_member_request"), (
        "Pedido coletivo nao deve efetivar cancelamento automaticamente"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_fora_prazo_pedido_roteia_para_humano_nunca_auto_efetiva(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """GAP-CANCEL-2: pedido do titular fora do prazo => catch-all -> ANALISE_HUMANA."""
    inst = await start_cancel(
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="individual",
        titularidade_confirmada=True,
        vinculo_ativo=True,
        dentro_prazo=False,
    )
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert _END_CANCELADO_BENEFICIARIO not in ended, "Pedido fora do prazo nunca auto-efetiva (GAP-CANCEL-2)"
    assert not (ended & _ENDS_ADVERSOS)
    assert not cancel_probe.notifications_of_type("cancel.effectuate_member_request"), (
        "Pedido fora do prazo nao deve efetivar cancelamento automaticamente"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_fraude_referida_nunca_auto_flag(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """fraude_referida => cancel_admissibility -> ANALISE_HUMANA."""
    inst = await start_cancel(tipo_solicitacao="fraude_referida")
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS)
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Happy paths
# ===========================================================================


@pytest.mark.xfail(reason=_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_cancelamento_a_pedido_beneficiario_l2(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Pedido do titular (individual, titularidade confirmada, dentro do prazo) => EFETIVAR_PEDIDO."""
    inst = await start_cancel(
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="individual",
        titularidade_confirmada=True,
        dentro_prazo=True,
    )
    iid = inst["id"]

    await cancel_probe.drain()
    ended = await _await_end(engine, iid)

    assert _END_CANCELADO_BENEFICIARIO in ended, f"Deve atingir End_CanceladoBeneficiario. ended={ended}"
    assert not await engine.list_user_tasks(iid), "Pedido do titular L2 nao cria User Task adversa"
    assert cancel_probe.has_event(_CANCEL_RECEIVED)
    assert cancel_probe.has_event(_CANCEL_COMPLETED, desfecho="cancelado_beneficiario")
    assert cancel_probe.notifications_of_type("cancel.effectuate_member_request")
    assert not cancel_probe.notifications_of_type("cancel.send_cancellation_notice"), (
        "Cancelamento a pedido NUNCA emite notificacao de rescisao (L0)"
    )


@pytest.mark.xfail(reason=_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_rescisao_pela_operadora_humano(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Inadimplencia com notificacao previa => SEGUE_ANALISE; humano RESCINDIR => End_ContratoRescindido."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    await engine.complete_task_as_human(ut.id, {"decisao_cancelamento": "RESCINDIR", **_CAMPOS_ADVERSOS})
    await cancel_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_RESCINDIDO in ended, f"Rescisao humana deve atingir End_ContratoRescindido. ended={ended}"
    assert cancel_probe.has_event(_CANCEL_COMPLETED, desfecho="rescindido_operadora")

    avisos = cancel_probe.notifications_of_type("cancel.send_cancellation_notice")
    assert avisos, "Worker send_cancellation_notice deve ser executado apos rescisao humana"
    a = avisos[0]
    assert a["decisao_cancelamento"] == "RESCINDIR"
    assert a["responsavel_id"] == "juridico-sintetico-001"


@pytest.mark.xfail(reason=_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_suspensao_por_inadimplencia_humano(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Inadimplencia => SEGUE_ANALISE; humano SUSPENDER => End_ContratoSuspenso."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_cancelamento": "SUSPENDER", **_CAMPOS_ADVERSOS})
    await cancel_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_SUSPENSO in ended, f"Suspensao humana deve atingir End_ContratoSuspenso. ended={ended}"
    assert cancel_probe.has_event(_CANCEL_COMPLETED, desfecho="suspenso")

    avisos = cancel_probe.notifications_of_type("cancel.send_cancellation_notice")
    assert avisos and avisos[0]["decisao_cancelamento"] == "SUSPENDER"


@pytest.mark.xfail(reason=_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_pedido_negado_humano(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """pedido_beneficiario + titularidade nao confirmada => ANALISE_HUMANA; humano MANTER => pedido_negado."""
    inst = await start_cancel(
        tipo_solicitacao="pedido_beneficiario",
        tipo_plano="coletivo_empresarial",
        titularidade_confirmada=False,
    )
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_cancelamento": "MANTER",
            "fundamentacao_contratual": "Titularidade nao comprovada / coletivo cabe ao estipulante",
            "responsavel_id": "juridico-sintetico-001",
        },
    )
    await cancel_probe.drain()

    ended = await _await_end(engine, iid)
    await _assert_no_adverse_without_human_task(engine, iid)
    assert _END_PEDIDO_NEGADO in ended, f"MANTER + pedido => End_PedidoCancelamentoNegado. ended={ended}"
    assert cancel_probe.has_event(_CANCEL_COMPLETED, desfecho="pedido_negado")
    assert not cancel_probe.notifications_of_type("cancel.send_cancellation_notice")
    confirmacoes = cancel_probe.notifications_of_type("cancel.confirm_maintained_decision")
    assert confirmacoes and confirmacoes[0]["fundamentacao_provided"] is True
    assert "fundamentacao_contratual" not in confirmacoes[0]


@pytest.mark.xfail(reason=_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_happy_path_contrato_mantido(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Inadimplencia => SEGUE_ANALISE; humano MANTER (ex.: purga da inadimplencia) => End_ContratoMantido."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {
            "decisao_cancelamento": "MANTER",
            "fundamentacao_contratual": "Purga da inadimplencia comprovada — vinculo mantido (teste)",
            "responsavel_id": "juridico-sintetico-001",
        },
    )
    await cancel_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_MANTIDO in ended, f"MANTER (inadimplencia) => End_ContratoMantido. ended={ended}"
    assert not (ended & _ENDS_ADVERSOS)
    assert cancel_probe.has_event(_CANCEL_COMPLETED, desfecho="mantido")
    assert cancel_probe.has_event(
        _CANCEL_COMPLETED, desfecho="mantido", responsavel_id="juridico-sintetico-001"
    )
    confirmacoes = cancel_probe.notifications_of_type("cancel.confirm_maintained_decision")
    assert confirmacoes and confirmacoes[0]["fundamentacao_provided"] is True
    assert "fundamentacao_contratual" not in confirmacoes[0]


# ===========================================================================
# Aceite exige campos / worker guard (D3)
# ===========================================================================


async def test_rescindir_exige_campos_worker_guard(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """RESCINDIR sem fundamentacao/referencia/comprovacao/responsavel => worker guard recusa."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_cancelamento": "RESCINDIR"})
    await cancel_probe.drain()

    ended = await engine.activity_instances_ended(iid)
    assert _END_RESCINDIDO not in ended, (
        "Rescisao sem campos obrigatorios NAO pode atingir End_ContratoRescindido (guard do worker)"
    )
    assert not cancel_probe.notifications_of_type("cancel.send_cancellation_notice"), (
        "send_cancellation_notice NAO deve emitir sem campos obrigatorios"
    )
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_manter_sem_fundamentacao_bloqueado_pelo_guard(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """GAP-CANCEL-3 (defesa-em-profundidade): MANTER sem fundamentacao nao vira desfecho."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    await engine.complete_task_as_human(
        ut.id,
        {"decisao_cancelamento": "MANTER", "responsavel_id": "juridico-sintetico-001"},
    )
    await cancel_probe.drain()

    ended = await _await_end(engine, iid)

    assert _END_MANTER_NAO_CONFIRMADO in ended, (
        f"MANTER sem fundamentacao deve ser capturado em {_END_MANTER_NAO_CONFIRMADO}. ended={ended}"
    )
    assert _END_MANTIDO not in ended and _END_PEDIDO_NEGADO not in ended, (
        f"MANTER sem fundamentacao NAO pode produzir desfecho mantido/pedido_negado. ended={ended}"
    )
    assert not (ended & _ENDS_ADVERSOS)

    assert not cancel_probe.notifications_of_type("cancel.confirm_maintained_decision"), (
        "confirm_maintained_decision NAO deve confirmar MANTER sem fundamentacao"
    )
    assert not cancel_probe.has_event(_CANCEL_COMPLETED, desfecho="mantido")
    assert not cancel_probe.has_event(_CANCEL_COMPLETED, desfecho="pedido_negado")

    open_incidents = await engine.incidents(iid)
    assert not open_incidents, (
        f"ERR_CANCEL_MANTER_NOT_HUMAN deve ser capturado pelo boundary (sem incidente): {open_incidents!r}"
    )

    await _assert_no_adverse_without_human_task(engine, iid)


async def test_efetivar_pedido_restrito_a_pedido_beneficiario(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """GAP-CANCEL-7: humano NAO pode desviar inadimplencia/for_cause/fraude para EFETIVAR_PEDIDO."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_cancelamento": "EFETIVAR_PEDIDO"})
    await cancel_probe.drain()

    ended = await _await_end(engine, iid)

    assert _END_MANTER_NAO_CONFIRMADO in ended, (
        f"EFETIVAR_PEDIDO fora de pedido_beneficiario deve cair no guard MANTER (fail-closed). ended={ended}"
    )
    assert _END_CANCELADO_BENEFICIARIO not in ended, (
        "EFETIVAR_PEDIDO NUNCA efetiva um caso de inadimplencia (GAP-CANCEL-7)"
    )
    assert not (ended & _ENDS_ADVERSOS)
    assert not cancel_probe.notifications_of_type("cancel.effectuate_member_request"), (
        "effectuate_member_request NAO deve ser executado para tipo_solicitacao != pedido_beneficiario"
    )
    assert not cancel_probe.has_event(_CANCEL_COMPLETED, desfecho="cancelado_beneficiario")
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_send_cancellation_notice_recusa_sem_humano() -> None:
    """Invocacao direta do entry function send_cancellation_notice_entry sem decisao humana =>
    `CancellationNotHumanError` (guard). Unit-style sobre o handler real (SEM engine).

    ADAPTED (port rule 1, verify each register_*/entry-function shape on v2 main): v2's
    `send_cancellation_notice_entry(variables: dict, *, kafka=None) -> dict` (dict-boundary, no
    `ExternalTask`) replaces donor's `make_send_cancellation_notice_handler(kafka) ->
    Callable[[ExternalTask], ...]`; the guard raises `CancellationNotHumanError`
    (`PermissionError` subclass) rather than `WorkerBpmnError(error_code=...)` — see module
    docstring. The 6 guard scenarios (a-f) are preserved verbatim; success-case assertions check
    the returned dict (v2's function does not itself call `kafka.publish` — see module docstring
    finding 1).
    """
    from maezo.tools.workers.cancel import CancellationNotHumanError

    kafka = FakeKafkaPublisher()

    # (a) decisao_cancelamento ausente -> recusa
    with pytest.raises(CancellationNotHumanError) as exc_a:
        send_cancellation_notice_entry({}, kafka=kafka)
    assert "ERR_CANCELLATION_NOT_HUMAN" in str(exc_a.value)

    # (b) decisao_cancelamento neutra (MANTER) -> recusa
    with pytest.raises(CancellationNotHumanError) as exc_b:
        send_cancellation_notice_entry({"decisao_cancelamento": "MANTER"}, kafka=kafka)
    assert "ERR_CANCELLATION_NOT_HUMAN" in str(exc_b.value)

    # (c) EFETIVAR_PEDIDO (clerical, nao adverso) tampouco passa pelo worker adverso -> recusa
    with pytest.raises(CancellationNotHumanError) as exc_c:
        send_cancellation_notice_entry({"decisao_cancelamento": "EFETIVAR_PEDIDO"}, kafka=kafka)
    assert "ERR_CANCELLATION_NOT_HUMAN" in str(exc_c.value)

    # (d) RESCINDIR mas faltando responsavel_id -> recusa
    with pytest.raises(CancellationNotHumanError) as exc_d:
        send_cancellation_notice_entry(
            {
                "decisao_cancelamento": "RESCINDIR",
                "fundamentacao_contratual": "x",
                "referencia_regulatoria": "RN 593",
                "comprovacao_notificacao_previa": "ref-comprovante",
            },
            kafka=kafka,
        )
    assert "ERR_CANCELLATION_NOT_HUMAN" in str(exc_d.value)
    assert not kafka.published, "Nenhuma notificacao deve ser publicada quando o guard recusa"

    # (e) decisao humana completa (RESCINDIR) -> registra e carrega responsavel_id
    result_e = send_cancellation_notice_entry(
        {
            "decisao_cancelamento": "RESCINDIR",
            "fundamentacao_contratual": "Rescisao fundamentada (teste)",
            "referencia_regulatoria": "RN 593/2023 — DRAFT/verify",
            "comprovacao_notificacao_previa": "ref-comprovante-0001",
            "responsavel_id": "juridico-sintetico-001",
            "tenant_id": "amh",
            "numero_contrato": "CONTRATO-TESTE-GUARD",
        },
        kafka=kafka,
    )
    assert result_e["registered"] is True
    assert result_e["decisao"] == "RESCINDIR"
    assert result_e["responsavel_id"] == "juridico-sintetico-001"

    # (f) SUSPENDER com campos completos tambem registra
    result_f = send_cancellation_notice_entry(
        {
            "decisao_cancelamento": "SUSPENDER",
            "fundamentacao_contratual": "Suspensao fundamentada (teste)",
            "referencia_regulatoria": "RN 593/2023 — DRAFT/verify",
            "comprovacao_notificacao_previa": "ref-comprovante-0002",
            "responsavel_id": "juridico-sintetico-002",
            "tenant_id": "amh",
            "numero_contrato": "CONTRATO-TESTE-GUARD-2",
        },
        kafka=kafka,
    )
    assert result_f["registered"] is True
    assert result_f["decisao"] == "SUSPENDER"
    assert result_f["responsavel_id"] == "juridico-sintetico-002"


# ===========================================================================
# Notificacao previa (event gateway — INVERTE auto-rescisao por timeout)
# ===========================================================================


async def test_notificacao_previa_pendente_publica_pended(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """for_cause + notificacao_previa_feita=false => PENDENTE_NOTIFICACAO; request_notification; pended."""
    inst = await start_cancel(
        tipo_solicitacao="for_cause_operadora",
        notificacao_previa_feita=False,
    )
    iid = inst["id"]

    await cancel_probe.drain()

    # T3.1 Wave 1 remedy B (#108/#123 has_event adaptation): the old execution-proof assert
    # (`notifications_of_type("cancel.request_notification")`) is structurally always-empty in v2
    # (dict-first worker, ADR-0026 — never reaches the internal notifications channel). Folded into
    # this single has_event() call with `tipo_solicitacao` matched from payload, which ALSO proves
    # request_notification ran (ST_PublishCancelPended sits immediately downstream on the same
    # token, so the event firing proves the worker's token flowed through PENDENTE_NOTIFICACAO).
    assert cancel_probe.has_event(_CANCEL_PENDED, tipo_solicitacao="for_cause_operadora"), (
        "cancel.pended deve ser publicado (ST_PublishCancelPended) apos request_notification"
    )

    ended = await engine.activity_instances_ended(iid)
    # wave2b2 belt-and-suspenders (verifier flag: tipo_solicitacao is an INPUT-known field, so the
    # has_event fold alone proves "some instance with that input published" — this engine-history
    # containment proves THIS instance's token ran request_notification AND the new publish task):
    assert "ST_RequestNotification" in ended, "request_notification deve ter executado nesta instancia"
    assert "ST_PublishCancelPended" in ended, "ST_PublishCancelPended deve ter executado nesta instancia"
    assert not (ended & _ENDS_ADVERSOS), "Pendencia de notificacao nunca rescinde automaticamente"
    assert await engine.instance_is_active(iid), "Instancia deve aguardar no event gateway de notificacao"


async def test_notificacao_ack_destrava_analise(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Aguardando no event gateway => msg.cancel.notification_ack => segue para UT_AnaliseRescisao."""
    inst = await start_cancel(
        tipo_solicitacao="for_cause_operadora",
        notificacao_previa_feita=False,
    )
    iid = inst["id"]
    business_key = inst["businessKey"]

    await cancel_probe.drain()

    correlate_payload = {
        "messageName": "msg.cancel.notification_ack",
        "businessKey": business_key,
        "processVariables": {
            "notificacao_previa_feita": {"value": True, "type": "Boolean"},
        },
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.cancel.notification_ack falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Ack de notificacao nunca auto-rescinde (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_prazo_notificacao_expira_vai_para_humano_nao_rescinde(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Aguardando no event gateway, sem ack => timer prazo_notificacao_previa => UT_AnaliseRescisao."""
    inst = await start_cancel(
        tipo_solicitacao="for_cause_operadora",
        notificacao_previa_feita=False,
    )
    iid = inst["id"]

    await cancel_probe.drain()

    job = await engine.await_timer_job(iid, "ICE_PrazoNotificacao")
    await engine.execute_job(job.id)
    await cancel_probe.drain()

    ut = await engine.await_user_task(iid, _UT_ANALISE)
    assert "juridico-contratos" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Expiracao do prazo de notificacao NUNCA auto-rescinde (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# Timers de SLA
# ===========================================================================


@pytest.mark.xfail(reason=_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Timer BT_AlertaSla (nao-interruptivo): notify_sla_risk recebe task; UT segue aberta."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    await _drive_to_analise(engine, cancel_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    await engine.execute_job(job.id)
    await cancel_probe.drain()

    assert cancel_probe.notifications_of_type("cancel.notify_sla_risk"), (
        "Worker notify_sla_risk deve ser executado no alerta de SLA"
    )

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISE in open_keys, "Timer nao-interruptivo nao deve cancelar a User Task"


async def test_timer_sla_estourado_coordenacao_assume(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Timer BT_SlaAnalise (interruptivo): UT_AnaliseRescisao cancelada; UT_CoordenacaoCancelamento criada."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    await _drive_to_analise(engine, cancel_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await cancel_probe.drain()

    assert cancel_probe.has_event(_CANCEL_SLA_BREACHED), "cancel.sla_breached deve ser publicado"

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-contratos" in ut_coord.candidate_groups

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISE not in open_keys, "UT_AnaliseRescisao deve ser cancelada (interruptivo)"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Estouro de SLA nunca auto-rescinde (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


async def test_coordenacao_assume_e_rescinde(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """SLA estourado; coordenacao assume e RESCINDIR => End_ContratoRescindido com UT humana."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    await _drive_to_analise(engine, cancel_probe, iid)
    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await cancel_probe.drain()

    ut_coord = await engine.await_user_task(iid, _UT_COORDENACAO)
    await engine.complete_task_as_human(
        ut_coord.id,
        {
            "decisao_cancelamento": "RESCINDIR",
            "fundamentacao_contratual": "Prazo esgotado — coordenacao rescinde (sintetico)",
            "referencia_regulatoria": "RN 593/2023 — DRAFT/verify",
            "comprovacao_notificacao_previa": "ref-comprovante-coord-0001",
            "responsavel_id": "coordenacao-sintetica-001",
        },
    )
    await cancel_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_RESCINDIDO in ended
    await _assert_no_adverse_without_human_task(engine, iid)
    assert cancel_probe.has_event(_CANCEL_COMPLETED, desfecho="rescindido_operadora")


async def test_dmn_cancel_sla_registra_fonte(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """tipo_solicitacao=inadimplencia => DMN cancel_sla resolve prazos (ISO); timers existem."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    await _drive_to_analise(engine, cancel_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    assert job.activity_id == "BT_AlertaSla"
    job_sla = await engine.await_timer_job(iid, "BT_SlaAnalise")
    assert job_sla.activity_id == "BT_SlaAnalise"


@pytest.mark.xfail(reason=_WORKER_KAFKA_GAP_REASON, strict=True)
async def test_cancel_routing_alimenta_dossie_humano(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """GAP-CANCEL-5: cancel_routing alimenta o dossie humano com natureza_caso/grupo_sugerido."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]

    await _drive_to_analise(engine, cancel_probe, iid)

    dossies = cancel_probe.notifications_of_type("cancel.prepare_dossier")
    assert dossies, "prepare_dossier deve ter sido executado apos BRT_Classificacao"
    assert dossies[0]["natureza_caso"] == "inadimplencia"
    assert dossies[0]["grupo_sugerido"] == "juridico-contratos"


# ===========================================================================
# Pendencia de informacao (humano solicita)
# ===========================================================================


async def test_solicitar_info_aguarda_correlacao(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """UT_AnaliseRescisao => humano SOLICITAR_INFO => aguarda msg.cancel.info_received => reabre UT."""
    inst = await start_cancel(tipo_solicitacao="inadimplencia", notificacao_previa_feita=True)
    iid = inst["id"]
    business_key = inst["businessKey"]

    ut = await _drive_to_analise(engine, cancel_probe, iid)
    await engine.complete_task_as_human(ut.id, {"decisao_cancelamento": "SOLICITAR_INFO"})
    await cancel_probe.drain()

    correlate_payload = {
        "messageName": "msg.cancel.info_received",
        "businessKey": business_key,
    }
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post("/message", json=correlate_payload)
        assert resp.status_code in (200, 204), (
            f"Correlacao msg.cancel.info_received falhou [{resp.status_code}]: {resp.text[:200]}"
        )

    ut2 = await engine.await_user_task(iid, _UT_ANALISE)
    assert "juridico-contratos" in ut2.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "SOLICITAR_INFO nunca auto-rescinde (L0)"
    await _assert_no_adverse_without_human_task(engine, iid)


# ===========================================================================
# DMN — shape e fail-safe (sem engine; varredura estatica do XML)
# ===========================================================================


def test_cancel_admissibility_sem_saida_adversa() -> None:
    """O dominio de roteamento da cancel_admissibility e EXATAMENTE
    {EFETIVAR_PEDIDO, SEGUE_ANALISE, PENDENTE_NOTIFICACAO, ANALISE_HUMANA}.
    """
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ADMISSIBILITY)
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

    assert roteamentos == {"EFETIVAR_PEDIDO", "SEGUE_ANALISE", "PENDENTE_NOTIFICACAO", "ANALISE_HUMANA"}, (
        f"dominio de roteamento inesperado: {roteamentos} — NAO pode conter RESCINDIR/SUSPENDER/NEGAR (L0)"
    )
    blob = " ".join(roteamentos)
    for proibido in ("RESCINDIR", "SUSPENDER", "NEGAR", "RETER"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na cancel_admissibility (L0)"
    assert last_rule_first_output == "ANALISE_HUMANA", "row catch-all deve rotear a ANALISE_HUMANA"


def test_cancel_routing_sem_saida_adversa() -> None:
    """cancel_routing so produz rotulos NEUTROS de natureza/grupo humano — nenhuma saida adversa."""
    from xml.etree import ElementTree as ET

    tree = ET.parse(_DMN_ROUTING)
    root = tree.getroot()

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    naturezas: set[str] = set()
    grupos: set[str] = set()
    for rule in (e for e in root.iter() if _local(e.tag) == "rule"):
        outputs = [c for c in rule if _local(c.tag) == "outputEntry"]
        assert len(outputs) >= 2, "cada rule deve ter natureza_caso e grupo_sugerido"
        nat = next((c for c in outputs[0] if _local(c.tag) == "text"), None)
        grp = next((c for c in outputs[1] if _local(c.tag) == "text"), None)
        assert nat is not None and nat.text and grp is not None and grp.text
        naturezas.add(nat.text.strip().strip('"'))
        grupos.add(grp.text.strip().strip('"'))

    esperadas = {"pedido_titular", "inadimplencia", "for_cause", "fraude_referida", "indeterminado"}
    assert naturezas == esperadas, f"naturezas inesperadas: {naturezas}"
    blob = " ".join(naturezas)
    for proibido in ("RESCINDIR", "SUSPENDER", "NEGAR", "RETER"):
        assert proibido not in blob, f"saida adversa proibida '{proibido}' na cancel_routing (L0)"
    assert grupos <= {"juridico-contratos", "gestao-contratos", "coordenacao-contratos"}, (
        f"grupo_sugerido fora do allowlist humano: {grupos}"
    )


def test_dmn_typeref_allowlist() -> None:
    """Toda DMN do processo usa typeRef in {string, boolean, integer, long, double, date}."""
    from xml.etree import ElementTree as ET

    allowed = {"string", "boolean", "integer", "long", "double", "date"}
    for dmn_path in (_DMN_ADMISSIBILITY, _DMN_ROUTING, _DMN_SLA):
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


async def test_business_key_uma_instancia_por_contrato(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa.

    GREEN since the T3.1 topic reconciliation (finding 2b FIXED — the `cancel_probe` fixture's
    drift-guard now passes naturally at setup). NOTE (unchanged from the phase-1 ledger row's
    disclosure): this test uses a HARDCODED business key, so it is single-clean-run-only — a
    rerun against a non-torn-down engine accumulates a second active instance and fails; CI
    provisions a fresh stack per run.
    """
    contrato = "CONTRATO-TESTE-IDEM-001"
    business_key = f"CANCEL-amh-{contrato}"

    first = await start_cancel(
        numero_contrato=contrato, tipo_solicitacao="inadimplencia", notificacao_previa_feita=True
    )
    existing = await engine.find_active_instances(business_key)
    assert len(existing) == 1

    active_before = await engine.find_active_instances(business_key)
    assert len(active_before) == 1
    assert active_before[0]["id"] == first["id"]
