"""SP-OP-REEMBOLSO-001 v0.2.0 — suite de integracao executavel (engine CIB Seven REAL, ADR-0011: sem mock).

DL-0161: 100% analise humana. O processo so ORGANIZA o pedido; uma pessoa decide direito e valor; o financeiro
paga FORA e registra `data_pagamento`; o Lucas avisa a beneficiaria. Cada teste:

1. inicia a instancia via REST com business key `REEMB-amh-{protocolo_reembolso}`;
2. drena as external tasks com o `reembolso_probe` (workers reais + publish generico);
3. avanca o HITL completando User Tasks como humano sintetico;
4. dispara timers via job execution (NUNCA sleep);
5. verifica invariantes pela historia do engine.

Dados sinteticos obvios (`REEMB-TESTE-NNNN`, `benef-TESTE-001`, tenant `amh`). Valores em CENTAVOS INTEIROS.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers import reembolso as reembolso_module
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.harness import (
    CibSevenWorkerTransport,
    FakeKafkaPublisher,
    WorkerHarness,
)
from maezo.tools.workers.reembolso import (
    REEMBOLSO_BPMN_ERROR_ALLOWLIST,
    FatosFaixaAuto,
    register_reembolso_workers,
)

from .conftest import CIBSEVEN_BASE_URL, drain_topics
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn"
_DMN_PRAZO = _REPO / "spec/processes/dmn/reembolso_prazo.dmn"
_DMN_SLA = _REPO / "spec/processes/dmn/reembolso_sla.dmn"
_DMN_FAIXA = _REPO / "spec/processes/dmn/reembolso_auto_approval.dmn"

# Topicos servidos pelos workers REAIS (fonte da verdade: BPMN) + o publish generico compartilhado.
_REEMBOLSO_WORKER_TOPICS = [
    "operadora.events.publish",
    "operadora.reembolso.check_coverage",
    "operadora.reembolso.request_documents",
    "operadora.reembolso.analyze_request",
    "operadora.reembolso.notify_sla_risk",
    "operadora.reembolso.send_reembolso_denial",
    "operadora.reembolso.validate_auto_criteria",  # faixa B dormente (R-076): relay
    "operadora.reembolso.register_auto_approval",  # faixa B dormente (R-076): guardado (R-058)
    "operadora.reembolso.publish_completed",  # topico de convencao; nenhuma task BPMN cai nele
]

_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"
_PROCESS_COMPLETED = "agents.events.process_completed"
_REEMBOLSO_PENDED = "agents.events.reembolso.pended"
_REEMBOLSO_SLA_BREACHED = "agents.events.reembolso.sla_breached"
_REEMBOLSO_COMPLETED = "agents.events.reembolso.completed"

_UT_ANALISTA = "UT_AnaliseReembolso"
_UT_AUDITOR = "UT_RevisaoAuditorMedico"
_UT_COORDENACAO = "UT_CoordenacaoReembolso"
_UT_PENDENCIA = "UT_DecidirPendenciaExpirada"
_UT_PAGAMENTO = "UT_RegistrarPagamento"

_END_ANALISTA = "End_ReembolsoAprovadoAnalista"
_END_NEGADO = "End_ReembolsoNegado"
_END_PARCIAL = "End_ReembolsoParcial"
_END_CANCELADO = "End_ReembolsoCancelado"
_END_PROTOCOLO_INVALIDO = "End_ReembolsoProtocoloInvalido"
_END_AUTO = "End_ReembolsoAprovadoAutomatico"
_ST_REGISTRAR_AUTO = "ST_RegistrarAprovacaoAuto"
_END_DECISAO = frozenset({_END_ANALISTA, _END_NEGADO, _END_PARCIAL})
_END_ADVERSOS = frozenset({_END_NEGADO, _END_PARCIAL})
_UT_HUMANAS = frozenset({_UT_ANALISTA, _UT_AUDITOR, _UT_COORDENACAO})

_DECISAO_NEGAR = {
    "decisao_reembolso": "NEGAR",
    "justificativa": "Fora da cobertura contratual.",
    "fundamentacao_contratual": "Clausula 12.3.",
    "texto_ao_beneficiario": "Nao foi possivel aprovar este reembolso. Se quiser, peca revisao a equipe.",
    "analista_id": "analista-sintetico-001",
}


@dataclass
class ReembolsoEngineProbe:
    """Driva os workers reais de reembolso contra o engine CIB Seven."""

    engine: EngineRest
    harness: WorkerHarness
    transport: CibSevenWorkerTransport
    kafka: FakeKafkaPublisher
    worker_id: str

    def events_on(self, topic: str) -> list[dict[str, Any]]:
        return [{"topic": t, "payload": p} for (t, p, _k) in self.kafka.published if t == topic]

    def has_event(self, topic: str, **match: Any) -> bool:
        return any(all(e["payload"].get(k) == v for k, v in match.items()) for e in self.events_on(topic))

    def avisos(self) -> list[str]:
        return [e["payload"].get("resultado", "") for e in self.events_on(_PROCESS_COMPLETED)]

    async def drain(self, *, rounds: int = 30) -> None:
        await drain_topics(
            self.transport, self.harness, self.worker_id, _REEMBOLSO_WORKER_TOPICS, rounds=rounds
        )


@pytest_asyncio.fixture
async def deploy_artifacts(engine: EngineRest) -> str:
    """Deploya o BPMN + as 3 DMN de reembolso da arvore no engine real (a da faixa B e' dormente)."""
    return await engine.deploy(_BPMN, _DMN_PRAZO, _DMN_SLA, _DMN_FAIXA, name="SP-OP-REEMBOLSO-001-qa")


@pytest_asyncio.fixture
async def reembolso_probe(
    engine: EngineRest, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[ReembolsoEngineProbe]:
    """Probe que serve as external tasks com os workers reais de reembolso e o publish generico."""
    worker_id = f"qa-reembolso-worker-{uuid.uuid4().hex[:8]}"
    transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
    harness = WorkerHarness(
        transport,
        worker_id=worker_id,
        tenant=audit_tenant,
        lock_duration_ms=10_000,
        audit_sink=audit_sink,
        bpmn_error_allowlist=REEMBOLSO_BPMN_ERROR_ALLOWLIST,
    )
    kafka = FakeKafkaPublisher()
    register_reembolso_workers(harness, kafka)
    from maezo.tools.workers.events import register_events_workers

    register_events_workers(harness, kafka)
    probe = ReembolsoEngineProbe(
        engine=engine, harness=harness, transport=transport, kafka=kafka, worker_id=worker_id
    )
    try:
        yield probe
    finally:
        await transport.close()


def _unique_protocolo(prefix: str = "REEMB-TESTE") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


@pytest_asyncio.fixture
async def start_reembolso(engine: EngineRest, deploy_artifacts: str) -> Callable[..., Any]:
    """Inicia uma instancia com business key REEMB-amh-{protocolo} e o payload canonico do Lucas.

    Nenhuma variavel e pre-resolvida por maquina: o pedido traz so o que a beneficiaria informou e os
    documentos
    declarados. `aviso_beneficiario_whatsapp=True` simula uma instancia aberta pelo Lucas (avisos
    habilitados).
    """

    async def _start(**overrides: Any) -> dict[str, Any]:
        protocolo = overrides.pop("protocolo_reembolso", _unique_protocolo())
        variables: dict[str, Any] = {
            "tenant_id": "amh",
            "protocolo_reembolso": protocolo,
            "beneficiario_pseudo_id": "benef-TESTE-001",
            "matricula_beneficiario": "MAT-TESTE-001",
            "tipo_reembolso": "fora_rede",
            "categoria_procedimento": "consulta",
            "valor_solicitado_cents": 12000,  # R$ 120,00
            "data_atendimento": "2026-05-20",
            "data_solicitacao": "2026-05-25",
            "relacao_com_titular": "titular",
            "motivo_nao_rede": "sem_prestador_no_prazo",
            "documentos_refs": "[]",
            "aviso_beneficiario_whatsapp": True,
            "agent_id": "lucas",
            "conversation_id": "conv-TESTE-001",
        }
        variables.update(overrides)
        return await engine.start_by_key("SP-OP-REEMBOLSO-001", f"REEMB-amh-{protocolo}", variables)

    return _start


async def _await_end(engine: EngineRest, iid: str, *, attempts: int = 60, delay: float = 0.25) -> set[str]:
    for _ in range(attempts):
        if await engine.history_state(iid) == "COMPLETED":
            return await engine.activity_instances_ended(iid)
        await asyncio.sleep(delay)
    return await engine.activity_instances_ended(iid)


async def _drive_to_analista(engine: EngineRest, probe: ReembolsoEngineProbe, iid: str) -> Any:
    await probe.drain()
    return await engine.await_user_task(iid, _UT_ANALISTA)


async def _decidir(
    engine: EngineRest,
    probe: ReembolsoEngineProbe,
    iid: str,
    variables: dict[str, Any],
    *,
    ut_key: str = _UT_ANALISTA,
) -> None:
    ut = await engine.await_user_task(iid, ut_key)
    await engine.complete_task_as_human(ut.id, variables)
    await probe.drain()


async def _registrar_pagamento(engine: EngineRest, probe: ReembolsoEngineProbe, iid: str) -> Any:
    ut = await engine.await_user_task(iid, _UT_PAGAMENTO)
    assert "financeiro-reembolso" in ut.candidate_groups
    await engine.complete_task_as_human(ut.id, {"data_pagamento": "2026-10-30"})
    await probe.drain()
    return ut


async def _assert_no_decision_end_without_human_task(engine: EngineRest, iid: str) -> None:
    """INVARIANTE L0: nenhum terminal de decisao existe sem User Task humana no historico."""
    ended = await engine.activity_instances_ended(iid)
    if ended & _END_DECISAO:
        assert ended & _UT_HUMANAS, (
            f"terminal de decisao {ended & _END_DECISAO} SEM User Task humana. ended={ended}"
        )


# ===========================================================================
# 100% analise humana
# ===========================================================================


async def test_todo_pedido_vai_para_a_analise_humana(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    """Mesmo um pedido 'perfeito' (valor baixo, todos os documentos) para na UT; nada e decidido por
    maquina."""
    inst = await start_reembolso(valor_solicitado_cents=100, tipo_reembolso="urgencia_emergencia")
    iid = inst["id"]

    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    assert "analise-reembolso" in ut.candidate_groups

    ended = await engine.activity_instances_ended(iid)
    assert {"ST_ValidarSolicitacao", "BRT_PrazoInformativo", "BRT_SlaAnalise", "ST_PrepararDossie"} <= ended
    assert not (ended & _END_DECISAO), "nenhum desfecho antes da decisao humana"
    assert await engine.incidents(iid) == []
    # O caminho automatico v1 nao existe mais no modelo deployado.
    for removido in ("BRT_Calculo", "ST_CalculateAmount", "BRT_AutoApproval", "ST_IssuePaymentAuto"):
        assert removido not in ended
    # A faixa B (R-076) roda em toda instancia, mas DORMENTE: avalia e manda a humano.
    assert {"ST_ValidarFaixaAuto", "BRT_FaixaAuto"} <= ended
    assert _ST_REGISTRAR_AUTO not in ended
    # A situacao do prazo e informativa: hoje NAO_AVALIADO, e a instancia segue para a analise assim mesmo.
    prazo = await engine.get_variable(iid, "prazo")
    assert prazo["situacao_prazo"] == "NAO_AVALIADO"


async def test_dias_desde_atendimento_e_resolvido_pelo_worker(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)
    assert await engine.get_variable(iid, "dias_desde_atendimento") == 5


# ===========================================================================
# Desfechos humanos
# ===========================================================================


async def test_aprovado_pelo_analista_passa_pelo_pagamento_humano(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)

    await _decidir(
        engine,
        reembolso_probe,
        iid,
        {
            "decisao_reembolso": "APROVAR",
            "valor_reembolso_aprovado_cents": 12000,
            "analista_id": "analista-sintetico-001",
        },
    )
    # Aprovado NAO termina o processo: falta o financeiro pagar (fora do sistema) e registrar.
    assert await engine.instance_is_active(iid)
    assert reembolso_probe.avisos() == ["reembolso_aprovado"]
    await _registrar_pagamento(engine, reembolso_probe, iid)

    ended = await _await_end(engine, iid)
    assert _END_ANALISTA in ended
    assert not (ended & _END_ADVERSOS)
    assert reembolso_probe.avisos() == ["reembolso_aprovado", "reembolso_pago"]
    assert reembolso_probe.has_event(_REEMBOLSO_COMPLETED, desfecho="aprovado_analista")
    assert await engine.get_history_variable(iid, "data_pagamento") == "2026-10-30"
    assert "issue_payment" not in " ".join(ended), "o sistema nao emite pagamento"
    await _assert_no_decision_end_without_human_task(engine, iid)


async def test_sem_a_flag_do_lucas_nenhum_aviso_e_publicado(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    """Instancia aberta por outra origem (ou flag desligada) segue calada: so os fatos de dominio saem."""
    inst = await start_reembolso(aviso_beneficiario_whatsapp=False)
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)
    await _decidir(
        engine,
        reembolso_probe,
        iid,
        {"decisao_reembolso": "APROVAR", "valor_reembolso_aprovado_cents": 12000},
    )
    await _registrar_pagamento(engine, reembolso_probe, iid)

    assert _END_ANALISTA in await _await_end(engine, iid)
    assert reembolso_probe.avisos() == []
    assert reembolso_probe.has_event(_REEMBOLSO_COMPLETED, desfecho="aprovado_analista")


async def test_aprovado_parcial_comunica_a_reducao_e_depois_paga(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)

    await _decidir(
        engine,
        reembolso_probe,
        iid,
        {
            "decisao_reembolso": "APROVAR_PARCIAL",
            "valor_reembolso_aprovado_cents": 8000,
            "justificativa": "Valor acima do praticado.",
            "fundamentacao_contratual": "Clausula 7.",
            "texto_ao_beneficiario": (
                "Aprovamos parte do valor pedido; a equipe explica o motivo se voce quiser."
            ),
            "analista_id": "analista-sintetico-001",
        },
    )
    assert reembolso_probe.avisos() == ["reembolso_parcial"]
    await _registrar_pagamento(engine, reembolso_probe, iid)

    ended = await _await_end(engine, iid)
    assert _END_PARCIAL in ended and _END_ANALISTA not in ended
    assert "ST_ComunicarReducao" in ended
    assert reembolso_probe.avisos() == ["reembolso_parcial", "reembolso_pago"]
    assert reembolso_probe.has_event(_REEMBOLSO_COMPLETED, desfecho="aprovado_parcial")
    await _assert_no_decision_end_without_human_task(engine, iid)


async def test_negado_pelo_analista(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)
    await _decidir(engine, reembolso_probe, iid, dict(_DECISAO_NEGAR))

    ended = await _await_end(engine, iid)
    assert _END_NEGADO in ended
    assert _UT_ANALISTA in ended, "a negativa so nasce na User Task humana"
    assert reembolso_probe.avisos() == ["reembolso_negado"]
    assert reembolso_probe.has_event(_REEMBOLSO_COMPLETED, desfecho="negado_analista")
    assert _UT_PAGAMENTO not in ended, "negado nao gera pagamento"
    await _assert_no_decision_end_without_human_task(engine, iid)


async def test_negar_sem_texto_ao_beneficiario_vira_incidente(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    """O guard L0 recusa: sem `texto_ao_beneficiario` nada e avisado e o terminal de negativa nao e
    atingido."""
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)
    incompleto = {k: v for k, v in _DECISAO_NEGAR.items() if k != "texto_ao_beneficiario"}
    await _decidir(engine, reembolso_probe, iid, incompleto)

    assert await engine.incidents(iid), "a recusa do guard deve abrir incidente (retries=0)"
    ended = await engine.activity_instances_ended(iid)
    assert _END_NEGADO not in ended
    assert reembolso_probe.avisos() == []


async def test_decisao_desconhecida_nao_tem_caminho(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    """O gateway de decisao NAO tem default: valor desconhecido nao vira aprovacao silenciosa."""
    inst = await start_reembolso()
    iid = inst["id"]
    ut = await _drive_to_analista(engine, reembolso_probe, iid)

    with pytest.raises(Exception):  # noqa: B017 — o motor recusa completar (sem flow elegivel)
        await engine.complete_task_as_human(ut.id, {"decisao_reembolso": "TALVEZ"})

    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA in open_keys, "a tarefa continua aberta: nada foi decidido"
    assert not (await engine.activity_instances_ended(iid) & _END_DECISAO)


async def test_escalar_para_o_medico_auditor(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)

    await _decidir(engine, reembolso_probe, iid, {"decisao_reembolso": "SOLICITAR_AUDITOR"})
    ut = await engine.await_user_task(iid, _UT_AUDITOR)
    assert "medico-auditor" in ut.candidate_groups

    await engine.complete_task_as_human(
        ut.id,
        {
            **_DECISAO_NEGAR,
            "cid10_referencia": "Z00.0",
            "parecer_auditor": "Sem indicacao clinica.",
            "auditor_id": "auditor-sintetico-001",
        },
    )
    await reembolso_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_NEGADO in ended and _UT_AUDITOR in ended
    await _assert_no_decision_end_without_human_task(engine, iid)


# ===========================================================================
# Pedir mais informacoes (SOLICITAR_INFO) e pendencia
# ===========================================================================


async def test_solicitar_info_avisa_aguarda_e_volta_para_a_analise(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    protocolo = _unique_protocolo()
    inst = await start_reembolso(protocolo_reembolso=protocolo)
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)

    await _decidir(
        engine,
        reembolso_probe,
        iid,
        {"decisao_reembolso": "SOLICITAR_INFO", "info_solicitada": "Pode enviar a nota fiscal legivel?"},
    )
    assert reembolso_probe.avisos() == ["reembolso_info_solicitada"]
    # O TEXTO da pergunta e PHI e NAO viaja no evento: so o fato (sem `info_solicitada`).
    for e in reembolso_probe.events_on(_PROCESS_COMPLETED):
        assert "info_solicitada" not in e["payload"]
    assert reembolso_probe.has_event(_REEMBOLSO_PENDED, tenant_id="amh", protocolo_reembolso=protocolo)
    assert await engine.instance_is_active(iid)

    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post(
            "/message",
            json={"messageName": "msg.reembolso.docs_received", "businessKey": inst["businessKey"]},
        )
        assert resp.status_code in (200, 204), f"correlacao falhou [{resp.status_code}]: {resp.text[:200]}"
    await reembolso_probe.drain()

    # A resposta volta DIRETO para a analise (nao ha mais admissibilidade por maquina).
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA in open_keys
    assert await engine.activity_instance_count(iid, _UT_ANALISTA) == 2
    assert not (await engine.activity_instances_ended(iid) & _END_DECISAO)


async def test_pendencia_expira_decisao_humana_nunca_auto_nega(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)
    await _decidir(
        engine,
        reembolso_probe,
        iid,
        {"decisao_reembolso": "SOLICITAR_INFO", "info_solicitada": "Falta o pedido medico."},
    )

    job = await engine.await_timer_job(iid, "ICE_PrazoPendencia")
    await engine.execute_job(job.id)
    await reembolso_probe.drain()

    ut = await engine.await_user_task(iid, _UT_PENDENCIA)
    assert "analise-reembolso" in ut.candidate_groups
    await engine.complete_task_as_human(ut.id, {"decisao_pendencia": "cancelar_solicitacao"})
    await reembolso_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_CANCELADO in ended
    assert not (ended & _END_ADVERSOS), "expirar a pendencia nunca nega nem reduz (L0)"
    assert reembolso_probe.has_event(_REEMBOLSO_COMPLETED, desfecho="cancelado_pendencia")


async def test_conceder_prazo_extra_volta_a_aguardar(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)
    await _decidir(
        engine,
        reembolso_probe,
        iid,
        {"decisao_reembolso": "SOLICITAR_INFO", "info_solicitada": "Falta a nota fiscal."},
    )
    job = await engine.await_timer_job(iid, "ICE_PrazoPendencia")
    await engine.execute_job(job.id)
    await reembolso_probe.drain()
    await _decidir(
        engine, reembolso_probe, iid, {"decisao_pendencia": "conceder_prazo_extra"}, ut_key=_UT_PENDENCIA
    )

    assert await engine.instance_is_active(iid)
    novo = await engine.await_timer_job(iid, "ICE_PrazoPendencia")
    assert novo.id != job.id, "o prazo extra rearma o timer de pendencia"


# ===========================================================================
# Timers de SLA
# ===========================================================================


async def test_timer_alerta_sla_nao_interruptivo(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_AlertaSla")
    await engine.execute_job(job.id)
    await reembolso_probe.drain()

    assert "ST_NotificarRiscoSla" in await engine.activity_instances_ended(iid)
    assert _UT_ANALISTA in {t.task_definition_key for t in await engine.list_user_tasks(iid)}


async def test_timer_sla_estourado_coordenacao_assume_e_nao_auto_nega(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    inst = await start_reembolso()
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)

    job = await engine.await_timer_job(iid, "BT_SlaAnalise")
    await engine.execute_job(job.id)
    await reembolso_probe.drain()

    assert reembolso_probe.has_event(_REEMBOLSO_SLA_BREACHED)
    ut = await engine.await_user_task(iid, _UT_COORDENACAO)
    assert "coordenacao-reembolso" in ut.candidate_groups
    assert _UT_ANALISTA not in {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert not (await engine.activity_instances_ended(iid) & _END_DECISAO), "estourar o SLA nunca decide"

    await engine.complete_task_as_human(ut.id, dict(_DECISAO_NEGAR))
    await reembolso_probe.drain()
    ended = await _await_end(engine, iid)
    assert _END_NEGADO in ended and _UT_COORDENACAO in ended
    await _assert_no_decision_end_without_human_task(engine, iid)


# ===========================================================================
# Validacao de origem e idempotencia
# ===========================================================================


async def test_protocolo_invalido_atinge_o_terminal_neutro(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    """`protocolo_reembolso` em branco => boundary BE_ReembolsoProtocoloInvalido => terminal NEUTRO, sem
    incidente."""
    inst = await start_reembolso(protocolo_reembolso="")
    iid = inst["id"]
    await reembolso_probe.drain()

    ended = await _await_end(engine, iid)
    assert _END_PROTOCOLO_INVALIDO in ended
    assert not (ended & _END_DECISAO), "validacao de origem nunca decide pagar/negar"
    assert await engine.incidents(iid) == []


async def test_business_key_uma_instancia_por_protocolo(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    protocolo = _unique_protocolo()
    inst = await start_reembolso(protocolo_reembolso=protocolo)
    ativas = await engine.find_active_instances(inst["businessKey"])
    assert len(ativas) == 1
    assert inst["businessKey"] == f"REEMB-amh-{protocolo}"


# ===========================================================================
# DL-0163: a resposta da beneficiaria ao "pedir informacao", pelo turno REAL do Lucas, contra o motor real
# ===========================================================================


async def test_resposta_da_beneficiaria_pelo_lucas_volta_a_analise_com_texto_e_arquivos(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    """O BPMN marca `aguardando_resposta`; o turno do Lucas (transporte HTTP real, sem dublê do motor) le'
    so' essa variavel, junta texto e arquivo e, no TERMINEI, faz UMA correlacao auditada que devolve o pedido
    a analise com `resposta_beneficiario` (pelo scrub) e `documentos_adicionais_refs`."""
    from datetime import UTC, datetime

    from maezo.domain.reembolso.pedido import PedidoReembolso
    from maezo.gateway.reembolso_anexos import AnexoGuardado
    from maezo.platform.webhooks.whatsapp.reembolso_store import MemoriaColetaReembolsoStore
    from maezo.platform.webhooks.whatsapp.reembolso_turno import ReembolsoTurno
    from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
    from maezo.tools.mcp_whatsapp.media import MidiaBaixada
    from tests.support.audit_fakes import FakeStartAuditSink

    protocolo = f"REE-{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}"
    conversa = "wa:amh:hk1_" + uuid.uuid4().hex
    inst = await start_reembolso(protocolo_reembolso=protocolo, conversation_id=conversa)
    iid = inst["id"]
    await _drive_to_analista(engine, reembolso_probe, iid)
    assert await engine.get_history_variable(iid, "aguardando_resposta") is False

    class _Custodia:
        async def guardar_anexo(self, *, conversation_id: str, mime: str, conteudo: bytes) -> AnexoGuardado:
            return AnexoGuardado("rdoc_" + "1" * 32, "c" * 64)

    class _Baixador:
        async def baixar(self, media_id: str) -> MidiaBaixada:
            return MidiaBaixada(conteudo=b"%PDF teste", mime="application/pdf")

    store = MemoriaColetaReembolsoStore()
    agora = datetime.now(UTC)
    await store.gravar(
        conversa,
        PedidoReembolso(etapa="aberto"),
        protocolo=protocolo,
        agora=agora,
        expira_em=agora.replace(year=agora.year + 1),
        revisao_esperada=None,
    )
    transporte = CibSevenHttpTransport(CIBSEVEN_BASE_URL)
    turno = ReembolsoTurno(
        tenant_id="amh",
        store=store,
        cibseven=transporte,  # type: ignore[arg-type]
        audit_sink=FakeStartAuditSink(),
        custodia=_Custodia(),
        baixador=_Baixador(),
    )

    async def _texto(texto: str) -> Any:
        return await turno.tratar_texto(
            conversation_id=conversa, texto=texto, beneficiario_pseudo_id="b", sinal_de_emergencia=False
        )

    try:
        # Sem pendencia, a mensagem NAO e' resposta (segue o fluxo de sempre).
        assert await _texto("bom dia") is None

        await _decidir(
            engine,
            reembolso_probe,
            iid,
            {"decisao_reembolso": "SOLICITAR_INFO", "info_solicitada": "Qual o nome do profissional?"},
        )
        assert await engine.get_history_variable(iid, "aguardando_resposta") is True

        assert (await _texto("Dra. Fulana, CPF 123.456.789-01")).evento == "resposta_iniciada"
        arquivo = await turno.tratar_arquivo(
            conversation_id=conversa, media_id="m1", mime_declarado="image/png"
        )
        assert arquivo is not None and arquivo.evento == "resposta_arquivo"
        assert (await _texto("terminei")).evento == "resposta_enviada"
        await reembolso_probe.drain()
    finally:
        await transporte.close()

    # Voltou a analise, a espera acabou, a resposta (sem o CPF) e o arquivo estao no processo.
    open_keys = {t.task_definition_key for t in await engine.list_user_tasks(iid)}
    assert _UT_ANALISTA in open_keys
    assert await engine.activity_instance_count(iid, _UT_ANALISTA) == 2
    assert await engine.get_variable(iid, "aguardando_resposta") is False
    resposta = await engine.get_variable(iid, "resposta_beneficiario")
    assert "Dra. Fulana" in resposta and "123.456.789-01" not in resposta
    # `deserialize=False`: como o codigo de producao le' (`deserializeValues=false`); senao vem o bean
    # do Jackson em vez do JSON.
    import json

    adicionais = json.loads(await engine.get_variable(iid, "documentos_adicionais_refs", deserialize=False))
    assert [d["tipo"] for d in adicionais] == ["complemento"]


async def test_conclusao_tipada_do_portal_passa_pela_guarda_l0(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    """O que a conclusao interina do portal manda (valor como Long, textos, analista_id do servidor) e' o que
    a guarda L0 de `send_reembolso_denial` aceita: parcial vai ao pagamento humano, sem incidente."""
    inst = await start_reembolso()
    iid = inst["id"]
    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    async with httpx.AsyncClient(base_url=CIBSEVEN_BASE_URL, timeout=20.0) as client:
        resp = await client.post(
            f"/task/{ut.id}/complete",
            json={
                "variables": {
                    "decisao_reembolso": {"value": "APROVAR_PARCIAL", "type": "String"},
                    "valor_reembolso_aprovado_cents": {"value": 8000, "type": "Long"},
                    "justificativa": {"value": "Teto contratual", "type": "String"},
                    "fundamentacao_contratual": {"value": "Clausula 5", "type": "String"},
                    "texto_ao_beneficiario": {"value": "Aprovamos parte do valor.", "type": "String"},
                    "analista_id": {"value": "prn_" + "a" * 32, "type": "String"},
                }
            },
        )
        assert resp.status_code in (200, 204), resp.text[:200]
    await reembolso_probe.drain()
    assert await engine.incidents(iid) == []
    pagamento = await engine.await_user_task(iid, _UT_PAGAMENTO)
    assert "financeiro-reembolso" in pagamento.candidate_groups


# ===========================================================================
# R-076 / R-070 opcao B — faixa B DORMENTE sobre a v2 (decisao do dono de 2026-11-01, PR #1149)
# ===========================================================================
#
# Producao: teto 0 (L0-core e amh), kill-switch de politica fechado, R-058 False, nenhuma fonte de fatos.
# `test_faixa_b_dormente_com_defaults_mesmo_com_sementes_hostis` prova isso contra o motor. Nos demais
# testes cada trava e' aberta APENAS no probe — teto/kill-switch/exposicao por uma matriz sintetica (seam
# `reembolso_ceiling_resolver`), os fatos por uma fonte fixa (seam `reembolso_fonte_faixa_auto`) e o R-058
# por monkeypatch — para provar que a DMN `reembolso_auto_approval` aprova SO' o caso integral e manda a
# humano cada violacao, pela regra humana dedicada (ruleId no historico do motor).

_HARD_BLOCK_LANE = """\
  clinical_decision:      { level: L0, hard: true }
  authorization_denial:   { level: L0, hard: true }
  nip_manter_negativa:    { level: L0, hard: true }
  fraud_accusation:       { level: L0, hard: true }
  contract_termination:   { level: L0, hard: true }
"""

_LANE_POLITICA: dict[str, Any] = {
    "max_value_brl": 300,
    "faixa_auto_desligada": "false",
    "exposicao_max_aprovacoes_beneficiario_30d": 2,
    "exposicao_max_valor_beneficiario_30d_brl": 600,
    "orcamento_diario_tenant_brl": 3000,
}

_LANE_FATOS = FatosFaixaAuto(
    requer_avaliacao_clinica=False,
    valor_tabela_cents=12000,
    limite_contratual_cents=20000,
    valor_nf_verificado=True,
    aprovacoes_auto_benef_30d=0,
    valor_auto_benef_30d_cents=0,
    valor_auto_tenant_dia_cents=0,
)

#: Pedido-base da faixa: fase 1 (livre escolha + consulta), R$ 100,00.
_LANE_START: dict[str, Any] = {"tipo_reembolso": "livre_escolha", "valor_solicitado_cents": 10000}

_lane_seq = itertools.count()


@pytest_asyncio.fixture
async def lane_probe(
    engine: EngineRest,
    audit_sink: Any,
    audit_tenant: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Callable[..., ReembolsoEngineProbe]]:
    """Fabrica de probes com a faixa B configuravel (so' neste processo de teste)."""
    transports: list[CibSevenWorkerTransport] = []

    def _make(
        *,
        politica: dict[str, Any] | None = None,
        fatos: FatosFaixaAuto = _LANE_FATOS,
        liberado: bool = True,
    ) -> ReembolsoEngineProbe:
        monkeypatch.setattr(reembolso_module, "REEMBOLSO_AUTO_PAGAMENTO_LIBERADO", liberado)
        params = ", ".join(
            f"{k}: {v}" for k, v in (politica if politica is not None else _LANE_POLITICA).items()
        )
        core_dir = tmp_path / f"lane-{next(_lane_seq)}"  # matriz e' lru_cache'd por caminho
        core_dir.mkdir()
        core = core_dir / "L0-core.yaml"
        core.write_text(
            f"version: 1\nactions:\n{_HARD_BLOCK_LANE}"
            f"  reembolso_auto_approval: {{ level: L2, params: {{ {params} }} }}\n",
            encoding="utf-8",
        )
        worker_id = f"qa-reembolso-lane-{uuid.uuid4().hex[:8]}"
        transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
        transports.append(transport)
        harness = WorkerHarness(
            transport,
            worker_id=worker_id,
            tenant=audit_tenant,
            lock_duration_ms=10_000,
            audit_sink=audit_sink,
            bpmn_error_allowlist=REEMBOLSO_BPMN_ERROR_ALLOWLIST,
        )
        kafka = FakeKafkaPublisher()
        register_reembolso_workers(
            harness,
            kafka,
            reembolso_ceiling_resolver=CeilingResolver(core_path=core),
            reembolso_fonte_faixa_auto=lambda _input: fatos,
        )
        from maezo.tools.workers.events import register_events_workers

        register_events_workers(harness, kafka)
        return ReembolsoEngineProbe(
            engine=engine, harness=harness, transport=transport, kafka=kafka, worker_id=worker_id
        )

    try:
        yield _make
    finally:
        for transport in transports:
            await transport.close()


async def _rule_ids_da_faixa(engine: EngineRest, iid: str) -> set[str]:
    """`ruleId` das saidas de `reembolso_auto_approval` nesta instancia (historico do motor)."""
    rows = await engine.history_decision_instances(
        instance_id=iid, decision_key="reembolso_auto_approval", include_outputs=True
    )
    assert rows, "BRT_FaixaAuto nao avaliou a DMN da faixa B"
    return {o.get("ruleId") for row in rows for o in (row.get("outputs") or [])}


async def test_faixa_b_dormente_com_defaults_mesmo_com_sementes_hostis(
    engine: EngineRest, reembolso_probe: ReembolsoEngineProbe, start_reembolso: Callable[..., Any]
) -> None:
    """DORMENCIA: workers de producao (sem seams), pedido perfeito da fase 1 e TODA semente que abriria a
    faixa se a DMN lesse a partida — o pedido vai a analise humana e nada e' aprovado por maquina."""
    inst = await start_reembolso(
        **_LANE_START,
        reembolso_auto_liberado=True,
        origem_aprovacao="AUTO_L2",
        faixa_auto_desligada=False,
        teto_l2_cents=999999,
        valor_tabela_dmn_cents=999999,
        limite_contratual_cents=999999,
        valor_nf_verificado=True,
        requer_avaliacao_clinica=False,
        aprovacoes_auto_benef_30d=0,
        limite_aprovacoes_auto_benef_30d=9,
        valor_auto_benef_30d_cents=0,
        limite_valor_auto_benef_30d_cents=999999,
        valor_auto_tenant_dia_cents=0,
        orcamento_auto_tenant_dia_cents=999999,
    )
    iid = inst["id"]
    ut = await _drive_to_analista(engine, reembolso_probe, iid)
    assert "analise-reembolso" in ut.candidate_groups
    ended = await engine.activity_instances_ended(iid)
    assert _ST_REGISTRAR_AUTO not in ended and _END_AUTO not in ended
    # Desligada pela politica real (kill-switch true), nao pela semente.
    assert await _rule_ids_da_faixa(engine, iid) == {"r_desligada_humano"}
    assert await engine.get_variable(iid, "reembolso_auto_liberado") is False
    assert await engine.get_variable(iid, "origem_aprovacao") == "HUMANA"
    assert await engine.get_variable(iid, "teto_l2_cents") == 0
    assert await engine.incidents(iid) == []


async def test_happy_path_aprovacao_automatica_l2_com_teto_positivo(
    engine: EngineRest,
    lane_probe: Callable[..., ReembolsoEngineProbe],
    start_reembolso: Callable[..., Any],
) -> None:
    """Faixa aberta SO' no probe + tudo favoravel => AUTO_APROVAR integral do valor SOLICITADO.

    B1 (revisao independente): a partida traz sementes HOSTIS abaixo do solicitado
    (`valor_calculado_tabela_cents=9000` e `valor_tabela_dmn_cents=9000`). Se a DMN lesse a partida, a
    celula de tabela mandaria a humano; o teste so' passa porque o relay escreveu 12000 no escopo do
    processo com o nome que a DMN le. O valor aprovado e' 10000 (o solicitado), nunca a tabela, e o
    pagamento continua humano (financeiro em UT_RegistrarPagamento).
    """
    probe = lane_probe()
    inst = await start_reembolso(
        **_LANE_START, valor_calculado_tabela_cents=9000, valor_tabela_dmn_cents=9000
    )
    iid = inst["id"]
    await probe.drain()

    assert await _rule_ids_da_faixa(engine, iid) == {"r_auto"}
    ended = await engine.activity_instances_ended(iid)
    assert _ST_REGISTRAR_AUTO in ended
    assert _UT_ANALISTA not in ended
    assert not [t for t in await engine.list_user_tasks(iid) if t.task_definition_key in _UT_HUMANAS]
    await _registrar_pagamento(engine, probe, iid)

    ended = await _await_end(engine, iid)
    assert _END_AUTO in ended, f"Deve atingir End_ReembolsoAprovadoAutomatico. ended={ended}"
    assert not (ended & _END_DECISAO), "aprovacao automatica nao usa terminal de decisao humana"
    assert await engine.get_history_variable(iid, "valor_reembolso_aprovado_cents") == 10000, (
        "o caminho automatico aprova o valor SOLICITADO (10000), nunca a tabela"
    )
    assert probe.has_event(_REEMBOLSO_COMPLETED, desfecho="aprovado_automatico")
    assert probe.avisos() == ["reembolso_aprovado", "reembolso_pago"]
    assert await engine.incidents(iid) == []


_FATOS = dataclasses.replace

# (id, politica, fatos, liberado, start overrides, ruleId esperado) — cada caso e' decidido pela regra
# humana dedicada (ruleId asserido no historico do motor). Na v2 nao ha `dentro_teto_l2`/`dentro_tabela`
# vindos do worker, entao teto, tabela e limite contratual sao celulas INDEPENDENTES da DMN; fato ausente
# (sem limite contratual, sem contador) nao casa com nada e cai no catch-all; com o R-058 fechado a DMN
# diz AUTO_APROVAR (r_auto) e quem barra e' o gateway.
_CASOS_HUMANOS: list[tuple[str, dict[str, Any], FatosFaixaAuto, bool, dict[str, Any], str]] = [
    ("interruptor_r058_fechado", _LANE_POLITICA, _LANE_FATOS, False, {}, "r_auto"),
    ("teto_zero", {**_LANE_POLITICA, "max_value_brl": 0}, _LANE_FATOS, True, {}, "r_acima_teto_humano"),
    (
        "acima_do_teto_nunca_paga_ate_o_teto",
        {**_LANE_POLITICA, "max_value_brl": 100},
        _LANE_FATOS,
        True,
        {"valor_solicitado_cents": 11000},
        "r_acima_teto_humano",
    ),
    (
        "kill_switch_de_politica",
        {**_LANE_POLITICA, "faixa_auto_desligada": "true"},
        _LANE_FATOS,
        True,
        {},
        "r_desligada_humano",
    ),
    (
        "kill_switch_ausente",
        {k: v for k, v in _LANE_POLITICA.items() if k != "faixa_auto_desligada"},
        _LANE_FATOS,
        True,
        {},
        "r_desligada_humano",
    ),
    (
        "nf_nao_verificada",
        _LANE_POLITICA,
        _FATOS(_LANE_FATOS, valor_nf_verificado=False),
        True,
        {},
        "r_nf_nao_verificada_humano",
    ),
    (
        "sem_limite_contratual",
        _LANE_POLITICA,
        _FATOS(_LANE_FATOS, limite_contratual_cents=None),
        True,
        {},
        "r_auto_catchall",
    ),
    (
        "acima_do_limite_contratual",
        _LANE_POLITICA,
        _FATOS(_LANE_FATOS, limite_contratual_cents=9000),
        True,
        {},
        "r_acima_limite_contratual_humano",
    ),
    (
        "acima_da_tabela",
        _LANE_POLITICA,
        _LANE_FATOS,
        True,
        {"valor_solicitado_cents": 15000},
        "r_fora_tabela_humano",
    ),
    ("sem_tabela", _LANE_POLITICA, _FATOS(_LANE_FATOS, valor_tabela_cents=None), True, {}, "r_auto_catchall"),
    (
        "valor_zero",
        _LANE_POLITICA,
        _LANE_FATOS,
        True,
        {"valor_solicitado_cents": 0},
        "r_valor_ausente_humano",
    ),
    (
        "contador_de_exposicao_ausente",
        _LANE_POLITICA,
        _FATOS(_LANE_FATOS, aprovacoes_auto_benef_30d=None),
        True,
        {},
        "r_auto_catchall",
    ),
    (
        "exposicao_quantidade_esgotada",
        _LANE_POLITICA,
        _FATOS(_LANE_FATOS, aprovacoes_auto_benef_30d=2),
        True,
        {},
        "r_exposicao_humano",
    ),
    (
        "exposicao_valor_esgotada",
        _LANE_POLITICA,
        _FATOS(_LANE_FATOS, valor_auto_benef_30d_cents=55000),
        True,
        {},
        "r_exposicao_valor_humano",
    ),
    (
        "orcamento_tenant_esgotado",
        _LANE_POLITICA,
        _FATOS(_LANE_FATOS, valor_auto_tenant_dia_cents=295000),
        True,
        {},
        "r_orcamento_tenant_humano",
    ),
    (
        "tipo_fora_da_fase1",
        _LANE_POLITICA,
        _LANE_FATOS,
        True,
        {"tipo_reembolso": "urgencia_emergencia"},
        "r_tipo_fora_fase1_humano",
    ),
    (
        "categoria_fora_da_fase1",
        _LANE_POLITICA,
        _LANE_FATOS,
        True,
        {"categoria_procedimento": "exame", "valor_solicitado_cents": 8000},
        "r_categoria_fora_fase1_humano",
    ),
    (
        "item_art_10b_fora",
        _LANE_POLITICA,
        _LANE_FATOS,
        True,
        {"categoria_procedimento": "bolsa_colostomia"},
        "r_categoria_fora_fase1_humano",
    ),
    (
        "merito_clinico",
        _LANE_POLITICA,
        _FATOS(_LANE_FATOS, requer_avaliacao_clinica=True),
        True,
        {},
        "r_clinica_humano",
    ),
]


@pytest.mark.parametrize(
    ("politica", "fatos", "liberado", "overrides", "rule_id"),
    [pytest.param(p, f, lib, o, r, id=i) for i, p, f, lib, o, r in _CASOS_HUMANOS],
)
async def test_opcao_b_cada_violacao_vai_a_humano_e_nunca_paga_automatico(
    engine: EngineRest,
    lane_probe: Callable[..., ReembolsoEngineProbe],
    start_reembolso: Callable[..., Any],
    politica: dict[str, Any],
    fatos: FatosFaixaAuto,
    liberado: bool,
    overrides: dict[str, Any],
    rule_id: str,
) -> None:
    """Teste NEGATIVO da guarda de alcada da faixa B (R-070 opcao B, R-076; reparo B2).

    Tudo aberto como no teste positivo, menos UMA condicao. O `ruleId` que decidiu e' o da regra humana
    dedicada (ou o catch-all/r_auto declarados); o pedido cai em `UT_AnaliseReembolso` e
    `ST_RegistrarAprovacaoAuto` nao executa: nunca aprovacao parcial, nunca "ate o teto".
    """
    probe = lane_probe(politica=politica, fatos=fatos, liberado=liberado)
    inst = await start_reembolso(**{**_LANE_START, **overrides})
    iid = inst["id"]

    ut = await _drive_to_analista(engine, probe, iid)
    assert "analise-reembolso" in ut.candidate_groups

    assert await _rule_ids_da_faixa(engine, iid) == {rule_id}
    ended = await engine.activity_instances_ended(iid)
    assert _ST_REGISTRAR_AUTO not in ended, f"faixa B aprovou com violacao da opcao B: {ended}"
    assert _END_AUTO not in ended
    assert await engine.get_variable(iid, "origem_aprovacao") == "HUMANA"
    assert await engine.incidents(iid) == []
