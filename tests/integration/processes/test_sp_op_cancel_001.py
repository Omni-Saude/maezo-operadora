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
  1. `operadora.events.publish` gap, FIXED (T3.1 R2): this suite originally documented that
     Cancel's BPMN sequences `Start_SolicitacaoCancelamento -> ST_PublishReceived ->
     ST_ResolveFacts -> ...` and the unregistered publish topic was the VERY FIRST activity,
     before `resolve_facts` even ran. `cancel_probe` now also registers
     `maezo.tools.workers.events.register_events_workers` (mirrors the donor's own
     `register_phase0_workers` composition) — but see finding 2b: this does NOT flip any test to
     green (finding 2b blocks earlier still, at fixture SETUP).
  2. `register_cancel_workers` (`src/maezo/tools/workers/cancel.py`) registers handlers for only
     4 of the 7 non-shared `operadora.cancel.*` topics the BPMN declares
     (`spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn`:
     `resolve_facts`, `prepare_dossier`, `request_notification`, `send_cancellation_notice`) — 3
     spec-declared topics have NO implementing entry function at all (module's own comment,
     verbatim): `confirm_maintained_decision`, `effectuate_member_request`, `notify_sla_risk`.
     Genuine v2 implementation gap, STILL OPEN — but UNREACHABLE by any test in this file today
     (finding 2b below blocks every `cancel_probe`-based test before this one is ever hit).
  2b. PROXIMATE BLOCKER for all 22 `cancel_probe`-based xfails below (verifier's nudge, T3.1 R2 —
     live-confirmed AFTER finding 1 was fixed): `register_cancel_workers` ALSO registers 2
     handlers with NO corresponding BPMN topic at all — `operadora.cancel.process_cancel` /
     `operadora.cancel.publish_completed` (module's own comment concedes `process_cancel` is
     "registered under a function-derived topic for registry completeness" with no spec topic).
     The donor's OWN `cancel_probe` drift-guard fixture (ported verbatim — "falha AQUI,
     explicita") catches this at fixture SETUP, for EVERY test that uses `cancel_probe` — not
     just the 2 originally tagged `test_nenhum_caminho_automatizado_rescinde_contrato` (the
     128-combination L0 sweep) and `test_business_key_uma_instancia_por_contrato`. Every xfail
     below now cites this finding (not findings 1/2, which are real but unreachable) — marked
     xfail rather than editing the ported guard (constraint 2: the guard assertion is unchanged).
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

# Topicos servidos pelos workers REAIS registrados no harness (drain generico). NOTA (finding 2
# acima): v2 `register_cancel_workers` nao registra handler para `_EFFECTUATE_TOPIC`,
# `_CONFIRM_MAINTAINED_TOPIC` nem `_NOTIFY_SLA_TOPIC` hoje — mantidos na lista de drain (fixture
# verbatim do donor) porque o drift-guard abaixo compara contra `harness.registered_topics`
# (subset real), nao o contrario; nao ha risco do drain travar numa task sem handler ficar
# "parada para sempre" adicional ao gap ja documentado (mesmo mecanismo: vira incidente).
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

# T3.1 R2 (verifier's nudge — tighten these 22 reason strings to name the PROXIMATE failure):
# finding 1 (the generic events.publish gap) is FIXED, but that does NOT flip any of the 22
# tests below to green. Live-confirmed (docker compose core, CIB Seven 2.1.0) AFTER wiring
# `register_events_workers` into `cancel_probe`: EVERY test using the `cancel_probe` fixture
# ERRORs at fixture SETUP — before the fixture even returns, let alone before any BPMN activity
# runs — because finding 2b's drift-guard (`assert not missing_from_drain`, ported verbatim from
# the donor, constraint 2: unchanged) ALWAYS fires: `register_cancel_workers` unconditionally
# registers 2 extra handlers with no BPMN topic (`operadora.cancel.process_cancel`,
# `operadora.cancel.publish_completed`), independent of anything this PR touches. Finding 2b is
# therefore the PROXIMATE blocker for ALL 22 of these tests (not just the 2 originally tagged
# `_REGISTRY_MISMATCH_REASON`) — findings 1 (publish gap, fixed) and 2 (3 missing entry
# functions: confirm_maintained_decision/effectuate_member_request/notify_sla_risk) are real,
# separately-documented gaps, but NEITHER is reachable: fixture setup fails before a single
# `ST_Publish*`/`ST_*` service task ever runs. Every xfail below now cites finding 2b — the ONE
# reason that actually explains the observed error for each and every one of them (findings 1/2
# stay documented above for completeness/background, not because they are the live blocker).
_REGISTRY_MISMATCH_REASON = (
    "v2 implementation gap (finding 2b, T3.1 R2, live-confirmed by the donor's OWN drift-guard "
    "fixture in `cancel_probe` — not touched, it did its job, PROXIMATE cause for every "
    "cancel_probe-based test in this file, independent of the events.publish gap T3.1 R2 fixes): "
    "`register_cancel_workers` registers 2 EXTRA handlers (`operadora.cancel.process_cancel`, "
    "`operadora.cancel.publish_completed`) that correspond to NO `camunda:topic` in "
    "spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn — orphaned registrations. "
    "`cancel_probe`'s drift-guard (`assert not missing_from_drain`) therefore ERRORs at fixture "
    "SETUP for every test using it, before any BPMN activity runs — 3 further BPMN-declared "
    "topics with no handler at all (confirm_maintained_decision/effectuate_member_request/"
    "notify_sla_risk, finding 2) are consequently unreachable too. The module's own comment "
    "concedes this: process_cancel is 'registered under a function-derived topic for registry "
    "completeness' with no corresponding spec topic. Not a fixture bug (the ported drift-guard "
    "assertion is verbatim donor code, constraint 2); src/** fix is out of scope for this PR."
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
async def cancel_probe(engine: EngineRest) -> AsyncIterator[CancelEngineProbe]:
    """Probe que serve as external tasks com os workers reais Phase-2 de cancel."""
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
        lock_duration_ms=10_000,
        bpmn_error_allowlist=frozenset({"ERR_CANCEL_MANTER_NOT_HUMAN"}),
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
async def test_nenhum_caminho_automatizado_rescinde_contrato(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """INVARIANTE L0 (contract_termination): NENHUM caminho automatizado rescinde/suspende/nega.

    Blocked at FIXTURE SETUP today (finding 2b — the donor's own `cancel_probe` drift-guard,
    ported verbatim, correctly refuses to proceed): `register_cancel_workers` registers 2 extra
    handlers with no corresponding BPMN topic, on top of 3 BPMN topics with no handler (finding
    2). Even setting that aside, every one of the 128 instances this test would start also gets
    stuck at `ST_PublishReceived` (finding 1) before ever reaching `cancel_admissibility` — so
    the sweep would not exercise DMN routing even if finding 2b's guard were satisfied. The
    assertion body is unchanged from the donor.
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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

    assert cancel_probe.notifications_of_type("cancel.request_notification"), (
        "Worker request_notification deve ser executado em PENDENTE_NOTIFICACAO"
    )
    assert cancel_probe.has_event(_CANCEL_PENDED), "cancel.pended deve ser publicado"

    ended = await engine.activity_instances_ended(iid)
    assert not (ended & _ENDS_ADVERSOS), "Pendencia de notificacao nunca rescinde automaticamente"
    assert await engine.instance_is_active(iid), "Instancia deve aguardar no event gateway de notificacao"


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
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


@pytest.mark.xfail(reason=_REGISTRY_MISMATCH_REASON, strict=True)
async def test_business_key_uma_instancia_por_contrato(
    engine: EngineRest,
    cancel_probe: CancelEngineProbe,
    start_cancel: Callable[..., Any],
) -> None:
    """Mesmo business key: consultar antes de iniciar; nao criar 2a instancia ativa.

    Does not call `cancel_probe.drain()` (would otherwise survive finding 1, like the
    escalation/auth idempotency tests) — but it DOES depend on the `cancel_probe` fixture being
    constructed, which trips finding 2b's drift-guard at SETUP regardless (see
    `_REGISTRY_MISMATCH_REASON`).
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
