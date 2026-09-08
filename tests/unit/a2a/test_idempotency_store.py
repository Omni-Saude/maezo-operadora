"""Unit tests for the DURABLE A2A idempotency store (R9, Guard 4) — WITHOUT a real Postgres.

Ported (v2-adapted) from the donor `Maezo-Healthcare-Plan` reference implementation's
`tests/unit/a2a/test_idempotency_store.py` as part of the T2.4 A2A W2 build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §2/§5. Mirrors `tests/unit/gateway/
test_audit_postgres.py`'s two-tier structure:

  - Tier 1 (always run, no DB): pure `DelegationResult` -> `result` jsonb mapping
    (`complete_params`/`_row_to_stored`/`_decode_result`); a FAKE asyncpg connection/pool that
    captures SQL+args and answers programmable `fetchval`/`fetchrow` — covers claim-won, replay,
    advisory-lock-before-insert ordering, `complete`'s SQL, and tenant/schema anti-injection
    validation.
  - Tier 2 (`@pytest.mark.integration`, REAL Postgres): claims a REAL row in a throwaway tenant
    schema against the ALREADY-MIGRATED `a2a_idempotency` table (0003_a2a_idempotency.py — see
    `idempotency.py`'s module docstring for why no new migration was added), proving the store
    actually round-trips through Postgres. SKIPS loudly (never silently) if
    `MAEZO_TEST_DATABASE_URL` / the docker-compose default is unreachable — there is no docker in
    this build environment, so this tier is expected to SKIP here; **the R1 verifier should bring
    up `docker compose --profile core up -d postgres` and re-run this file to exercise it for
    real** (mirrors the exact convention `test_audit_postgres.py` already established for the
    sibling audit sink — deliberately NOT an `xfail`: a strict xfail would itself start FAILING
    the moment Postgres becomes reachable and the test legitimately passes).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest

from maezo.a2a import DelegationResult, PostgresIdempotencyStore, RejectionReason, StoredResult
from maezo.a2a.idempotency import (
    CLAIM_SQL,
    COMPLETE_SQL,
    STATUS_DONE,
    _decode_result,
    _result_payload,
    _row_to_stored,
    complete_params,
)
from maezo.gateway.audit_postgres import normalize_dsn


class _FakeConn:
    """Fake asyncpg connection: captures `execute()`/`fetchval()`/`fetchrow()`; programmable replies."""

    def __init__(self, *, fetchval_result: Any = None, fetchrow_result: Any = None) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._fetchval_result = fetchval_result
        self._fetchrow_result = fetchrow_result

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.executed.append((sql, args))
        return self._fetchval_result

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.executed.append((sql, args))
        return self._fetchrow_result

    def transaction(self) -> Any:
        conn = self

        @asynccontextmanager
        async def _tx() -> AsyncIterator[_FakeConn]:
            yield conn

        return _tx()


class _FakePool:
    """Fake asyncpg pool: always hands out the same `_FakeConn` via `acquire()`."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.closed = False

    def acquire(self) -> Any:
        conn = self._conn

        @asynccontextmanager
        async def _acquire() -> AsyncIterator[_FakeConn]:
            yield conn

        return _acquire()

    async def close(self) -> None:
        self.closed = True


def _store(conn: _FakeConn) -> PostgresIdempotencyStore:
    return PostgresIdempotencyStore(dsn="postgresql://x", tenant="amh", pool=_FakePool(conn))  # type: ignore[arg-type]


# --- Pure mapping (no DB) -----------------------------------------------------------------------


def test_complete_params_success() -> None:
    result = DelegationResult.ok("t1", "process://RECURSO-amh-1")
    task_id, result_json = complete_params(result=result)
    assert task_id == "t1"
    assert json.loads(result_json) == {
        "success": True,
        "output_ref": "process://RECURSO-amh-1",
        "rejection_reason": None,
        "detail": None,
        "meta": {},
    }


def test_complete_params_success_persists_handler_meta() -> None:
    """Dossier A2A edges: the handler's bounded non-PHI summary tokens (`DelegationResult.meta`)
    ride in the existing `result` jsonb (additive key, no migration) so a durable REPLAY returns
    the SAME shape as the first delivery."""
    result = DelegationResult.ok("t1", "process://CRED-amh-P1", meta={"route": "human_review"})
    _task_id, result_json = complete_params(result=result)
    assert json.loads(result_json)["meta"] == {"route": "human_review"}


def test_complete_params_rejection() -> None:
    result = DelegationResult.rejected(
        "t2", RejectionReason.TASK_TYPE_NOT_ACCEPTED, detail="marina does not accept x"
    )
    task_id, result_json = complete_params(result=result)
    assert task_id == "t2"
    assert json.loads(result_json) == {
        "success": False,
        "output_ref": None,
        "rejection_reason": "task_type_not_accepted",
        "detail": "marina does not accept x",
        "meta": {},
    }


def test_result_payload_pure_mapping() -> None:
    stored = StoredResult(task_id="t1", success=True, output_ref="fhir://Task/1")
    assert _result_payload(stored) == {
        "success": True,
        "output_ref": "fhir://Task/1",
        "rejection_reason": None,
        "detail": None,
        "meta": {},
    }


def test_row_to_stored_decodes_meta_and_tolerates_its_absence() -> None:
    """Pre-existing rows (persisted before the `meta` key existed) decode to `{}`; a persisted
    meta round-trips with values coerced to str."""
    legacy = _row_to_stored("t1", {"result": json.dumps({"success": True, "output_ref": "x"})})
    assert legacy.meta == {}
    with_meta = _row_to_stored(
        "t2", {"result": json.dumps({"success": True, "output_ref": "x", "meta": {"route": "human_review"}})}
    )
    assert with_meta.meta == {"route": "human_review"}


def test_decode_result_accepts_dict_passthrough() -> None:
    assert _decode_result({"success": True}) == {"success": True}


def test_decode_result_parses_json_text() -> None:
    assert _decode_result('{"success": false, "detail": "x"}') == {"success": False, "detail": "x"}


def test_decode_result_none_is_empty_dict() -> None:
    assert _decode_result(None) == {}


def test_decode_result_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        _decode_result("[1, 2, 3]")


def test_row_to_stored_reconstructs_result() -> None:
    row = {"result": json.dumps({"success": True, "output_ref": "fhir://Task/x"})}
    stored = _row_to_stored("t1", row)
    assert stored == StoredResult(task_id="t1", success=True, output_ref="fhir://Task/x")


def test_row_to_stored_defaults_missing_success_to_false() -> None:
    row = {"result": json.dumps({})}
    stored = _row_to_stored("t1", row)
    assert stored.success is False


# --- Fake-connection SQL/behavior coverage -----------------------------------------------------


async def test_claim_won_returns_none() -> None:
    conn = _FakeConn(fetchval_result="t1")  # RETURNING task_id -> claim won
    store = _store(conn)
    result = await store.claim_or_get(tenant="amh", task_id="t1")
    assert result is None


async def test_claim_acquires_advisory_lock_before_insert() -> None:
    conn = _FakeConn(fetchval_result="t1")
    store = _store(conn)
    await store.claim_or_get(tenant="amh", task_id="t1")
    sqls = [sql for sql, _ in conn.executed]
    assert any("pg_advisory_xact_lock" in sql for sql in sqls)
    lock_index = next(i for i, sql in enumerate(sqls) if "pg_advisory_xact_lock" in sql)
    claim_index = next(i for i, sql in enumerate(sqls) if sql is CLAIM_SQL or sql == CLAIM_SQL)
    assert lock_index < claim_index


async def test_claim_sql_uses_unknown_placeholders_for_route_columns() -> None:
    conn = _FakeConn(fetchval_result="t1")
    store = _store(conn)
    await store.claim_or_get(tenant="amh", task_id="t1")
    claim_call = next(args for sql, args in conn.executed if sql == CLAIM_SQL)
    # (task_id, tenant, task_type, origin, target) — the route columns are placeholders at claim
    # time (the envelope has not been validated yet); `complete` never rewrites them.
    assert claim_call == ("t1", "amh", "unknown", "unknown", "unknown")


async def test_claim_conflict_with_done_row_returns_stored_result() -> None:
    done_row = {
        "status": STATUS_DONE,
        "result": json.dumps({"success": True, "output_ref": "fhir://Task/done"}),
    }
    conn = _FakeConn(fetchval_result=None, fetchrow_result=done_row)  # ON CONFLICT DO NOTHING -> no row
    store = _store(conn)
    result = await store.claim_or_get(tenant="amh", task_id="t1")
    assert result == StoredResult(task_id="t1", success=True, output_ref="fhir://Task/done")


async def test_claim_conflict_with_processing_row_polls_then_times_out() -> None:
    processing_row = {"status": "processing", "result": None}
    conn = _FakeConn(fetchval_result=None, fetchrow_result=processing_row)
    store = _store(conn)
    # Tiny poll budget so the test is fast.
    store._poll_interval_s = 0.001  # type: ignore[attr-defined]
    store._poll_max_attempts = 2  # type: ignore[attr-defined]
    result = await store.claim_or_get(tenant="amh", task_id="t1")
    assert result is None  # best-effort timeout — caller falls back to the normal path


async def test_complete_seals_row_with_result_json() -> None:
    conn = _FakeConn()
    store = _store(conn)
    result = DelegationResult.ok("t1", "fhir://Task/done")
    await store.complete(tenant="amh", task_id="t1", result=result)
    complete_call = next(args for sql, args in conn.executed if sql == COMPLETE_SQL)
    task_id, tenant, result_json = complete_call
    assert task_id == "t1"
    assert tenant == "amh"
    assert json.loads(result_json)["output_ref"] == "fhir://Task/done"


def test_tenant_validated_as_schema_identifier_anti_injection() -> None:
    with pytest.raises(ValueError, match="not a valid schema identifier"):
        PostgresIdempotencyStore(dsn="postgresql://x", tenant="amh; DROP TABLE a2a_idempotency;--")


def test_dsn_normalized_at_construction() -> None:
    store = PostgresIdempotencyStore(
        dsn="postgresql+asyncpg://maezo:maezo@localhost:5433/maezo", tenant="amh"
    )
    assert store._dsn == "postgresql://maezo:maezo@localhost:5433/maezo"  # type: ignore[attr-defined]


async def test_aclose_closes_owned_pool() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    store = PostgresIdempotencyStore(dsn="postgresql://x", tenant="amh", pool=pool)
    await store.aclose()
    assert pool.closed is True


# --- Tier 2: REAL Postgres (integration-marked; skips loudly if unreachable) --------------------

# Mirrors platform/migrations/versions/0003_a2a_idempotency.py's `a2a_idempotency` upgrade() DDL
# EXACTLY (duplicated here purely for test speed/isolation — same rationale as
# test_audit_postgres.py's own `_AUDIT_CHAIN_DDL`; the real migration is what the verifier's live
# run against docker-compose Postgres exercises end-to-end).
_A2A_IDEMPOTENCY_DDL = """
    CREATE TABLE a2a_idempotency (
        task_id          text NOT NULL,
        tenant           text NOT NULL,
        task_type        text NOT NULL,
        origin           text NOT NULL,
        target           text NOT NULL,
        status           text NOT NULL DEFAULT 'processing',
        result           jsonb,
        created_at       timestamptz NOT NULL DEFAULT now(),
        completed_at     timestamptz,

        PRIMARY KEY (task_id, tenant)
    )
"""


def _default_test_dsn() -> str:
    # Matches tests/unit/gateway/test_audit_postgres.py's own convention.
    return os.environ.get("MAEZO_TEST_DATABASE_URL", "postgresql://maezo:maezo@localhost:5433/maezo")


async def _postgres_reachable(dsn: str) -> bool:
    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture
def pg_dsn() -> str:
    dsn = _default_test_dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL) — "
            "PostgresIdempotencyStore DB-backed tests SKIPPED (visible, not silent). R1 verifier: "
            "start it with `docker compose --profile core up -d postgres` and re-run this file."
        )
    return dsn


async def _make_tenant_schema(dsn: str) -> str:
    tenant_id = f"kt{uuid.uuid4().hex[:16]}"  # schema_for_tenant requires [a-z][a-z0-9_]*
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'CREATE SCHEMA "{tenant_id}"')
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        await conn.execute(_A2A_IDEMPOTENCY_DDL)
    finally:
        await conn.close()
    return tenant_id


async def _drop_tenant_schema(dsn: str, tenant_id: str) -> None:
    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'DROP SCHEMA "{tenant_id}" CASCADE')
    finally:
        await conn.close()


@pytest.fixture
async def tenant_schema(pg_dsn: str) -> AsyncIterator[str]:
    tenant_id = await _make_tenant_schema(pg_dsn)
    yield tenant_id
    await _drop_tenant_schema(pg_dsn, tenant_id)


@pytest.mark.integration
async def test_claim_then_complete_round_trips_through_real_postgres(pg_dsn: str, tenant_schema: str) -> None:
    store = PostgresIdempotencyStore(dsn=pg_dsn, tenant=tenant_schema)
    try:
        first_claim = await store.claim_or_get(tenant=tenant_schema, task_id="t1")
        assert first_claim is None  # claim won — the caller "executes"

        await store.complete(
            tenant=tenant_schema,
            task_id="t1",
            result=DelegationResult.ok("t1", "fhir://Task/real-pg"),
        )

        replay = await store.claim_or_get(tenant=tenant_schema, task_id="t1")
        assert replay == StoredResult(task_id="t1", success=True, output_ref="fhir://Task/real-pg")
    finally:
        await store.aclose()


@pytest.mark.integration
async def test_concurrent_claim_across_stores_never_double_persists(pg_dsn: str, tenant_schema: str) -> None:
    """Two independent store instances (simulating two replicas) racing the SAME task_id.

    `claim_or_get`'s `None` return is best-effort (a timed-out poll also returns `None`, per the
    docstring) — the TRUE exactly-once guarantee is the PK + `complete`'s idempotent
    `WHERE status = 'processing'` (only the first `complete` for a task_id actually changes a
    row). This proves that belt-and-suspenders property directly: even if both replicas decide to
    "execute" and race to `complete` with DIFFERENT results, the row ends up sealed with exactly
    ONE of them — never a mix, never overwritten by the loser.
    """
    store_a = PostgresIdempotencyStore(dsn=pg_dsn, tenant=tenant_schema, poll_max_attempts=2)
    store_b = PostgresIdempotencyStore(dsn=pg_dsn, tenant=tenant_schema, poll_max_attempts=2)
    try:
        await asyncio.gather(
            store_a.claim_or_get(tenant=tenant_schema, task_id="concurrent-1"),
            store_b.claim_or_get(tenant=tenant_schema, task_id="concurrent-1"),
        )
        result_a = DelegationResult.ok("concurrent-1", "fhir://Task/from-a")
        result_b = DelegationResult.rejected("concurrent-1", RejectionReason.NO_HANDLER)
        await asyncio.gather(
            store_a.complete(tenant=tenant_schema, task_id="concurrent-1", result=result_a),
            store_b.complete(tenant=tenant_schema, task_id="concurrent-1", result=result_b),
        )
        sealed = await store_a.claim_or_get(tenant=tenant_schema, task_id="concurrent-1")
        assert sealed is not None
        # Whichever `complete` landed first, the row reflects exactly ONE coherent outcome.
        assert sealed in (
            StoredResult(task_id="concurrent-1", success=True, output_ref="fhir://Task/from-a"),
            StoredResult(
                task_id="concurrent-1",
                success=False,
                rejection_reason=str(RejectionReason.NO_HANDLER),
            ),
        )
    finally:
        await store_a.aclose()
        await store_b.aclose()
