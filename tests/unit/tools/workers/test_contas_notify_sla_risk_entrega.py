"""R-107 (F-135, GAP-CONTAS-7) — entrega real do alerta de risco de SLA de CONTAS a um canal.

O gap, como o revisor deve conferir: `operadora.contas.notify_sla_risk` (`ST_NotificarRiscoSla`,
alimentado so pelo timer NAO-interruptivo `BT_AlertaSlaContas`) registrava a etapa em log e
devolvia `{}` — nenhum canal era contatado. O contrato SP-OP-CONTAS-001 nomeia o canal que falta:
`operadora.notifications.internal`, "como `recurso`/`lgpd` fazem". AUTH e LGPD ja publicam ali.

O que estes testes provam (todos com `delivered` no nome, para o DoD
`pytest -q tests/unit -k "notify_sla_risk and delivered"`):

- com a bandeira escura `MAEZO_CONTAS_SLA_RISK_ENTREGA` LIGADA, o alerta de CONTAS e publicado no
  canal interno ja existente, com `type="contas.notify_sla_risk"` (convencao fechada
  `<dominio>.notify_sla_risk` do bridge), `best_effort=False` e chave de particao nao vazia;
- com a bandeira DESLIGADA (padrao), o comportamento e byte-a-byte o historico: `FunctionWorker`,
  nenhuma publicacao;
- falha de broker PROPAGA (erro tecnico com retry/incidente da harness, ADR-0030) — nunca e
  engolida enquanto a etapa declara sucesso;
- o payload e um conjunto FECHADO de chaves de negocio: nenhuma variavel PHI atravessa (teste
  negativo, ADR-0006);
- AUTH e LGPD continuam entregando no MESMO canal (regressao);
- a fiacao inteira da bandeira: `register_contas_workers`, `register_all_workers` e o
  `register_default_workers` REAL (a fiacao settings/bring-up esta em
  `tests/unit/runtime/test_worker_runtime_service.py`).

Sem engine: a harness e dirigida com `FakeWorkerTransport` + `FakeKafkaPublisher`.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog

from maezo.platform.integrations.notifications_bridge import _record_sla_alert_outcome
from maezo.platform.notification_bridge import SLA_ALERT_DOMAINS, SLA_ALERT_TYPE_SUFFIX
from maezo.runtime.worker_runtime.service import register_default_workers
from maezo.tools.workers import auth, contas, lgpd
from maezo.tools.workers.bootstrap import register_all_workers
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerHarness,
)
from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS

_TOPIC = "operadora.contas.notify_sla_risk"
_CANAL = "operadora.notifications.internal"


def _harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="r107-probe")


def _task(variables: dict[str, Any] | None = None, *, topic: str = _TOPIC) -> ExternalTask:
    return ExternalTask(
        task_id="task-r107",
        topic=topic,
        process_instance_id="proc-r107",
        business_key="CONTAS-amh-LOTE-009",
        worker_id="w-r107",
        variables=variables
        if variables is not None
        else {
            "tenant_id": "amh",
            "numero_lote_tiss": "LOTE-009",
            "sla_remaining": "P2D",
            "grupo": "coordenacao-contas",
        },
    )


async def _executar_registrado(harness: WorkerHarness, task: ExternalTask) -> Any:
    """Executa o que REALMENTE esta registrado no topico (handler cru ou `FunctionWorker`)."""
    handler = harness._handlers.get(_TOPIC)
    if handler is not None:
        return await handler(task)
    worker = harness.registry.get(_TOPIC)
    assert worker is not None, "topico de CONTAS sem servidor"
    return worker.execute(dict(task.variables))


class _BrokerFora:
    """Produtor cujo publish falha — o broker fora do ar."""

    def __init__(self) -> None:
        self.best_effort_calls: list[bool | None] = []

    async def publish(
        self,
        topic: str,
        value: dict[str, Any],
        *,
        key: str | None = None,
        best_effort: bool | None = None,
        unordered: bool = False,
    ) -> bool:
        self.best_effort_calls.append(best_effort)
        raise ConnectionError("broker indisponivel (sintetico)")


# ---------------------------------------------------------------------------
# CONTAS — a entrega (bandeira ligada)
# ---------------------------------------------------------------------------


async def test_contas_notify_sla_risk_delivered_ao_canal_interno_com_a_bandeira_ligada() -> None:
    harness = _harness()
    kafka = FakeKafkaPublisher()
    contas.register_contas_workers(harness, kafka, contas_sla_risk_entrega=True)

    result = await _executar_registrado(harness, _task())

    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == _CANAL
    assert payload == {
        "type": "contas.notify_sla_risk",
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-009",
    }
    assert payload["type"].endswith(SLA_ALERT_TYPE_SUFFIX)
    # Fail-closed: o publish e o UNICO efeito da etapa (ADR-0030, propagacao crua).
    assert kafka.best_effort_calls == [False]
    # GAP-SC-04-a: nunca um publish sem chave (round-robin entre particoes).
    assert key is not None and key != ""
    # FAB-NOTIFIED-TRIO: publicar e PEDIDO de alerta, nunca prova de aviso — nada e afirmado.
    assert result == {}


async def test_contas_notify_sla_risk_delivered_nao_ocorre_com_a_bandeira_desligada() -> None:
    """Padrao (escuro): byte-a-byte o historico — `FunctionWorker`, nenhum publish."""
    harness = _harness()
    kafka = FakeKafkaPublisher()
    contas.register_contas_workers(harness, kafka)

    assert harness.registry.get(_TOPIC) is not None
    assert await _executar_registrado(harness, _task()) == {}
    assert kafka.published == []


async def test_contas_notify_sla_risk_delivered_falha_de_broker_propaga() -> None:
    """Falha de entrega NAO e engolida: propaga para a escada retry/incidente da harness.

    `ST_NotificarRiscoSla` nao declara boundary de erro e a falha e tecnica (transitoria), nao um
    desfecho de negocio — logo nenhum `bpmnError` e inventado (ADR-0030 §2). A propagacao fica no
    ramo lateral do timer nao-interruptivo; `UT_AnalistaContas` segue aberta.
    """
    harness = _harness()
    kafka = _BrokerFora()
    contas.register_contas_workers(harness, kafka, contas_sla_risk_entrega=True)

    with pytest.raises(ConnectionError):
        await _executar_registrado(harness, _task())
    assert kafka.best_effort_calls == [False]


async def test_contas_notify_sla_risk_delivered_payload_sem_phi() -> None:
    """Teste negativo (ADR-0006): PHI no escopo do processo NAO atravessa para o canal."""
    harness = _harness()
    kafka = FakeKafkaPublisher()
    contas.register_contas_workers(harness, kafka, contas_sla_risk_entrega=True)
    variaveis = {
        "tenant_id": "amh",
        "numero_lote_tiss": "LOTE-009",
        "grupo": "coordenacao-contas",
        "sla_remaining": "P2D",
        "cpf_beneficiario": "000.000.000-00",
        "nome_beneficiario": "Fulano Sintetico",
        "matricula_beneficiario": "MAT-SINT-1",
        "cid_principal": "Z00.0",
        "justificativa_clinica": "texto clinico sintetico",
    }

    await _executar_registrado(harness, _task(variaveis))

    _topic, payload, _key = kafka.published[0]
    assert set(payload) == {"type", "tenant_id", "numero_lote_tiss"}
    assert not set(payload) & set(PHI_PROCESS_VARS)
    serializado = repr(payload)
    for valor in ("000.000.000-00", "Fulano", "MAT-SINT-1", "Z00.0", "clinico"):
        assert valor not in serializado


async def test_contas_notify_sla_risk_delivered_sem_produtor_avisa_e_nao_fabrica() -> None:
    """Bandeira ligada sem produtor: loga alto (`notified_asserted=False`) e completa com `{}` —
    a mesma postura dos gemeos AUTH/RECURSO/LGPD; o ramo do alerta precisa alcancar
    `End_RiscoSlaNotificado`."""
    harness = _harness()
    contas.register_contas_workers(harness, None, contas_sla_risk_entrega=True)

    with structlog.testing.capture_logs() as logs:
        result = await _executar_registrado(harness, _task())

    assert result == {}
    avisos = [e for e in logs if e.get("event") == "contas_notify_sla_risk_no_producer"]
    assert len(avisos) == 1
    assert avisos[0]["notified_asserted"] is False
    assert avisos[0]["log_level"] == "warning"


async def test_contas_notify_sla_risk_delivered_mantem_o_escopo_de_fetch_restritivo() -> None:
    """O handler cru herda o MESMO escopo de variaveis do `FunctionWorker` (sem `variables="all"`)."""
    harness = _harness()
    contas.register_contas_workers(harness, FakeKafkaPublisher(), contas_sla_risk_entrega=True)

    assert harness._topic_variables[_TOPIC] == contas._CONTAS_FETCH_SCOPES[_TOPIC]
    assert "numero_lote_tiss" in harness._topic_variables[_TOPIC]


def test_contas_notify_sla_risk_delivered_e_observado_pelo_bridge_sem_ser_engolido() -> None:
    """Limite declarado: o bridge AINDA nao roteia `contas.notify_sla_risk` a SP-OP-ESCALATION-001
    (nova origem do ESCALATION = decisao do dono, como a #17 foi para AUTH). Ate la, a mensagem
    entregue e CONTADA e AVISADA como `unrecognised_shape` — nunca uma passagem silenciosa."""
    assert "contas.notify_sla_risk" not in SLA_ALERT_DOMAINS
    with structlog.testing.capture_logs() as logs:
        _record_sla_alert_outcome("contas.notify_sla_risk", [])
    avisos = [e for e in logs if e.get("event") == "notifications_bridge.sla_alert_not_escalated"]
    assert len(avisos) == 1
    assert avisos[0]["outcome"] == "unrecognised_shape"


# ---------------------------------------------------------------------------
# AUTH e LGPD — regressao: continuam entregando no MESMO canal
# ---------------------------------------------------------------------------


async def test_auth_notify_sla_risk_delivered_no_mesmo_canal() -> None:
    kafka = FakeKafkaPublisher()
    task = _task(
        {"tenant_id": "amh", "numero_guia_tiss": "GUIA-7", "beneficiario_pseudo_id": "PSEUDO-7"},
        topic="operadora.auth.notify_sla_risk",
    )

    await auth.make_notify_sla_risk_handler(kafka)(task)

    topic, payload, _key = kafka.published[0]
    assert topic == _CANAL
    assert payload["type"] == "auth.notify_sla_risk"
    assert kafka.best_effort_calls == [False]


async def test_lgpd_notify_sla_risk_delivered_no_mesmo_canal() -> None:
    kafka = FakeKafkaPublisher()
    task = _task(
        {
            "tenant_id": "amh",
            "titular_pseudo_id": "TIT-1",
            "tipo_requisicao": "acesso",
            "sla_breach_task_name": "UT_RevisaoDpo",
            "sla_breach_phase": "ack",
        },
        topic="operadora.lgpd.notify_sla_risk",
    )

    await lgpd.make_notify_sla_risk_handler(kafka)(task)

    topic, payload, _key = kafka.published[0]
    assert topic == _CANAL
    assert payload["type"] == "lgpd.notify_sla_risk"
    assert kafka.best_effort_calls == [False]


# ---------------------------------------------------------------------------
# Fiacao da bandeira: register_all_workers e register_default_workers REAIS
# ---------------------------------------------------------------------------


async def test_contas_notify_sla_risk_delivered_via_register_all_workers() -> None:
    harness = _harness()
    kafka = FakeKafkaPublisher()
    register_all_workers(harness, kafka, contas_sla_risk_entrega=True)

    await _executar_registrado(harness, _task())

    assert [t for t, _p, _k in kafka.published] == [_CANAL]


async def test_contas_notify_sla_risk_delivered_via_register_default_workers() -> None:
    harness = _harness()
    kafka = FakeKafkaPublisher()
    register_default_workers(harness, kafka=kafka, contas_sla_risk_entrega=True)  # type: ignore[arg-type]

    await _executar_registrado(harness, _task())

    assert [t for t, _p, _k in kafka.published] == [_CANAL]


async def test_contas_notify_sla_risk_delivered_register_default_workers_escuro_por_padrao() -> None:
    harness = _harness()
    kafka = FakeKafkaPublisher()
    register_default_workers(harness, kafka=kafka)  # type: ignore[arg-type]

    await _executar_registrado(harness, _task())

    assert kafka.published == []
    assert harness.registry.get(_TOPIC) is not None
