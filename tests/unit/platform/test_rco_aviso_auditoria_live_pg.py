"""DL-0184 — migracao 0036 e o outbox dos avisos K1 AO VIVO (Postgres real, nunca DDL simulado).

Cadeia alembic REAL sobre um schema de tenant descartavel (molde `test_rco_decisao_regra_live_pg.py`).
Provas:

- `upgrade head` aplica a 0036; `enfileirar` grava uma linha por (aviso, fila) e a re-entrega nao duplica;
- o relay (`drain_once`, produtor falso) publica o payload exato e marca `entregue` com `tentativas = 1`;
- publicacao que falha marca `falhou` com o codigo fechado e `tentativas + 1`; o resto do lote volta a
  `pendente`; `reprocessar` + nova varredura entrega;
- lease vencido e' reivindicado de novo; os CHECKs recusam estado e codigo fora do vocabulario;
- harness + store real: a tarefa completa e as linhas aparecem no schema do tenant;
- banco fora -> `OutboxAvisosIndisponivelError` (so o tipo);
- `downgrade 0035` apaga a tabela e um segundo `upgrade head` a restaura.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from maezo.platform import rco_avisos_relay
from maezo.platform.rco_avisos import (
    OutboxAvisosIndisponivelError,
    PostgresAvisosRco,
    linhas_de_aviso,
    montar_payload,
)
from maezo.platform.rco_composicao import ResultadoComposicao
from maezo.platform.rco_manifesto import AvisoK6
from maezo.tools.workers._audit_ctx import registrar_composicao
from maezo.tools.workers.harness import ExternalTask, FakeAuditSink, FakeWorkerTransport, WorkerHarness
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.platform.test_rco_avisos import ProdutorFalso

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[3]

SINAL = "aviso_urgencia_carencia"
AVISOS = {SINAL: AvisoK6(SINAL, ("auditoria_medica", "cadastro"), "OMNI-GERAL-CAR-0003:efeito.prazos.2")}


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture(autouse=True)
def _skip_if_postgres_unreachable() -> None:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL / "
            "MAEZO_PG_HOST_PORT) — DL-0184 rco_aviso_auditoria live tests SKIPPED (visible, not silent)."
        )


async def _migrate(dsn: str, tenant: str, direction: str, revision: str) -> None:
    env = dict(os.environ, ALEMBIC_DATABASE_URL=dsn)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-x",
        f"tenant={tenant}",
        direction,
        revision,
        cwd=_ROOT,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 300)
        assert process.returncode == 0, output.decode(errors="replace")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def _schema_novo(dsn: str) -> str:
    tenant = "av" + uuid.uuid4().hex[:20]
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'CREATE SCHEMA "{tenant}"')
    finally:
        await admin.close()
    await _migrate(dsn, tenant, "upgrade", "head")
    return tenant


async def _drop(dsn: str, tenant: str) -> None:
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'DROP SCHEMA "{tenant}" CASCADE')
    finally:
        await admin.close()


async def _sql(dsn: str, tenant: str, sql: str, *args: Any) -> list[Any]:
    raw = await asyncpg.connect(dsn)
    try:
        await raw.execute(f'SET search_path TO "{tenant}", public')
        return list(await raw.fetch(sql, *args))
    finally:
        await raw.close()


def _linhas(tenant: str, task_id: str = "task-1") -> Any:
    return linhas_de_aviso(
        tenant=tenant,
        task_id=task_id,
        composicoes=[("composicao_carencia_cpt", (SINAL,))],
        avisos_do_pin=AVISOS,
    )


async def test_outbox_relay_falha_reprocessar_lease_e_downgrade_ao_vivo() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    try:
        store = PostgresAvisosRco(dsn=dsn, tenant=tenant)
        assert await store.enfileirar(_linhas(tenant)) == 2
        assert await store.enfileirar(_linhas(tenant)) == 0  # re-entrega: nada duplica
        assert await store.contar_por_status() == {"pendente": 2}

        # publicacao: a primeira falha -> `falhou` (codigo fechado, tentativas 1); a segunda volta a pendente
        rel = await rco_avisos_relay.drain_once(store, ProdutorFalso(falhas=1), reivindicada_por="relay-1")
        assert (rel.falhas, rel.liberadas, rel.erro) == (1, 1, "publicacao_falhou")
        rows = await _sql(
            dsn, tenant, "SELECT fila, status, tentativas, ultimo_erro FROM rco_aviso_auditoria ORDER BY id"
        )
        assert [tuple(r) for r in rows] == [
            ("auditoria_medica", "falhou", 1, "publicacao_falhou"),
            ("cadastro", "pendente", 0, None),
        ]

        produtor = ProdutorFalso()
        await rco_avisos_relay.drain_once(store, produtor, reivindicada_por="relay-1")
        assert await store.contar_por_status() == {"falhou": 1, "entregue": 1}
        assert await rco_avisos_relay.reprocessar(store) == 1
        await rco_avisos_relay.drain_once(store, produtor, reivindicada_por="relay-1")
        rows = await _sql(
            dsn, tenant, "SELECT status, tentativas, entregue_em FROM rco_aviso_auditoria ORDER BY id"
        )
        assert [(r["status"], r["tentativas"]) for r in rows] == [("entregue", 2), ("entregue", 1)]
        assert all(r["entregue_em"] is not None for r in rows)
        assert [p[1] for p in produtor.publicadas] == [montar_payload(x) for x in reversed(_linhas(tenant))]

        # lease vencido: a linha reivindicada por um relay que caiu volta a ser reivindicavel
        assert await store.enfileirar(_linhas(tenant, "task-2")) == 2
        presas = await store.reivindicar(reivindicada_por="relay-caiu", lote=1, lease_s=0.0)
        assert len(presas) == 1
        await asyncio.sleep(0.05)
        de_novo = await store.reivindicar(reivindicada_por="relay-2", lote=10, lease_s=60.0)
        assert {a.id for a in de_novo} >= {presas[0].id}
        # quem perdeu o lease nao marca
        assert await store.marcar_entregue([presas[0].id], reivindicada_por="relay-caiu") == 0

        # CHECKs: estado e codigo fora do vocabulario sao recusados pelo banco
        for sql in (
            "UPDATE rco_aviso_auditoria SET status = 'perdida' WHERE id = 1",
            "UPDATE rco_aviso_auditoria SET status = 'falhou', ultimo_erro = 'texto livre', "
            "entregue_em = NULL WHERE id = 1",
            "UPDATE rco_aviso_auditoria SET status = 'falhou', ultimo_erro = NULL, "
            "entregue_em = NULL WHERE id = 1",
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                await _sql(dsn, tenant, sql)

        await _migrate(dsn, tenant, "downgrade", "0035")
        existe = await _sql(dsn, tenant, "SELECT to_regclass('rco_aviso_auditoria') AS t")
        assert existe[0]["t"] is None
        await _migrate(dsn, tenant, "upgrade", "head")
        existe = await _sql(dsn, tenant, "SELECT to_regclass('rco_aviso_auditoria') AS t")
        assert existe[0]["t"] is not None
    finally:
        await _drop(dsn, tenant)


async def test_harness_com_store_real_grava_depois_de_completar() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    try:
        transport = FakeWorkerTransport()
        harness = WorkerHarness(
            transport,
            worker_id="w",
            tenant=tenant,
            audit_sink=FakeAuditSink(),
            rco_avisos=PostgresAvisosRco(dsn=dsn, tenant=tenant),
            rco_avisos_do_pin=lambda: AVISOS,
        )

        async def handler(task: ExternalTask) -> dict[str, Any]:
            registrar_composicao(
                ResultadoComposicao(
                    composicao="composicao_carencia_cpt", desfecho="escolhido", sinais=(SINAL,)
                ),
                modo="sombra",
            )
            return {"ok": True}

        harness.register("operadora.test.rco_aviso", handler)
        task = ExternalTask(
            task_id="task-1",
            topic="operadora.test.rco_aviso",
            process_instance_id="p",
            business_key="CONTA-x-guia-1",
            worker_id="w",
        )
        await harness._handle(task)
        await harness._handle(task)  # re-entrega
        assert transport.completed == [("task-1", {"ok": True})] * 2
        rows = await _sql(
            dsn, tenant, "SELECT tenant, task_id, fila, status FROM rco_aviso_auditoria ORDER BY id"
        )
        assert [tuple(r) for r in rows] == [
            (tenant, "task-1", "auditoria_medica", "pendente"),
            (tenant, "task-1", "cadastro", "pendente"),
        ]
        colunas = await _sql(
            dsn,
            tenant,
            "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 "
            "AND table_name = 'rco_aviso_auditoria'",
            tenant,
        )
        assert "business_key" not in {c["column_name"] for c in colunas}
    finally:
        await _drop(dsn, tenant)


async def test_banco_fora_vira_erro_tipado() -> None:
    store = PostgresAvisosRco(dsn="postgresql://ninguem@127.0.0.1:1/nada", tenant="amh")
    with pytest.raises(OutboxAvisosIndisponivelError) as exc:
        await store.enfileirar(_linhas("amh"))
    assert "ninguem" not in str(exc.value)
