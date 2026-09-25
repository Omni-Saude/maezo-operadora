"""GAP-XHITL-4 — o ciclo inteiro, com Kafka FALSO e historico do motor FALSO (sem broker, sem motor).

Teste de integracao de componentes: o `HelenaDispatcher` e o grafo da Helena sao os REAIS, com o
checkpointer real em memoria (`InMemorySaver`), a costura cercada real do motor (`gate_cibseven`)
e do WhatsApp (`gate_whatsapp`); so' as bordas externas sao dublês (`FakeBridgeKafkaConsumer`,
`FakeCibSevenTransport`, cliente WhatsApp, inferencia). Fica em `tests/unit/` porque
`tests/integration/` exige o stack docker-compose e proibe mock de motor (ADR-0011).

Os sete "Pronto quando" da spec, na ordem em que acontecem aqui:
  1. a Helena escala o caso (SP-OP-ESCALATION-001 iniciado pelo chokepoint auditado);
  2. o humano conclui `devolvido_agente` com instrucao (semeado no HISTORICO do motor falso);
  3. o beneficiario RECEBE a mensagem com o conteudo da instrucao;
  4. ela passou pela cerca de saida (a variante com conteudo proibido e' barrada);
  5. o turno aparece na telemetria com desfecho proprio;
  6. `resolvido_humano` / `emergencia_acionada` nao disparam retomada;
  7. uma mensagem malformada vai para a fila morta e o consumidor continua vivo.
"""

from __future__ import annotations

from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from maezo.agents.helena.graph import (
    DESFECHO_RETOMADA_ENVIADA,
    DESFECHO_RETOMADA_RECUSADA,
    RETOMADA_RECUSADA_PLACEHOLDER,
)
from maezo.gateway.effect_pep import DecisionContext
from maezo.gateway.pseudonymizer import Pseudonymizer
from maezo.gateway.seams import SeamContext
from maezo.gateway.seams.cibseven import gate_cibseven
from maezo.platform.integrations.agent_resume import (
    PROCESS_COMPLETED_TOPIC,
    EngineHistoryInstructionSource,
    ResumeHandler,
    run_resume_loop,
)
from maezo.platform.integrations.notifications_bridge import (
    REASON_RESUME_MISSING_ANCHOR,
    BridgeDlqShunt,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
)
from maezo.platform.observability import get_metrics_collector
from maezo.platform.webhooks.whatsapp.dispatch import HelenaDispatcher, InboundMessage
from maezo.runtime.checkpoint import Checkpointer, checkpoint_thread_config
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport, HistoricProcessVariables
from maezo.tools.mcp_whatsapp.server import WhatsAppServer
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

_PHONE = "5511999999999"


class _Inference:
    """Classifica como pedido de humano; qualquer redacao seguinte devolve um texto neutro."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, prompt: str, **_: Any) -> str:
        self.calls += 1
        if self.calls == 1:
            return '{"intent": "human_request", "population": "none", "psychosocial_risk": false}'
        return "Recebemos sua mensagem e encaminhamos seu caso para a nossa equipe."


class _WhatsApp(WhatsAppServer):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[tuple[str, str]] = []

    async def send_message(self, to: str, text: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        self.sent.append((to, text))
        return {"messages": [{"id": f"wamid.out.{len(self.sent)}"}]}


class _Recipients:
    async def resolve(self, *, tenant_id: str, conversation_id: str) -> str | None:
        return _PHONE


class _DlqPublisher:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish_dlq(self, topic: str, *, raw: bytes, key: bytes | None, headers: Any) -> None:
        self.published.append(topic)


def _seam() -> SeamContext:
    return SeamContext(tenant="amh", principal="helena", decision=DecisionContext())


def _evento(conv: str, pid: str, resultado: str) -> dict[str, Any]:
    return {
        "tenant_id": "amh",
        "agent_id": "helena",
        "conversation_id": conv,
        "resultado": resultado,
        "_business_key": f"ESC-amh-{conv}",
        "_process_instance_id": pid,
    }


async def _escalar() -> tuple[HelenaDispatcher, FakeCibSevenTransport, _WhatsApp, InMemorySaver, str, str]:
    engine = FakeCibSevenTransport()
    whatsapp = _WhatsApp()
    saver = InMemorySaver()
    dispatcher = HelenaDispatcher(
        tenant_id="amh",
        inference=_Inference(),  # type: ignore[arg-type]
        dmn=FakeDmnTransport(),
        cibseven=gate_cibseven(engine, _seam()),
        whatsapp_client=whatsapp,
        pseudonymizer=Pseudonymizer(),
        audit_sink=FakeStartAuditSink(),
        checkpointer=Checkpointer(saver=saver),
        seam_context=_seam(),
    )
    # 1. A Helena escala o caso.
    turno = await dispatcher.dispatch(
        InboundMessage(from_number=_PHONE, text="quero falar com uma pessoa", message_id="wamid.in.1")
    )
    conv = str(turno["conversation_id"])
    instancia = await engine.find_active_instance(f"ESC-amh-{conv}")
    assert turno["escalation_started"] is True and instancia is not None
    return dispatcher, engine, whatsapp, saver, conv, instancia.instance_id


def _humano_devolve(engine: FakeCibSevenTransport, conv: str, pid: str, notas: str) -> None:
    """2. O humano conclui `devolvido_agente` — o que `completion_engine` grava no motor."""
    engine.seed_historic_variables(
        HistoricProcessVariables(
            instance_id=pid,
            business_key=f"ESC-amh-{conv}",
            process_key="SP-OP-ESCALATION-001",
            state="COMPLETED",
            variables={"resultado": "devolvido_agente", "notas_resolucao": notas},
        )
    )


def _labels(desfecho: str) -> dict[str, str]:
    return {"agent_id": "helena", "desfecho": desfecho, "route": "retomada", "motivo_categoria": ""}


async def test_ciclo_completo_escala_devolve_e_o_beneficiario_recebe_a_instrucao() -> None:
    dispatcher, engine, whatsapp, saver, conv, pid = await _escalar()
    instrucao = "Sua guia 123 foi liberada. Apresente o documento com foto na recepcao."
    _humano_devolve(engine, conv, pid, instrucao)
    enviados_antes = len(whatsapp.sent)

    registry = get_metrics_collector().registry
    antes = registry.get_sample_value("maezo_agent_desfecho_total", _labels(DESFECHO_RETOMADA_ENVIADA)) or 0.0

    publisher = _DlqPublisher()
    dlq = BridgeDlqShunt(publisher=publisher, audit_sink=FakeStartAuditSink(), tenant_id="amh")
    handler = ResumeHandler(
        tenant_id="amh",
        instructions=EngineHistoryInstructionSource(dispatcher.cibseven),
        recipients=_Recipients(),
        resumers={"helena": dispatcher},
    )
    consumer = FakeBridgeKafkaConsumer(
        [
            BridgeMessage.from_value({"lixo": True}, topic=PROCESS_COMPLETED_TOPIC, offset=0),  # 7.
            BridgeMessage.from_value(
                _evento(conv, pid, "resolvido_humano"), topic=PROCESS_COMPLETED_TOPIC, offset=1
            ),
            BridgeMessage.from_value(
                _evento(conv, pid, "emergencia_acionada"), topic=PROCESS_COMPLETED_TOPIC, offset=2
            ),
            BridgeMessage.from_value(
                _evento(conv, pid, "devolvido_agente"), topic=PROCESS_COMPLETED_TOPIC, offset=3
            ),
        ]
    )
    await run_resume_loop(consumer, handler, dlq=dlq)

    # 7. malformada na fila morta, laco vivo ate' o fim.
    assert dlq.shunted == [(f"{PROCESS_COMPLETED_TOPIC}.dlq", REASON_RESUME_MISSING_ANCHOR)]
    assert consumer.commits == 4
    # 6 + 3. Dos tres desfechos, SO' `devolvido_agente` gerou envio — e ele leva a instrucao.
    novos = whatsapp.sent[enviados_antes:]
    assert len(novos) == 1
    destino, texto = novos[0]
    assert destino == _PHONE
    assert instrucao in texto
    # 5. desfecho proprio na telemetria.
    depois = registry.get_sample_value("maezo_agent_desfecho_total", _labels(DESFECHO_RETOMADA_ENVIADA))
    assert depois == antes + 1
    # O turno rodou no MESMO thread de checkpoint da conversa (o estado do escalonamento voltou).
    salvo = await saver.aget_tuple(checkpoint_thread_config(conv))
    assert salvo is not None
    valores = salvo.checkpoint["channel_values"]
    assert valores["desfecho"] == DESFECHO_RETOMADA_ENVIADA
    assert valores["retomada_instrucoes"] is None
    assert valores["escalation_started"] is False  # saneado: o caso nao esta mais escalado


async def test_instrucao_com_conteudo_proibido_passa_pela_cerca_e_nao_sai() -> None:
    dispatcher, engine, whatsapp, _, conv, pid = await _escalar()
    _humano_devolve(engine, conv, pid, "Fique tranquilo, nao e nada grave.")
    enviados_antes = len(whatsapp.sent)
    handler = ResumeHandler(
        tenant_id="amh",
        instructions=EngineHistoryInstructionSource(dispatcher.cibseven),
        recipients=_Recipients(),
        resumers={"helena": dispatcher},
    )
    receipt = await handler.handle(_evento(conv, pid, "devolvido_agente"))
    # 4. a cerca barrou: sai o placeholder de recusa, nunca a nota.
    assert receipt.desfecho == DESFECHO_RETOMADA_RECUSADA
    assert whatsapp.sent[enviados_antes:] == [(_PHONE, RETOMADA_RECUSADA_PLACEHOLDER)]


async def test_destinatario_que_nao_bate_com_a_conversa_e_recusado() -> None:
    dispatcher, _, whatsapp, _, conv, pid = await _escalar()
    enviados_antes = len(whatsapp.sent)
    with pytest.raises(ValueError, match="recipient does not match"):
        await dispatcher.resume(conversation_id=conv, instrucoes="x", raw_to="5511000000000", resume_ref=pid)
    assert whatsapp.sent[enviados_antes:] == []
