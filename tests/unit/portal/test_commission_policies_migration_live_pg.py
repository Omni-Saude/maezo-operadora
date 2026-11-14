"""D-36 migration 0029 live: the DRAFT pin and the reversible round trip (dono, 25/10/2026).

Real Alembic chain over a scratch tenant schema on the compose Postgres — no simulated DDL,
no reconstructed state. Proofs:

- `upgrade head` applies 0029 on top of the whole linear chain;
- `status` is pinned to `'DRAFT'` at the DATABASE level: an INSERT with another state and an
  UPDATE flipping the state BOTH violate the CHECK — activation is never an UPDATE, it is a
  successor migration after the A8 Forma 1 ratification (the table cannot be talked into it);
- the kind/slot consistency CHECKs hold (a percentual draft cannot carry centavos);
- absent value slots store as NULL (structure without value is the shipped state);
- `downgrade 0028` drops the table (drafts carry no acquired right) and a second `upgrade
  head` restores it — the round trip the downgrade contract promises.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
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
            "MAEZO_PG_HOST_PORT) — commission D-36 migration live tests SKIPPED (visible, not "
            "silent). Start it with `docker compose --profile core up -d postgres` and re-run "
            "this file."
        )


async def _migrate(dsn: str, tenant: str, direction: str, revision: str) -> None:
    """The real CLI/env.py with the tenant schema; URL only in the child environment."""
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
        output, _ = await asyncio.wait_for(process.communicate(), 120)
        assert process.returncode == 0, output.decode(errors="replace")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


_DRAFT_COLUMNS = (
    "tenant, policy_ref, dimensao, dimensao_ref, ajuste_tipo, percentual, valor, "
    "faixa_min, faixa_max, moeda, vigencia_inicio, vigencia_fim, precedencia, revision, "
    "criado_por, criado_em, atualizado_por, atualizado_em"
)


async def test_draft_pin_e_ida_e_volta_reversivel() -> None:
    dsn = _default_test_dsn()
    tenant = "pf" + uuid.uuid4().hex[:20]
    admin = await asyncpg.connect(dsn)
    try:
        await admin.execute(f'CREATE SCHEMA "{tenant}"')
    finally:
        await admin.close()
    try:
        await _migrate(dsn, tenant, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f'SET search_path TO "{tenant}"')
            # Structure WITH a value and structure WITHOUT any value both store; the default
            # state of a row is exactly 'DRAFT' and no other state is expressible.
            await conn.execute(
                f"INSERT INTO commission_adjustment_policies ({_DRAFT_COLUMNS}) VALUES ("
                "$1,'cap-' || repeat('a', 32), 'produto', 'SKU-0001', 'percentual', -2.5, "
                "NULL, NULL, NULL, NULL, NULL, NULL, 1, 1, 'staff-1', now(), 'staff-1', now())",
                tenant,
            )
            await conn.execute(
                f"INSERT INTO commission_adjustment_policies ({_DRAFT_COLUMNS}) VALUES ("
                "$1,'cap-' || repeat('b', 32), 'corretor', 'CORRETOR-77', 'faixa', NULL, "
                "NULL, 500, 9000, 'BRL', NULL, NULL, 3, 1, 'staff-1', now(), 'staff-1', now())",
                tenant,
            )
            stored = await conn.fetch(
                "SELECT ajuste_tipo, percentual, valor, faixa_min, faixa_max, status FROM "
                "commission_adjustment_policies ORDER BY policy_ref"
            )
            assert [row["status"] for row in stored] == ["DRAFT", "DRAFT"]
            percentual_row = next(row for row in stored if row["ajuste_tipo"] == "percentual")
            assert percentual_row["percentual"] == Decimal("-2.5000")
            # The foreign slots of the declared kind stay NULL, even when the kind carries a value.
            assert percentual_row["valor"] is None
            assert percentual_row["faixa_min"] is None
            faixa_row = next(row for row in stored if row["ajuste_tipo"] == "faixa")
            assert faixa_row["percentual"] is None  # NULL slots stay NULL
            assert faixa_row["valor"] is None
            assert faixa_row["faixa_min"] == Decimal("500.00")
            assert faixa_row["faixa_max"] == Decimal("9000.00")

            # The PIN: 'ATIVO' is not expressible — INSERT and UPDATE both violate the CHECK.
            # Activation is a successor migration after A8 Forma 1, never a statement.
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    f"INSERT INTO commission_adjustment_policies ({_DRAFT_COLUMNS}, status) VALUES ("
                    "$1,'cap-' || repeat('c', 32), 'canal', 'CANAL-APP', 'percentual', NULL, "
                    "NULL, NULL, NULL, NULL, NULL, NULL, 1, 1, 'staff-1', now(), 'staff-1', now(), 'ATIVO')",
                    tenant,
                )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    "UPDATE commission_adjustment_policies SET status = 'ATIVO' WHERE tenant = $1",
                    tenant,
                )
            # Kind/slot structural consistency: a percentual draft cannot carry centavos.
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    f"INSERT INTO commission_adjustment_policies ({_DRAFT_COLUMNS}) VALUES ("
                    "$1,'cap-' || repeat('d', 32), 'produto', 'SKU-0002', 'percentual', 5, "
                    "1000, NULL, NULL, NULL, NULL, NULL, 1, 1, 'staff-1', now(), 'staff-1', now())",
                    tenant,
                )
        finally:
            await conn.close()
        await _migrate(dsn, tenant, "downgrade", "0028")
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f'SET search_path TO "{tenant}"')
            gone = await conn.fetchval("SELECT to_regclass('commission_adjustment_policies')")
            assert gone is None, "downgrade must drop the draft table (no acquired right)"
        finally:
            await conn.close()
        await _migrate(dsn, tenant, "upgrade", "head")
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f'SET search_path TO "{tenant}"')
            back = await conn.fetchval("SELECT to_regclass('commission_adjustment_policies')")
            assert back is not None, "the round trip must restore the draft table"
        finally:
            await conn.close()
    finally:
        admin = await asyncpg.connect(dsn)
        try:
            await admin.execute(f'DROP SCHEMA "{tenant}" CASCADE')
        finally:
            await admin.close()
