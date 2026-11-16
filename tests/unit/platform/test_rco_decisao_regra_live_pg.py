"""5c-2 — migracao 0035 e o store do registro de decisoes K1 AO VIVO (Postgres real, nunca DDL simulado).

Cadeia alembic REAL sobre um schema de tenant descartavel (molde `test_pagto_obrigacao_ledger_live_pg.py`).
Provas:

- `upgrade head` aplica a 0035; o store grava a linha com o ordinal 1-based por (regra, versao);
- CONCORRENCIA: N avaliacoes simultaneas da MESMA regra recebem os ordinais 1..N, sem repeticao nem
  buraco (o lock transacional serializa; o indice unico parcial e' a segunda barreira);
- IDEMPOTENCIA: a re-entrega da mesma tarefa nao grava de novo e nao consome ordinal;
- regras (ou versoes da mesma regra) diferentes contam separado; decisao nao amostravel nao tem ordinal;
- APPEND-ONLY nas DUAS tabelas: UPDATE, DELETE e TRUNCATE direto no banco levantam a excecao do trigger;
  o desfecho e' LINHA NOVA ligada por FK; CHECKs recusam vocabulario fechado violado e chave bruta;
- banco fora -> `RegistroRcoIndisponivelError`;
- `downgrade -1` apaga as duas tabelas e as funcoes, e um segundo `upgrade head` as restaura.
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

from maezo.platform import rco_ativacao as at
from maezo.platform.rco_manifesto import ParametrosAtivacao, RegraK1
from maezo.platform.rco_registro import (
    ContextoDecisao,
    PostgresRegistroDecisoesRco,
    RegistroRcoIndisponivelError,
    avaliar_regra,
    chave_idempotencia,
)
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[3]

PARAMETROS = ParametrosAtivacao(3, 7, 50, 5, 30, 2)


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
            "MAEZO_PG_HOST_PORT) — 5c-2 rco_decisao_regra live tests SKIPPED (visible, not "
            "silent). Start it with `docker compose --profile core up -d postgres` and re-run this file."
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
    tenant = "rc" + uuid.uuid4().hex[:20]
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


def _avaliacao(id_regra: str = "REGRA-1", versao: str = "1.0.0", decisao: str = "glosar") -> Any:
    av = avaliar_regra(
        decision_key="rco_x",
        sha_dmn="sha256:" + "a" * 64,
        regra=RegraK1(id_regra, versao, "b" * 64, "plena", False),
        parametros=PARAMETROS,
        row={"decisao": decisao, "classeAdverso": "contas", "motivoTab38": "1801", "fila": "faturamento"},
        estados=at.EstadosManuais({}),
        habilitado=False,
    )
    assert av is not None
    return av


def _ctx(tenant: str, tarefa: str, **campos: Any) -> ContextoDecisao:
    base: dict[str, Any] = {
        "tenant": tenant,
        "chave_idempotencia": chave_idempotencia(tenant, tarefa, "rco_x"),
        "chave_negocio": f"AUTH-{tenant}-{tarefa}",
        "chave_amostra": b"segredo",
        "id_chave_amostra": "k1",
    }
    base.update(campos)
    return ContextoDecisao(**base)


async def _linhas(dsn: str, tenant: str, sql: str, *args: Any) -> list[Any]:
    raw = await asyncpg.connect(dsn)
    try:
        await raw.execute(f'SET search_path TO "{tenant}", public')
        return list(await raw.fetch(sql, *args))
    finally:
        await raw.close()


async def test_ordinal_idempotencia_append_only_e_downgrade_ao_vivo() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    try:
        store = PostgresRegistroDecisoesRco(dsn=dsn, tenant=tenant)

        # ordinal 1-based por (regra, versao); as 3 primeiras sao `inicial` (revisao_inicial = 3).
        for i in range(1, 6):
            assert await store.registrar(_avaliacao(), _ctx(tenant, f"t{i}")) is True
        rows = await _linhas(
            dsn,
            tenant,
            "SELECT ordinal, amostrada, motivo_amostra, acao, modo FROM rco_decisao_regra ORDER BY id",
        )
        assert [r["ordinal"] for r in rows] == [1, 2, 3, 4, 5]
        assert [r["motivo_amostra"] for r in rows[:3]] == ["inicial"] * 3
        assert all(r["acao"] == "registrar_apenas" and r["modo"] == "sombra" for r in rows)
        # o 4o e o 5o sao decididos pelo HMAC: amostrada <=> motivo `aleatoria`
        assert all(r["amostrada"] == (r["motivo_amostra"] == "aleatoria") for r in rows[3:])

        # IDEMPOTENCIA: a re-entrega do t3 nao grava e nao consome ordinal.
        assert await store.registrar(_avaliacao(), _ctx(tenant, "t3")) is False
        assert await store.registrar(_avaliacao(), _ctx(tenant, "t6")) is True
        ordinais = [
            r["ordinal"]
            for r in await _linhas(dsn, tenant, "SELECT ordinal FROM rco_decisao_regra ORDER BY id")
        ]
        assert ordinais == [1, 2, 3, 4, 5, 6]

        # outra regra e outra versao da mesma regra contam separado; decisao sem amostragem nao tem ordinal.
        await store.registrar(_avaliacao("REGRA-2"), _ctx(tenant, "u1"))
        await store.registrar(_avaliacao("REGRA-1", "2.0.0"), _ctx(tenant, "u2"))
        await store.registrar(_avaliacao(decisao="liberar"), _ctx(tenant, "u3"))
        por_regra = {
            (r["id_regra"], r["versao_regra"], r["decisao"]): r["ordinal"]
            for r in await _linhas(
                dsn,
                tenant,
                "SELECT id_regra, versao_regra, decisao, max(ordinal) AS ordinal "
                "FROM rco_decisao_regra GROUP BY 1, 2, 3",
            )
        }
        assert por_regra[("REGRA-2", "1.0.0", "glosar")] == 1
        assert por_regra[("REGRA-1", "2.0.0", "glosar")] == 1
        assert por_regra[("REGRA-1", "1.0.0", "liberar")] is None
        assert por_regra[("REGRA-1", "1.0.0", "glosar")] == 6

        raw = await asyncpg.connect(dsn)
        try:
            await raw.execute(f'SET search_path TO "{tenant}", public')
            total = await raw.fetchval("SELECT count(*) FROM rco_decisao_regra")
            primeiro = await raw.fetchval("SELECT id FROM rco_decisao_regra ORDER BY id LIMIT 1")

            # DESFECHO: linha nova ligada por FK; a decisao nao e' tocada.
            await raw.execute(
                "INSERT INTO rco_decisao_regra_desfecho (rco_decisao_regra_id, concordou, desfecho_em) "
                "VALUES ($1, 'sim', clock_timestamp())",
                primeiro,
            )
            await raw.execute(
                "INSERT INTO rco_decisao_regra_desfecho (rco_decisao_regra_id, concordou) VALUES ($1, 'nao')",
                primeiro,
            )
            assert await raw.fetchval("SELECT count(*) FROM rco_decisao_regra_desfecho") == 2
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await raw.execute(
                    "INSERT INTO rco_decisao_regra_desfecho (rco_decisao_regra_id, concordou) "
                    "VALUES (-1, 'sim')"
                )
            with pytest.raises(asyncpg.CheckViolationError):
                await raw.execute(
                    "INSERT INTO rco_decisao_regra_desfecho (rco_decisao_regra_id, concordou) "
                    "VALUES ($1, 'talvez')",
                    primeiro,
                )

            # APPEND-ONLY nas duas tabelas.
            for tabela, coluna in (
                ("rco_decisao_regra", "modo"),
                ("rco_decisao_regra_desfecho", "concordou"),
            ):
                for sql in (
                    f"UPDATE {tabela} SET {coluna} = {coluna}",
                    f"DELETE FROM {tabela}",
                    # as duas juntas: e' o unico TRUNCATE que o Postgres deixa chegar ao trigger da mae
                    f"TRUNCATE {tabela}, rco_decisao_regra, rco_decisao_regra_desfecho",
                ):
                    with pytest.raises(asyncpg.RaiseError, match="append-only"):
                        await raw.execute(sql)
            # a mae sozinha nem chega ao trigger: a FK da filha ja recusa o TRUNCATE
            with pytest.raises(asyncpg.FeatureNotSupportedError):
                await raw.execute("TRUNCATE rco_decisao_regra")
            assert await raw.fetchval("SELECT count(*) FROM rco_decisao_regra") == total

            # CHECKs: vocabulario fechado, texto livre recusado, ordinal unico.
            base = (
                "INSERT INTO rco_decisao_regra (tenant, chave_idempotencia, id_regra, versao_regra, sha_dmn, "
                "decision_key, estado_manual, ativacao_pacote, ativacao_efetiva, modo, acao, amostrada, "
                "{col}) VALUES ($1, $2, 'R', '1.0.0', $3, 'rco_x', 'sombra', 'plena', 'plena', 'sombra', "
                "'registrar_apenas', false, {val})"
            )
            sha = "sha256:" + "a" * 64
            colunas = {
                r["column_name"]
                for r in await raw.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = $1 AND table_name LIKE 'rco_decisao_regra%'",
                    tenant,
                )
            }
            assert not any("negocio" in c or "pseudo" in c for c in colunas)  # chave de negocio nao e gravada
            for col, val in (
                ("decisao", "'Maria da Silva'"),  # texto livre
                ("motivo_amostra", "'inicial'"),  # amostrada=false com motivo
                ("ordinal", "0"),
            ):
                with pytest.raises(asyncpg.CheckViolationError):
                    await raw.execute(base.format(col=col, val=val), tenant, "f" * 64, sha)
            await raw.execute(base.format(col="ordinal", val="1"), tenant, "e" * 64, sha)
            with pytest.raises(asyncpg.UniqueViolationError):  # a segunda barreira do ordinal
                await raw.execute(base.format(col="ordinal", val="1"), tenant, "d" * 64, sha)
        finally:
            await raw.close()

        await _migrate(dsn, tenant, "downgrade", "-1")
        raw = await asyncpg.connect(dsn)
        try:
            for tabela in ("rco_decisao_regra", "rco_decisao_regra_desfecho"):
                assert await raw.fetchval("SELECT to_regclass($1)", f'"{tenant}".{tabela}') is None
            assert (
                await raw.fetchval(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = $1 AND p.proname LIKE 'rco_decisao_regra%'",
                    tenant,
                )
                == 0
            )
        finally:
            await raw.close()
        await _migrate(dsn, tenant, "upgrade", "head")
    finally:
        await _drop(dsn, tenant)


async def test_avaliacoes_concorrentes_da_mesma_regra_recebem_ordinais_distintos_e_sem_buraco() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    try:
        store = PostgresRegistroDecisoesRco(dsn=dsn, tenant=tenant)
        n = 24
        resultados = await asyncio.gather(
            *(store.registrar(_avaliacao(), _ctx(tenant, f"c{i}")) for i in range(n)),
            # a mesma tarefa chegando duas vezes ao mesmo tempo: so' uma grava
            *(store.registrar(_avaliacao(), _ctx(tenant, "dup")) for _ in range(4)),
        )
        assert resultados.count(True) == n + 1
        ordinais = sorted(
            r["ordinal"] for r in await _linhas(dsn, tenant, "SELECT ordinal FROM rco_decisao_regra")
        )
        assert ordinais == list(range(1, n + 2))  # sem repeticao e sem buraco
    finally:
        await _drop(dsn, tenant)


async def test_banco_fora_e_registro_indisponivel() -> None:
    store = PostgresRegistroDecisoesRco(dsn="postgresql://maezo:maezo@127.0.0.1:1/maezo", tenant="amh")
    with pytest.raises(RegistroRcoIndisponivelError):
        await store.registrar(_avaliacao(), _ctx("amh", "t1"))
