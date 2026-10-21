"""Inbox duravel de `escalation.notify_team` contra infraestrutura REAL (test-spec SP-OP-ESCALATION-001,
secao "Inbox duravel de `notify_team`", WP-J1-09 — ADR-0051, ADR-0037 XRD-10).

Os tres testes que o test-spec declara e que nao existiam. O que e' REAL: a instancia de
SP-OP-ESCALATION-001 no motor (BPMN deste checkout, implantado no tenant da execucao), os workers de
producao (`register_events_workers` + `register_escalation_workers`) publicando a notificacao com o
`AioKafkaEventsProducer` no broker, o consumidor de producao (`AioKafkaBridgeConsumer`) num grupo
PROPRIO, o laco de producao (`run_inbox_loop`) e o `PostgresEscalationTeamNoticeInbox` sobre um schema
por execucao com a cadeia alembic INTEIRA aplicada (`audit_pg`, `tests/integration/conftest.py`: o
mesmo `command.upgrade(..., "head")` de `test_wamid_dedup_live_pg.py`). A notificacao nunca e'
forjada: e' a que `ST_NotificarTime` publicou.

"Entrega" e' a linha commitada mais o recibo, nunca o offset. A ordem e' observada NO INSTANTE do
commit: `ConsumidorLimitado.antes_do_commit` conta as linhas do caso imediatamente antes de o offset
ser confirmado no broker.
"""

from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import httpx
import pytest

from maezo.gateway.audit_postgres import normalize_dsn, schema_for_tenant
from maezo.platform.integrations.events_kafka_producer import AioKafkaEventsProducer
from maezo.platform.integrations.notifications_inbox import (
    NOTICE_AUDIENCE,
    NOTIFICATIONS_TOPIC,
    NoticeRecordOutcome,
    PostgresEscalationTeamNoticeInbox,
    TeamNotice,
    TeamNoticeReceipt,
    run_inbox_loop,
)
from maezo.runtime.worker_runtime.service import PRODUCTION_BPMN_ERROR_ALLOWLIST
from maezo.tools.workers.escalation import NOTIFY_TEAM_NOTIFICATION_TYPE, register_escalation_workers
from maezo.tools.workers.events import register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, WorkerHarness
from tests.integration.agents._engine_helpers import candidate_groups
from tests.integration.conftest import _audit_pg_dsn
from tests.integration.processes.engine_rest import EngineRest
from tests.support.engine_tenant_deploy import deploy_for_tenant
from tests.support.escalation_chain import (
    ConsumidorLimitado,
    consumidor_kafka_real,
    drenar_por_chave,
    grupo_unico,
    kafka_bootstrap_servers,
    mensagem_da_chave,
    offsets_confirmados,
    tarefas_humanas,
)

pytestmark = pytest.mark.integration

#: Os resolvedores de coordenada que esta suite USA, com os nomes que a cerca
#: `tests/unit/ci/test_live_suite_defaults_are_served.py` executa e confere contra o docker-compose:
#: o Postgres e' o de `audit_pg` (`tests/integration/conftest.py::_audit_pg_dsn`) e o broker e' o de
#: `tests/support/escalation_chain.kafka_bootstrap_servers`.
_audit_dsn = _audit_pg_dsn
_kafka_bootstrap_servers = kafka_bootstrap_servers

_PRAZO_KAFKA_S = 30.0


@pytest.fixture(scope="module", autouse=True)
def _escalation_do_tenant(engine_base_url: str, audit_tenant: str) -> None:
    deploy_for_tenant(engine_base_url, audit_tenant, "SP-OP-ESCALATION-001")


@dataclass
class _InboxQueRegistra:
    """O inbox REAL, com os recibos que ele devolveu guardados para a assercao (nada e' decidido aqui)."""

    real: PostgresEscalationTeamNoticeInbox
    recibos: list[TeamNoticeReceipt] = field(default_factory=list)

    async def record(self, notice: TeamNotice) -> TeamNoticeReceipt:
        recibo = await self.real.record(notice)
        self.recibos.append(recibo)
        return recibo


async def _fechar(inbox: PostgresEscalationTeamNoticeInbox) -> None:
    pool = inbox._pool  # o inbox nao expoe `aclose`; o pool e' dele e e' fechado aqui
    if pool is not None:
        await pool.close()


async def _linhas(dsn: str, tenant: str, business_key: str) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{schema_for_tenant(tenant)}"')
        registros = await conn.fetch(
            "SELECT business_key, audience, grupo_atendimento, notice_ref FROM escalation_team_notice "
            "WHERE tenant = $1 AND business_key = $2",
            tenant,
            business_key,
        )
    finally:
        await conn.close()
    return [dict(r) for r in registros]


@dataclass
class _Caso:
    business_key: str
    instance_id: str
    grupo_da_tarefa: str


@pytest.fixture
async def escalonar(
    engine_base_url: str, engine_client: httpx.AsyncClient, audit_sink: Any, audit_tenant: str
) -> AsyncIterator[Any]:
    """Inicia UMA instancia real e a leva, com os workers de producao, ate' `UT_TratarEscalonamento`.

    Devolve uma corotina: o teste conecta o consumidor do inbox ANTES de chamar (a notificacao tem de
    nascer depois da ancora do grupo)."""
    token = uuid.uuid4().hex[:8]
    worker_id = f"esc-inbox-{token}"
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
    criadas: list[str] = []

    async def _rodar() -> _Caso:
        conversa = f"conv-inbox-{token}"
        business_key = f"ESC-{audit_tenant}-{conversa}"
        variaveis = EngineRest._to_camunda_vars(
            {
                "tenant_id": audit_tenant,
                "source_agent_id": "helena",
                "source_agent_version": "qa-1.0",
                "conversation_id": conversa,
                "beneficiario_pseudo_id": "PSEUDO-TESTE-001",
                "canal": "whatsapp",
                "motivo_categoria": "solicitacao_humano",
                "severidade": "leve",
                "resumo_contexto": "Beneficiario Teste 001 — sintese sintetica para humano",
            }
        )
        resposta = await engine_client.post(
            f"/process-definition/key/SP-OP-ESCALATION-001/tenant-id/{audit_tenant}/start",
            json={"businessKey": business_key, "variables": variaveis},
        )
        resposta.raise_for_status()
        instance_id = str(resposta.json()["id"])
        criadas.append(instance_id)

        async def _ha_tarefa() -> bool:
            return bool(await tarefas_humanas(engine_client, instance_id))

        tratadas = await drenar_por_chave(
            engine_client,
            harness,
            worker_id=worker_id,
            business_key=business_key,
            instance_id=instance_id,
            pronto=_ha_tarefa,
        )
        # Canal primario: notify_team publicou (sem fallback do supervisor).
        assert [t.topic for t in tratadas].count("operadora.escalation.notify_team") == 1
        assert "operadora.escalation.notify_supervisor" not in [t.topic for t in tratadas]
        tarefa = (await tarefas_humanas(engine_client, instance_id))[0]
        assert tarefa["taskDefinitionKey"] == "UT_TratarEscalonamento"
        grupos = await candidate_groups(engine_client, tarefa["id"])
        assert len(grupos) == 1
        return _Caso(business_key=business_key, instance_id=instance_id, grupo_da_tarefa=next(iter(grupos)))

    try:
        yield _rodar
    finally:
        await producer.close()
        await transporte.close()
        for instance_id in criadas:
            resposta = await engine_client.delete(
                f"/process-instance/{instance_id}", params={"skipCustomListeners": "true"}
            )
            assert resposta.status_code in (204, 404)


async def test_notify_team_grava_linha_de_inbox_antes_do_offset(
    escalonar: Any, audit_pg: tuple[str, str]
) -> None:
    dsn, tenant = audit_pg
    inbox = _InboxQueRegistra(PostgresEscalationTeamNoticeInbox(dsn=dsn, tenant=tenant))
    async with consumidor_kafka_real(NOTIFICATIONS_TOPIC, grupo=grupo_unico("esc-d2-ordem")) as cons:
        caso = await escalonar()
        linhas_no_commit: list[int] = []

        async def _no_commit() -> None:
            linhas_no_commit.append(len(await _linhas(dsn, tenant, caso.business_key)))

        limitado = ConsumidorLimitado(
            cons, do_caso=mensagem_da_chave(caso.business_key), antes_do_commit=_no_commit
        )
        try:
            await asyncio.wait_for(run_inbox_loop(limitado, inbox), timeout=_PRAZO_KAFKA_S)
        finally:
            await _fechar(inbox.real)

        mensagem = limitado.entregues[0]
        assert mensagem.value["type"] == NOTIFY_TEAM_NOTIFICATION_TYPE
        # A linha ja' existia quando o offset foi confirmado (XRD-10: entrega e' a linha, nunca o offset).
        assert linhas_no_commit == [1]
        [linha] = await _linhas(dsn, tenant, caso.business_key)
        assert linha["grupo_atendimento"] == caso.grupo_da_tarefa  # o candidateGroups da User Task
        assert linha["audience"] == NOTICE_AUDIENCE == "staff"
        assert linha["business_key"] == caso.business_key
        assert [r.outcome for r in inbox.recibos] == [NoticeRecordOutcome.RECORDED]
        assert linha["notice_ref"] == inbox.recibos[0].notice_ref
        confirmados = await offsets_confirmados(cons)
        assert confirmados[mensagem.partition] == mensagem.offset + 1


class _QuedaDepoisDaLinhaError(RuntimeError):
    """O processo do inbox morre DEPOIS de gravar e ANTES de confirmar o offset."""


async def test_notify_team_reentregue_nao_duplica_a_linha(escalonar: Any, audit_pg: tuple[str, str]) -> None:
    dsn, tenant = audit_pg
    grupo = grupo_unico("esc-d2-reentrega")
    inbox = _InboxQueRegistra(PostgresEscalationTeamNoticeInbox(dsn=dsn, tenant=tenant))
    try:
        async with consumidor_kafka_real(NOTIFICATIONS_TOPIC, grupo=grupo) as cons:
            caso = await escalonar()

            async def _cair() -> None:
                raise _QuedaDepoisDaLinhaError

            primeira = ConsumidorLimitado(
                cons, do_caso=mensagem_da_chave(caso.business_key), antes_do_commit=_cair
            )
            with pytest.raises(_QuedaDepoisDaLinhaError):
                await asyncio.wait_for(run_inbox_loop(primeira, inbox), timeout=_PRAZO_KAFKA_S)
            assert primeira.commits == 0
        # O daemon "reinicia": outro consumidor do MESMO grupo retoma do offset confirmado e o broker
        # entrega a MESMA mensagem de novo.
        async with consumidor_kafka_real(NOTIFICATIONS_TOPIC, grupo=grupo, ancorar_no_fim=False) as cons2:
            segunda = ConsumidorLimitado(cons2, do_caso=mensagem_da_chave(caso.business_key))
            await asyncio.wait_for(run_inbox_loop(segunda, inbox), timeout=_PRAZO_KAFKA_S)
            assert segunda.entregues[0].offset == primeira.entregues[0].offset
            assert segunda.entregues[0].raw == primeira.entregues[0].raw
            confirmados = await offsets_confirmados(cons2)
            assert confirmados[segunda.entregues[0].partition] == segunda.entregues[0].offset + 1
    finally:
        await _fechar(inbox.real)

    assert [r.outcome for r in inbox.recibos] == [NoticeRecordOutcome.RECORDED, NoticeRecordOutcome.DUPLICATE]
    assert inbox.recibos[0].notice_ref == inbox.recibos[1].notice_ref
    assert inbox.recibos[1].recorded_at == inbox.recibos[0].recorded_at  # data a entrega, nao a reentrega
    assert len(await _linhas(dsn, tenant, caso.business_key)) == 1


async def test_notify_team_sem_banco_nao_acka(escalonar: Any, audit_pg: tuple[str, str]) -> None:
    dsn, tenant = audit_pg
    grupo = grupo_unico("esc-d2-sem-banco")
    reservada = socket.socket()
    reservada.bind(
        ("127.0.0.1", 0)
    )  # reservada SEM listen: conexao recusada de verdade, sem corrida de porta
    dsn_fora = f"postgresql://maezo:maezo@127.0.0.1:{reservada.getsockname()[1]}/maezo"
    sem_banco = PostgresEscalationTeamNoticeInbox(dsn=dsn_fora, tenant=tenant)
    try:
        async with consumidor_kafka_real(NOTIFICATIONS_TOPIC, grupo=grupo) as cons:
            ancora = await offsets_confirmados(cons)
            caso = await escalonar()
            limitado = ConsumidorLimitado(cons, do_caso=mensagem_da_chave(caso.business_key))
            with pytest.raises(OSError):
                await asyncio.wait_for(run_inbox_loop(limitado, sem_banco), timeout=_PRAZO_KAFKA_S)
            assert limitado.commits == 0
            assert await offsets_confirmados(cons) == ancora  # o offset NAO andou
            perdida = limitado.entregues[0]
    finally:
        reservada.close()
        await _fechar(sem_banco)
    assert await _linhas(dsn, tenant, caso.business_key) == []

    # O banco volta: a MESMA mensagem e' reentregue ao grupo e vira linha — o aviso nao se perdeu.
    inbox = _InboxQueRegistra(PostgresEscalationTeamNoticeInbox(dsn=dsn, tenant=tenant))
    try:
        async with consumidor_kafka_real(NOTIFICATIONS_TOPIC, grupo=grupo, ancorar_no_fim=False) as cons2:
            de_novo = ConsumidorLimitado(cons2, do_caso=mensagem_da_chave(caso.business_key))
            await asyncio.wait_for(run_inbox_loop(de_novo, inbox), timeout=_PRAZO_KAFKA_S)
    finally:
        await _fechar(inbox.real)
    assert de_novo.entregues[0].offset == perdida.offset
    assert [r.outcome for r in inbox.recibos] == [NoticeRecordOutcome.RECORDED]
    assert len(await _linhas(dsn, tenant, caso.business_key)) == 1
