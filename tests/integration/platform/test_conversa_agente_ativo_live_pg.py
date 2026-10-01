"""LIVE-Postgres: `conversa_agente_ativo` (migration 0017) e o adaptador CAS do roteador (ADR-0062).

Tier 2 (`@pytest.mark.integration`, L2). Aplica a cadeia REAL do alembic (`0001..head`) em DOIS
schemas descartaveis — um por tenant, como o `env.py` faz em producao — e exercita
`PostgresAgenteAtivoStore` com as MESMAS constantes de SQL do caminho de producao:

1. o DDL da 0017 aplica e os CHECKs de §5 recusam o que precisam recusar;
2. CAS CONCORRENTE: dois INSERTs simultaneos, exatamente um vence; dois UPDATEs com a mesma
   revisao esperada, exatamente um vence; N roteadores concorrentes na mesma conversa terminam
   com a revisao igual ao numero de escritas (ninguem se perde, ninguem duplica);
3. ISOLAMENTO DE TENANT: o tenant B nao le nem sobrescreve a linha do A — nem pela porta (o
   adaptador recusa antes do SQL), nem por baixo dela (o CHECK de prefixo recusa a linha
   forjada, e o schema do B nao enxerga a tabela do A);
4. a purga apaga so' o que passou da idade, no limite do lote, e so' do proprio tenant.

Pula ALTO (nunca erra, nunca finge) sem Postgres alcancavel:

    docker compose up -d postgres
    MAEZO_TEST_DATABASE_URL=postgresql://maezo:maezo@localhost:5433/maezo \\
      uv run pytest tests/integration/platform/test_conversa_agente_ativo_live_pg.py -v
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import-untyped]  # no py.typed upstream
import pytest

from maezo.gateway.audit_postgres import normalize_dsn
from maezo.platform.webhooks.whatsapp import pre_roteamento
from maezo.platform.webhooks.whatsapp.roteamento import (
    ConversaRouter,
    LinhaAgenteAtivo,
    PostgresAgenteAtivoStore,
    RoteamentoError,
)

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGORA = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _dsn() -> str:
    explicit = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", "5433")
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


async def _reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # qualquer falha de conexao e' "pular", nao "erro"
        return False
    await conn.close()
    return True


def _alembic_config(dsn: str, tenant_id: str) -> Any:
    from alembic.config import Config

    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "src" / "maezo" / "platform" / "migrations"))
    async_dsn = dsn if "+asyncpg" in dsn else dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    cfg.set_main_option("sqlalchemy.url", async_dsn)
    cfg.cmd_opts = argparse.Namespace(x=[f"tenant={tenant_id}"])
    return cfg


@pytest.fixture(scope="module")
def pg_dsn() -> str:
    dsn = _dsn()
    if not asyncio.run(_reachable(dsn)):
        pytest.skip(
            f"COULD NOT VERIFY: Postgres not reachable at {dsn!r} (override with "
            "MAEZO_TEST_DATABASE_URL) — see the module docstring for the bring-up command."
        )
    return dsn


@pytest.fixture(scope="module")
def tenants(pg_dsn: str) -> Iterator[tuple[str, str]]:
    """Dois tenants, cada um no seu schema, com a cadeia inteira aplicada."""
    from alembic import command

    sufixo = uuid.uuid4().hex[:10]
    a, b = f"rota{sufixo}", f"rotb{sufixo}"

    async def _ddl(sql: str) -> None:
        conn = await asyncpg.connect(normalize_dsn(pg_dsn))
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    for tenant in (a, b):
        asyncio.run(_ddl(f'CREATE SCHEMA IF NOT EXISTS "{tenant}"'))
        command.upgrade(_alembic_config(pg_dsn, tenant), "head")
    yield a, b
    for tenant in (a, b):
        asyncio.run(_ddl(f'DROP SCHEMA IF EXISTS "{tenant}" CASCADE'))


@pytest.fixture
async def stores(
    pg_dsn: str, tenants: tuple[str, str]
) -> AsyncIterator[tuple[PostgresAgenteAtivoStore, ...]]:
    a, b = tenants
    criados = (
        PostgresAgenteAtivoStore(dsn=pg_dsn, tenant=a),
        PostgresAgenteAtivoStore(dsn=pg_dsn, tenant=a),
        PostgresAgenteAtivoStore(dsn=pg_dsn, tenant=b),
    )
    yield criados
    for store in criados:
        await store.aclose()


def _conversa(tenant: str) -> str:
    return f"wa:{tenant}:hk1_{uuid.uuid4().hex}"


def _linha(
    conversa: str, *, agente: str = "helena", motivo: str = "inicio", quando: datetime = _AGORA
) -> LinhaAgenteAtivo:
    lucas = agente == "lucas"
    return LinhaAgenteAtivo(
        conversation_id=conversa,
        agente_ativo=agente,  # type: ignore[arg-type]
        revisao=0,
        transicao_motivo=motivo,  # type: ignore[arg-type]
        lucas_cobranca_subtipo="boleto_2via" if lucas else None,
        lucas_competencia="2026-09" if lucas else None,
        ativo_desde=quando,
        ultimo_turno_em=quando,
        expira_em=quando + timedelta(minutes=60),
    )


async def _sql(pg_dsn: str, schema: str, sql: str, *args: Any) -> Any:
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{schema}"')
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 1. DDL e CHECKs
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("coluna", "valor"),
    [
        ("agente_ativo", "andre"),
        ("transicao_motivo", "outro"),
        ("lucas_competencia", "2026-13"),
        ("conversation_id", "wa:{t}:sem_pseudonimo"),
    ],
)
async def test_checks_recusam_valor_fora_do_dominio(
    pg_dsn: str, tenants: tuple[str, str], coluna: str, valor: str
) -> None:
    a, _ = tenants
    base: dict[str, Any] = {
        "tenant": a,
        "conversation_id": _conversa(a),
        "agente_ativo": "lucas",
        "transicao_motivo": "handoff_cobranca",
        "lucas_competencia": "2026-09",
    }
    base[coluna] = valor.format(t=a)
    with pytest.raises(asyncpg.CheckViolationError):
        await _sql(
            pg_dsn,
            a,
            "INSERT INTO conversa_agente_ativo (tenant, conversation_id, agente_ativo, transicao_motivo, "
            "lucas_competencia, ativo_desde, ultimo_turno_em, expira_em) VALUES ($1,$2,$3,$4,$5,$6,$6,$7)",
            base["tenant"],
            base["conversation_id"],
            base["agente_ativo"],
            base["transicao_motivo"],
            base["lucas_competencia"],
            _AGORA,
            _AGORA + timedelta(minutes=60),
        )


async def test_check_recusa_colunas_do_lucas_com_a_helena_ativa_e_vencimento_invertido(
    stores: tuple[PostgresAgenteAtivoStore, ...], tenants: tuple[str, str]
) -> None:
    a, _ = tenants
    store = stores[0]
    linha = _linha(_conversa(a))
    with pytest.raises(asyncpg.CheckViolationError):
        await store.gravar(
            LinhaAgenteAtivo(**{**_campos(linha), "lucas_cobranca_subtipo": "vencimento"}),
            revisao_esperada=None,
        )
    with pytest.raises(asyncpg.CheckViolationError):
        await store.gravar(LinhaAgenteAtivo(**{**_campos(linha), "expira_em": _AGORA}), revisao_esperada=None)


def _campos(linha: LinhaAgenteAtivo) -> dict[str, Any]:
    import dataclasses

    return {f.name: getattr(linha, f.name) for f in dataclasses.fields(linha)}


# ---------------------------------------------------------------------------
# 2. CAS concorrente
# ---------------------------------------------------------------------------
async def test_dois_inserts_simultaneos_exatamente_um_vence(
    stores: tuple[PostgresAgenteAtivoStore, ...], tenants: tuple[str, str]
) -> None:
    a, _ = tenants
    conversa = _conversa(a)
    primeiro, segundo = stores[0], stores[1]
    venceu = await asyncio.gather(
        primeiro.gravar(_linha(conversa, motivo="inicio"), revisao_esperada=None),
        segundo.gravar(_linha(conversa, motivo="retorno_saude"), revisao_esperada=None),
    )
    assert sorted(venceu) == [False, True]
    linha = await primeiro.ler(conversa)
    assert linha is not None and linha.revisao == 0


async def test_dois_updates_com_a_mesma_revisao_exatamente_um_vence(
    stores: tuple[PostgresAgenteAtivoStore, ...], tenants: tuple[str, str]
) -> None:
    a, _ = tenants
    conversa = _conversa(a)
    assert await stores[0].gravar(_linha(conversa), revisao_esperada=None)
    venceu = await asyncio.gather(
        stores[0].gravar(_linha(conversa, motivo="retorno_saude"), revisao_esperada=0),
        stores[1].gravar(_linha(conversa, motivo="retorno_falha"), revisao_esperada=0),
    )
    assert sorted(venceu) == [False, True]
    linha = await stores[1].ler(conversa)
    assert linha is not None and linha.revisao == 1
    # o perdedor rele' e grava com a revisao nova
    assert await stores[1].gravar(_linha(conversa, motivo="retorno_falha"), revisao_esperada=1)
    linha = await stores[0].ler(conversa)
    assert linha is not None and (linha.revisao, linha.transicao_motivo) == (2, "retorno_falha")


async def test_n_roteadores_concorrentes_nenhuma_escrita_se_perde(
    stores: tuple[PostgresAgenteAtivoStore, ...], tenants: tuple[str, str]
) -> None:
    a, _ = tenants
    conversa = _conversa(a)
    lexicos = pre_roteamento.carregar()
    roteadores = [
        ConversaRouter(
            tenant_id=a,
            store=stores[i % 2],
            lexicos=lexicos,
            inatividade=timedelta(minutes=60),
            max_tentativas=20,
        )
        for i in range(6)
    ]
    await asyncio.gather(
        *(
            r.registrar_turno(conversation_id=conversa, sinais=None, resultado_helena={"intent": "greeting"})
            for r in roteadores
        )
    )
    linha = await stores[0].ler(conversa)
    assert linha is not None and linha.revisao == len(roteadores) - 1


# ---------------------------------------------------------------------------
# 3. Isolamento de tenant (L2)
# ---------------------------------------------------------------------------
async def test_tenant_b_nao_le_nem_sobrescreve_a_linha_do_tenant_a(
    pg_dsn: str, stores: tuple[PostgresAgenteAtivoStore, ...], tenants: tuple[str, str]
) -> None:
    a, b = tenants
    store_a, store_b = stores[0], stores[2]
    conversa_a = _conversa(a)
    assert await store_a.gravar(_linha(conversa_a), revisao_esperada=None)

    # pela porta: o adaptador do B recusa a conversa do A antes de qualquer SQL
    with pytest.raises(RoteamentoError):
        await store_b.ler(conversa_a)
    with pytest.raises(RoteamentoError):
        await store_b.gravar(_linha(conversa_a, motivo="retorno_falha"), revisao_esperada=0)

    # por baixo da porta: o schema do B nao tem a linha do A ...
    assert (
        await _sql(pg_dsn, b, "SELECT 1 FROM conversa_agente_ativo WHERE conversation_id = $1", conversa_a)
        == []
    )
    # ... e uma linha forjada no schema do A com o tenant B e a conversa do A e' recusada pelo CHECK
    with pytest.raises(asyncpg.CheckViolationError):
        await _sql(
            pg_dsn,
            a,
            "INSERT INTO conversa_agente_ativo (tenant, conversation_id, agente_ativo, transicao_motivo, "
            "ativo_desde, ultimo_turno_em, expira_em) VALUES ($1,$2,'helena','inicio',$3,$3,$4)",
            b,
            conversa_a,
            _AGORA,
            _AGORA + timedelta(minutes=60),
        )

    # a linha do A segue intacta
    linha = await store_a.ler(conversa_a)
    assert linha is not None and (linha.revisao, linha.transicao_motivo) == (0, "inicio")


# ---------------------------------------------------------------------------
# 4. Purga e PHI
# ---------------------------------------------------------------------------
async def test_purga_apaga_so_o_velho_no_limite_e_so_do_proprio_tenant(
    pg_dsn: str, stores: tuple[PostgresAgenteAtivoStore, ...], tenants: tuple[str, str]
) -> None:
    a, b = tenants
    store_a, store_b = stores[0], stores[2]
    velho = _AGORA - timedelta(days=40)
    velhas_a = [_conversa(a) for _ in range(3)]
    for conversa in velhas_a:
        assert await store_a.gravar(_linha(conversa, quando=velho), revisao_esperada=None)
    nova_a = _conversa(a)
    assert await store_a.gravar(_linha(nova_a), revisao_esperada=None)
    velha_b = _conversa(b)
    assert await store_b.gravar(_linha(velha_b, quando=velho), revisao_esperada=None)

    assert await store_a.purgar_antigas(antes_de=_AGORA - timedelta(days=30), limite=2) == 2
    assert await store_a.purgar_antigas(antes_de=_AGORA - timedelta(days=30), limite=2) == 1
    for conversa in velhas_a:
        assert await store_a.ler(conversa) is None
    assert await store_a.ler(nova_a) is not None
    assert await store_b.ler(velha_b) is not None


async def test_nenhuma_linha_carrega_telefone(pg_dsn: str, tenants: tuple[str, str]) -> None:
    a, _ = tenants
    linhas = await _sql(pg_dsn, a, "SELECT * FROM conversa_agente_ativo")
    for linha in linhas:
        for valor in dict(linha).values():
            assert "55119" not in str(valor)
