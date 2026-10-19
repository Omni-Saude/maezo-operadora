"""DL-0126 — prova ao vivo: o prazo de ciencia estourado vira UMA linha do supervisor no inbox.

Cadeia exercitada de ponta a ponta, sem mock de motor (AGENTS.md regra 4):

  motor CIB Seven REAL (SP-OP-ESCALATION-001 + escalation_routing.dmn da arvore)
  -> job do timer `BT_SlaAck` executado (sem sleep) -> `ST_NotificarSupervisor`
  -> worker REAL `make_notify_supervisor_handler` (via `EngineProbe` do suite irmao)
  -> o payload publicado e' enviado a um broker Kafka REAL (topico proprio do teste), DUAS vezes
  -> `run_inbox_loop` + `AioKafkaBridgeConsumer` REAIS
  -> `PostgresEscalationTeamNoticeInbox` REAL (schema migrado do lane)
  => exatamente UMA linha `grupo_atendimento = supervisao-atendimento` com o `business_key` do caso.

Por que `BT_SlaAck` e nao `BT_SlaResolucao`: no BPMN so' o timer de ciencia (e o fallback de canal)
passa por `operadora.escalation.notify_supervisor`; o de resolucao vai a `ST_PublishResolBreach`
-> `UT_SupervisorAssume` e nunca gera aviso ao supervisor.

Sem motor, sem Postgres ou sem broker: SKIP ALTO (nunca verde falso).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.platform.integrations.notifications_bridge import AioKafkaBridgeConsumer
from maezo.platform.integrations.notifications_inbox import (
    PostgresEscalationTeamNoticeInbox,
    TeamNotice,
    TeamNoticeReceipt,
    run_inbox_loop,
)
from maezo.tools.workers.escalation import GRUPO_SUPERVISAO, MOTIVO_ALERTA_SLA_ACK

from .engine_rest import EngineRest

# Fixtures do suite irmao (mesmo probe com os workers REAIS, mesmo deploy, mesmo start canonico).
from .test_sp_op_escalation_001 import (  # noqa: F401
    EngineProbe,
    StartEscalation,
    deploy_artifacts,
    probe,
    start_escalation,
)

pytestmark = pytest.mark.integration

_TIMEOUT_S = 60.0


def _bootstrap() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


async def _kafka_ou_skip(servers: str) -> None:
    from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]

    producer = AIOKafkaProducer(bootstrap_servers=servers)
    try:
        await asyncio.wait_for(producer.start(), timeout=5.0)
    except Exception:
        with contextlib.suppress(Exception):
            await producer.stop()
        pytest.skip(f"COULD NOT VERIFY: no reachable Kafka broker at {servers!r}")
    await producer.stop()


async def _criar_topico(servers: str, topic: str) -> None:
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic  # type: ignore[import-untyped]

    admin = AIOKafkaAdminClient(bootstrap_servers=servers)
    await admin.start()
    try:
        await asyncio.wait_for(
            admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)]), timeout=15.0
        )
    finally:
        await admin.close()


class _ContaEPara:
    """Inbox REAL embrulhada: conta as gravacoes e para o laco depois de `alvo` mensagens."""

    def __init__(self, real: PostgresEscalationTeamNoticeInbox, alvo: int, stop: asyncio.Event) -> None:
        self.real = real
        self.alvo = alvo
        self.stop = stop
        self.recibos: list[TeamNoticeReceipt] = []

    async def record(self, notice: TeamNotice) -> TeamNoticeReceipt:
        recibo = await self.real.record(notice)
        self.recibos.append(recibo)
        if len(self.recibos) >= self.alvo:
            self.stop.set()
        return recibo


async def test_prazo_de_ciencia_estourado_vira_uma_linha_do_supervisor_no_inbox(
    engine: EngineRest,
    probe: EngineProbe,  # noqa: F811 - fixture importada do suite irmao
    start_escalation: StartEscalation,  # noqa: F811
    audit_pg: tuple[str, str],
) -> None:
    dsn, tenant = audit_pg
    servers = _bootstrap()
    await _kafka_ou_skip(servers)

    # 1. Motor real: caso aberto, timer de CIENCIA executado como job, worker real publica o aviso.
    inst = await start_escalation(motivo_categoria="red_flag_clinico", severidade="grave", tenant_id=tenant)
    iid = inst["id"]
    await probe.drain()
    await engine.await_user_task(iid, "UT_TratarEscalonamento")
    job = await engine.await_timer_job(iid, "BT_SlaAck")
    await engine.execute_job(job.id)
    await probe.drain()

    avisos = [v for v in probe.notified_supervisors if v.get("motivo_alerta") == MOTIVO_ALERTA_SLA_ACK]
    assert len(avisos) == 1, probe.notified_supervisors
    aviso = avisos[0]
    business_key = aviso["business_key"]
    assert business_key == inst.get("businessKey", business_key)
    assert aviso["alert_to"] == GRUPO_SUPERVISAO

    # 2. Kafka real: topico proprio do teste, o MESMO aviso entregue duas vezes (reentrega).
    from aiokafka import AIOKafkaProducer

    topic = f"it.escalation-supervisor-inbox.{uuid.uuid4().hex[:8]}"
    await _criar_topico(servers, topic)
    stop = asyncio.Event()
    inbox = _ContaEPara(PostgresEscalationTeamNoticeInbox(dsn=dsn, tenant=tenant), alvo=2, stop=stop)
    consumer = AioKafkaBridgeConsumer(
        bootstrap_servers=servers, topic=topic, group_id=f"it-esc-inbox-{uuid.uuid4().hex[:8]}"
    )
    await consumer.start()
    producer = AIOKafkaProducer(bootstrap_servers=servers)
    await producer.start()
    try:
        # Espera a particao ser atribuida e le do inicio (topico novo, so' tem o que este teste publica).
        raw_consumer = consumer._consumer
        loop = asyncio.get_running_loop()
        limite = loop.time() + _TIMEOUT_S
        while not raw_consumer.assignment():
            assert loop.time() < limite, "particao nunca atribuida"
            await asyncio.sleep(0.2)
        await raw_consumer.seek_to_beginning()
        corpo = json.dumps(aviso).encode("utf-8")
        await producer.send_and_wait(topic, corpo, key=business_key.encode("utf-8"))
        await producer.send_and_wait(topic, corpo, key=business_key.encode("utf-8"))
        await asyncio.wait_for(run_inbox_loop(consumer, inbox, stop_event=stop), timeout=_TIMEOUT_S)
    finally:
        await producer.stop()
        await consumer.stop()

    assert [str(r.outcome) for r in inbox.recibos] == ["recorded", "duplicate"]

    # 3. Postgres real: UMA linha, grupo de supervisao, o caso nomeado.
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{schema_for_tenant(tenant)}"')
        linhas = await conn.fetch(
            "SELECT grupo_atendimento, business_key, audience FROM escalation_team_notice "
            "WHERE business_key = $1",
            business_key,
        )
    finally:
        await conn.close()
    assert [dict(linha) for linha in linhas] == [
        {"grupo_atendimento": GRUPO_SUPERVISAO, "business_key": business_key, "audience": "staff"}
    ]
