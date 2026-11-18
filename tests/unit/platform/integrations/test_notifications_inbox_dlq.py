"""DL-0186: com a fila morta ligada no inbox (como `main()` liga), nenhuma quarentena e silenciosa e nenhum
aviso de escalonamento BEM FORMADO muda de caminho.

(1) Quarentena de um aviso de escalonamento malformado e de um aviso rco `rco_fila_sem_grupo`: contador
`maezo_notifications_inbox_dlq_total{tipo,reason}` (e o `maezo_bridge_dlq_total{topic,reason}` do shunt),
linha de log `notifications_inbox.dlq_shunted` so com coordenadas e codigos, e o fato na cadeia de auditoria
(`BridgeDlqShunt` grava `bridge_dlq:<topico>` com `reason_code` e `raw_sha256`, nunca o payload).

(2) Regressao: os casos bem formados que `test_notifications_inbox.py` ja fixa (equipe, `falha_tecnica`, o
digest pinado antes da DL-0117, supervisor nos dois motivos) passam pelo laco COM a fila morta e gravam o
mesmo que sem ela; o publicador da fila morta nunca e chamado e a cadeia nao recebe nada.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from maezo.platform import observability
from maezo.platform.integrations import notifications_inbox as inbox_mod
from maezo.platform.integrations.notifications_bridge import (
    BRIDGE_DLQ_REASONS,
    REASON_INVALID_JSON,
    REASON_MISSING_ESCALATION_ANCHOR,
    REASON_RCO_FILA_SEM_GRUPO,
    BridgeDlqShunt,
    BridgeMessage,
    FakeBridgeKafkaConsumer,
)
from maezo.platform.integrations.notifications_inbox import run_inbox_loop
from maezo.runtime.metrics import MetricsCollector
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.platform.integrations.test_notifications_inbox import (
    _NOTICE_REF_TEAM_PRE_DL0117,
    _notification,
    _SpyInbox,
    _supervisor,
)
from tests.unit.platform.integrations.test_notifications_inbox_rco import (
    InboxRcoEmMemoria,
    _mensagem,
    _Pub,
)

TOPICO = "operadora.notifications.internal"


def _msg(payload: Any, offset: int = 0) -> BridgeMessage:
    return BridgeMessage.from_value(payload, topic=TOPICO, offset=offset)


def _dlq() -> tuple[BridgeDlqShunt, _Pub, FakeStartAuditSink]:
    pub, audit = _Pub(), FakeStartAuditSink()
    return BridgeDlqShunt(publisher=pub, audit_sink=audit, tenant_id="amh"), pub, audit  # type: ignore[arg-type]


@pytest.fixture
def coletor(monkeypatch: pytest.MonkeyPatch) -> MetricsCollector:
    coletor = MetricsCollector()
    monkeypatch.setattr(observability, "_get_metrics_collector", lambda: coletor)
    return coletor


def test_tipos_fechados_tem_paridade() -> None:
    assert inbox_mod.INBOX_DLQ_TIPOS == observability.INBOX_DLQ_TIPOS


@pytest.mark.parametrize(
    ("payload", "tipo", "reason"),
    [
        (_notification(business_key=None), "escalation.notify_team", REASON_MISSING_ESCALATION_ANCHOR),
        (_supervisor(business_key=None), "escalation.notify_supervisor", REASON_MISSING_ESCALATION_ANCHOR),
        (_mensagem("cadastro"), "rco.aviso_auditoria", REASON_RCO_FILA_SEM_GRUPO),
    ],
)
async def test_quarentena_nunca_e_silenciosa(
    coletor: MetricsCollector, payload: dict[str, Any], tipo: str, reason: str
) -> None:
    dlq, pub, audit = _dlq()
    consumer = FakeBridgeKafkaConsumer([_msg(payload, offset=7)])
    with structlog.testing.capture_logs() as logs:
        await run_inbox_loop(
            consumer,
            _SpyInbox(),
            dlq=dlq,
            avisos_rco=InboxRcoEmMemoria(),
            grupos_do_pin=lambda: {"auditoria_medica": "medico-auditor"},  # `cadastro` sem grupo
        )
    assert consumer.commits == 1
    assert [topic for topic, _ in pub.sent] == [f"{TOPICO}.dlq"]
    # metrica: por tipo e codigo (nova) e por topico e codigo (a do shunt)
    assert coletor.inbox_dlq_total.labels(tipo=tipo, reason=reason)._value.get() == 1
    assert coletor.bridge_dlq.labels(topic=TOPICO, reason=reason)._value.get() == 1
    # log: so coordenadas e codigos
    linhas = [e for e in logs if e["event"] == "notifications_inbox.dlq_shunted"]
    assert linhas == [
        {
            "event": "notifications_inbox.dlq_shunted",
            "log_level": "warning",
            "tipo": tipo,
            "reason": reason,
            "dlq_topic": f"{TOPICO}.dlq",
            "source_topic": TOPICO,
            "partition": 0,
            "offset": 7,
        }
    ]
    for valor in ("ESC-amh", "plantao-clinico", "task-1", payload.get("chave_idempotencia") or "-x-"):
        assert valor not in repr(linhas)
    # cadeia: um fato com o codigo e o hash dos bytes, nunca o payload
    assert len(audit.records) == 1
    fato = audit.records[0]
    assert fato.action == f"bridge_dlq:{TOPICO}"
    assert fato.details["reason_code"] == reason
    assert len(fato.details["raw_sha256"]) == 64
    assert "business_key" not in repr(fato.details) and "ESC-amh" not in repr(fato.details)


async def test_mensagem_que_nao_e_json_conta_como_nao_json(coletor: MetricsCollector) -> None:
    dlq, _, audit = _dlq()
    mensagem = BridgeMessage(
        topic=TOPICO,
        partition=0,
        offset=1,
        raw=b"{nao json",
        parse_error="invalid JSON payload",
        parse_code=REASON_INVALID_JSON,
    )
    await run_inbox_loop(FakeBridgeKafkaConsumer([mensagem]), _SpyInbox(), dlq=dlq)
    rotulos = {
        (s.labels["tipo"], s.labels["reason"])
        for m in coletor.inbox_dlq_total.collect()
        for s in m.samples
        if s.name.endswith("_total")
    }
    assert {t for t, _ in rotulos} == {"nao_json"} and {r for _, r in rotulos} <= BRIDGE_DLQ_REASONS
    assert len(audit.records) == 1


def test_tipo_alheio_nunca_vira_rotulo() -> None:
    assert inbox_mod.tipo_para_dlq(_msg({"type": "qualquer.coisa.com.dado"})) == "outro"
    assert inbox_mod.tipo_para_dlq(_msg({"sem": "type"})) == "outro"


_BEM_FORMADOS = [
    _notification(),
    _notification(severidade=None, motivo_categoria="falha_tecnica"),
    _notification(
        business_key="ESC-amh-conv-1",
        grupo_atendimento="plantao-clinico",
        severidade="grave",
        prioridade="P1",
        motivo_categoria="red_flag_clinico",
    ),
    _supervisor(),
    _supervisor(motivo_alerta="notificacao_primaria_falhou"),
]


async def test_avisos_de_escalonamento_bem_formados_nao_mudam_de_caminho(coletor: MetricsCollector) -> None:
    """Mesmo laco, mesma entrada: sem fila morta (o caminho de antes) e com ela (como `main()` liga)."""
    sem = _SpyInbox()
    consumer_sem = FakeBridgeKafkaConsumer([_msg(p, i) for i, p in enumerate(_BEM_FORMADOS)])
    await run_inbox_loop(consumer_sem, sem)

    com = _SpyInbox()
    dlq, pub, audit = _dlq()
    consumer_com = FakeBridgeKafkaConsumer([_msg(p, i) for i, p in enumerate(_BEM_FORMADOS)])
    await run_inbox_loop(consumer_com, com, dlq=dlq, avisos_rco=InboxRcoEmMemoria(), grupos_do_pin=lambda: {})

    assert com.recorded == sem.recorded and len(com.recorded) == len(_BEM_FORMADOS)
    assert consumer_com.commits == consumer_sem.commits == len(_BEM_FORMADOS)
    assert pub.sent == [] and audit.records == [] and dlq.shunted == []
    # os valores ja fixados em `test_notifications_inbox.py` continuam os mesmos
    assert com.recorded[2].notice_ref == _NOTICE_REF_TEAM_PRE_DL0117
    assert com.recorded[3].grupo_atendimento == "supervisao-atendimento"
    assert com.recorded[1].severidade is None and com.recorded[1].motivo_categoria == "falha_tecnica"
    assert len({n.notice_ref for n in com.recorded}) == len(_BEM_FORMADOS)
    assert all(s.value == 0 for m in coletor.inbox_dlq_total.collect() for s in m.samples)
