"""GAP-XHITL-4 — consumidor de retomada (`agent_resume`): filtro, fila morta, falha fechada, PHI.

Criterios da spec cobertos aqui: 6 (os outros dois desfechos NAO disparam retomada) e 7 (mensagem
malformada vai para a fila morta e o consumidor continua vivo), mais a regra de PHI (a nota nunca
viaja no evento; a cerca de `phi_vars` recusa quem tentar) e a postura fail-closed herdada do
`notifications_bridge`. O fluxo ponta a ponta (Helena real, historico falso, Kafka falso) esta' em
`test_agent_resume_flow.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from maezo.platform.integrations.agent_resume import (
    DEFAULT_RESUME_CONSUMER_GROUP_ID,
    PROCESS_COMPLETED_TOPIC,
    AgentResumeSettings,
    EngineHistoryInstructionSource,
    MalformedResumeEventError,
    RecipientCustodyUnavailableError,
    ResumeHandler,
    ResumeOutcome,
    build_recipient_resolver,
    parse_process_completed,
    run_resume_loop,
)
from maezo.platform.integrations.notifications_bridge import (
    BRIDGE_DLQ_REASONS,
    REASON_RESUME_ANCHOR_MISMATCH,
    REASON_RESUME_HISTORY_MISMATCH,
    REASON_RESUME_MISSING_ANCHOR,
    REASON_RESUME_PHI_IN_EVENT,
    REASON_RESUME_RECIPIENT_UNAVAILABLE,
    REASON_RESUME_UNKNOWN_RESULTADO,
    BridgeDlqShunt,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
)
from maezo.tools.mcp_cibseven.transport import CibSevenError, FakeCibSevenTransport, HistoricProcessVariables
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS
from tests.support.audit_fakes import FakeStartAuditSink

_CONV = "wa:amh:hk1_" + "b" * 64
_BK = f"ESC-amh-{_CONV}"
_PID = "pi-123"
_REPO = Path(__file__).resolve().parents[4]


def _evento(resultado: str = "devolvido_agente", **extra: Any) -> dict[str, Any]:
    return {
        "tenant_id": "amh",
        "agent_id": "helena",
        "conversation_id": _CONV,
        "resultado": resultado,
        "_business_key": _BK,
        "_process_instance_id": _PID,
        **extra,
    }


def _historico(**variables: Any) -> HistoricProcessVariables:
    base = {"resultado": "devolvido_agente", "notas_resolucao": "Sua guia foi liberada."}
    base.update(variables)
    return HistoricProcessVariables(
        instance_id=_PID,
        business_key=_BK,
        process_key="SP-OP-ESCALATION-001",
        state="COMPLETED",
        variables=base,
    )


class _Engine(FakeCibSevenTransport):
    """Fake do motor que conta as leituras de historico (e pode falhar como infra)."""

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.reads: list[tuple[str, tuple[str, ...]]] = []
        self._fail = fail

    async def read_historic_variables(
        self, process_instance_id: str, names: tuple[str, ...]
    ) -> HistoricProcessVariables | None:
        self.reads.append((process_instance_id, names))
        if self._fail:
            raise CibSevenError("engine unreachable")
        return await super().read_historic_variables(process_instance_id, names)


class _Recipients:
    def __init__(self, number: str | None = "5511999999999") -> None:
        self.number = number

    async def resolve(self, *, tenant_id: str, conversation_id: str) -> str | None:
        return self.number


class _Resumer:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail

    async def resume(
        self, *, conversation_id: str, instrucoes: str, raw_to: str, resume_ref: str
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "instrucoes": instrucoes,
                "raw_to": raw_to,
                "resume_ref": resume_ref,
            }
        )
        if self._fail is not None:
            raise self._fail
        return {"desfecho": "retomada_enviada"}


class _DlqPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def publish_dlq(self, topic: str, *, raw: bytes, key: bytes | None, headers: Any) -> None:
        self.published.append((topic, raw))


def _handler(
    engine: _Engine | None = None,
    *,
    resumer: _Resumer | None = None,
    recipients: _Recipients | None = None,
) -> tuple[ResumeHandler, _Engine, _Resumer]:
    engine = engine or _Engine()
    if not engine._historic_variables:
        engine.seed_historic_variables(_historico())
    resumer = resumer or _Resumer()
    handler = ResumeHandler(
        tenant_id="amh",
        instructions=EngineHistoryInstructionSource(engine),
        recipients=recipients or _Recipients(),
        resumers={"helena": resumer},
    )
    return handler, engine, resumer


def _msg(value: Any, offset: int = 0) -> BridgeMessage:
    return BridgeMessage.from_value(value, topic=PROCESS_COMPLETED_TOPIC, offset=offset)


# --- criterio 6: filtro dos tres desfechos ----------------------------------------------------


async def test_devolvido_agente_acorda_o_agente_com_a_nota_do_historico() -> None:
    handler, engine, resumer = _handler()
    receipt = await handler.handle(_evento())
    assert receipt.outcome is ResumeOutcome.RESUMED
    assert resumer.calls == [
        {
            "conversation_id": _CONV,
            "instrucoes": "Sua guia foi liberada.",
            "raw_to": "5511999999999",
            "resume_ref": _PID,
        }
    ]
    assert engine.reads == [(_PID, ("notas_resolucao", "resultado"))]


@pytest.mark.parametrize("resultado", ["resolvido_humano", "emergencia_acionada"])
async def test_outros_desfechos_nao_acordam_a_helena_nem_leem_o_historico(resultado: str) -> None:
    handler, engine, resumer = _handler()
    receipt = await handler.handle(_evento(resultado))
    assert receipt.outcome is ResumeOutcome.SKIPPED_RESULTADO
    assert resumer.calls == []
    assert engine.reads == []  # nem a nota e' buscada


async def test_os_tres_desfechos_no_laco_so_um_acorda_e_todos_confirmam() -> None:
    handler, _, resumer = _handler()
    consumer = FakeBridgeKafkaConsumer(
        [_msg(_evento("resolvido_humano"), 0), _msg(_evento("emergencia_acionada"), 1), _msg(_evento(), 2)]
    )
    await run_resume_loop(consumer, handler)
    assert len(resumer.calls) == 1
    assert consumer.commits == 3


async def test_agente_sem_porta_de_retomada_e_confirmado_com_aviso() -> None:
    handler, engine, resumer = _handler()
    receipt = await handler.handle(_evento(agent_id="lucas"))
    assert receipt.outcome is ResumeOutcome.SKIPPED_NO_RESUMER
    assert resumer.calls == [] and engine.reads == []


# --- criterio 7: malformada -> fila morta, laco vivo ---------------------------------------------


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (["not", "an", "object"], "not_a_json_object"),
        ({k: v for k, v in _evento().items() if k != "_business_key"}, REASON_RESUME_MISSING_ANCHOR),
        (_evento(_process_instance_id=""), REASON_RESUME_MISSING_ANCHOR),
        (_evento("devolvido"), REASON_RESUME_UNKNOWN_RESULTADO),
        (_evento(_business_key="ESC-amh-outra-conversa"), REASON_RESUME_ANCHOR_MISMATCH),
        (_evento(tenant_id="outro"), REASON_RESUME_ANCHOR_MISMATCH),
    ],
)
def test_malformacao_levanta_com_codigo_fechado(value: Any, code: str) -> None:
    with pytest.raises(MalformedResumeEventError) as exc:
        parse_process_completed(value, tenant_id="amh")
    assert exc.value.code == code
    assert exc.value.code in BRIDGE_DLQ_REASONS


async def test_malformada_vai_para_dlq_e_o_laco_continua() -> None:
    handler, _, resumer = _handler()
    publisher = _DlqPublisher()
    sink = FakeStartAuditSink()
    dlq = BridgeDlqShunt(publisher=publisher, audit_sink=sink, tenant_id="amh")
    undecodable = BridgeMessage(
        topic=PROCESS_COMPLETED_TOPIC,
        partition=0,
        offset=1,
        raw=b"\xff{nope",
        parse_error="invalid JSON payload",
        parse_code="invalid_json",
    )
    consumer = FakeBridgeKafkaConsumer([_msg({"sem": "campos"}, 0), undecodable, _msg(_evento(), 2)])
    await run_resume_loop(consumer, handler, dlq=dlq)

    assert [topic for topic, _ in publisher.published] == [f"{PROCESS_COMPLETED_TOPIC}.dlq"] * 2
    assert dlq.shunted == [
        (f"{PROCESS_COMPLETED_TOPIC}.dlq", REASON_RESUME_MISSING_ANCHOR),
        (f"{PROCESS_COMPLETED_TOPIC}.dlq", "invalid_json"),
    ]
    assert len(sink.calls) == 2  # cada quarentena tem seu fato ADR-0007
    assert len(resumer.calls) == 1  # o evento bom DEPOIS das duas malformadas foi atendido
    assert consumer.commits == 3


async def test_sem_dlq_malformada_propaga_e_nada_e_confirmado() -> None:
    handler, _, _ = _handler()
    consumer = FakeBridgeKafkaConsumer([_msg({"sem": "campos"})])
    with pytest.raises(MalformedResumeEventError):
        await run_resume_loop(consumer, handler)
    assert consumer.commits == 0


# --- fail-closed: falha transitoria NAO confirma ------------------------------------------------


async def test_motor_fora_propaga_e_offset_fica() -> None:
    handler, _, resumer = _handler(_Engine(fail=True))
    dlq = BridgeDlqShunt(publisher=_DlqPublisher(), audit_sink=FakeStartAuditSink(), tenant_id="amh")
    consumer = FakeBridgeKafkaConsumer([_msg(_evento())])
    with pytest.raises(CibSevenError):
        await run_resume_loop(consumer, handler, dlq=dlq)
    assert consumer.commits == 0 and resumer.calls == [] and dlq.shunted == []


async def test_falha_de_envio_do_agente_propaga_e_offset_fica() -> None:
    from maezo.agents.helena.graph import RetomadaEnvioFalhouError

    handler, _, _ = _handler(resumer=_Resumer(fail=RetomadaEnvioFalhouError("down")))
    dlq = BridgeDlqShunt(publisher=_DlqPublisher(), audit_sink=FakeStartAuditSink(), tenant_id="amh")
    consumer = FakeBridgeKafkaConsumer([_msg(_evento())])
    with pytest.raises(RetomadaEnvioFalhouError):
        await run_resume_loop(consumer, handler, dlq=dlq)
    assert consumer.commits == 0 and dlq.shunted == []


def test_fonte_de_instrucao_recusa_transporte_sem_leitura_de_historico() -> None:
    class _SoAtivo:
        async def find_active_instance(self, business_key: str) -> None:
            return None

    with pytest.raises(TypeError):
        EngineHistoryInstructionSource(_SoAtivo())


def test_producao_recusa_subir_sem_custodia_de_destinatario() -> None:
    with pytest.raises(RecipientCustodyUnavailableError):
        build_recipient_resolver(AgentResumeSettings())


async def test_custodia_sem_numero_vai_para_dlq() -> None:
    handler, _, resumer = _handler(recipients=_Recipients(None))
    with pytest.raises(MalformedResumeEventError) as exc:
        await handler.handle(_evento())
    assert exc.value.code == REASON_RESUME_RECIPIENT_UNAVAILABLE
    assert resumer.calls == []


# --- o historico e' o fato: o evento nao basta ----------------------------------------------------


@pytest.mark.parametrize(
    "historico",
    [
        None,
        _historico(resultado="resolvido_humano"),
        _historico(notas_resolucao="   "),
        HistoricProcessVariables(_PID, "ESC-amh-outra", "SP-OP-ESCALATION-001", "COMPLETED", {}),
        HistoricProcessVariables(
            _PID,
            _BK,
            "SP-OP-AUTH-001",
            "COMPLETED",
            {"resultado": "devolvido_agente", "notas_resolucao": "x"},
        ),
    ],
)
async def test_evento_sem_lastro_no_historico_nao_acorda_ninguem(
    historico: HistoricProcessVariables | None,
) -> None:
    engine = _Engine()
    if historico is not None:
        engine.seed_historic_variables(historico)
    else:
        engine.seed_historic_variables(
            HistoricProcessVariables("outra-instancia", _BK, "SP-OP-ESCALATION-001", "COMPLETED", {})
        )
    handler, _, resumer = _handler(engine)
    with pytest.raises(MalformedResumeEventError) as exc:
        await handler.handle(_evento())
    assert exc.value.code == REASON_RESUME_HISTORY_MISMATCH
    assert resumer.calls == []


# --- PHI: a nota NUNCA viaja no evento ------------------------------------------------------------


@pytest.mark.parametrize("phi_var", sorted(PHI_PROCESS_VARS))
async def test_evento_carregando_variavel_phi_e_recusado_e_nao_lido(phi_var: str) -> None:
    handler, engine, resumer = _handler()
    segredo = "texto livre que nunca deveria estar aqui"
    with pytest.raises(MalformedResumeEventError) as exc:
        await handler.handle(_evento(**{phi_var: segredo}))
    assert exc.value.code == REASON_RESUME_PHI_IN_EVENT
    assert segredo not in str(exc.value)  # o valor nao entra nem na mensagem do erro
    assert engine.reads == [] and resumer.calls == []


async def test_nota_no_evento_vai_para_dlq_com_auditoria_sem_o_texto() -> None:
    handler, _, resumer = _handler()
    sink = FakeStartAuditSink()
    dlq = BridgeDlqShunt(publisher=_DlqPublisher(), audit_sink=sink, tenant_id="amh")
    consumer = FakeBridgeKafkaConsumer([_msg(_evento(notas_resolucao="Paciente com ..."))])
    await run_resume_loop(consumer, handler, dlq=dlq)
    assert dlq.shunted == [(f"{PROCESS_COMPLETED_TOPIC}.dlq", REASON_RESUME_PHI_IN_EVENT)]
    record, _ = sink.calls[0]
    assert "Paciente" not in json.dumps(record.details)
    assert resumer.calls == [] and consumer.commits == 1


def test_bpmn_publica_process_completed_sem_nenhuma_variavel_phi() -> None:
    """A outra metade da cerca: o PUBLICADOR (BPMN) nao lista variavel PHI no payload."""
    bpmn = (_REPO / "spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn").read_text(
        encoding="utf-8"
    )
    bloco = bpmn[bpmn.index('id="ST_PublishProcessCompleted"') :]
    bloco = bloco[: bloco.index("</bpmn:serviceTask>")]
    assert "agents.events.process_completed" in bloco
    match = re.search(r'name="event_payload_vars">([^<]+)<', bloco)
    assert match is not None
    publicadas = {v.strip() for v in match.group(1).split(",")}
    assert publicadas == {"tenant_id", "agent_id", "conversation_id", "resultado"}
    assert not publicadas & PHI_PROCESS_VARS


def test_defaults_do_consumidor() -> None:
    settings = AgentResumeSettings()
    assert settings.kafka_topic == PROCESS_COMPLETED_TOPIC == "agents.events.process_completed"
    assert settings.kafka_group_id == DEFAULT_RESUME_CONSUMER_GROUP_ID
