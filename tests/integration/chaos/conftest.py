"""Shared fixtures for `tests/integration/chaos/` — T3.3 W0 seam-level chaos harness.

Scope (docs/design/T3.3-chaos-resilience.md §3/§4): the suites under this package are the
BUILD-NOW, non-engine, seam-level fault-injection suites (B1a/B1b/C1-down). They need a REAL
Postgres with the audit-lane migrations 0001->0005 applied (same production bootstrap as
`tests/integration/conftest.py`'s `audit_pg`), but explicitly NOT a running CIB Seven engine —
fault injection happens at the Python seam (monkeypatched `PostgresAuditSink` methods /
`CibSevenTransport` call sites on a `FakeCibSevenTransport`), never a real container kill.

Two deliberate departures from the parent `tests/integration/conftest.py`:

  1. **The engine-reachability autouse skip is neutralized for this subpackage.** The parent
     conftest's `_skip_if_engine_unreachable` (session-scoped, autouse) would otherwise SKIP every
     test collected anywhere under `tests/integration/`, including here, whenever CIB Seven is not
     running — even though these suites never touch the engine. Redefining the SAME fixture name
     here overrides it (pytest fixture resolution: nearest conftest wins) with a documented no-op,
     so `tests/integration/chaos/` is runnable against PG alone.

  2. **A per-TEST throwaway tenant schema, not the session-scoped `audit_pg` tenant.** The parent
     `audit_pg`/`audit_tenant` fixtures share ONE tenant schema for the whole pytest session (by
     design, for the engine-correctness suites in `tests/integration/processes/`). Chaos suites
     deliberately inject crashes/faults that can leave partial/mutated rows mid-test — sharing a
     schema with any other suite (or between chaos tests themselves) would let one test's induced
     fault corrupt another's `verify_chain()` read. `chaos_tenant_schema` mirrors
     `tests/unit/gateway/test_audit_postgres.py`'s `tenant_schema` fixture instead: one fresh
     schema, real migrations 0001->0005 applied, per test function, dropped on teardown.

HARNESS CONTRACT for later builders (A1/A2/A3 engine-correctness; W2 container-control — see
docs/design/T3.3-chaos-resilience.md §4 Wave W2, a separate serialized NIGHTLY job, NOT this one):
  - `chaos_pg_dsn` (session): the reachable lane Postgres DSN; skips LOUDLY (never silently) if
    unreachable, exactly like the parent `audit_pg`'s posture (ADR-0011 "could-not-verify").
  - `chaos_tenant_schema` (function): one throwaway, migrated (0001->0005) tenant schema per test.
  - `chaos_sink` (function): a real `PostgresAuditSink` bound to `chaos_tenant_schema`, closed on
    teardown.
  - `dead_dsn` (function): a syntactically valid but UNREACHABLE Postgres DSN — the connect-refusal
    approximation of "PG is down" (design §2 Class-C note: CI-runnable, honestly labeled as the
    fail-closed-on-LOSS half, not a substitute for a true failover/recovery drill).
  - `assert_chain_valid` / `count_chain_rows` / `count_dedup_rows`: thin helpers over the REAL
    `maezo.gateway.audit_postgres.verify_chain` (never reimplemented — imported and wrapped) plus
    the two audit tables' row counts, shared by every suite in this package.
  - `tests/integration/chaos/mutations.py`: the MUTATION-CHECK scaffold — monkeypatch-installable
    "broken variant" functions, one per row of the design's §4 mutation table, gated behind the
    `MAEZO_CHAOS_MUTATE=<id>` environment variable so a companion test is SKIPPED (visible, not
    silently green) in normal runs and only exercised on demand — see that module's docstring.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.gateway.audit_postgres import (
    ChainVerificationResult,
    PostgresAuditSink,
    normalize_dsn,
    schema_for_tenant,
    verify_chain,
)
from tests.integration.conftest import _apply_migrations, _audit_pg_dsn, _pg_reachable

pytestmark = [pytest.mark.integration, pytest.mark.chaos]


@pytest.fixture(scope="session", autouse=True)
def _skip_if_engine_unreachable() -> None:
    """OVERRIDES `tests/integration/conftest.py`'s autouse engine-reachability skip.

    See module docstring point 1 — the chaos seam suites are PG-only by design (B1a/B1b/C1-down
    inject faults at the Python seam against a `FakeCibSevenTransport`, never a live engine), so
    a CIB Seven-unreachable environment must NOT skip them.
    """
    return None


@pytest.fixture(scope="session")
def chaos_pg_dsn() -> str:
    """The reachable lane Postgres DSN for the chaos package. Skips LOUDLY if unreachable
    (ADR-0011 could-not-verify posture — mirrors the parent `audit_pg` fixture exactly, just
    without requiring `engine_base_url` first since there is no engine gate here)."""
    dsn = _audit_pg_dsn()
    if not _pg_reachable(dsn):
        pytest.skip(
            f"COULD NOT VERIFY: lane Postgres unreachable at {dsn!r} (override with "
            "MAEZO_TEST_DATABASE_URL / MAEZO_PG_HOST_PORT). The chaos seam suites need a real "
            "Postgres with the audit-lane migrations: `docker compose --profile core up -d postgres`."
        )
    return dsn


@pytest.fixture
async def chaos_tenant_schema(chaos_pg_dsn: str) -> AsyncIterator[str]:
    """One fresh, REAL-migrated (0001->0005 via `_apply_migrations`, not a DDL mirror) tenant
    schema per test — see module docstring point 2 for why this is per-test, not session-scoped."""
    tenant_id = f"chaos{uuid.uuid4().hex[:16]}"  # schema_for_tenant: [a-z][a-z0-9_]*
    schema_for_tenant(tenant_id)  # fail fast (construction-time) if the generated id is ever unsafe

    conn = await asyncpg.connect(normalize_dsn(chaos_pg_dsn))
    try:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{tenant_id}"')
    finally:
        await conn.close()

    _apply_migrations(chaos_pg_dsn, tenant_id)

    yield tenant_id

    conn = await asyncpg.connect(normalize_dsn(chaos_pg_dsn))
    try:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{tenant_id}" CASCADE')
    finally:
        await conn.close()


@pytest.fixture
async def chaos_sink(chaos_pg_dsn: str, chaos_tenant_schema: str) -> AsyncIterator[PostgresAuditSink]:
    """A real `PostgresAuditSink` bound to `chaos_tenant_schema` — function-scoped (asyncpg pools
    bind to the event loop that first uses them; each pytest-asyncio test gets its own loop, same
    rationale as the parent `audit_sink` fixture)."""
    sink = PostgresAuditSink(chaos_pg_dsn, chaos_tenant_schema)
    try:
        yield sink
    finally:
        await sink.aclose()


#: A syntactically valid but UNREACHABLE Postgres DSN (port 1 — a privileged port no Postgres
#: ever binds) — the C1-down connect-refusal approximation of "the audit sink / PG is down"
#: (design §2 Class-C note; mirrors `tests/unit/gateway/test_audit_postgres.py`'s
#: `test_emit_fail_closed_on_unreachable_database`).
DEAD_PORT_DSN = "postgresql://maezo:maezo@localhost:1/maezo"


@pytest.fixture
def dead_dsn() -> str:
    return DEAD_PORT_DSN


async def assert_chain_valid(
    dsn: str, tenant_id: str, *, expected_records: int | None = None
) -> ChainVerificationResult:
    """Thin assertion wrapper over the REAL `maezo.gateway.audit_postgres.verify_chain` — reused,
    never reimplemented (T3.3 harness contract: every suite in this package must call THIS, not a
    home-grown chain walk)."""
    result = await verify_chain(dsn, tenant_id)
    assert result.valid, (
        f"chain invalid for tenant={tenant_id!r}: {result.reason} (break_at={result.break_at_hash})"
    )
    if expected_records is not None:
        assert result.total_records == expected_records, (
            f"tenant={tenant_id!r}: expected {expected_records} chain record(s), "
            f"found {result.total_records}"
        )
    return result


async def count_chain_rows(dsn: str, tenant_id: str) -> int:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        count: int = await conn.fetchval("SELECT count(*) FROM audit_chain")
    finally:
        await conn.close()
    return count


async def count_dedup_rows(dsn: str, tenant_id: str) -> int:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        count: int = await conn.fetchval("SELECT count(*) FROM audit_emit_dedup")
    finally:
        await conn.close()
    return count
