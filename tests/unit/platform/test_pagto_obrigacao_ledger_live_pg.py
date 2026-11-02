"""Pacote PAGTO PR-E — migracao 0033 e o store AO VIVO (Postgres real, nunca DDL simulado).

Cadeia alembic REAL sobre um schema de tenant descartavel no Postgres do compose/CI (molde
`test_legitimacy_screens_migration_live_pg.py`). Provas:

- `upgrade head` aplica a 0033; o store grava a `REGISTRADA` e devolve os indicios dos tres niveis
  (nivel 1 mesma obrigacao em outra ordem, nivel 2 soma contra a instancia de origem, nivel 3 a
  ordem similar com a idade contada pelo relogio do banco);
- IDEMPOTENCIA: a re-entrega da mesma ordem nao cria segunda linha e nao se ve como duplicata;
- `LIBERADA` vence `REGISTRADA` no nivel 1; nivel 3 ignora o mesmo lote e a mesma obrigacao;
- CONCORRENCIA: duas ordens da MESMA obrigacao gravando ao mesmo tempo — os locks transacionais
  serializam, e pelo menos uma ve a outra (nunca as duas passam limpas);
- APPEND-ONLY: UPDATE, DELETE e TRUNCATE direto no banco levantam a excecao do trigger; CHECKs
  fechados recusam evento/origem/chave fora do vocabulario;
- banco fora -> `RazaoIndisponivelError` (o detector a traduz em INDETERMINADA);
- `downgrade 0030` apaga tabela e funcao, e um segundo `upgrade head` as restaura.
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

from maezo.platform.pagto_obrigacao.ledger import (
    EntradaObrigacao,
    PostgresRazaoObrigacoesPagamento,
    RazaoIndisponivelError,
    chave_obrigacao,
)
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
            "MAEZO_PG_HOST_PORT) — PAGTO PR-E pagto_obrigacao_ledger live tests SKIPPED (visible, not "
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


def _entrada(tenant: str, **campos: Any) -> EntradaObrigacao:
    base: dict[str, Any] = {
        "tenant": tenant,
        "evento": "REGISTRADA",
        "origem": "contas",
        "chave_obrigacao": chave_obrigacao(tenant, "contas", ("LOTE-1", "prov-1", "GUIA-1")),
        "ordem_pagamento_id": "ORDEM-1",
        "prestador_id": "prov-1",
        "valor_cents": 30_000,
        "numero_lote_tiss": "LOTE-1",
        "competencia": "2026-06",
        "origem_instancia_id": "pi-origem-1",
    }
    base.update(campos)
    return EntradaObrigacao(**base)


async def _schema_novo(dsn: str) -> str:
    tenant = "po" + uuid.uuid4().hex[:20]
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


async def test_niveis_idempotencia_e_append_only_ao_vivo() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    try:
        store = PostgresRazaoObrigacoesPagamento(dsn=dsn, tenant=tenant)

        primeira = await store.registrar_e_ler(_entrada(tenant))
        assert primeira.gravou is True
        assert primeira.nivel1_situacao == "SEM_OUTRA_ORDEM"
        assert primeira.nivel2_soma_outras_cents == 0
        assert primeira.nivel3_avaliado is True and primeira.nivel3_similar_idade_dias is None

        # Re-entrega da MESMA ordem: nada novo, e ela nao se ve como duplicata.
        reentrega = await store.registrar_e_ler(_entrada(tenant))
        assert reentrega.gravou is False
        assert reentrega.nivel1_situacao == "SEM_OUTRA_ORDEM"

        # Nivel 1 + nivel 2: a mesma obrigacao em outra ordem, mesma instancia de origem.
        segunda = await store.registrar_e_ler(_entrada(tenant, ordem_pagamento_id="ORDEM-2"))
        assert (segunda.nivel1_situacao, segunda.nivel1_ordem_id) == ("REGISTRADA_OUTRA_ORDEM", "ORDEM-1")
        assert segunda.nivel2_soma_outras_cents == 30_000

        # LIBERADA da ORDEM-1 vence no nivel 1; liberar duas vezes e' idempotente.
        assert await store.registrar_liberacao(_entrada(tenant, evento="LIBERADA")) is True
        assert await store.registrar_liberacao(_entrada(tenant, evento="LIBERADA")) is False
        terceira = await store.registrar_e_ler(_entrada(tenant, ordem_pagamento_id="ORDEM-3"))
        assert (terceira.nivel1_situacao, terceira.nivel1_ordem_id) == ("LIBERADA_OUTRA_ORDEM", "ORDEM-1")
        # Nivel 2 soma UMA vez por ordem (a LIBERADA nao soma de novo): ORDEM-1 + ORDEM-2.
        assert terceira.nivel2_soma_outras_cents == 60_000

        # Mesmo lote (outra guia) NAO e' similar de nivel 3: todas as anteriores sao do LOTE-1.
        mesmo_lote = await store.registrar_e_ler(
            _entrada(
                tenant,
                ordem_pagamento_id="ORDEM-5",
                chave_obrigacao=chave_obrigacao(tenant, "contas", ("LOTE-1", "prov-1", "GUIA-7")),
                origem_instancia_id=None,
            )
        )
        assert mesmo_lote.nivel3_similar_idade_dias is None
        assert mesmo_lote.nivel2_soma_outras_cents is None
        # Nivel 3: outra obrigacao, mesmo prestador/competencia/valor, OUTRO lote -> similar, idade 0
        # (a mais recente: ORDEM-5).
        similar = await store.registrar_e_ler(
            _entrada(
                tenant,
                ordem_pagamento_id="ORDEM-4",
                numero_lote_tiss="LOTE-9",
                chave_obrigacao=chave_obrigacao(tenant, "contas", ("LOTE-9", "prov-1", "GUIA-1")),
                origem_instancia_id="pi-origem-9",
            )
        )
        assert similar.nivel1_situacao == "SEM_OUTRA_ORDEM"
        assert (similar.nivel3_similar_idade_dias, similar.nivel3_ordem_id) == (0, "ORDEM-5")
        # Valor diferente tambem nao e' similar.
        outro_valor = await store.registrar_e_ler(
            _entrada(
                tenant,
                ordem_pagamento_id="ORDEM-6",
                numero_lote_tiss="LOTE-8",
                valor_cents=30_001,
                chave_obrigacao=chave_obrigacao(tenant, "contas", ("LOTE-8", "prov-1", "GUIA-1")),
            )
        )
        assert outro_valor.nivel3_similar_idade_dias is None
        # Sem competencia, o nivel 3 nem e' avaliado.
        sem_comp = await store.registrar_e_ler(
            _entrada(
                tenant,
                ordem_pagamento_id="ORDEM-7",
                competencia=None,
                chave_obrigacao=chave_obrigacao(tenant, "outro", ("ORDEM-7",)),
                origem="outro",
            )
        )
        assert sem_comp.nivel3_avaliado is False

        raw = await asyncpg.connect(dsn)
        try:
            await raw.execute(f'SET search_path TO "{tenant}", public')
            linhas = await raw.fetchval("SELECT count(*) FROM pagto_obrigacao_ledger")
            assert linhas == 8  # 7 REGISTRADA + 1 LIBERADA
            for sql in (
                "UPDATE pagto_obrigacao_ledger SET valor_cents = 1",
                "DELETE FROM pagto_obrigacao_ledger",
                "TRUNCATE pagto_obrigacao_ledger",
            ):
                with pytest.raises(asyncpg.RaiseError, match="append-only"):
                    await raw.execute(sql)
            for coluna, valor in (
                ("evento", "CANCELADA"),
                ("origem", "fraude"),
                ("chave_obrigacao", "OBRIG-recurso-" + "a" * 64),  # prefixo != origem contas
            ):
                with pytest.raises(asyncpg.CheckViolationError):
                    await raw.execute(
                        "INSERT INTO pagto_obrigacao_ledger (tenant, evento, origem, chave_obrigacao, "
                        "ordem_pagamento_id, prestador_id, valor_cents, versao_detector) VALUES "
                        f"($1, {'$2' if coluna == 'evento' else repr('REGISTRADA')}, "
                        f"{'$2' if coluna == 'origem' else repr('contas')}, "
                        f"{'$2' if coluna == 'chave_obrigacao' else repr('OBRIG-contas-' + 'b' * 64)}, "
                        "'O-X', 'p', 1, 'v1')",
                        tenant,
                        valor,
                    )
            assert await raw.fetchval("SELECT count(*) FROM pagto_obrigacao_ledger") == 8
        finally:
            await raw.close()

        await _migrate(dsn, tenant, "downgrade", "0030")
        raw = await asyncpg.connect(dsn)
        try:
            existe = await raw.fetchval("SELECT to_regclass($1)", f'"{tenant}".pagto_obrigacao_ledger')
            assert existe is None
        finally:
            await raw.close()
        await _migrate(dsn, tenant, "upgrade", "head")
    finally:
        await _drop(dsn, tenant)


async def test_duas_ordens_concorrentes_da_mesma_obrigacao_nunca_passam_as_duas_limpas() -> None:
    dsn = _default_test_dsn()
    tenant = await _schema_novo(dsn)
    try:
        store = PostgresRazaoObrigacoesPagamento(dsn=dsn, tenant=tenant)
        for rodada in range(5):
            chave = chave_obrigacao(tenant, "outro", (f"OBRIG-R{rodada}",))
            a, b = await asyncio.gather(
                store.registrar_e_ler(
                    _entrada(tenant, origem="outro", chave_obrigacao=chave, ordem_pagamento_id=f"A{rodada}")
                ),
                store.registrar_e_ler(
                    _entrada(tenant, origem="outro", chave_obrigacao=chave, ordem_pagamento_id=f"B{rodada}")
                ),
            )
            situacoes = {a.nivel1_situacao, b.nivel1_situacao}
            assert "REGISTRADA_OUTRA_ORDEM" in situacoes, (rodada, a, b)
    finally:
        await _drop(dsn, tenant)


async def test_banco_fora_e_razao_indisponivel() -> None:
    store = PostgresRazaoObrigacoesPagamento(dsn="postgresql://maezo:maezo@127.0.0.1:1/maezo", tenant="amh")
    with pytest.raises(RazaoIndisponivelError):
        await store.registrar_e_ler(_entrada("amh"))
