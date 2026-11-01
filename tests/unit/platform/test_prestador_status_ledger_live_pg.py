"""MPTT P3 migracao 0032 AO VIVO: append-only, idempotencia por evento, enums fechados, ida e volta.

Cadeia alembic REAL sobre um schema de tenant descartavel no Postgres do compose/CI (molde
`test_legitimacy_screens_migration_live_pg.py`) — nenhum DDL simulado. Provas:

- `upgrade head` aplica a 0032 e o store `PostgresLedgerStatusPrestador` grava e le de volta
  EXATAMENTE a linha (round trip), e `ler_situacao` ve a ultima situacao em ordem de registro;
- IDEMPOTENCIA: o mesmo evento (mesma `chave_evento`) gravado de novo nao cria segunda linha
  (`registrar` -> False), e a mesma chave com OUTRO conteudo e' `LedgerConflitoError`, nunca
  sobrescrita;
- APPEND-ONLY: UPDATE, DELETE e TRUNCATE direto no banco levantam a excecao do trigger;
- ENUMS FECHADOS no banco: estado de leitura (`DESCONHECIDO`/`INDISPONIVEL`), valor de fraude
  na dimensao situacao e origem incoerente violam o CHECK `valor_por_dimensao`;
- `downgrade 0030` apaga tabela e funcao, e um segundo `upgrade head` as restaura.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import date
from pathlib import Path

import asyncpg
import pytest

from maezo.platform.prestador_status.ledger import (
    EntradaLedger,
    LedgerConflitoError,
    PostgresLedgerStatusPrestador,
    SituacaoLida,
    ler_situacao,
)
from maezo.platform.prestador_status.projecao import Dimensao, Origem
from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[3]


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
            "MAEZO_PG_HOST_PORT) — MPTT P3 prestador_status_ledger live tests SKIPPED (visible, not "
            "silent). Start it with `docker compose --profile core up -d postgres` and re-run this file."
        )


async def _migrate(dsn: str, tenant: str, direction: str, revision: str) -> None:
    """A CLI/env.py real com o schema do tenant; URL so' no ambiente do filho."""
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


def _entrada(tenant: str, **campos: object) -> EntradaLedger:
    base: dict[str, object] = {
        "tenant": tenant,
        "prestador_id": "SINT-PG-1",
        "origem": Origem.BASELINE_AMH,
        "dimensao": Dimensao.SITUACAO,
        "valor": "CREDENCIADO",
        "data_efeito": date(2019, 3, 1),
        "chave_evento": "BASELINE_AMH:" + "a" * 64 + ":SINT-PG-1",
        "fonte_ref": "a" * 64,
        "evidencia_ref": "fixture://baseline-sintetica-pg",
        "versao_projecao": "mptt-p3-projecao/v1",
        "tipo_prestador": "clinica",
        "especialidade": "Cardiologia",
        "regiao_saude": "RS-Sintetica-1",
    }
    base.update(campos)
    return EntradaLedger(**base)  # type: ignore[arg-type]


async def test_append_only_idempotente_e_fechado_ao_vivo() -> None:
    dsn = _default_test_dsn()
    tenant = "pp" + uuid.uuid4().hex[:20]
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'CREATE SCHEMA "{tenant}"')
    finally:
        await admin.close()
    try:
        await _migrate(dsn, tenant, "upgrade", "head")
        store = PostgresLedgerStatusPrestador(dsn=dsn, tenant=tenant)
        raw = await asyncpg.connect(dsn)
        try:
            await raw.execute(f'SET search_path TO "{tenant}"')

            # ── round trip + leitura de situacao ──
            baseline = _entrada(tenant)
            assert await store.registrar(baseline) is True
            assert await store.historico(tenant, "SINT-PG-1") == (baseline,)
            assert await store.prestadores(tenant) == ("SINT-PG-1",)
            assert (await ler_situacao(store, tenant, "SINT-PG-1")).situacao is SituacaoLida.CREDENCIADO
            assert (await ler_situacao(store, tenant, "SINT-PG-404")).situacao is SituacaoLida.DESCONHECIDO

            # ── idempotencia: mesmo evento = mesma linha; mesma chave com outro conteudo = conflito ──
            assert await store.registrar(baseline) is False
            with pytest.raises(LedgerConflitoError):
                await store.registrar(_entrada(tenant, valor="DESCREDENCIADO"))
            assert await raw.fetchval("SELECT count(*) FROM prestador_status_ledger") == 1

            # ── delta do CRED depois da baseline: a leitura ve o ultimo registro ──
            delta = _entrada(
                tenant,
                origem=Origem.CRED,
                valor="DESCREDENCIADO",
                data_efeito=date(2026, 12, 1),
                chave_evento="CRED:inst-pg-1:End_PrestadorDescredenciado",
                fonte_ref="inst-pg-1",
                evidencia_ref=f"CRED-{tenant}-SINT-PG-1",
            )
            assert await store.registrar(delta) is True
            leitura = await ler_situacao(store, tenant, "SINT-PG-1")
            assert (leitura.situacao, leitura.data_efeito, leitura.origem) == (
                SituacaoLida.DESCREDENCIADO,
                date(2026, 12, 1),
                Origem.CRED,
            )
            fraude = _entrada(
                tenant,
                origem=Origem.FRAUDE,
                dimensao=Dimensao.FRAUDE,
                valor="MONITORAR",
                data_efeito=None,
                chave_evento="FRAUDE:inst-pg-2:End_MonitorarSemAcao",
                fonte_ref="inst-pg-2",
                evidencia_ref=f"FRAUDE-{tenant}-CASO-1",
                tipo_prestador=None,
                especialidade=None,
                regiao_saude=None,
            )
            assert await store.registrar(fraude) is True
            # a linha de fraude nao muda a situacao lida
            assert (await ler_situacao(store, tenant, "SINT-PG-1")).situacao is SituacaoLida.DESCREDENCIADO

            # ── append-only: o banco recusa UPDATE, DELETE e TRUNCATE ──
            with pytest.raises(asyncpg.RaiseError, match="append-only"):
                await raw.execute("UPDATE prestador_status_ledger SET valor = 'CREDENCIADO'")
            with pytest.raises(asyncpg.RaiseError, match="append-only"):
                await raw.execute("DELETE FROM prestador_status_ledger")
            with pytest.raises(asyncpg.RaiseError, match="append-only"):
                await raw.execute("TRUNCATE prestador_status_ledger")
            assert await raw.fetchval("SELECT count(*) FROM prestador_status_ledger") == 3

            # ── enums fechados no BANCO (o CHECK nomeado e' o que dispara) ──
            async def recusado(origem: str, dimensao: str, valor: str) -> None:
                with pytest.raises(
                    asyncpg.CheckViolationError, match="prestador_status_ledger_valor_por_dimensao"
                ):
                    await raw.execute(
                        "INSERT INTO prestador_status_ledger (tenant, prestador_id, origem, dimensao, valor,"
                        " chave_evento, fonte_ref, evidencia_ref, versao_projecao)"
                        " VALUES ($1, 'SINT-PG-2', $2, $3, $4, $5, 'x', 'y', 'mptt-p3-projecao/v1')",
                        tenant,
                        origem,
                        dimensao,
                        valor,
                        "k-" + uuid.uuid4().hex,
                    )

            await recusado("CRED", "situacao", "DESCONHECIDO")
            await recusado("CRED", "situacao", "INDISPONIVEL")
            await recusado("CRED", "situacao", "SUSPENSO")
            await recusado("CRED", "situacao", "CONFIRMADA")
            await recusado("FRAUDE", "situacao", "CREDENCIADO")
            await recusado("CRED", "fraude", "CONFIRMADA")
            await recusado("FRAUDE", "fraude", "ABERTA")
            assert await raw.fetchval("SELECT count(*) FROM prestador_status_ledger") == 3
        finally:
            await raw.close()

        # ── ida e volta reversivel ──
        await _migrate(dsn, tenant, "downgrade", "0030")
        check = await asyncpg.connect(dsn)
        try:
            assert (
                await check.fetchval(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema = $1 "
                    "AND table_name = 'prestador_status_ledger'",
                    tenant,
                )
                == 0
            )
            assert (
                await check.fetchval(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = $1 AND p.proname = 'prestador_status_ledger_imutavel'",
                    tenant,
                )
                == 0
            )
        finally:
            await check.close()
        await _migrate(dsn, tenant, "upgrade", "head")
        store2 = PostgresLedgerStatusPrestador(dsn=dsn, tenant=tenant)
        assert await store2.prestadores(tenant) == ()
    finally:
        admin = await asyncpg.connect(dsn)
        try:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{tenant}" CASCADE')
        finally:
            await admin.close()
