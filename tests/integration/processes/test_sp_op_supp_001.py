"""SP-OP-SUPP-001 — prova E2E-local do wiring GP11 (engine real + store 0021 real + DMN real).

Insumos ACEITOS PELO DONO (2026-10-07 — VW0-DECISION-REGISTER §"INCORPORAÇÃO VW4-ANSWERS",
sha `ab262f7b…`). O que cada prova EXIGE engine/Postgres para valer (nada disso é provável em
unit — ADR-0011):

1.  **DMN engine-side com `k` cross-input** — `< k_piso`/`>= k_piso` em unary test referencia
    OUTRO input da tabela (a cifra vive na constante do código); deploy + matching provados
    live no CIB Seven 2.1.0 (célula null não casa → catch-all fail-closed).
2.  **Relógio por registro armado pelo STORE** — os três timers `timeDate` de
    `UT_RotearEncarregado` leem as variáveis devolvidas por `verify_subject` a partir da
    migration 0021 (calendário own-code seg–sex SEM feriados). Os due dates dos jobs do
    engine têm de ser EXATAMENTE `t_lembrete`/`t_escalonado`/`t_deadline` do registro — e os
    três boundaries disparáveis por `execute_job` publicam `sla_lembrete`/`sla_escalonado`/
    `sla_estourado` na ordem, com o timeout INTERRUPTING encerrando em `End_AnaliseHumana...`.
3.  **Recusas tipadas preservadas no engine** — sem store ⇒ boundary
    `ERR_SUPP_SUBJECT_UNRESOLVED` ⇒ `End_SujeitoIrresolvivel` (UNKNOWN, nunca zero, ZERO
    incidentes — recusa modelada ≠ incidente); sem transport ⇒ boundary
    `ERR_SUPP_ROUTING_UNAVAILABLE` ⇒ `End_RoteamentoIndisponivel`.

Este módulo SELF-CONTAINS suas fixtures (convenção da família — não edita o conftest.py
compartilhado). Postgres: a lane aplica as migrations REAIS 0001→head (inclui a 0021) no
schema do run.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from maezo.gateway.vendor_suppression import PostgresSuppressionStore, SuppressionCommand
from maezo.tools.workers.dmn_transport import CibSevenDmnTransport
from maezo.tools.workers.events import EVENTS_BPMN_ERROR_ALLOWLIST, register_events_workers
from maezo.tools.workers.harness import CibSevenWorkerTransport, FakeKafkaPublisher, WorkerHarness
from maezo.tools.workers.suppression import (
    SUPPRESSION_BPMN_ERROR_ALLOWLIST,
    SUPPRESSION_ROUTE_TOPIC,
    SUPPRESSION_VERIFY_TOPIC,
    register_suppression_workers,
)

from .conftest import CIBSEVEN_BASE_URL
from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

_REPO = Path(__file__).resolve().parents[3]
_BPMN = _REPO / "spec/processes/bpmn/SP-OP-SUPP-001_Direitos_de_Nao_Contato.bpmn"
_DMN = _REPO / "spec/processes/dmn/suppression_routing.dmn"

_PUBLISH_TOPIC = "operadora.events.publish"

_K = 100  # K_ANON_FLOOR ratificado — espelhado aqui só para legibilidade do cenário


class _SuppProbe:
    """Família SUPP: harness real com os workers de supressão + publicador genérico."""

    def __init__(
        self,
        engine_rest: EngineRest,
        *,
        store: PostgresSuppressionStore | None,
        dmn: CibSevenDmnTransport | None,
        kafka: FakeKafkaPublisher,
        audit_sink: Any,
        tenant: str,
    ) -> None:
        self.engine_rest = engine_rest
        self.kafka = kafka
        self.worker_id = "supp-it-" + uuid.uuid4().hex[:8]
        self.transport = CibSevenWorkerTransport(CIBSEVEN_BASE_URL)
        # T-C (ADR-0007): audit-BEFORE-complete é fail-closed — sem sink, TODO completion vira
        # transient e o drain nunca avança. O probe usa o MESMO PostgresAuditSink da lane.
        # ADR-0030: a allowlist de erros modelados é CONSTRUÇÃO do harness (produção une as
        # constantes por módulo — sem ela, `WorkerBpmnError` é DEMOTED a failure(retries) e o
        # boundary do processo nunca dispara — prova E2E-local do wiring).
        self.harness = WorkerHarness(
            self.transport,
            worker_id=self.worker_id,
            tenant=tenant,
            audit_sink=audit_sink,
            bpmn_error_allowlist=SUPPRESSION_BPMN_ERROR_ALLOWLIST | EVENTS_BPMN_ERROR_ALLOWLIST,
        )
        register_events_workers(self.harness, kafka=kafka)
        register_suppression_workers(self.harness, suppression_store=store, suppression_dmn_transport=dmn)

    async def drain(self) -> None:
        """Ciclo bounded de fetch-and-lock + `_handle` até os três tópicos ociosarem."""
        from .conftest import drain_topics

        await drain_topics(
            self.transport,
            self.harness,
            self.worker_id,
            [_PUBLISH_TOPIC, SUPPRESSION_VERIFY_TOPIC, SUPPRESSION_ROUTE_TOPIC],
        )

    async def aclose(self) -> None:
        await self.transport.close()


@pytest_asyncio.fixture
async def supp_engine(audit_pg: tuple[str, str]) -> AsyncIterator[AsyncEngine]:
    """AsyncEngine sobre a lane Postgres (migrations REAIS aplicadas — inclui a 0021)."""
    from sqlalchemy.engine import make_url

    url = make_url(audit_pg[0])
    engine = create_async_engine(
        URL.create(
            "postgresql+asyncpg",
            username=url.username,
            password=url.password,
            host=url.host or "localhost",
            port=url.port or 5432,
            database=url.database or "postgres",
        ),
        echo=False,
        hide_parameters=True,
        # env.py cria as tabelas no schema do TENANT (search_path "{tenant}, public") — o
        # store do teste tem de ler o MESMO schema em que a 0021 foi aplicada.
        connect_args={"server_settings": {"search_path": f"{audit_pg[1]}, public"}},
    )
    yield engine
    await engine.dispose()


def _command(tenant: str, *, subject: str = "subj-it-1", channel: str = "chan-A") -> SuppressionCommand:
    return SuppressionCommand(
        tenant=tenant,
        subject_ref=subject,
        contact_channel=channel,
        canal="whatsapp",
        categoria_sujeito="vendedor",
        motivo="oposicao art. 18 §2o (E2E GP11)",
    )


async def _start_instance(
    engine_rest: EngineRest,
    business_key: str,
    variables: dict[str, Any],
    *,
    tenant: str,
    subject: str = "subj-it-1",
) -> str:
    started = await engine_rest.start_by_key(
        "SP-OP-SUPP-001",
        business_key,
        {
            "tenant_id": tenant,
            "subject_ref": subject,
            "contact_channel": "chan-A",
            "canal": "whatsapp",
            "categoria_sujeito": "vendedor",
            **variables,
        },
    )
    return str(started["id"])


def _published_topics(kafka: FakeKafkaPublisher) -> list[str]:
    # `published` é (topic, payload, partition_key | None).
    return [entry[0] for entry in kafka.published]


def _published(kafka: FakeKafkaPublisher) -> dict[str, list[dict[str, Any]]]:
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for topic, payload, _key in kafka.published:
        by_topic.setdefault(topic, []).append(dict(payload))
    return by_topic


async def _drain_until_idle(probe: _SuppProbe, cycles: int = 6) -> None:
    for _ in range(cycles):
        await probe.drain()


# ----------------------------------------------------------------------------------
# 1. Caminho honrado E2E — store real → verify (relógio) → route (DMN engine-side)
# ----------------------------------------------------------------------------------


async def test_caminho_honrado_e2e_com_relogio_do_registro(
    engine: EngineRest,
    supp_engine: AsyncEngine,
    audit_sink: Any,
    audit_tenant: str,
) -> None:
    await engine.deploy(_BPMN, name="gp11-supp-bpmn")
    await engine.deploy(_DMN, name="gp11-supp-dmn")
    store = PostgresSuppressionStore(supp_engine)
    dmn = CibSevenDmnTransport(CIBSEVEN_BASE_URL)
    kafka = FakeKafkaPublisher()
    probe = _SuppProbe(engine, store=store, dmn=dmn, kafka=kafka, audit_sink=audit_sink, tenant=audit_tenant)
    try:
        record = await store.record(_command(audit_tenant))
        assert record.status == "pendente"
        business_key = f"SUPP-it-gp11-{record.suppression_ref[:16]}-{uuid.uuid4().hex[:8]}"
        instance_id = await _start_instance(
            engine_rest=engine,
            business_key=business_key,
            variables={"classe_campo": "C2", "celula_tamanho": _K + 150},  # célula ≥ k=100
            tenant=audit_tenant,
        )
        await _drain_until_idle(probe)

        # DMN avaliada ENGINE-SIDE decidiu REGISTRO_HONRADO (row r_c2_ok — célula >= k_piso).
        assert await engine.history_state(instance_id) == "COMPLETED"
        ended = await engine.activity_instances_ended(instance_id)
        assert "End_Honrado" in ended
        assert "UT_RotearEncarregado" not in ended
        topics = _published_topics(kafka)
        assert "agents.events.vendor.suppression.recorded" in topics
        assert "agents.events.vendor.suppression.honored_in_list" in topics
        assert "agents.events.vendor.suppression.completed" not in topics
    finally:
        await dmn.close()
        await probe.aclose()


# ----------------------------------------------------------------------------------
# 2. Rota humana arma o relógio 50/80/100 e os três boundaries disparam em ordem
# ----------------------------------------------------------------------------------


async def test_rota_humana_arma_o_relogio_e_escala_50_80_100(
    engine: EngineRest,
    supp_engine: AsyncEngine,
    audit_sink: Any,
    audit_tenant: str,
) -> None:
    await engine.deploy(_BPMN, name="gp11-supp-bpmn")
    await engine.deploy(_DMN, name="gp11-supp-dmn")
    store = PostgresSuppressionStore(supp_engine)
    dmn = CibSevenDmnTransport(CIBSEVEN_BASE_URL)
    kafka = FakeKafkaPublisher()
    probe = _SuppProbe(engine, store=store, dmn=dmn, kafka=kafka, audit_sink=audit_sink, tenant=audit_tenant)
    try:
        record = await store.record(_command(audit_tenant, subject="subj-it-2"))
        business_key = f"SUPP-it-gp11-{record.suppression_ref[:16]}-{uuid.uuid4().hex[:8]}"
        instance_id = await _start_instance(
            engine_rest=engine,
            business_key=business_key,
            variables={"classe_campo": "C1", "celula_tamanho": None},  # classe proibida → humano
            tenant=audit_tenant,
            subject="subj-it-2",
        )
        await _drain_until_idle(probe)

        # A UT do encarregado está aberta com grupo `dpo` e o relógio do REGISTRO armado.
        assert await engine.instance_is_active(instance_id)
        timers = {t.activity_id: t for t in await engine.list_timer_jobs(instance_id)}
        assert set(timers) == {"BT_Lembrete50", "BT_Escala80", "BT_Timeout100"}
        _assert_due(timers["BT_Lembrete50"].due_date, record.t_lembrete)
        _assert_due(timers["BT_Escala80"].due_date, record.t_escalonado)
        _assert_due(timers["BT_Timeout100"].due_date, record.t_deadline)

        # 50% → lembrete (não-interrupting: a decisão humana continua).
        await engine.execute_job(timers["BT_Lembrete50"].id)
        await _drain_until_idle(probe)
        assert "agents.events.vendor.suppression.sla_lembrete" in _published_topics(kafka)
        assert await engine.instance_is_active(instance_id)
        # 80% → escala ao encarregado (não-interrupting).
        await engine.execute_job(timers["BT_Escala80"].id)
        await _drain_until_idle(probe)
        assert "agents.events.vendor.suppression.sla_escalonado" in _published_topics(kafka)
        assert await engine.instance_is_active(instance_id)
        # 100% → timeout ANALISE_HUMANA (interrupting — o registro fica visível e atrasado).
        await engine.execute_job(timers["BT_Timeout100"].id)
        await _drain_until_idle(probe)
        assert "agents.events.vendor.suppression.sla_estourado" in _published_topics(kafka)
        assert await engine.history_state(instance_id) == "COMPLETED"
        ended = await engine.activity_instances_ended(instance_id)
        assert "End_AnaliseHumanaSlaEstourado" in ended
        events = _published(kafka)
        estourado = events["agents.events.vendor.suppression.sla_estourado"][0]
        assert estourado.get("desfecho") == "analise_humana"
    finally:
        await dmn.close()
        await probe.aclose()


def _assert_due(due: str | None, expected: datetime) -> None:
    assert due is not None
    observed = datetime.fromisoformat(due.replace("Z", "+00:00"))
    delta = abs((observed - expected).total_seconds())
    assert delta < 5.0, f"due do timer {due} != relógio do registro {expected.isoformat()}"


# ----------------------------------------------------------------------------------
# 3. Recusas tipadas preservadas NO ENGINE (UNKNOWN ≠ zero; transport ausente)
# ----------------------------------------------------------------------------------


async def test_store_ausente_mantem_subject_unresolved_no_engine(
    engine: EngineRest,
    audit_sink: Any,
    audit_tenant: str,
) -> None:
    await engine.deploy(_BPMN, name="gp11-supp-bpmn")
    await engine.deploy(_DMN, name="gp11-supp-dmn")
    kafka = FakeKafkaPublisher()
    probe = _SuppProbe(
        engine,
        store=None,
        dmn=CibSevenDmnTransport(CIBSEVEN_BASE_URL),
        kafka=kafka,
        audit_sink=audit_sink,
        tenant=audit_tenant,
    )
    try:
        business_key = "SUPP-it-gp11-sem-store-" + uuid.uuid4().hex
        instance_id = await _start_instance(
            engine_rest=engine,
            business_key=business_key,
            variables={"classe_campo": "C2", "celula_tamanho": _K + 1},
            tenant=audit_tenant,
        )
        await _drain_until_idle(probe)

        assert await engine.history_state(instance_id) == "COMPLETED"
        ended = await engine.activity_instances_ended(instance_id)
        assert "End_SujeitoIrresolvivel" in ended
        events = _published(kafka)
        completed = events["agents.events.vendor.suppression.completed"][0]
        assert completed.get("desfecho") == "sujeito_irresolvivel"
        # Recusa modelada ≠ incidente: ZERO incidents no caminho do boundary.
        assert await engine.incidents(instance_id) == []
        assert "agents.events.vendor.suppression.honored_in_list" not in _published_topics(kafka)
    finally:
        await probe.aclose()


async def test_transport_ausente_mantem_routing_unavailable_no_engine(
    engine: EngineRest,
    supp_engine: AsyncEngine,
    audit_sink: Any,
    audit_tenant: str,
) -> None:
    await engine.deploy(_BPMN, name="gp11-supp-bpmn")
    await engine.deploy(_DMN, name="gp11-supp-dmn")
    store = PostgresSuppressionStore(supp_engine)
    kafka = FakeKafkaPublisher()
    probe = _SuppProbe(engine, store=store, dmn=None, kafka=kafka, audit_sink=audit_sink, tenant=audit_tenant)
    try:
        record = await store.record(_command(audit_tenant, subject="subj-it-3"))
        business_key = f"SUPP-it-gp11-{record.suppression_ref[:16]}-{uuid.uuid4().hex[:8]}"
        instance_id = await _start_instance(
            engine_rest=engine,
            business_key=business_key,
            variables={"classe_campo": "C2", "celula_tamanho": _K + 1},
            tenant=audit_tenant,
            subject="subj-it-3",
        )
        await _drain_until_idle(probe)

        assert await engine.history_state(instance_id) == "COMPLETED"
        ended = await engine.activity_instances_ended(instance_id)
        assert "End_RoteamentoIndisponivel" in ended
        assert await engine.incidents(instance_id) == []
        completed = _published(kafka)["agents.events.vendor.suppression.completed"][0]
        assert completed.get("desfecho") == "roteamento_indisponivel"
    finally:
        await probe.aclose()
