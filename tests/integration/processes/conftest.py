"""Shared fixtures for `tests/integration/processes/` — real CIB Seven engine (ADR-0011).

T3.1 phase 1 (V2-COMPLETION-PLAN §3): ports the v1 donor's per-family process-integration
suites (`Maezo-Healthcare-Plan tests/integration/processes/*.py`, READ-ONLY) onto the v2 spine.
R2 verified the spine preserves v1's fixture surfaces EXACTLY for this purpose (design §16.1):
`WorkerHarness` ctor + `_handle`, `ExternalTask` fields, `register_<name>_workers(harness,
kafka=None, **seams)`, `FakeKafkaPublisher`, `_to_camunda_var`.

Port rule 3 (charter): hoist the per-file fixture-quartet duplication into this shared
conftest.py ONLY where the construction contract stays IDENTICAL across families. That holds
for exactly two things:

  1. The `engine` fixture (+ its `_engine_available()` probe + `CIBSEVEN_BASE_URL` env
     resolution) — every donor family file defines a byte-identical copy of this fixture, so
     it is hoisted here verbatim.
  2. `drain_topics()` — a helper (NOT a fixture; each family's own `<Family>EngineProbe.drain()`
     calls it) that cycles bounded fetch-and-lock + `harness._handle` until the family's worker
     topics idle out. This needs ONE real adaptation vs the donor: v2's
     `WorkerTransport.fetch_and_lock` signature is documented as v2-specific, NOT preserved from
     v1 (`maezo/tools/workers/harness.py` module docstring, design §16.2) — it takes
     `list[TopicSubscription]` (per-topic lock duration) and an `async_response_timeout_ms`
     long-poll kwarg, where v1's transport took a bare `list[str]` + a single `lock_duration_ms`.
     This is a mechanical fixture adaptation (port rule 1), not a logic/assertion change: the
     bounded-cycle drain semantics (fetch -> handle -> idle-counter early-exit) are preserved
     verbatim from the donor's per-family `drain()` methods.

Everything else — `deploy_artifacts`, the family-specific `<Family>EngineProbe` dataclass,
`start_<family>` — stays LOCAL to each family's test file (self-contained, matching the donor's
own convention: e.g. donor's `test_sp_op_cancel_001.py` docstring: "Este modulo SELF-CONTAINS
suas fixtures (NAO edita o conftest.py compartilhado)"), because the probe construction contract
(which workers/topics it wires) is NOT identical across families.

NOTE: this package sits under the existing `tests/integration/` whose own `conftest.py` already
defines a session-scoped autouse `_skip_if_engine_unreachable` gate against `ENGINE_REST_URL`
(cascades to this subpackage). This module's `engine` fixture is a SEPARATE, per-test skip using
the donor's own env var name, `CIBSEVEN_BASE_URL` (charter: "CIBSEVEN_BASE_URL env/port
conventions matching v2 compose") — both default to the identical
`http://localhost:8080/engine-rest` (`docker-compose.yml`'s `cibseven` service, host port 8080),
so in CI (no port remap) both checks agree. For a local run against a remapped port, export BOTH
`ENGINE_REST_URL` and `CIBSEVEN_BASE_URL` to the same remapped URL.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import pytest_asyncio

from maezo.tools.workers.harness import CibSevenWorkerTransport, TopicSubscription, WorkerHarness

from .engine_rest import EngineRest

pytestmark = pytest.mark.integration

#: Donor env var name, preserved (charter: "CIBSEVEN_BASE_URL env/port conventions matching v2
#: compose") — default matches `docker-compose.yml`'s `cibseven` service (host port 8080).
CIBSEVEN_BASE_URL = os.environ.get("CIBSEVEN_BASE_URL", "http://localhost:8080/engine-rest")


async def _engine_available(base_url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/version")
            return resp.status_code < 500
    except httpx.RequestError:
        return False


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[EngineRest]:
    if not await _engine_available(CIBSEVEN_BASE_URL):
        pytest.skip(f"CIB Seven indisponivel em {CIBSEVEN_BASE_URL} (suba com `make dev-stack`)")
    e = EngineRest(CIBSEVEN_BASE_URL)
    try:
        yield e
    finally:
        await e.aclose()


async def drain_topics(
    transport: CibSevenWorkerTransport,
    harness: WorkerHarness,
    worker_id: str,
    topics: list[str],
    *,
    rounds: int = 30,
    lock_duration_ms: int = 10_000,
    async_response_timeout_ms: int = 1_000,
    idle_limit: int = 3,
    settle_delay: float = 0.1,
) -> None:
    """Bounded fetch-and-lock + `harness._handle` drain cycle (real workers, real engine).

    Ported behavior from the donor's per-family `EngineProbe.drain()` (e.g.
    `test_sp_op_escalation_001.py` conftest, `test_sp_op_auth_001.py`, `test_sp_op_cancel_001.py`):
    cycle fetch-and-lock across `topics`, dispatch every fetched task through the SAME real
    `harness._handle` the production loop uses (`_handle` is a preserved v1 fixture surface,
    charter/design §16.1), and stop early once `idle_limit` consecutive empty rounds are seen.

    Adapted (not weakened) for v2's `WorkerTransport.fetch_and_lock` signature (module docstring
    above): wraps `topics` into `TopicSubscription` and long-polls each round for
    `async_response_timeout_ms` (so an idle round already waited, unlike the donor's fixed
    `lock_duration_ms` + explicit `asyncio.sleep(delay)` — v1's transport had no long-poll
    timeout). A short `settle_delay` still runs after a round that fetched tasks, mirroring the
    donor's unconditional inter-round pause so async engine-side continuations (e.g. a service
    task completing unlocks the next one) have time to land before the next fetch.
    """
    import asyncio  # kept local: this helper is the only asyncio.sleep call site

    subs = [TopicSubscription(t, lock_duration_ms) for t in topics]
    idle = 0
    for _ in range(rounds):
        tasks = await transport.fetch_and_lock(
            worker_id, subs, max_tasks=10, async_response_timeout_ms=async_response_timeout_ms
        )
        for task in tasks:
            await harness._handle(task)  # preserved v1 fixture surface (design §16.1)
        if tasks:
            idle = 0
            await asyncio.sleep(settle_delay)
        else:
            idle += 1
            if idle >= idle_limit:
                return


# ---------------------------------------------------------------------------------------------
# T3.3 A1/A2 shared helper (docs/design/T3.3-chaos-resilience.md — cross-process handoff /
# agent-own-start idempotency correctness suites, `test_t33_a1_*.py` / `test_t33_a2_*.py`).
# Hoisted here (port rule 3: identical contract across both consumers) rather than duplicated —
# a thin wrapper over the REAL `maezo.gateway.audit_postgres.verify_chain`/table read, never a
# reimplementation, mirroring `tests/integration/chaos/conftest.py`'s sibling helpers (that
# package's version is per-test-throwaway-schema; this one reads the SESSION-scoped
# `audit_tenant`/`audit_pg` schema this package's suites already share).
# ---------------------------------------------------------------------------------------------


@asynccontextmanager
async def _audit_schema_conn(dsn: str, tenant_id: str) -> AsyncIterator[Any]:
    """One asyncpg connection with `search_path` already set to the run's tenant schema.

    Hoisted out of `count_chain_rows` (FAB-SLA-RISK-NOTIFIED-SLICE4 repair) so the three readers
    below share ONE connect/`SET search_path`/close sequence and ONE local asyncpg import —
    kept local because asyncpg is an integration-lane dependency, exactly as before.
    """
    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    conn = await asyncpg.connect(normalize_dsn(dsn))
    try:
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        yield conn
    finally:
        await conn.close()


async def count_chain_rows(dsn: str, tenant_id: str) -> int:
    """Row count of `audit_chain` for `tenant_id` — used to assert an exact DELTA (e.g. "the
    re-delivered start added ZERO new chain rows") since `audit_tenant`'s schema is shared by
    every test in the session, not exclusive to one test."""
    async with _audit_schema_conn(dsn, tenant_id) as conn:
        count: int = await conn.fetchval("SELECT count(*) FROM audit_chain")
    return count


async def count_chain_rows_for_action(dsn: str, tenant_id: str, action: str) -> int:
    """Row count of `audit_chain` restricted to ONE `action` (the worker's external-task topic).

    Same DELTA discipline as `count_chain_rows` above, one notch narrower: `WorkerHarness
    ._build_audit_record` sets `action = task.topic`, so this counts the durable completions the
    harness wrote for a single topic. FAB-SLA-RISK-NOTIFIED-SLICE4 (repair): used to pin
    "exactly ONE completion of this topic happened in this window" on a real, durable record
    instead of on a process variable a worker echoed back.
    """
    async with _audit_schema_conn(dsn, tenant_id) as conn:
        count: int = await conn.fetchval("SELECT count(*) FROM audit_chain WHERE action = $1", action)
    return count


async def audit_rows_for_task_ids(
    dsn: str, tenant_id: str, task_ids: list[str]
) -> list[tuple[str, str, str]]:
    """`(agent_id, action, decision)` of the `audit_chain` rows the harness wrote FOR these
    external-task ids, ordered by chain insertion.

    The join is the harness's own exactly-once contract: `PostgresAuditSink.emit_once` claims
    `audit_emit_dedup(tenant, dedup_key)` and stores the resulting `record_hash` in the SAME
    transaction as the `audit_chain` insert, and the worker-completion dedup key is
    `f"{tenant}:{task_id}"` (`src/maezo/tools/workers/harness.py::_audit_dedup_key`). So a row
    returned here is bound to ONE external task of ONE process instance — the binding
    `audit_chain` itself cannot give (it has no `process_instance_id` column).

    A task id with no claim simply yields no row: the caller asserts the exact expected tuples,
    never a truthiness.
    """
    async with _audit_schema_conn(dsn, tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT c.agent_id, c.action, c.decision FROM audit_chain c "
            "JOIN audit_emit_dedup d ON d.record_hash = c.record_hash "
            "WHERE d.tenant = $1 AND d.dedup_key = ANY($2::text[]) ORDER BY c.timestamp",
            tenant_id,
            [f"{tenant_id}:{task_id}" for task_id in task_ids],
        )
    return [(str(r["agent_id"]), str(r["action"]), str(r["decision"])) for r in rows]
