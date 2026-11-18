"""DL-0186 — reconciliacao `sinaisAuditoria` x `rco_aviso_auditoria` AO VIVO (Postgres real, alembic real).

Num schema de tenant descartavel em `head`: o harness com a auditoria REAL (`PostgresAuditSink`) e o outbox
REAL conclui `task-1` (avisos gravados) e, sem outbox ligado, `task-2` (auditoria com `sinaisAuditoria`,
nenhuma linha: a janela de aviso perdido de DL-0185). A reconciliacao, so leitura, acha exatamente os dois
avisos de `task-2` como `ausente_no_outbox`, e nada muda nas tabelas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from maezo.gateway.audit_postgres import PostgresAuditSink
from maezo.platform import rco_avisos_reconciliacao as rr
from maezo.platform.rco_avisos import PostgresAvisosRco, chave_idempotencia
from maezo.platform.rco_composicao import ResultadoComposicao
from maezo.platform.rco_manifesto import AvisoK6, ComposicaoK1
from maezo.tools.workers._audit_ctx import registrar_composicao
from maezo.tools.workers.harness import ExternalTask, FakeWorkerTransport, WorkerHarness
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.platform.test_rco_aviso_auditoria_live_pg import (
    _drop,
    _schema_novo,
    _skip_if_postgres_unreachable,  # noqa: F401  # fixture autouse: pula visivelmente sem Postgres
    _sql,
)

pytestmark = pytest.mark.integration

SINAL = "aviso_urgencia_carencia"
COMPOSICAO = "composicao_carencia_cpt"
AVISOS = {SINAL: AvisoK6(SINAL, ("auditoria_medica", "cadastro"), "OMNI-GERAL-CAR-0003:efeito.prazos.2")}
GRUPOS = (
    ComposicaoK1(COMPOSICAO, ("rco_geral_carencia", "rco_geral_cpt"), "mais_restritiva", "rco_geral_cpt"),
)


async def test_reconciliacao_acha_o_aviso_que_nao_chegou_ao_outbox() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    auditoria = PostgresAuditSink(dsn, tenant)
    try:

        async def handler(task: ExternalTask) -> dict[str, Any]:
            # a entrada auditada da decisao do grupo (o que `record_dmn_version` grava), depois a composicao
            from maezo.tools.workers import _audit_ctx

            coletor = _audit_ctx._DMN_AUDIT_COLLECTOR.get()
            assert coletor is not None
            coletor["rco_geral_carencia"] = {"version": 1, "shaDmn": "sha256:x"}
            registrar_composicao(
                ResultadoComposicao(
                    composicao=COMPOSICAO,
                    desfecho="escolhido",
                    avaliadas=("rco_geral_carencia",),
                    sinais=(SINAL,),
                ),
                modo="sombra",
            )
            return {"ok": True}

        def _harness(outbox: PostgresAvisosRco | None) -> WorkerHarness:
            h = WorkerHarness(
                FakeWorkerTransport(),
                worker_id="w",
                tenant=tenant,
                audit_sink=auditoria,
                rco_avisos=outbox,
                rco_avisos_do_pin=lambda: AVISOS,
            )
            h.register("operadora.test.rco_aviso", handler)
            return h

        def _task(task_id: str) -> ExternalTask:
            return ExternalTask(
                task_id=task_id,
                topic="operadora.test.rco_aviso",
                process_instance_id="p",
                business_key="bk-1",
                worker_id="w",
            )

        await _harness(PostgresAvisosRco(dsn=dsn, tenant=tenant))._handle(_task("task-1"))
        await _harness(None)._handle(_task("task-2"))  # outbox nao configurado: aviso perdido

        antes = await _sql(dsn, tenant, "SELECT count(*) FROM rco_aviso_auditoria")
        agora = datetime.now(UTC)
        rel = await rr.executar(
            rr.PostgresFonteReconciliacao(dsn=dsn, tenant=tenant),
            tenant=tenant,
            desde=agora - timedelta(hours=1),
            ate=agora + timedelta(hours=1),
            composicoes=GRUPOS,
            avisos_do_pin=AVISOS,
        )
        assert rel.registros == 2 and rel.esperados == 4
        assert rel.encontrados_por_status == {"pendente": 2}
        assert [(d.codigo, d.task_id, d.fila, d.chave_idempotencia) for d in rel.divergencias] == [
            (
                "ausente_no_outbox",
                "task-2",
                fila,
                chave_idempotencia(tenant, "task-2", COMPOSICAO, SINAL, fila),
            )
            for fila in ("auditoria_medica", "cadastro")
        ]
        assert await _sql(dsn, tenant, "SELECT count(*) FROM rco_aviso_auditoria") == antes  # so leitura

        # fora da janela: nada
        vazio = await rr.executar(
            rr.PostgresFonteReconciliacao(dsn=dsn, tenant=tenant),
            tenant=tenant,
            desde=agora - timedelta(days=3),
            ate=agora - timedelta(days=2),
            composicoes=GRUPOS,
            avisos_do_pin=AVISOS,
        )
        assert (vazio.registros, vazio.ok) == (0, True)
    finally:
        await auditoria.aclose()
        await _drop(dsn, tenant)
