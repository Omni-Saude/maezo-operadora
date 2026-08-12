"""LIVE-Postgres binders for the engine-path attacks — what the fake/model suites cannot prove.

Tier 2 (`@pytest.mark.integration`), placed HERE and NOT under `tests/integration/` for the same
reason `test_idempotency_store.py` / `test_outbox_live_pg.py` / `test_a2a_edge_live_pg.py` are: this
needs a REAL Postgres but explicitly NOT the CIB Seven BPMN engine, and `tests/integration/`'s
package-wide autouse `_skip_if_engine_unreachable` fixture would gate it on an engine it does not
use. SKIPS LOUDLY (never silently) when no server is reachable — deliberately not an `xfail`, which
would start FAILING the moment Postgres becomes reachable and the tests legitimately pass.

The two Half-A attacks whose defense is REAL Postgres semantics (not a Python model) bind here:

  1. **Cross-tenant replay — the REAL composite primary key.** Against the REAL migration-0003 DDL
     (`a2a_idempotency`, `PRIMARY KEY (task_id, tenant)`), two rows with the SAME `task_id` under
     DIFFERENT tenant values COEXIST — so tenant B's claim can never collide with tenant A's. The
     RED control is a hand-rolled tenant-BLIND table (`PRIMARY KEY (task_id)`) into which the second
     tenant's row COLLIDES (a `UniqueViolationError`), proving the tenant column is what carries the
     isolation. (Schema-per-tenant is the PRIMARY isolation, `idempotency.py` search_path; this
     binds the in-table defense-in-depth the composite PK adds, ADR-0039 §4.5.)

  2. **Crash-before-complete — the REAL claim/poll/seal SQL.** A row left 'processing' (the crash
     window) makes a FRESH `PostgresIdempotencyStore` instance's `claim_or_get` poll and time out to
     `None` — the re-execution path — against real SQL, not the model store's shortcut; and
     `complete`'s `WHERE status = 'processing'` guard means a second `complete` with a DIFFERENT
     result never overwrites the sealed one (idempotent seal).

The outbox's at-least-once lease-expiry redelivery already binds live in
`test_outbox_live_pg.py::test_an_unmarked_claim_is_reclaimed_after_its_lease_expires`; the
`test_duplicate_fact_delivery_attack.py` consumer contract rides on top of that and needs no second
live proof here.

R1 verifier: `docker compose --profile core up -d postgres` (or any local server via
`MAEZO_TEST_DATABASE_URL`) and re-run this file. The FULL CIB Seven live proof is a train-close
activity, not this leg's — do not start docker for it here.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.a2a import DelegationResult, PostgresIdempotencyStore, RejectionReason, StoredResult
from maezo.gateway.audit_postgres import normalize_dsn

pytestmark = pytest.mark.integration

_MIGRATION = (
    Path(__file__).resolve().parents[4] / "src/maezo/platform/migrations/versions/0003_a2a_idempotency.py"
)


def _a2a_idempotency_ddl() -> str:
    """The REAL `CREATE TABLE a2a_idempotency` from migration 0003 — extracted, never re-typed, so a
    schema change that broke the composite PK would break this proof (the point of a live binder)."""
    source = _MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    for block in re.findall(r'op\.execute\("""(.*?)"""\)', upgrade, re.DOTALL):
        if "CREATE TABLE IF NOT EXISTS a2a_idempotency" in block:
            return block
    raise AssertionError("non-vacuity: migration 0003 must define a2a_idempotency")


#: The RED control's tenant-BLIND table — the donor's single-column `PRIMARY KEY (task_id)` the
#: `idempotency.py` docstring warns against. Hand-rolled here ONLY so the collision it causes is
#: observable against a real server; it is never part of the real schema.
_TENANT_BLIND_DDL = """
    CREATE TABLE a2a_idempotency_tenant_blind (
        task_id text NOT NULL,
        tenant  text NOT NULL,
        status  text NOT NULL DEFAULT 'processing',
        PRIMARY KEY (task_id)
    )
"""


def _default_test_dsn() -> str:
    return os.environ.get("MAEZO_TEST_DATABASE_URL", "postgresql://maezo:maezo@localhost:5433/maezo")


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # noqa: BLE001 — any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture
def pg_dsn() -> str:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL) — A2A "
            "attack live binders SKIPPED (visible, not silent). Start it with "
            "`docker compose --profile core up -d postgres` and re-run this file."
        )
    return dsn


@pytest.fixture
async def tenant_schema(pg_dsn: str) -> AsyncIterator[str]:
    tenant_id = f"atk{uuid.uuid4().hex[:16]}"  # schema_for_tenant requires [a-z][a-z0-9_]*
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'CREATE SCHEMA "{tenant_id}"')
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        await conn.execute(_a2a_idempotency_ddl())
        await conn.execute(_TENANT_BLIND_DDL)
    finally:
        await conn.close()
    yield tenant_id
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'DROP SCHEMA "{tenant_id}" CASCADE')
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 1. Cross-tenant replay — the REAL composite primary key vs the tenant-blind RED
# ---------------------------------------------------------------------------


async def test_real_composite_pk_permits_two_tenants_same_task_id(pg_dsn: str, tenant_schema: str) -> None:
    """DEFENSE HOLDS, live: `PRIMARY KEY (task_id, tenant)` lets tenant A's and tenant B's rows for
    the SAME task_id COEXIST — no collision, so B's claim never lands on A's row."""
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        insert = (
            "INSERT INTO a2a_idempotency (task_id, tenant, task_type, origin, target) "
            "VALUES ($1, $2, 'authorization.analyze', 'helena', 'rafael')"
        )
        await conn.execute(insert, "task-xt-live-1", "tenant-a")
        await conn.execute(insert, "task-xt-live-1", "tenant-b")  # SAME task_id, other tenant
        rows = await conn.fetch(
            "SELECT tenant FROM a2a_idempotency WHERE task_id = $1 ORDER BY tenant", "task-xt-live-1"
        )
        assert [r["tenant"] for r in rows] == ["tenant-a", "tenant-b"]  # both persisted, distinct
    finally:
        await conn.close()


async def test_red_control_tenant_blind_pk_collides_on_the_second_tenant(
    pg_dsn: str, tenant_schema: str
) -> None:
    """RED CONTROL, live: with the tenant dropped from the PK, tenant B's row COLLIDES with tenant
    A's for the same task_id — a `UniqueViolationError`. This is the cross-tenant serve/leak the real
    composite PK prevents, made concrete against a real server."""
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_schema}"')
        insert = "INSERT INTO a2a_idempotency_tenant_blind (task_id, tenant) VALUES ($1, $2)"
        await conn.execute(insert, "task-xt-live-1", "tenant-a")
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await conn.execute(insert, "task-xt-live-1", "tenant-b")  # collides on PRIMARY KEY (task_id)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 2. Crash-before-complete — the REAL claim/poll/seal SQL
# ---------------------------------------------------------------------------


async def test_crash_before_complete_processing_row_makes_a_fresh_store_re_execute(
    pg_dsn: str, tenant_schema: str
) -> None:
    """DEFENSE/DISCLOSURE, live: a row left 'processing' (complete never ran — the crash window)
    makes a FRESH store instance's `claim_or_get` poll and time out to `None` against real SQL — the
    re-execution path leg-2 disclosed. Then `complete` seals it, and a subsequent claim replays."""
    crasher = PostgresIdempotencyStore(dsn=pg_dsn, tenant=tenant_schema)
    fresh = PostgresIdempotencyStore(
        dsn=pg_dsn, tenant=tenant_schema, poll_interval_s=0.01, poll_max_attempts=2
    )
    try:
        # (1) first execution claims the row, then "crashes" before complete -> row stays 'processing'.
        assert await crasher.claim_or_get(tenant=tenant_schema, task_id="task-crash-live-1") is None

        # (2) the retry (a fresh replica) sees 'processing', polls, times out -> None => RE-EXECUTE.
        assert await fresh.claim_or_get(tenant=tenant_schema, task_id="task-crash-live-1") is None

        # (3) the retry completes -> the row seals 'done'.
        await fresh.complete(
            tenant=tenant_schema,
            task_id="task-crash-live-1",
            result=DelegationResult.ok("task-crash-live-1", "process://RECURSO-crash-1"),
        )
        replay = await fresh.claim_or_get(tenant=tenant_schema, task_id="task-crash-live-1")
        assert replay == StoredResult(
            task_id="task-crash-live-1", success=True, output_ref="process://RECURSO-crash-1"
        )
    finally:
        await crasher.aclose()
        await fresh.aclose()


async def test_complete_is_idempotent_a_second_seal_never_overwrites_the_first(
    pg_dsn: str, tenant_schema: str
) -> None:
    """DEFENSE HOLDS, live: `complete`'s `WHERE status = 'processing'` guard means a SECOND
    `complete` with a DIFFERENT result (e.g. two replicas both racing to seal after a crash) can
    never overwrite the sealed row — exactly ONE coherent outcome survives."""
    store = PostgresIdempotencyStore(dsn=pg_dsn, tenant=tenant_schema)
    try:
        assert await store.claim_or_get(tenant=tenant_schema, task_id="task-seal-live-1") is None
        await store.complete(
            tenant=tenant_schema,
            task_id="task-seal-live-1",
            result=DelegationResult.ok("task-seal-live-1", "process://WINNER"),
        )
        # A second, LATER seal attempt with a different (rejection) result must be a no-op.
        await store.complete(
            tenant=tenant_schema,
            task_id="task-seal-live-1",
            result=DelegationResult.rejected("task-seal-live-1", RejectionReason.NO_HANDLER),
        )
        sealed = await store.claim_or_get(tenant=tenant_schema, task_id="task-seal-live-1")
        assert sealed == StoredResult(task_id="task-seal-live-1", success=True, output_ref="process://WINNER")
    finally:
        await store.aclose()
