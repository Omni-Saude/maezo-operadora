"""ADR-0060 D3-D5 (T1.8): the staff membership witness reads the PINNED native schema.

Before T1.8 the witness filtered its relation pins by ``n.nspname='public'`` and read
``public.mzo_portal_read_membership`` by hand. With decision D-C2 the ``mzo_*`` relations
live in ``maezo_native``, so the schema became a pin of the ``portal-staff-material.v2``
manifest. This module proves, on a real PostgreSQL catalog and as a separate LOGIN witness
role, that:

* the pin accepts the relations of the pinned schema and refuses a ``public`` homonym, a
  prefix-sharing ``maezo_native_v2`` homonym (D5, exact comparison) and an absent schema;
* the membership read is qualified by the pinned schema (quoted identifier), so a row in a
  homonym is never read.

The witness qualifies every name, so ``pg_temp`` cannot capture it; the unqualified-name
``to_regclass`` rule (D4) is the Java store's and is proven by
``StaffCaseNativeSchemaEngineIT``.

Same convention as ``tests/unit/portal/test_staff_case_ddl_live_pg.py``: without
``MAEZO_TEST_DATABASE_URL`` the live cases are an honest SKIP ("COULD NOT VERIFY").
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from maezo.gateway.audit_postgres import normalize_dsn
from maezo.gateway.external_cases.models import Scope
from maezo.gateway.staff_cases.models import StaffCaseError
from maezo.gateway.staff_cases.postgres import NativeMembershipSource, RelationPin

SCHEMA = "maezo_native"
HOMONYMS = ("public", "maezo_native_v2")
TABLES = ("mzo_portal_read_membership", "mzo_human_principal")
SCOPE = Scope(tenant="tenant", environment="test", engine_name="engine", database_incarnation="inc")
ARGS = dict(tenant="tenant", environment="test", engine="engine", incarnation="inc", principal="principal")
_DDL = """
CREATE TABLE {s}.mzo_portal_read_membership(
  tenant_ text NOT NULL, environment_ text NOT NULL, engine_ text NOT NULL,
  incarnation_ text NOT NULL, principal_ text NOT NULL, payload_ text NOT NULL, source_ text NOT NULL);
CREATE TABLE {s}.mzo_human_principal(
  tenant_ text NOT NULL, principal_ text NOT NULL, issuer_ text, subject_ text, rev_ bigint,
  active_ boolean, valid_until_ bigint, groups_ text);
"""


class FakeEngine:
    dialect = SimpleNamespace(name="postgresql")
    echo = False
    sync_engine = SimpleNamespace(hide_parameters=True)


def pins(oid: int = 20) -> dict[str, RelationPin]:
    return {name: RelationPin(oid + i, "native_owner") for i, name in enumerate(TABLES)}


@pytest.mark.parametrize(
    "schema",
    [
        "Maezo_native",
        "maezo-native",
        "1maezo",
        "a" * 64,
        "",
        "maezo_native;drop",
        '"maezo_native"',
        "maezo.native",
        "maezo_native\n",
        None,
    ],
)
def test_witness_refuses_a_native_schema_outside_the_identifier_regex(schema) -> None:
    with pytest.raises(StaffCaseError):
        NativeMembershipSource(FakeEngine(), SCOPE, "witness_login", pins(), native_schema=schema)  # type: ignore[arg-type]


def test_witness_read_is_qualified_by_the_pinned_schema_only() -> None:
    source = NativeMembershipSource(FakeEngine(), SCOPE, "witness_login", pins(), native_schema=SCHEMA)  # type: ignore[arg-type]
    assert source.native_schema == SCHEMA
    assert '"maezo_native".mzo_portal_read_membership' in source.read_sql
    assert '"maezo_native".mzo_human_principal' in source.read_sql
    assert "public." not in source.read_sql


def _dsn() -> str:
    dsn = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("COULD NOT VERIFY: MAEZO_TEST_DATABASE_URL required for the native witness catalog")
    return normalize_dsn(dsn)


@dataclass(frozen=True)
class Catalog:
    url: str
    login: str
    pins: dict[str, dict[str, RelationPin]]


@asynccontextmanager
async def _catalog() -> AsyncIterator[Catalog]:
    """Throwaway database: same relation names and owner in three schemas, one LOGIN witness."""
    dsn = _dsn()
    token = uuid.uuid4().hex[:12]
    database, owner, witness, password = (
        f"t18w_{token}",
        f"t18w_owner_{token}",
        f"t18w_rt_{token}",
        uuid.uuid4().hex,
    )
    admin = await asyncpg.connect(dsn, timeout=5)
    try:
        await admin.execute(f'CREATE ROLE "{owner}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f"CREATE ROLE \"{witness}\" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{password}'")
        await admin.execute(f'CREATE DATABASE "{database}"')
        try:
            conn = await asyncpg.connect(dsn, database=database, timeout=5)
            try:
                found: dict[str, dict[str, RelationPin]] = {}
                for schema in (SCHEMA, *HOMONYMS):
                    if schema != "public":
                        await conn.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION "{owner}"')
                    await conn.execute(_DDL.format(s=f'"{schema}"'))
                    await conn.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{witness}"')
                    for name in TABLES:
                        await conn.execute(f'ALTER TABLE "{schema}".{name} OWNER TO "{owner}"')
                        await conn.execute(f'REVOKE ALL ON "{schema}".{name} FROM PUBLIC')
                        await conn.execute(f'GRANT SELECT ON "{schema}".{name} TO "{witness}"')
                    rows = await conn.fetch(
                        """SELECT c.relname, c.oid::bigint AS oid FROM pg_class c
                           JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=$1""",
                        schema,
                    )
                    found[schema] = {r["relname"]: RelationPin(int(r["oid"]), owner) for r in rows}
                    # The same principal exists in every schema; only the payload says where.
                    await conn.execute(
                        f'INSERT INTO "{schema}".mzo_portal_read_membership VALUES'
                        "('tenant','test','engine','inc','principal',$1,'{}')",
                        schema,
                    )
                    await conn.execute(
                        f'INSERT INTO "{schema}".mzo_human_principal(tenant_,principal_) VALUES'
                        "('tenant','principal')"
                    )
            finally:
                await conn.close()
            base = make_url(dsn.replace("postgresql://", "postgresql+asyncpg://", 1))
            url = base.set(username=witness, password=password, database=database).render_as_string(
                hide_password=False
            )
            yield Catalog(url=url, login=witness, pins=found)
        finally:
            await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    finally:
        await admin.execute(f'DROP ROLE IF EXISTS "{witness}"')
        await admin.execute(f'DROP ROLE IF EXISTS "{owner}"')
        await admin.close()


async def _run(schema: str, pinned: str) -> str:
    """Pin check then the qualified read, as the witness login; returns the payload read."""
    async with _catalog() as catalog:
        engine = create_async_engine(catalog.url, hide_parameters=True)
        try:
            source = NativeMembershipSource(
                engine, SCOPE, catalog.login, catalog.pins[pinned], native_schema=schema, seconds=5
            )
            async with engine.connect() as connection:
                await source.require_pins(connection)
                row = (await connection.execute(text(source.read_sql), ARGS)).mappings().one()
                return row["payload_"]
        finally:
            await engine.dispose()


@pytest.mark.integration
def test_pinned_schema_is_accepted_and_the_read_comes_from_it() -> None:
    assert asyncio.run(_run(SCHEMA, SCHEMA)) == SCHEMA


@pytest.mark.integration
@pytest.mark.parametrize("homonym", HOMONYMS)
def test_homonym_schema_does_not_satisfy_the_pinned_relations(homonym: str) -> None:
    # Pins are the maezo_native OIDs; a deployment that names another schema is refused.
    with pytest.raises(StaffCaseError):
        asyncio.run(_run(homonym, SCHEMA))


@pytest.mark.integration
def test_absent_schema_is_refused() -> None:
    with pytest.raises(StaffCaseError):
        asyncio.run(_run("maezo_nativ", SCHEMA))
