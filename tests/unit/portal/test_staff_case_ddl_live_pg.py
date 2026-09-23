"""The staff DDL executed on a real PostgreSQL, and its grants read back against `StaffCaseStore`.

Onda 0 (S1, PG 17.11) proved two defects that no string search caught:

* I7 — three `FOREIGN KEY ... REFERENCES t` without a column list pointed at a table whose
  PRIMARY KEY has one column fewer, so `CREATE TABLE` fails with
  ``number of referencing and referenced columns for foreign key disagree``;
* I8 — the grant matrix of `mzo_staff_case_checkpoint_chunk` contradicted the store's pin
  (UPDATE required on every relation not classified immutable), so every staff call would be
  refused at `StaffCaseStore` construction. Resolved by least privilege: the store classifies
  `_chunk` as immutable (it only ever INSERTs chunks) and the DDL grants `SELECT,INSERT`.

This module installs `staff-case-schema-postgres.sql` + `staff-case-event-postgres.sql` in a
throwaway database, as a separate non-superuser installer, for a separate LOGIN runtime role,
and re-evaluates the store's own per-relation predicate. The expectation (owned set, immutable
suffixes, installed prefix) is parsed from `StaffCaseStore.java`, so the DDL has to obey the
store, never the other way round.

Schema: the DDL is unqualified and installs wherever the installer's `search_path` points. The
production schema is decision D-C2 (plan `portal-autoridade-nativa-dev.md`) and the store's
`nspname='public'` pin is T1.8; this test deliberately installs in a scratch schema and reads
the catalog by that schema, so it does not choose one.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.gateway.audit_postgres import normalize_dsn

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[3]
_ENGINE = _ROOT / "src/maezo/portal/engine/java/src/main"
_RESOURCES = _ENGINE / "resources"
_STORE = _ENGINE / "java/br/com/maezo/human/StaffCaseStore.java"
_DDL = ("staff-case-schema-postgres.sql", "staff-case-event-postgres.sql")
_SCHEMA = "staff_ddl_probe"


def _store_rule() -> tuple[frozenset[str], tuple[str, ...], str]:
    """(OWNED, immutable suffixes, installed prefix) exactly as `StaffCaseStore` declares them."""
    java = _STORE.read_text(encoding="utf-8")
    owned = re.search(r"OWNED=Set\.of\((.*?)\);", java, re.S)
    immutable = re.search(r"boolean immutable=(.*?);", java)
    installed = re.search(r'boolean installed=table\.startsWith\("([a-z_]+)"\);', java)
    assert owned and immutable and installed, "StaffCaseStore pin rule moved; update this fence"
    tables = frozenset(re.findall(r'"([a-z_]+)"', owned.group(1)))
    suffixes = tuple(re.findall(r'table\.endsWith\("([a-z_]+)"\)', immutable.group(1)))
    # The whole expression must be only endsWith(...) terms joined by ||: anything else would
    # be a rule this fence does not model.
    assert immutable.group(1).count("||") == len(suffixes) - 1 > 0
    assert len(tables) == 14
    return tables, suffixes, installed.group(1)


def _dsn() -> str:
    dsn = os.environ.get("MAEZO_TEST_DATABASE_URL")
    if not dsn:
        # Same convention as tests/unit/gateway/test_native_owner_store_pg.py: without a
        # disposable server this is an honest SKIP ("COULD NOT VERIFY"), never an error.
        pytest.skip("COULD NOT VERIFY: MAEZO_TEST_DATABASE_URL required to execute the staff DDL")
    return normalize_dsn(dsn)


async def _install(dsn: str, check) -> None:
    """Throwaway database + installer + runtime role; runs the DDL, then `check(conn, names)`."""
    token = uuid.uuid4().hex[:12]
    database, installer, runtime = f"staff_ddl_{token}", f"staff_inst_{token}", f"staff_rt_{token}"
    admin = await asyncpg.connect(dsn, timeout=5)
    try:
        assert await admin.fetchval("SELECT current_setting('server_version_num')::int") >= 160000
        await admin.execute(f'CREATE ROLE "{installer}" NOLOGIN')
        await admin.execute(f'GRANT "{installer}" TO CURRENT_USER')
        await admin.execute(
            f"CREATE ROLE \"{runtime}\" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{uuid.uuid4().hex}'"
        )
        await admin.execute(f'CREATE DATABASE "{database}"')
        try:
            conn = await asyncpg.connect(dsn, database=database, timeout=5)
            try:
                await conn.execute(f'CREATE SCHEMA "{_SCHEMA}" AUTHORIZATION "{installer}"')
                await conn.execute(f'SET ROLE "{installer}"')
                await conn.execute(f'SET search_path TO "{_SCHEMA}"')
                await conn.execute("SELECT set_config('maezo.staff_native_role', $1, false)", runtime)
                for name in _DDL:
                    await conn.execute((_RESOURCES / name).read_text(encoding="utf-8"))
                await check(conn, {"installer": installer, "runtime": runtime})
            finally:
                await conn.close()
        finally:
            await admin.execute(f'DROP DATABASE "{database}"')
    finally:
        await admin.execute(f'DROP ROLE IF EXISTS "{runtime}"')
        await admin.execute(f'DROP ROLE IF EXISTS "{installer}"')
        await admin.close()


def test_store_rule_is_parsed_from_the_store() -> None:
    tables, suffixes, installed = _store_rule()
    assert "mzo_staff_case_checkpoint_chunk" in tables
    assert installed == "mzo_staff_case_designation_"
    assert "_event" in suffixes and "_chunk" in suffixes


def test_staff_ddl_installs_and_grants_match_the_store_pin() -> None:
    dsn = _dsn()
    tables, suffixes, installed_prefix = _store_rule()

    async def check(conn, names) -> None:
        rows = await conn.fetch(
            """
            SELECT c.relname, c.relkind::text AS relkind, c.relrowsecurity, c.relforcerowsecurity,
              pg_get_userbyid(c.relowner) AS owner,
              pg_has_role($2::name, c.relowner, 'MEMBER') AS owner_member,
              has_table_privilege($2::name, c.oid, 'SELECT') AS can_read,
              has_table_privilege($2::name, c.oid, 'INSERT') AS ins,
              has_table_privilege($2::name, c.oid, 'UPDATE') AS upd,
              has_table_privilege($2::name, c.oid, 'DELETE') AS del,
              has_table_privilege($2::name, c.oid, 'TRUNCATE') AS trunc
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = $1 AND c.relname = ANY($3::text[])
            """,
            _SCHEMA,
            names["runtime"],
            sorted(tables),
        )
        found = {row["relname"]: row for row in rows}
        assert set(found) == tables
        mismatches = {}
        for table, row in sorted(found.items()):
            immutable = table.endswith(suffixes)
            installed = table.startswith(installed_prefix)
            expected = {
                "relkind": "r",
                "relrowsecurity": False,
                "relforcerowsecurity": False,
                "owner": names["installer"],
                "owner_member": False,
                "can_read": True,
                "ins": not installed,
                "upd": not installed and not immutable,
                "del": False,
                "trunc": False,
            }
            actual = {key: row[key] for key in expected}
            if actual != expected:
                mismatches[table] = {k: (actual[k], v) for k, v in expected.items() if actual[k] != v}
        assert mismatches == {}, f"(actual, required by StaffCaseStore): {mismatches}"

    asyncio.run(_install(dsn, check))


def test_current_rows_are_bound_to_the_exact_digest_of_their_version() -> None:
    """The three repaired FKs must reference the (key + digest) UNIQUE, not just exist."""
    dsn = _dsn()
    key = ("t", "dev", "default", "inc")
    digest, other = "a" * 64, "b" * 64

    async def check(conn, _names) -> None:
        # Still the installer (owner): only the FK is under test here, not the grant matrix.
        seed = (
            "INSERT INTO mzo_staff_case_designation_event VALUES($1,$2,$3,$4,1,$5,'{}','{}')",
            "INSERT INTO mzo_staff_case_source_event VALUES($1,$2,$3,$4,'src',1,'pub',$5,'{}')",
            "INSERT INTO mzo_staff_case_policy_version VALUES($1,$2,$3,$4,'pol',1,$5,'pub','{}')",
            "INSERT INTO mzo_staff_case_designation_current VALUES($1,$2,$3,$4,1,$5)",
            "INSERT INTO mzo_staff_case_policy_current VALUES($1,$2,$3,$4,'pol',1,$5)",
            "INSERT INTO mzo_staff_case_policy_dependency VALUES($1,$2,$3,$4,'g',1,'pol',1,$5)",
        )
        for statement in seed:
            await conn.execute(statement, *key, digest)
        # Same key and revision, a digest the version never had: each must be refused.
        refused = (
            "UPDATE mzo_staff_case_designation_current SET designation_digest=$2 WHERE tenant=$1",
            "UPDATE mzo_staff_case_policy_current SET head_digest=$2 WHERE tenant=$1",
            "UPDATE mzo_staff_case_policy_dependency SET head_digest=$2 WHERE tenant=$1",
        )
        for statement in refused:
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await conn.execute(statement, key[0], other)

    asyncio.run(_install(dsn, check))
