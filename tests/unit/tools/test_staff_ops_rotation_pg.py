"""Rotacao da designacao pelo `rows` num PostgreSQL REAL (>= 17), com o DDL de verdade das duas tabelas.

O DDL e o trecho de `staff-case-schema-postgres.sql` que cria `mzo_staff_case_designation_event`,
`..._current` e o trigger da trava; a designacao aqui e so o JSON que o instalador le
(`expected_previous_revision`): a assinatura e conferida em `verify_designation`, testada a parte.
Sem `MAEZO_TEST_DATABASE_URL` (superusuario de um servidor descartavel) = SKIP honesto.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from tools.staff_ops import rows
from tools.staff_ops.common import OpsError

pytestmark = pytest.mark.integration

_SQL = (
    Path(__file__).resolve().parents[3]
    / "src/maezo/portal/engine/java/src/main/resources/staff-case-schema-postgres.sql"
)
SCOPE = dict(tenant="amh", environment="dev", engine_name="default", database_incarnation="i:rot")


def _ddl() -> str:
    text = _SQL.read_text(encoding="utf-8")
    return text[
        text.index("CREATE TABLE mzo_staff_case_designation_event") : text.index(
            "CREATE TABLE mzo_staff_case_source_event"
        )
    ]


def _rows(revision: int, previous: int, salt: str = "") -> dict[str, Any]:
    raw = json.dumps(
        dict(designation_revision=str(revision), expected_previous_revision=str(previous), salt=salt)
    ).encode()
    return dict(
        scope=SCOPE,
        designation_revision=revision,
        designation=raw,
        designation_sha256=hashlib.sha256(raw).hexdigest(),
        proof=f'{{"proof":{revision}}}'.encode(),
    )


@pytest.fixture
def database() -> Any:
    url = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("MAEZO_TEST_DATABASE_URL ausente")
    import asyncpg  # type: ignore[import-untyped]

    schema = "rot_" + uuid.uuid4().hex[:10]
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")

    async def setup() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(f"CREATE SCHEMA {schema}; SET search_path={schema}; " + _ddl())
        finally:
            await conn.close()

    async def teardown() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                f"DROP SCHEMA {schema} CASCADE; DROP FUNCTION IF EXISTS mzo_staff_case_designation_lock"
            )
        finally:
            await conn.close()

    asyncio.run(setup())
    yield dsn, schema
    asyncio.run(teardown())


async def _apply(dsn: str, schema: str, value: dict[str, Any]) -> dict[str, str]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL search_path = {schema}")
            return await rows.install_designation(conn, value)
    finally:
        await conn.close()


async def _state(dsn: str, schema: str) -> tuple[list[tuple[int, str]], tuple[int, str]]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        events = await conn.fetch(
            "SELECT designation_revision, designation_digest FROM "
            f"{schema}.mzo_staff_case_designation_event ORDER BY 1"
        )
        current = await conn.fetchrow(
            "SELECT designation_revision, designation_digest FROM "
            f"{schema}.mzo_staff_case_designation_current"
        )
        return [(e[0], e[1]) for e in events], (current[0], current[1])
    finally:
        await conn.close()


def test_rotation_r1_to_r2_is_atomic_idempotent_and_keeps_history(database: Any) -> None:
    dsn, schema = database
    r1, r2 = _rows(1, 0), _rows(2, 1)
    first = asyncio.run(_apply(dsn, schema, r1))
    assert first == dict(designation_event="inserida", designation_current="inserida")
    assert asyncio.run(_apply(dsn, schema, r1)) == dict(
        designation_event="igual", designation_current="igual"
    )
    rotated = asyncio.run(_apply(dsn, schema, r2))
    assert rotated == dict(designation_event="inserida", designation_current="rotacionada r1->r2")
    assert asyncio.run(_apply(dsn, schema, r2)) == dict(
        designation_event="igual", designation_current="igual"
    )
    events, current = asyncio.run(_state(dsn, schema))
    assert events == [(1, r1["designation_sha256"]), (2, r2["designation_sha256"])]
    assert current == (2, r2["designation_sha256"])


@pytest.mark.parametrize(
    ("value", "match"),
    [
        (_rows(1, 0), "menor"),  # voltar para a r1
        (_rows(2, 1, salt="outra"), "corrente e outra"),  # mesma revisao, outro digest
        (_rows(3, 1), "expected_previous"),  # pula a corrente
    ],
)
def test_rotation_refusals_write_nothing(database: Any, value: dict[str, Any], match: str) -> None:
    dsn, schema = database
    asyncio.run(_apply(dsn, schema, _rows(1, 0)))
    asyncio.run(_apply(dsn, schema, _rows(2, 1)))
    before = asyncio.run(_state(dsn, schema))
    with pytest.raises(OpsError, match=match):
        asyncio.run(_apply(dsn, schema, value))
    assert asyncio.run(_state(dsn, schema)) == before


def test_concurrent_rotations_one_wins(database: Any) -> None:
    dsn, schema = database
    asyncio.run(_apply(dsn, schema, _rows(1, 0)))

    async def race() -> list[Any]:
        return await asyncio.gather(
            _apply(dsn, schema, _rows(2, 1, salt="a")),
            _apply(dsn, schema, _rows(2, 1, salt="b")),
            return_exceptions=True,
        )

    outcomes = asyncio.run(race())
    wins = [o for o in outcomes if isinstance(o, dict)]
    assert len(wins) == 1 and wins[0]["designation_current"] == "rotacionada r1->r2"
    assert [type(o) for o in outcomes if not isinstance(o, dict)] == [OpsError]
    events, current = asyncio.run(_state(dsn, schema))
    assert [e[0] for e in events] == [1, 2] and current[0] == 2
