"""LIVE-Postgres proof: durable LangGraph checkpoint persistence becomes REAL (T3.4/F4).

Tier 2 (`@pytest.mark.integration`), placed HERE (not under `tests/integration/`) deliberately —
mirrors `tests/unit/a2a/test_a2a_edge_live_pg.py`: this suite needs a REAL Postgres but explicitly
NOT the CIB Seven BPMN engine (the `tests/integration/` package's autouse engine-reachability
fixture would otherwise gate it on the engine). No Alembic migrations are applied: the checkpoint
tables are provisioned by the saver's own awaited `setup()` (upstream-owned schema — see
`platform/migrations/versions/0006_retire_dead_checkpoint_tables.py`), which is exactly what this
suite proves works.

What this proves that the mock-only unit suite cannot:
  1. `Checkpointer.connect_and_setup` opens a REAL `AsyncPostgresSaver` and its awaited `setup()` provisions
     the four upstream tables (`checkpoints`/`checkpoint_blobs`/`checkpoint_writes`/
     `checkpoint_migrations`) — verified compatible with langgraph-checkpoint 4.1.1 +
     langgraph-checkpoint-postgres 3.0.5.
  2. Round-trip: a graph run under a PHI-safe thread id writes checkpoint rows keyed by that id.
  3. RESTART-RESUME: after `aclose()` (simulated process death) a FRESH `connect_and_setup` on the
     same DB recovers the persisted state via `aget_state` — the durable-persistence claim, real.

Skips LOUDLY (never errors, never fakes) when no Postgres is reachable — see `_dsn` below for
why the default now points at the compose stack (gap LIVE-SUITES-SILENT-SKIP-AUDIT).
"""

from __future__ import annotations

import asyncio
import operator
import os
import uuid
from typing import Annotated, TypedDict

import pytest

from maezo.runtime.checkpoint import Checkpointer, checkpoint_thread_config

pytestmark = pytest.mark.integration


def _dsn() -> str:
    """`MAEZO_TEST_CHECKPOINT_DATABASE_URL` wins; otherwise the compose Postgres.

    Gap LIVE-SUITES-SILENT-SKIP-AUDIT (2026-09-04): the fallback used to be
    `postgresql://ckpt:ckpt@localhost:5658/ckpt` — "a FREE, dedicated Postgres (deliberately not
    the compose stack's 5432/5433)" whose role, database AND port nothing in this repo has ever
    created or published. Both of this file's tests therefore reported "COULD NOT VERIFY" in every
    environment that has ever run them. Now it mirrors
    `tests/integration/conftest.py::_audit_pg_dsn`: the local compose stack
    (`${MAEZO_PG_HOST_PORT:-5433}`) or a CI job that pins `MAEZO_PG_HOST_PORT=5432`. The
    upstream saver's own `setup()` provisions its four tables wherever it is pointed, and every
    test here deletes the thread it created, so a shared database costs nothing.
    """
    explicit = os.environ.get("MAEZO_TEST_CHECKPOINT_DATABASE_URL")
    if explicit:
        return explicit
    port = os.environ.get("MAEZO_PG_HOST_PORT", "5433")
    return f"postgresql://maezo:maezo@localhost:{port}/maezo"


async def _postgres_reachable(dsn: str) -> bool:
    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    try:
        conn = await asyncio.wait_for(asyncpg.connect(normalize_dsn(dsn)), timeout=2.0)
    except Exception:  # any connection failure means "skip", not "error"
        return False
    await conn.close()
    return True


@pytest.fixture(scope="module")
def pg_dsn() -> str:
    dsn = _dsn()
    if not asyncio.run(_postgres_reachable(dsn)):
        pytest.skip(
            f"COULD NOT VERIFY: Postgres not reachable at {dsn!r} (override with "
            "MAEZO_TEST_CHECKPOINT_DATABASE_URL / MAEZO_PG_HOST_PORT). Bring the project's own "
            "stack up with `docker compose --profile core up -d postgres` and re-run this file; "
            "this suite provisions the checkpoint tables via the saver's own awaited setup() — "
            "no migrations needed."
        )
    return dsn


class _St(TypedDict):
    steps: Annotated[list[str], operator.add]


def _build_counter_graph():  # type: ignore[no-untyped-def]
    from langgraph.graph import StateGraph

    graph = StateGraph(_St)

    async def n1(_s: _St) -> dict[str, list[str]]:
        return {"steps": ["A"]}

    async def n2(_s: _St) -> dict[str, list[str]]:
        return {"steps": ["B"]}

    graph.add_node("n1", n1)
    graph.add_node("n2", n2)
    graph.add_edge("__start__", "n1")
    graph.add_edge("n1", "n2")
    graph.add_edge("n2", "__end__")
    return graph


async def test_connect_and_setup_is_idempotent(pg_dsn: str) -> None:
    """Awaited `setup()` runs on every boot without error (checkpoint_migrations owns versioning)."""
    ck1 = await Checkpointer.connect_and_setup(pg_dsn)
    await ck1.aclose()
    ck2 = await Checkpointer.connect_and_setup(pg_dsn)  # second boot: idempotent
    await ck2.aclose()


async def test_checkpoint_round_trip_and_restart_resume(pg_dsn: str) -> None:
    """Run a graph under a PHI-safe thread id, then recover its state from a FRESH connection."""
    # PHI-safe thread id (KEYED conversation id convention; never a raw phone/CPF, never an unkeyed
    # hash). The `hk1_` marker is required by `assert_phi_safe_thread_id` (ADR-0035 extension).
    thread_id = f"wa:amh:hk1_{uuid.uuid4().hex}{uuid.uuid4().hex}"
    cfg = checkpoint_thread_config(thread_id)

    # RUN 1 — process instance #1 writes checkpoints.
    ck1 = await Checkpointer.connect_and_setup(pg_dsn)
    compiled1 = _build_counter_graph().compile(checkpointer=ck1.saver)
    result1 = await compiled1.ainvoke({"steps": []}, config=cfg)
    assert result1["steps"] == ["A", "B"]

    snap1 = await compiled1.aget_state(cfg)
    assert snap1.values["steps"] == ["A", "B"]
    await ck1.aclose()  # simulate process death — pool torn down

    # RUN 2 — a FRESH process instance on the SAME DB recovers the persisted state.
    ck2 = await Checkpointer.connect_and_setup(pg_dsn)
    try:
        compiled2 = _build_counter_graph().compile(checkpointer=ck2.saver)
        snap2 = await compiled2.aget_state(cfg)
        assert snap2.values["steps"] == ["A", "B"], "state did not survive restart"
        assert snap2.config["configurable"]["thread_id"] == thread_id
    finally:
        # Clean up this test's thread so re-runs stay isolated.
        await ck2.saver.adelete_thread(thread_id)  # type: ignore[union-attr]
        await ck2.aclose()
