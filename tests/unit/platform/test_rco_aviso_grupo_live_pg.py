"""DL-0186 — migracao 0037 (`rco_aviso_grupo` + `fila_sem_grupo`) AO VIVO (Postgres real, nunca DDL simulado).

Cadeia alembic REAL sobre um schema de tenant descartavel (molde `test_rco_aviso_auditoria_live_pg.py`).
Provas:

- uma linha antiga do outbox com `ultimo_erro = 'publicacao_falhou'` (gravada na 0036) continua valida depois
  da 0037;
- o relay real (`PostgresAvisosRco` + `drain_once`) marca `falhou`/`fila_sem_grupo` ANTES de publicar, sem
  contar tentativa, e o CHECK novo aceita o codigo; codigo fora do vocabulario continua recusado;
- `PostgresInboxAvisosRco` grava UMA linha por aviso (`RECORDED`), a re-entrega e' `DUPLICATE` com o
  `registrado_em` original; aviso de outro tenant e' recusado;
- `rco_aviso_grupo` e' imutavel (UPDATE, DELETE e TRUNCATE levantam) e os CHECKs recusam forma fora do token,
  audiencia diferente de `staff` e `notice_ref` fora de sha256;
- `downgrade 0036` com linha `fila_sem_grupo` FALHA e nada muda; depois de `--reprocessar` o downgrade
  devolve o CHECK original e apaga a tabela; um segundo `upgrade head` a restaura.
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
from maezo.platform.integrations.notifications_inbox import (
    NoticeRecordOutcome,
    PostgresInboxAvisosRco,
    resolver_grupo,
)
from maezo.platform.rco_avisos import PostgresAvisosRco, linhas_de_aviso
from maezo.platform.rco_manifesto import AvisoK6
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn
from tests.unit.platform.test_rco_avisos import ProdutorFalso

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[3]

SINAL = "aviso_urgencia_carencia"
AVISOS = {SINAL: AvisoK6(SINAL, ("auditoria_medica", "cadastro"), "OMNI-GERAL-CAR-0003:efeito.prazos.2")}
GRUPOS = {"auditoria_medica": "medico-auditor", "cadastro": "cadastro-beneficiarios"}


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
            "MAEZO_PG_HOST_PORT) — DL-0186 rco_aviso_grupo live tests SKIPPED (visible, not silent)."
        )


async def _migrate(dsn: str, tenant: str, direction: str, revision: str, *, ok: bool = True) -> str:
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
        texto = output.decode(errors="replace")
        assert (process.returncode == 0) is ok, texto
        return texto
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def _schema_novo(dsn: str, revisao: str = "head") -> str:
    tenant = "ag" + uuid.uuid4().hex[:20]
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'CREATE SCHEMA "{tenant}"')
    finally:
        await admin.close()
    await _migrate(dsn, tenant, "upgrade", revisao)
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


async def _existe(dsn: str, tenant: str, tabela: str) -> bool:
    rows = await _sql(dsn, tenant, f"SELECT to_regclass('{tabela}') AS t")
    return rows[0]["t"] is not None


async def _funcoes_imutavel(dsn: str, tenant: str) -> int:
    """`rco_aviso_grupo_imutavel` no schema deste tenant (outros schemas de teste nao contam)."""
    rows = await _sql(
        dsn,
        tenant,
        "SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE p.proname = 'rco_aviso_grupo_imutavel' AND n.nspname = $1",
        tenant,
    )
    return int(rows[0]["n"])


async def test_check_alargado_relay_sem_grupo_e_downgrade_explicito_ao_vivo() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn, "0036")
    try:
        # Na 0036: uma linha que falhou na publicacao (o unico codigo de entao).
        store = PostgresAvisosRco(dsn=dsn, tenant=tenant)
        assert await store.enfileirar(_linhas(tenant, "task-antiga")) == 2
        rel = await rco_avisos_relay.drain_once(
            store, ProdutorFalso(falhas=1), reivindicada_por="r", grupos_do_pin=lambda: GRUPOS
        )
        assert rel.erro == "publicacao_falhou"
        with pytest.raises(asyncpg.CheckViolationError):  # a 0036 ainda nao conhece o codigo novo
            await _sql(
                dsn, tenant, "UPDATE rco_aviso_auditoria SET ultimo_erro = 'fila_sem_grupo' WHERE id = 1"
            )

        await _migrate(dsn, tenant, "upgrade", "head")
        assert await _existe(dsn, tenant, "rco_aviso_grupo")
        antiga = await _sql(
            dsn, tenant, "SELECT status, ultimo_erro, tentativas FROM rco_aviso_auditoria WHERE id = 1"
        )
        assert [tuple(r) for r in antiga] == [("falhou", "publicacao_falhou", 1)]  # continua valida

        # Relay real: `cadastro` sem grupo -> `fila_sem_grupo` antes de publicar, sem tentativa.
        assert await store.enfileirar(_linhas(tenant, "task-2")) == 2
        produtor = ProdutorFalso()
        so_auditoria = {"auditoria_medica": GRUPOS["auditoria_medica"]}
        rel = await rco_avisos_relay.drain_once(
            store, produtor, reivindicada_por="r", grupos_do_pin=lambda: so_auditoria
        )
        # reivindica a `cadastro` antiga (devolvida a pendente) e as duas novas: as duas `cadastro` sem grupo
        assert (rel.reivindicadas, rel.entregues, rel.sem_grupo, rel.erro) == (3, 1, 2, None)
        rows = await _sql(
            dsn,
            tenant,
            "SELECT fila, status, ultimo_erro, tentativas FROM rco_aviso_auditoria WHERE task_id = 'task-2' "
            "ORDER BY id",
        )
        assert [tuple(r) for r in rows] == [
            ("auditoria_medica", "entregue", None, 1),
            ("cadastro", "falhou", "fila_sem_grupo", 0),
        ]
        assert "cadastro" not in {p[1]["fila"] for p in produtor.publicadas if p[1]["task_id"] == "task-2"}
        with pytest.raises(asyncpg.CheckViolationError):
            await _sql(dsn, tenant, "UPDATE rco_aviso_auditoria SET ultimo_erro = 'texto livre' WHERE id = 1")

        # Downgrade com `fila_sem_grupo`: recusa explicita, nada muda.
        saida = await _migrate(dsn, tenant, "downgrade", "0036", ok=False)
        assert "downgrade 0037 recusado" in saida
        assert await _existe(dsn, tenant, "rco_aviso_grupo")
        versao = await _sql(dsn, tenant, f'SELECT version_num FROM "{tenant}_alembic_version"')
        assert [r["version_num"] for r in versao] == ["0037"]

        assert await _funcoes_imutavel(dsn, tenant) == 1  # a funcao vive no schema DESTE tenant
        # O operador decide (`--reprocessar`) e repete: CHECK original de volta, tabela e funcao apagadas.
        assert await rco_avisos_relay.reprocessar(store) == 3  # as duas `fila_sem_grupo` e a antiga
        await _migrate(dsn, tenant, "downgrade", "0036")
        assert not await _existe(dsn, tenant, "rco_aviso_grupo")
        assert await _funcoes_imutavel(dsn, tenant) == 0
        check = await _sql(
            dsn,
            tenant,
            "SELECT pg_get_constraintdef(c.oid) AS d FROM pg_constraint c JOIN pg_namespace n "
            "ON n.oid = c.connamespace WHERE n.nspname = $1 "
            "AND c.conname = 'rco_aviso_auditoria_ultimo_erro_check'",
            tenant,
        )
        assert "fila_sem_grupo" not in check[0]["d"] and "publicacao_falhou" in check[0]["d"]
        await _migrate(dsn, tenant, "upgrade", "head")
        assert await _existe(dsn, tenant, "rco_aviso_grupo")
    finally:
        await _drop(dsn, tenant)


async def test_destino_postgres_idempotente_imutavel_e_checks_ao_vivo() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    sink = PostgresInboxAvisosRco(dsn=dsn, tenant=tenant)
    try:
        avisos = [resolver_grupo(linha, GRUPOS) for linha in _linhas(tenant)]
        recibos = [await sink.registrar(a) for a in avisos]
        assert [r.outcome for r in recibos] == [NoticeRecordOutcome.RECORDED] * 2
        de_novo = await sink.registrar(avisos[0])
        assert de_novo.outcome is NoticeRecordOutcome.DUPLICATE
        assert de_novo.recorded_at == recibos[0].recorded_at and de_novo.notice_ref == recibos[0].notice_ref
        rows = await _sql(
            dsn, tenant, "SELECT tenant, task_id, fila, grupo, audience FROM rco_aviso_grupo ORDER BY fila"
        )
        assert [tuple(r) for r in rows] == [
            (tenant, "task-1", "auditoria_medica", "medico-auditor", "staff"),
            (tenant, "task-1", "cadastro", "cadastro-beneficiarios", "staff"),
        ]
        colunas = await _sql(
            dsn,
            tenant,
            "SELECT column_name FROM information_schema.columns WHERE table_schema = $1 "
            "AND table_name = 'rco_aviso_grupo'",
            tenant,
        )
        assert {c["column_name"] for c in colunas}.isdisjoint({"business_key", "payload", "mensagem", "cpf"})

        with pytest.raises(ValueError, match="wrong tenant schema"):
            await sink.registrar(resolver_grupo(_linhas("outro")[0], GRUPOS))

        for sql in (
            "UPDATE rco_aviso_grupo SET grupo = 'outro-grupo'",
            "DELETE FROM rco_aviso_grupo",
            "TRUNCATE rco_aviso_grupo",
        ):
            with pytest.raises(asyncpg.RaiseError, match="imutavel"):
                await _sql(dsn, tenant, sql)

        base = (
            "INSERT INTO rco_aviso_grupo (tenant, notice_ref, chave_idempotencia, task_id, composicao, "
            "sinal, "
            "fila, prazo_parametro, grupo, audience) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)"
        )
        ok = [
            tenant,
            "a" * 64,
            "b" * 64,
            "task-9",
            "composicao_carencia_cpt",
            SINAL,
            "cadastro",
            "OMNI-GERAL-CAR-0003:efeito.prazos.2",
            "g",
            "staff",
        ]
        for indice, ruim in (
            (1, "nao-e-sha"),
            (2, "B" * 64),
            (3, "task com espaco"),
            (5, "sinal livre"),
            (6, "fila/x"),
            (7, "prazo com espaco"),
            (8, "grupo livre com texto"),
            (9, "beneficiario"),
            (0, "ten ant"),
        ):
            valores = list(ok)
            valores[indice] = ruim
            with pytest.raises(asyncpg.CheckViolationError):
                await _sql(dsn, tenant, base, *valores)
        await _sql(dsn, tenant, base, *ok)  # a forma certa entra
    finally:
        pool = sink._pool
        if pool is not None:
            await pool.close()
        await _drop(dsn, tenant)
