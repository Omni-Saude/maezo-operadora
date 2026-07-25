"""B1a / B1b — seam-level crash-between-seams suites (T3.3 W1-seam-faults, BUILD-NOW 🟢).

Both suites fault-inject a synthetic exception at an exact seam (never a real process kill —
`tests/unit/gateway/test_audit_postgres.py::test_kill_test_emit_once_atomic_across_crash` already
proves the REAL-SIGKILL sibling of B1a) and assert the fail-closed invariant the design's Class B
table names still holds across the simulated crash + a retry:

  - **B1a** discharges the `audit_postgres.py:310` docstring's explicit promise ("Crash between
    claim and chain-insert -> both roll back together ... This is the property the kill-test
    proves.") via a deterministic seam patch on `PostgresAuditSink._insert_chain_row`, instead of
    a real SIGKILL.
  - **B1b** proves the `start_process_idempotent` emit-before-effect fence
    (`transport.py:595-615`): a crash landing between the durable audit emit and the engine
    start converges, on retry, to exactly ONE engine start and exactly ONE audit chain link.

Each suite ships a MUTATION-CHECK companion (skipped unless `MAEZO_CHAOS_MUTATE=<id>` is set —
see `mutations.py`'s docstring) that reproduces the design's §4 mutation for that suite and
re-asserts the SAME invariant, proving it is non-vacuous: that companion is EXPECTED TO FAIL when
actually run with the mutation active.
"""

from __future__ import annotations

import pytest

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import AuditPersistenceError, PostgresAuditSink
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    FakeCibSevenTransport,
    start_process_idempotent,
)

from . import mutations
from .conftest import assert_chain_valid, count_chain_rows, count_dedup_rows

pytestmark = [pytest.mark.integration, pytest.mark.chaos]


def _record(tenant_id: str, marker: str) -> AuditRecord:
    return AuditRecord(
        agent_id="chaos-crash-seams",
        tenant_id=tenant_id,
        agent_version="1.0.0",
        action="task_complete",
        decision="ALLOW",
        details={"marker": marker},
    )


def _provenance(tenant_id: str) -> AgentDecisionProvenance:
    return AgentDecisionProvenance(
        agent_id="chaos-b1b",
        agent_version="1.0.0",
        tenant_id=tenant_id,
        decision_basis={"route": "CANCEL"},
    )


# ---------------------------------------------------------------------------------------------
# B1a — crash between the dedup-claim and the chain-insert (audit_postgres.py:310 kill-test).
# ---------------------------------------------------------------------------------------------


async def test_b1a_crash_between_claim_and_chain_insert_atomic_rollback(
    chaos_pg_dsn: str,
    chaos_tenant_schema: str,
    chaos_sink: PostgresAuditSink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seam-fault sibling of the unit-level SIGKILL kill-test: raise INSIDE the advisory-locked
    transaction, between the dedup-claim INSERT and the chain-link INSERT, and prove BOTH roll
    back together — no dangling claim (GAP hazard), no orphan link (DUPLICATE hazard)."""
    key = f"{chaos_tenant_schema}:b1a-effect-1"
    real_insert = PostgresAuditSink._insert_chain_row
    calls = {"n": 0}

    async def _raise_once(self: PostgresAuditSink, conn: object, record: AuditRecord) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("SIMULATED CRASH — between dedup-claim and chain-insert (B1a)")
        await real_insert(self, conn, record)  # type: ignore[arg-type]

    monkeypatch.setattr(PostgresAuditSink, "_insert_chain_row", _raise_once)

    with pytest.raises(AuditPersistenceError):
        await chaos_sink.emit_once(_record(chaos_tenant_schema, "first-attempt"), dedup_key=key)

    # Atomic rollback: the simulated crash must leave NEITHER the claim NOR the chain link behind.
    assert await count_chain_rows(chaos_pg_dsn, chaos_tenant_schema) == 0, (
        "a chain link survived the simulated crash without its dedup claim (non-atomic)"
    )
    assert await count_dedup_rows(chaos_pg_dsn, chaos_tenant_schema) == 0, (
        "a dedup claim survived the simulated crash without its chain link (GAP hazard: a retry "
        "would be suppressed and the effect left unaudited)"
    )

    monkeypatch.undo()  # restore the real _insert_chain_row before the (real) re-delivery

    redelivered_hash = await chaos_sink.emit_once(
        _record(chaos_tenant_schema, "re-delivery"), dedup_key=key
    )

    assert await count_chain_rows(chaos_pg_dsn, chaos_tenant_schema) == 1, (
        "re-delivery after the crash wrote more (or fewer) than exactly one link"
    )
    assert await count_dedup_rows(chaos_pg_dsn, chaos_tenant_schema) == 1
    result = await assert_chain_valid(chaos_pg_dsn, chaos_tenant_schema, expected_records=1)
    assert result.verified_records == 1
    assert redelivered_hash  # the re-delivery emitted for real (not a stale dedup hit)


@pytest.mark.skipif(
    not mutations.mutation_active("b1a"),
    reason="mutation-check only runs when MAEZO_CHAOS_MUTATE=b1a "
    "(see docs/design/T3.3-chaos-resilience.md §4 / tests/integration/chaos/mutations.py)",
)
async def test_b1a_mutation_check_chain_insert_outside_lock_turns_suite_red(
    chaos_pg_dsn: str,
    chaos_tenant_schema: str,
    chaos_sink: PostgresAuditSink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION-CHECK: reproduces the design's B1a mutation ("move the chain-insert OUTSIDE the
    advisory-lock txn") via `mutations.broken_emit_once_chain_insert_outside_advisory_lock` — a
    monkeypatched stand-in; `src/` is never edited. Under the mutation, the SAME crash injection
    now leaves the claim COMMITTED (dedup rows == 1) with NO matching chain link (chain rows ==
    0) — an orphan claim (GAP hazard). This test is EXPECTED TO FAIL when actually run with
    `MAEZO_CHAOS_MUTATE=b1a` — that failure IS the proof the green test above is non-vacuous.
    """
    key = f"{chaos_tenant_schema}:b1a-mutation-effect"
    record = _record(chaos_tenant_schema, "mutation")
    real_insert = PostgresAuditSink._insert_chain_row
    calls = {"n": 0}

    async def _raise_once(self: PostgresAuditSink, conn: object, rec: AuditRecord) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("SIMULATED CRASH (mutation-check)")
        await real_insert(self, conn, rec)  # type: ignore[arg-type]

    monkeypatch.setattr(PostgresAuditSink, "_insert_chain_row", _raise_once)

    with pytest.raises(Exception):  # noqa: B017 - the broken variant's own (unwrapped) raise
        await mutations.broken_emit_once_chain_insert_outside_advisory_lock(
            chaos_sink, record, dedup_key=key
        )

    # Same invariant the green test proves — under the mutation this MUST fail (proving the
    # green test would have caught the real defect):
    assert await count_chain_rows(chaos_pg_dsn, chaos_tenant_schema) == 0
    assert await count_dedup_rows(chaos_pg_dsn, chaos_tenant_schema) == 0


# ---------------------------------------------------------------------------------------------
# B1b — crash between the durable audit emit and the engine start (emit-before-effect fence).
# ---------------------------------------------------------------------------------------------


async def test_b1b_crash_between_emit_and_engine_start_converges_to_one_start(
    chaos_pg_dsn: str,
    chaos_tenant_schema: str,
    chaos_sink: PostgresAuditSink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crash BETWEEN the durable audit emit (`start_process_idempotent` step 1) and the engine
    start (steps 2-3, mocked via `FakeCibSevenTransport`): emit-before-effect must hold — the
    durable row survives the crash, and a retry converges to exactly ONE engine start."""
    transport = FakeCibSevenTransport()
    provenance = _provenance(chaos_tenant_schema)
    business_key = f"CANCEL-{chaos_tenant_schema}-C-1"
    process_key = "SP-OP-CANCEL-001"
    variables = {"numero_contrato": "C-1"}

    real_find = FakeCibSevenTransport.find_active_instance
    real_start = FakeCibSevenTransport.start_process_instance
    find_calls = {"n": 0}
    start_calls = {"n": 0}

    async def _find_raise_once(self: FakeCibSevenTransport, business_key: str) -> object:
        find_calls["n"] += 1
        if find_calls["n"] == 1:
            raise RuntimeError("SIMULATED CRASH — after emit, before find_active_instance/start (B1b)")
        return await real_find(self, business_key)

    async def _start_counting(
        self: FakeCibSevenTransport, process_key: str, business_key: str, variables: dict[str, object]
    ) -> object:
        start_calls["n"] += 1
        return await real_start(self, process_key, business_key, variables)

    monkeypatch.setattr(FakeCibSevenTransport, "find_active_instance", _find_raise_once)
    monkeypatch.setattr(FakeCibSevenTransport, "start_process_instance", _start_counting)

    with pytest.raises(RuntimeError, match="SIMULATED CRASH"):
        await start_process_idempotent(
            transport,
            process_key=process_key,
            business_key=business_key,
            variables=variables,
            audit_sink=chaos_sink,
            provenance=provenance,
        )

    # The crash landed exactly where intended: before the engine was ever touched.
    assert find_calls["n"] == 1
    assert start_calls["n"] == 0, "the engine start was attempted despite the simulated crash"
    # But the durable audit row survived — emit-before-effect held even though the caller "died".
    await assert_chain_valid(chaos_pg_dsn, chaos_tenant_schema, expected_records=1)

    # Retry — SAME business_key/process_key/variables/provenance (re-delivery).
    instance = await start_process_idempotent(
        transport,
        process_key=process_key,
        business_key=business_key,
        variables=variables,
        audit_sink=chaos_sink,
        provenance=provenance,
    )

    assert start_calls["n"] == 1, "the retry did not start the engine exactly once"
    assert instance.already_existed is False  # the first REAL start (post-crash retry)
    # Still exactly ONE audit link — emit_once deduped the re-delivery, no double-audit.
    await assert_chain_valid(chaos_pg_dsn, chaos_tenant_schema, expected_records=1)

    # A THIRD call (a re-delivery of the NOW-started effect) must NOT double-start — the anti-dupla
    # / idempotency guarantee stays causally decisive across the crash, not vacuously true.
    instance_again = await start_process_idempotent(
        transport,
        process_key=process_key,
        business_key=business_key,
        variables=variables,
        audit_sink=chaos_sink,
        provenance=provenance,
    )
    assert start_calls["n"] == 1, "a THIRD delivery double-started the engine"
    assert instance_again.instance_id == instance.instance_id
    assert instance_again.already_existed is True


@pytest.mark.skipif(
    not mutations.mutation_active("b1b"),
    reason="mutation-check only runs when MAEZO_CHAOS_MUTATE=b1b "
    "(see docs/design/T3.3-chaos-resilience.md §4 / tests/integration/chaos/mutations.py)",
)
async def test_b1b_mutation_check_effect_before_emit_turns_suite_red(
    chaos_pg_dsn: str,
    chaos_tenant_schema: str,
    chaos_sink: PostgresAuditSink,
) -> None:
    """MUTATION-CHECK: reproduces the design's B1b/B1c mutation ("reorder to effect-BEFORE-
    emit") via `mutations.broken_start_process_effect_before_emit` — a monkeypatched stand-in for
    `start_process_idempotent`; `transport.py` is never edited. A crash injected right after the
    (now-first) engine start, before the (now-last) emit, must leave an UN-AUDITED start — which
    the "audit precedes every effect" assertion below correctly flags as a failure. This test is
    EXPECTED TO FAIL when actually run with `MAEZO_CHAOS_MUTATE=b1b`.
    """
    transport = FakeCibSevenTransport()
    provenance = _provenance(chaos_tenant_schema)
    business_key = f"CANCEL-{chaos_tenant_schema}-mutation"
    process_key = "SP-OP-CANCEL-001"
    variables: dict[str, object] = {"numero_contrato": "mutation"}

    async def _emit_raise_once(record: AuditRecord, *, dedup_key: str) -> str:
        raise RuntimeError("SIMULATED CRASH (mutation-check) — right before the (late) emit")

    # Patch the INSTANCE's emit_once (not the class) — chaos_sink is a real PostgresAuditSink and
    # this mutation-check only needs to crash the (now-late) emit call, not the whole sink.
    chaos_sink.emit_once = _emit_raise_once  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="SIMULATED CRASH"):
        await mutations.broken_start_process_effect_before_emit(
            transport,
            process_key=process_key,
            business_key=business_key,
            variables=variables,
            audit_sink=chaos_sink,
            provenance=provenance,
        )

    # Invariant the green suite enforces: an effect must NEVER exist without a preceding audit
    # row. Under the mutation this is expected to FAIL (proving non-vacuousness).
    active = await transport.find_active_instance(business_key)
    chain_records = await count_chain_rows(chaos_pg_dsn, chaos_tenant_schema)
    assert active is None or chain_records >= 1, (
        "an un-audited engine start occurred (mutation reproduced the effect-before-emit bug): "
        f"active_instance={active!r} chain_records={chain_records}"
    )
