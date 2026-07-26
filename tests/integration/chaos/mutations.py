"""T3.3 W0 MUTATION-CHECK scaffold (docs/design/T3.3-chaos-resilience.md §4 verification protocol).

"A chaos test is real ONLY if it FAILS when the mechanism is broken." Each function below is a
monkeypatch-installable "broken variant" of a real fail-closed mechanism, reproducing exactly one
row of the design's §4 mutation table — WITHOUT ever editing `src/` (T3.3 constraint: this branch
touches only the chaos test package, the harness, this doc, and CI/pyproject wiring; no src
behavior changes, not even temporarily-reverted ones).

Usage pattern (see `test_crash_between_seams.py` / `test_sink_down_failclosed.py`): each mutation
has ONE green test (proves the real fail-closed invariant holds) and ONE companion "mutation-check"
test, gated by `mutation_active("<id>")` reading the `MAEZO_CHAOS_MUTATE` environment variable:

    @pytest.mark.skipif(not mutation_active("b1a"), reason="only runs when MAEZO_CHAOS_MUTATE=b1a")
    async def test_..._mutation_check_turns_red(...):
        ... apply mutations.broken_...(...) instead of the real code path ...
        ... re-assert the SAME invariant the green test proves ...

In normal CI/local runs (`MAEZO_CHAOS_MUTATE` unset) every mutation-check test is SKIPPED —
visible, never silently green. To PROVE a suite is non-vacuous, run it explicitly with the
mutation active, e.g.:

    MAEZO_CHAOS_MUTATE=b1a uv run pytest tests/integration/chaos/test_crash_between_seams.py -k mutation -q

That run is EXPECTED TO FAIL — the failure IS the proof the corresponding green test would have
caught the same defect in the real code. See scratchpad/t33-w0w1-report.md for the captured
red/green transcript for each of the three mutations this branch ships (b1a, b1b, c1_down).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from maezo.gateway.audit_postgres import (
    _ADVISORY_LOCK_SQL,
    _DEDUP_CLAIM_SQL,
    _DEDUP_LOOKUP_SQL,
    AuditPersistenceError,
    PostgresAuditSink,
)
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenTransport,
    ProcessInstance,
    build_start_audit_record,
    start_dedup_key,
)

if TYPE_CHECKING:
    from maezo.gateway.audit import AuditRecord


def mutation_active(mutation_id: str) -> bool:
    """True iff `MAEZO_CHAOS_MUTATE` is set to exactly `mutation_id` — the gate every
    mutation-check companion test in this package uses via `@pytest.mark.skipif`."""
    return os.environ.get("MAEZO_CHAOS_MUTATE") == mutation_id


# ---------------------------------------------------------------------------------------------
# B1a mutation: "move the chain-insert OUTSIDE the advisory-lock txn" (docs/design/
# T3.3-chaos-resilience.md §4 table, row B1a). The real `PostgresAuditSink.emit_once`
# (audit_postgres.py:295-397) runs the dedup-claim INSERT and the chain-link INSERT inside ONE
# `pg_advisory_xact_lock`-held transaction, so a crash between them rolls back BOTH atomically.
# This broken variant commits the claim in its OWN transaction, then attempts the chain-insert in
# a SEPARATE connection/transaction afterward — reproducing the mutation without editing
# audit_postgres.py. A test that then crash-injects (raises) inside `_insert_chain_row` sees the
# claim survive ALONE (an orphan claim — the GAP hazard the design's table predicts).
# ---------------------------------------------------------------------------------------------


async def broken_emit_once_chain_insert_outside_advisory_lock(
    sink: PostgresAuditSink, record: AuditRecord, *, dedup_key: str
) -> str:
    """B1a MUTATION double for `PostgresAuditSink.emit_once` — see module docstring."""
    pool = await sink._ensure_pool()  # noqa: SLF001 — mutation scaffold reaches into the real sink
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(_ADVISORY_LOCK_SQL, sink._tenant_id)  # noqa: SLF001

        prior_hash: str | None = await conn.fetchval(_DEDUP_LOOKUP_SQL, sink._tenant_id, dedup_key)  # noqa: SLF001
        if prior_hash is not None:
            return prior_hash

        tail = await sink._fetch_tail(conn)  # noqa: SLF001
        record.prev_hash = tail
        record.record_hash = record._compute_hash()  # noqa: SLF001

        claimed: str | None = await conn.fetchval(
            _DEDUP_CLAIM_SQL,
            sink._tenant_id,
            dedup_key,
            record.record_hash,  # noqa: SLF001
        )
        if claimed is None:
            raise AuditPersistenceError(
                f"audit_emit_dedup claim for tenant={sink._tenant_id!r} "  # noqa: SLF001
                f"dedup_key={dedup_key!r} collided — refusing to fork the chain"
            )
    # MUTATION: the `async with ... conn.transaction():` block above already COMMITTED the claim
    # HERE — the chain-insert below now runs in a brand-new connection/transaction, no longer
    # atomic with the claim. This is the exact defect row B1a's mutation describes.
    pool2 = await sink._ensure_pool()  # noqa: SLF001
    async with pool2.acquire() as conn2:
        await sink._insert_chain_row(conn2, record)  # noqa: SLF001 — crash-injection lands here in tests
    return record.record_hash


# ---------------------------------------------------------------------------------------------
# B1b mutation: "reorder to effect-BEFORE-emit" (design §4 table, row B1b/B1c). The real
# `start_process_idempotent` (transport.py:560-623) emits the durable ADR-0007 audit row BEFORE
# any engine effect (`find_active_instance`/`start_process_instance`). This broken variant
# reverses that order — reproducing the mutation without editing transport.py.
# ---------------------------------------------------------------------------------------------


async def broken_start_process_effect_before_emit(
    transport: CibSevenTransport,
    *,
    process_key: str,
    business_key: str,
    variables: dict[str, Any],
    audit_sink: AuditStartSink,
    provenance: AgentDecisionProvenance,
) -> ProcessInstance:
    """B1b/B1c MUTATION double for `start_process_idempotent` — see module docstring."""
    # MUTATION: the engine effect happens FIRST.
    existing = await transport.find_active_instance(business_key)
    if existing is not None:
        instance = existing
    else:
        instance = await transport.start_process_instance(process_key, business_key, variables)

    # The durable audit record is only built/emitted AFTER the effect — un-audited-start window.
    record = build_start_audit_record(
        provenance, process_key=process_key, business_key=business_key, variables=variables
    )
    await audit_sink.emit_once(
        record, dedup_key=start_dedup_key(provenance.tenant_id, process_key, business_key)
    )
    return instance


# ---------------------------------------------------------------------------------------------
# C1-down mutation: "make emit_once swallow AuditPersistenceError (fail-open)" (design §4 table,
# row C1-down). The real `emit_once`/`emit` NEVER swallow a DB failure (module docstring "FAIL-
# CLOSED": every path re-raises as `AuditPersistenceError`). This broken variant catches it and
# fabricates a success hash instead — the exact fail-open hazard fail-closed forbids.
# ---------------------------------------------------------------------------------------------

_FAIL_OPEN_FABRICATED_HASH = "0" * 64  # never a real record_hash — visibly synthetic in assertions


async def broken_emit_once_fail_open_swallow(
    sink: PostgresAuditSink, record: AuditRecord, *, dedup_key: str
) -> str:
    """C1-down MUTATION double: swallows `AuditPersistenceError` and fabricates success.

    Calls the REAL `PostgresAuditSink.emit_once` via the CLASS (`PostgresAuditSink.emit_once(sink,
    ...)`), never `sink.emit_once(...)` — a caller that installs this as an INSTANCE override of
    `sink.emit_once` (the intended usage: patch the instance so a harness picks up the broken
    variant) would otherwise recurse into itself through instance attribute lookup.
    """
    try:
        return await PostgresAuditSink.emit_once(sink, record, dedup_key=dedup_key)
    except AuditPersistenceError:
        return _FAIL_OPEN_FABRICATED_HASH
