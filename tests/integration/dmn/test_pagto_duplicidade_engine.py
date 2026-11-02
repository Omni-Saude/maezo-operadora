"""Pacote PAGTO PR-E — `pagto_duplicidade` avaliada pelo MOTOR REAL (CIB Seven, ADR-0028), nunca por mock.

O teste de unidade (`tests/unit/spec/test_pagto_duplicidade_dmn.py`) prova a forma da tabela; ESTE
arquivo prova o que so' o motor responde: a precedencia FIRST, o teste unario `null` casando a
entrada presente e nula (sem ordem similar), os limites fechados da janela `[0..180]` / `> 180`, e
o catch-all fail-closed (nivel nao avaliavel nunca vira NENHUMA).

E um caso PONTA A PONTA quando o Postgres do job tambem esta' de pe: o detector REAL
(`detect_duplicidade`) com a razao REAL (migracao 0033 num schema descartavel) e a DMN REAL no motor
— duas ordens da mesma conta: a primeira sai NENHUMA, a segunda CONFIRMADA.

Os esperados sao DERIVADOS da tabela, nao de uma corrida anterior do motor: se o motor discordar, a
divergencia e' o achado. Sem motor alcancavel o pacote inteiro PULA com motivo explicito.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from maezo.tools.workers.dmn_transport import CibSevenDmnTransport, DmnVersion

pytestmark = pytest.mark.integration
_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
async def dmn(engine_base_url: str) -> AsyncIterator[CibSevenDmnTransport]:
    transport = CibSevenDmnTransport(engine_base_url, timeout=30.0)
    try:
        yield transport
    finally:
        await transport.close()


def _fatos(
    nivel1: Any = "SEM_OUTRA_ORDEM", nivel2: Any = "NAO_EXCEDE", avaliado: Any = True, idade: Any = None
) -> dict[str, Any]:
    return {
        "nivel1_situacao": nivel1,
        "nivel2_situacao": nivel2,
        "nivel3_avaliado": avaliado,
        "nivel3_similar_idade_dias": idade,
    }


@pytest.mark.parametrize(
    ("variaveis", "status", "motivo"),
    [
        (_fatos(), "NENHUMA", "SEM_INDICIO"),
        (_fatos(idade=181), "NENHUMA", "SEM_INDICIO"),
        (_fatos(idade=180), "SUSPEITA", "DUP_SIMILAR_NA_JANELA"),
        (_fatos(idade=0), "SUSPEITA", "DUP_SIMILAR_NA_JANELA"),
        (_fatos(nivel2="EXCEDE"), "SUSPEITA", "DUP_SOMA_EXCEDE_ORIGEM"),
        (_fatos(nivel1="REGISTRADA_OUTRA_ORDEM"), "CONFIRMADA", "DUP_OBRIGACAO_EM_CURSO"),
        # FIRST: nivel 1 liberada vence nivel 2 e nivel 3.
        (
            _fatos(nivel1="LIBERADA_OUTRA_ORDEM", nivel2="EXCEDE", idade=1),
            "CONFIRMADA",
            "DUP_OBRIGACAO_LIBERADA",
        ),
        # Fail-closed: nivel nao avaliavel nunca vira NENHUMA.
        (_fatos(nivel2="NAO_AVALIAVEL"), "INDETERMINADA", "NIVEL_NAO_AVALIADO"),
        (_fatos(nivel1="NAO_AVALIAVEL"), "INDETERMINADA", "NIVEL_NAO_AVALIADO"),
        (_fatos(avaliado=False), "INDETERMINADA", "NIVEL_NAO_AVALIADO"),
        (_fatos(idade=-1), "INDETERMINADA", "NIVEL_NAO_AVALIADO"),
        (_fatos(None, None, None, None), "INDETERMINADA", "NIVEL_NAO_AVALIADO"),
    ],
)
async def test_pagto_duplicidade_first_fail_closed_no_motor(
    dmn: CibSevenDmnTransport, variaveis: dict[str, Any], status: str, motivo: str
) -> None:
    rows, version = await dmn.evaluate("pagto_duplicidade", variaveis)
    assert isinstance(version, DmnVersion) and version.key == "pagto_duplicidade"
    assert rows, "FIRST sem linha casada — a tabela tem catch-all"
    assert (rows[0]["duplicidade_status"], rows[0]["duplicidade_motivo_codigo"]) == (status, motivo)


# --- ponta a ponta: detector real + razao real + DMN real -------------------------------------


def _pg_dsn() -> str:
    from tests.unit.a2a.test_outbox_live_pg import _default_test_dsn

    return _default_test_dsn()


async def _pg_alcancavel(dsn: str) -> bool:
    import asyncpg

    try:
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=2.0)
    except Exception:  # qualquer falha de conexao = pular com motivo, nunca erro
        return False
    await conn.close()
    return True


async def _migrar(dsn: str, tenant: str) -> None:
    env = dict(os.environ, ALEMBIC_DATABASE_URL=dsn)
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", "-x", f"tenant={tenant}", "upgrade", "head",
        cwd=_ROOT, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )  # fmt: skip
    output, _ = await asyncio.wait_for(process.communicate(), 300)
    assert process.returncode == 0, output.decode(errors="replace")


async def test_detector_real_razao_real_dmn_real(engine_base_url: str) -> None:
    import asyncpg

    from maezo.platform.pagto_obrigacao.ledger import PostgresRazaoObrigacoesPagamento
    from maezo.tools.workers.pagto_duplicidade import detect_duplicidade

    dsn = _pg_dsn()
    if not await _pg_alcancavel(dsn):
        pytest.skip(f"COULD NOT VERIFY: Postgres unreachable at {dsn!r} — ponta a ponta PR-E SKIPPED")
    tenant = "pe" + uuid.uuid4().hex[:20]
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'CREATE SCHEMA "{tenant}"')
    finally:
        await admin.close()
    try:
        await _migrar(dsn, tenant)
        razao = PostgresRazaoObrigacoesPagamento(dsn=dsn, tenant=tenant)
        ordem: dict[str, Any] = {
            "ordem_pagamento_id": "ORDEM-E2E-1",
            "lastro_origem": "contas_adjudicacao_humana",
            "numero_lote_tiss": "LOTE-E2E",
            "numero_guia_tiss": "GUIA-E2E",
            "prestador_id": "prov-e2e",
            "competencia": "2026-06",
            "valor_pagamento_cents": 40_000,
            "lastro_status": "CONFIRMADO",
            "lastro_origem_instancia_id": "pi-e2e",
            "lastro_valor_origem_cents": 40_000,
        }

        def _detectar(variaveis: dict[str, Any]) -> dict[str, Any]:
            # O worker e' sincrono e roda numa thread propria no harness (asyncio.to_thread).
            transporte = CibSevenDmnTransport(engine_base_url, timeout=30.0)
            try:
                return detect_duplicidade(variaveis, ledger=razao, dmn=transporte)
            finally:
                asyncio.run(transporte.close())

        primeira = await asyncio.to_thread(_detectar, dict(ordem))
        assert primeira["duplicidade_status"] == "NENHUMA"
        assert primeira["duplicidade_motivo_codigo"] == "SEM_INDICIO"
        segunda = await asyncio.to_thread(_detectar, {**ordem, "ordem_pagamento_id": "ORDEM-E2E-2"})
        assert segunda["duplicidade_status"] == "CONFIRMADA"
        assert segunda["duplicidade_motivo_codigo"] == "DUP_OBRIGACAO_EM_CURSO"
        assert segunda["duplicidade_ordem_conflitante_id"] == "ORDEM-E2E-1"
        assert segunda["duplicidade_nivel2_situacao"] == "EXCEDE"
    finally:
        admin = await asyncpg.connect(dsn)
        try:
            await admin.execute(f'DROP SCHEMA "{tenant}" CASCADE')
        finally:
            await admin.close()
