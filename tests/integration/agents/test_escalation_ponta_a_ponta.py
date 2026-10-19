"""SP-OP-ESCALATION-001 de ponta a ponta: mensagem -> Helena -> motor -> humano -> Kafka -> retomada.

O que e' REAL: o `HelenaDispatcher` e o grafo da Helena (checkpointer em memoria do LangGraph, a
costura cercada `gate_cibseven`), o motor CIB Seven (start do SP-OP-ESCALATION-001 no tenant da
conversa, DMN `escalation_routing`, User Task, historico), os workers de producao
(`register_events_workers` + `register_escalation_workers`) publicando com o `AioKafkaEventsProducer`
REAL no broker, a conclusao humana pela REST do motor (o mesmo `POST /task/{id}/complete` do portal),
o consumidor de producao (`AioKafkaBridgeConsumer`) com o laco de producao (`run_resume_loop`), o
`ResumeHandler` com a fonte de producao `EngineHistoryInstructionSource` (le o HISTORICO do motor) e
a fila morta de producao (`build_dlq_shunt`: publicador Kafka real + auditoria Postgres real).

O que e' FALSO, e so' isto: o modelo (respostas deterministicas: o classificador diz "pedido de
humano"), o remetente do WhatsApp (captura o envio; nada sai para a Meta) e a custodia do telefone
(KMS local, `tests/support/recipient_custody_fakes.py`). Dados sinteticos: telefone na faixa
`5511900000xxx`, tenant por execucao.

Os tres casos do contrato do consumidor (`agent_resume.py`, Filtro / Cruzamento com o historico):

* `devolvido_agente` -> EXATAMENTE uma mensagem, para o numero da custodia, com o texto que a
  Helena compoe da `notas_resolucao` (golden de `compor_mensagem_de_retomada`, nunca digitado);
* `resolvido_humano` -> nada e' enviado e o offset do grupo avanca;
* historico divergente -> um `process_completed` FORJADO (o evento e' Zona Geral, qualquer produtor
  do topico pode publica-lo) que diz `devolvido_agente` para uma instancia REAL que o humano concluiu
  como `resolvido_humano`: vai para `agents.events.process_completed.dlq` com o motivo
  `resume_history_mismatch` e nada e' enviado.

Sem `sleep`: drenagem e consumo em rodadas limitadas (long-poll do motor, `wait_for` no broker).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.helena.graph import compor_mensagem_de_retomada
from maezo.gateway.audit_postgres import PostgresAuditSink
from maezo.gateway.effect_pep import DecisionContext
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.cibseven import gate_cibseven
from maezo.platform.integrations.agent_resume import (
    PROCESS_COMPLETED_TOPIC,
    EngineHistoryInstructionSource,
    NotifyTeamAlerter,
    ResumeHandler,
    run_resume_loop,
)
from maezo.platform.integrations.events_kafka_producer import AioKafkaEventsProducer
from maezo.platform.integrations.notifications_bridge import (
    REASON_RESUME_HISTORY_MISMATCH,
    NotificationsBridgeSettings,
    build_dlq_shunt,
)
from maezo.platform.topic_registry import dlq_topic_for
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.runtime.checkpoint import Checkpointer
from maezo.runtime.worker_runtime.service import PRODUCTION_BPMN_ERROR_ALLOWLIST
from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
from maezo.tools.mcp_whatsapp.server import WhatsAppServer
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.escalation import register_escalation_workers
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, WorkerHarness
from tests.integration.processes.engine_rest import EngineRest
from tests.support.dmn_first_hit import DMN_DIR, evaluate, read_live_table
from tests.support.engine_tenant_deploy import deploy_for_tenant
from tests.support.escalation_chain import (
    ConsumidorLimitado,
    CustodiaFalsa,
    consumidor_kafka_real,
    custodia_falsa,
    drenar_por_chave,
    grupo_unico,
    instancia_encerrada,
    kafka_bootstrap_servers,
    mensagem_da_chave,
    offsets_confirmados,
    tarefas_humanas,
)

from ._engine_helpers import active_instances, candidate_groups, process_variables

pytestmark = pytest.mark.integration

#: Prazo de cada leitura no broker (limite, nunca espera fixa).
_PRAZO_KAFKA_S = 30.0


@pytest.fixture(scope="module", autouse=True)
def _escalation_do_tenant(engine_base_url: str, audit_tenant: str) -> None:
    """A Helena inicia no tenant da conversa: ESCALATION + a DMN dela DO tenant desta execucao."""
    deploy_for_tenant(engine_base_url, audit_tenant, "SP-OP-ESCALATION-001")


class _ModeloDeterministico:
    """O classificador diz "pedido de humano"; qualquer redacao seguinte e' um texto neutro."""

    def __init__(self) -> None:
        self.chamadas = 0

    async def generate(self, prompt: str, **_: Any) -> str:
        self.chamadas += 1
        if self.chamadas == 1:
            return '{"intent": "human_request", "population": "none", "psychosocial_risk": false}'
        return "Recebemos sua mensagem e encaminhamos seu caso para a nossa equipe."


class _RemetenteFalso(WhatsAppServer):
    """Captura o envio. Nada sai para a Meta."""

    def __init__(self) -> None:
        super().__init__()
        self.enviados: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        self.enviados.append((to, text))
        return {"messages": [{"id": f"wamid.teste.{len(self.enviados)}"}]}


@dataclass
class _Cadeia:
    tenant: str
    telefone: str
    dispatcher: HelenaDispatcher
    remetente: _RemetenteFalso
    custodia: CustodiaFalsa
    engine: EngineRest
    client: httpx.AsyncClient
    harness: WorkerHarness
    worker_id: str
    producer: AioKafkaEventsProducer
    audit_dsn: str


@pytest.fixture
async def cadeia(
    engine_base_url: str,
    engine_client: httpx.AsyncClient,
    audit_sink: Any,
    audit_tenant: str,
    audit_pg: tuple[str, str],
) -> AsyncIterator[_Cadeia]:
    token = uuid.uuid4().hex[:8]
    # Faixa sintetica (`mcp_whatsapp.send_message` suprime envio para ela em qualquer ambiente).
    telefone = f"5511900000{int(token, 16) % 1000:03d}"
    seam = SeamContext(tenant=audit_tenant, principal="helena", decision=DecisionContext())
    cibseven = CibSevenHttpTransport(engine_base_url, tenant_id=audit_tenant, timeout=30.0)
    dmn = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    remetente = _RemetenteFalso()
    custodia = custodia_falsa(audit_tenant)
    dispatcher = HelenaDispatcher(
        tenant_id=audit_tenant,
        inference=_ModeloDeterministico(),  # type: ignore[arg-type]
        dmn=dmn,
        cibseven=gate_cibseven(cibseven, seam),
        whatsapp_client=remetente,
        pseudonymizer=Pseudonymizer(),
        audit_sink=audit_sink,
        checkpointer=Checkpointer(saver=InMemorySaver()),
        seam_context=seam,
        recipient_vault=custodia.receptor,
    )
    worker_id = f"esc-ponta-{token}"
    transporte = CibSevenWorkerTransport(engine_base_url, timeout=30.0)
    producer = AioKafkaEventsProducer(bootstrap_servers=kafka_bootstrap_servers())
    harness = WorkerHarness(
        transporte,
        worker_id=worker_id,
        tenant=audit_tenant,
        audit_sink=audit_sink,
        bpmn_error_allowlist=PRODUCTION_BPMN_ERROR_ALLOWLIST,
    )
    register_events_workers(harness, producer, tenant_id=audit_tenant)
    register_escalation_workers(harness, producer)
    engine = EngineRest(engine_base_url)
    try:
        yield _Cadeia(
            tenant=audit_tenant,
            telefone=telefone,
            dispatcher=dispatcher,
            remetente=remetente,
            custodia=custodia,
            engine=engine,
            client=engine_client,
            harness=harness,
            worker_id=worker_id,
            producer=producer,
            audit_dsn=audit_pg[0],
        )
    finally:
        await producer.close()
        await transporte.close()
        await engine.aclose()
        await dmn.close()
        await cibseven.close()


@dataclass
class _Caso:
    conversation_id: str
    business_key: str
    instance_id: str
    nota: str


async def _escalar_e_concluir(c: _Cadeia, *, resultado: str, nota: str) -> _Caso:
    """Mensagem -> Helena -> instancia no motor -> workers reais -> User Task no grupo da DMN ->
    o humano conclui pela REST do motor -> workers reais publicam o fim no Kafka real."""
    turno = await c.dispatcher.dispatch(
        InboundMessage(
            from_number=c.telefone,
            text="quero falar com uma pessoa",
            message_id=f"wamid.in.{uuid.uuid4().hex}",
        )
    )
    assert turno["escalation_started"] is True
    conversation_id = str(turno["conversation_id"])
    business_key = f"ESC-{c.tenant}-{conversation_id}"
    ativas = await active_instances(c.client, business_key)
    assert len(ativas) == 1, f"esperava UMA instancia ativa para {business_key}"
    assert ativas[0]["tenantId"] == c.tenant
    instance_id = str(ativas[0]["id"])

    async def _ha_tarefa() -> bool:
        return bool(await tarefas_humanas(c.client, instance_id))

    await drenar_por_chave(
        c.client,
        c.harness,
        worker_id=c.worker_id,
        business_key=business_key,
        instance_id=instance_id,
        pronto=_ha_tarefa,
    )
    tarefas = await tarefas_humanas(c.client, instance_id)
    assert [t["taskDefinitionKey"] for t in tarefas] == ["UT_TratarEscalonamento"]
    # O grupo vem da DMN viva, avaliada com as variaveis que o MOTOR tem (nunca digitado).
    variaveis = await process_variables(c.client, instance_id)
    rota = evaluate(
        read_live_table(DMN_DIR / "escalation_routing.dmn"),
        {
            "motivo_categoria": variaveis["motivo_categoria"]["value"],
            "severidade": variaveis["severidade"]["value"],
        },
    )
    assert await candidate_groups(c.client, tarefas[0]["id"]) == {rota.saidas["grupo_atendimento"]}

    await c.engine.complete_task_as_human(tarefas[0]["id"], {"resultado": resultado, "notas_resolucao": nota})

    async def _encerrada() -> bool:
        return await instancia_encerrada(c.client, instance_id)

    await drenar_por_chave(
        c.client,
        c.harness,
        worker_id=c.worker_id,
        business_key=business_key,
        instance_id=instance_id,
        pronto=_encerrada,
    )
    fim = "End_DevolvidoAoAgente" if resultado == "devolvido_agente" else "End_ResolvidoPorHumano"
    assert fim in await c.engine.activity_instances_ended(instance_id)
    return _Caso(
        conversation_id=conversation_id, business_key=business_key, instance_id=instance_id, nota=nota
    )


def _handler(c: _Cadeia) -> ResumeHandler:
    """A composicao de `agent_resume.main()`, com a custodia falsa no lugar da cifrada por KMS."""
    return ResumeHandler(
        tenant_id=c.tenant,
        instructions=EngineHistoryInstructionSource(c.dispatcher.cibseven),
        recipients=c.custodia.retomada,
        resumers={"helena": c.dispatcher},
        alerter=NotifyTeamAlerter(c.producer),
    )


async def _rodar_retomada(
    c: _Cadeia, consumidor: Any, business_key: str, dlq: Any = None
) -> ConsumidorLimitado:
    limitado = ConsumidorLimitado(consumidor, do_caso=mensagem_da_chave(business_key))
    await asyncio.wait_for(run_resume_loop(limitado, _handler(c), dlq=dlq), timeout=_PRAZO_KAFKA_S)
    assert len(limitado.entregues) == 1, "o laco deveria ter recebido exatamente o evento deste caso"
    return limitado


async def test_devolvido_agente_chega_ao_beneficiario_uma_vez_com_a_nota(cadeia: _Cadeia) -> None:
    c = cadeia
    nota = "Sua guia 123 foi liberada. Apresente o documento com foto na recepcao."
    async with consumidor_kafka_real(PROCESS_COMPLETED_TOPIC, grupo=grupo_unico("esc-d1-devolvido")) as cons:
        caso = await _escalar_e_concluir(c, resultado="devolvido_agente", nota=nota)
        enviados_antes = len(c.remetente.enviados)
        limitado = await _rodar_retomada(c, cons, caso.business_key)

        evento = limitado.entregues[0].value
        # O fato que viajou no broker e' Zona Geral: ancora, nunca a nota (ADR-0006).
        assert evento["resultado"] == "devolvido_agente"
        assert evento["_process_instance_id"] == caso.instance_id
        assert "notas_resolucao" not in evento
        novos = c.remetente.enviados[enviados_antes:]
        assert len(novos) == 1, f"a retomada deve sair EXATAMENTE uma vez; saiu {len(novos)}"
        destino, texto = novos[0]
        assert destino == c.telefone  # o numero que a custodia recebeu do webhook
        assert texto == compor_mensagem_de_retomada(nota)
        assert nota in texto
        assert limitado.commits == 1
        confirmados = await offsets_confirmados(cons)
        assert confirmados[limitado.entregues[0].partition] == limitado.entregues[0].offset + 1


async def test_resolvido_humano_nao_envia_nada_e_o_offset_avanca(cadeia: _Cadeia) -> None:
    c = cadeia
    async with consumidor_kafka_real(PROCESS_COMPLETED_TOPIC, grupo=grupo_unico("esc-d1-resolvido")) as cons:
        caso = await _escalar_e_concluir(c, resultado="resolvido_humano", nota="Resolvido por telefone.")
        enviados_antes = len(c.remetente.enviados)
        # 1. O MOTOR nao publica `process_completed` neste desfecho (BPMN: so' `End_DevolvidoAoAgente`
        #    passa por `ST_PublishProcessCompleted`) — fato do historico, nao de uma espera no broker.
        assert "ST_PublishProcessCompleted" not in await c.engine.activity_instances_ended(caso.instance_id)
        # 2. Se um `process_completed` com `resolvido_humano` chegar ao topico (o filtro do consumidor
        #    existe para isso), ele e' confirmado SEM efeito: nada sai e o offset avanca.
        await _publicar_forjado(c, caso, resultado="resolvido_humano")
        limitado = await _rodar_retomada(c, cons, caso.business_key)

        assert c.remetente.enviados[enviados_antes:] == []
        assert limitado.commits == 1
        confirmados = await offsets_confirmados(cons)
        assert confirmados[limitado.entregues[0].partition] == limitado.entregues[0].offset + 1


async def test_historico_divergente_vai_para_a_fila_morta_e_nada_sai(cadeia: _Cadeia) -> None:
    c = cadeia
    dlq_topico = dlq_topic_for(PROCESS_COMPLETED_TOPIC)
    async with (
        consumidor_kafka_real(PROCESS_COMPLETED_TOPIC, grupo=grupo_unico("esc-d1-diverge")) as cons,
        consumidor_kafka_real(dlq_topico, grupo=grupo_unico("esc-d1-dlq-sonda")) as sonda_dlq,
    ):
        caso = await _escalar_e_concluir(c, resultado="resolvido_humano", nota="Resolvido por telefone.")
        enviados_antes = len(c.remetente.enviados)
        # O historico REAL diz `resolvido_humano`; o evento forjado diz `devolvido_agente`.
        await _publicar_forjado(c, caso, resultado="devolvido_agente")
        auditoria = PostgresAuditSink(c.audit_dsn, c.tenant)
        dlq = build_dlq_shunt(
            NotificationsBridgeSettings(
                TENANT_ID=c.tenant, KAFKA_BOOTSTRAP_SERVERS=kafka_bootstrap_servers()
            ),
            auditoria,
        )
        await dlq.publisher.start()
        try:
            limitado = await _rodar_retomada(c, cons, caso.business_key, dlq=dlq)
        finally:
            await dlq.publisher.stop()
            await auditoria.aclose()

        assert dlq.shunted == [(dlq_topico, REASON_RESUME_HISTORY_MISMATCH)]
        assert c.remetente.enviados[enviados_antes:] == []
        # A fila morta e' um fato no broker: os bytes originais, com o motivo fechado no cabecalho.
        na_dlq = ConsumidorLimitado(sonda_dlq, do_caso=mensagem_da_chave(caso.business_key))
        recebidas = await asyncio.wait_for(_coletar(na_dlq), timeout=_PRAZO_KAFKA_S)
        assert recebidas[0].raw == limitado.entregues[0].raw
        assert dict(recebidas[0].headers)["maezo_dlq_reason"] == REASON_RESUME_HISTORY_MISMATCH.encode()
        # Malformacao confirmada na fila morta: o offset avanca (o laco nao trava a particao).
        confirmados = await offsets_confirmados(cons)
        assert confirmados[limitado.entregues[0].partition] == limitado.entregues[0].offset + 1


async def _coletar(limitado: ConsumidorLimitado) -> list[Any]:
    return [mensagem async for mensagem in limitado]


async def _publicar_forjado(c: _Cadeia, caso: _Caso, *, resultado: str) -> None:
    """Um `process_completed` na forma EXATA do `ST_PublishProcessCompleted` (Zona Geral), publicado
    por um produtor qualquer do topico — o cenario que o cruzamento com o historico existe para barrar."""
    from aiokafka import AIOKafkaProducer

    produtor = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap_servers())
    await produtor.start()
    try:
        await produtor.send_and_wait(
            PROCESS_COMPLETED_TOPIC,
            json.dumps(
                {
                    "tenant_id": c.tenant,
                    "agent_id": "helena",
                    "conversation_id": caso.conversation_id,
                    "resultado": resultado,
                    "_business_key": caso.business_key,
                    "_process_instance_id": caso.instance_id,
                }
            ).encode("utf-8"),
            key=caso.business_key.encode("utf-8"),
        )
    finally:
        await produtor.stop()


#: Ponto de extensao (frente A, NAO coberta aqui): os eventos `agent_resume.falha` /
#: `agent_resume.fila_morta` e o aviso ao supervisor no inbox entram como casos novos deste modulo,
#: reutilizando `_escalar_e_concluir` e `_rodar_retomada`.
