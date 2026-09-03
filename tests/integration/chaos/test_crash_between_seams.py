"""B1a / B1b — seam-level crash-between-seams suites (T3.3 W1-seam-faults, BUILD-NOW 🟢).

Both suites fault-inject a synthetic exception at an exact seam (never a real process kill —
`tests/unit/gateway/test_audit_postgres.py::test_kill_test_emit_once_atomic_across_crash` already
proves the REAL-SIGKILL sibling of B1a) and assert the fail-closed invariant the design's Class B
table names still holds across the simulated crash + a retry:

  - **B1a** discharges the `audit_postgres.py:310` docstring's explicit promise ("Crash between
    claim and chain-insert -> both roll back together ... This is the property the kill-test
    proves.") via a deterministic seam patch on `PostgresAuditSink._insert_chain_row`, instead of
    a real SIGKILL.
  - **B1b** proves the `start_process_idempotent` emit-before-effect fence: a crash landing
    between the durable audit emit and the engine start leaves the durable row behind, never an
    un-audited effect. WHAT THE RETRY MAY THEN DO depends on the subject key's
    `StartDedupPosture` (GAP-D3-02 / DL-0046), so B1b is THREE tests plus a recovery drill —
    `NON_STRICT` converges to one start, `EXCLUSIVE`/`PERMANENT` wedge loudly, and the operator
    runbook step un-wedges. See the section header above those tests for the reasoning.

Each suite ships a MUTATION-CHECK companion (skipped unless `MAEZO_CHAOS_MUTATE=<id>` is set —
see `mutations.py`'s docstring) that reproduces the design's §4 mutation for that suite and
re-asserts the SAME invariant, proving it is non-vacuous: that companion is EXPECTED TO FAIL when
actually run with the mutation active. B1b ships TWO, because it asserts two independent claims:
`b1b` mutates the emit/effect ORDER, and `b1b_posture` (GAP-D3-02) mutates the POSTURE VERDICT —
the first is structurally blind to the second.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from maezo.gateway.audit import AuditRecord
from maezo.gateway.audit_postgres import AuditPersistenceError, PostgresAuditSink
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    FakeCibSevenTransport,
    ProcessInstance,
    StartClaimWithoutInstanceError,
    StartOutcome,
    start_dedup_key,
    start_process_idempotent,
)

from . import mutations
from .conftest import assert_chain_valid, count_chain_rows, count_dedup_rows, delete_dedup_row

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


def _provenance(tenant_id: str, *, route: str = "CANCEL") -> AgentDecisionProvenance:
    return AgentDecisionProvenance(
        agent_id="chaos-b1b",
        agent_version="1.0.0",
        tenant_id=tenant_id,
        decision_basis={"route": route},
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

    redelivered_hash = await chaos_sink.emit_once(_record(chaos_tenant_schema, "re-delivery"), dedup_key=key)

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
        await mutations.broken_emit_once_chain_insert_outside_advisory_lock(chaos_sink, record, dedup_key=key)

    # Same invariant the green test proves — under the mutation this MUST fail (proving the
    # green test would have caught the real defect):
    assert await count_chain_rows(chaos_pg_dsn, chaos_tenant_schema) == 0
    assert await count_dedup_rows(chaos_pg_dsn, chaos_tenant_schema) == 0


# ---------------------------------------------------------------------------------------------
# B1b — crash between the durable audit emit and the engine start (emit-before-effect fence).
#
# ONE SEAM, THREE CONTRACTS (GAP-D3-02 / DL-0046). The crash lands in the same place in every test
# below — after `emit_once` durably wrote the start CLAIM, before the engine was ever touched — so
# the emit-before-effect half of B1b is identical for all three. What a RE-DELIVERY is then
# allowed to do is decided by the subject key's `StartDedupPosture`, not by this suite:
#
#   * `NON_STRICT` (`SP-OP-NIP-001`) — the claim deduplicates the AUDIT ROW only; the effect is
#     gated by `find_active_instance` alone. The re-delivery re-attempts and CONVERGES TO EXACTLY
#     ONE ENGINE START. This is B1b's original contract, unchanged; only its subject key moved,
#     because the key it used to run on (`SP-OP-CANCEL-001`) no longer has this posture.
#   * `EXCLUSIVE` (`SP-OP-CANCEL-001`) and `PERMANENT` (`SP-OP-PAGTO-001`) — the claim also GATES
#     THE EFFECT. "A claim exists and the engine has no instance anywhere" is then exactly case 3
#     of `_resolve_strict_dedup_hit`: the state is produced BOTH by "the POST never took effect"
#     (this crash — retrying would be right) AND by "a racer holds the claim and its POST is still
#     in flight" (retrying would be a SECOND concurrent `UT_AnaliseRescisao`, or a second payment
#     release). Nothing durable separates the two, and this gate never reads absence of evidence
#     as permission — so the re-delivery WEDGES LOUDLY (`StartClaimWithoutInstanceError`): zero
#     engine starts, the claim left intact, the offending `dedup_key` named for an operator.
#
# WHY THESE ARE THREE TESTS AND NOT ONE TOLERANT TEST. "Either outcome is acceptable" would pass
# under both the old and the new policy and would therefore prove neither; and it would pass
# against a gate that had silently regressed to auto-restart, which is the duplicate-effect defect
# GAP-D3-02 exists to prevent. Each posture asserts its own exact terminal state.
#
# THE WEDGE IS A DESIGNED STATE, WITH A DOCUMENTED EXIT — `docs/runbooks/engine-processes.md`
# §5, "Chave travada: claim duravel sem instancia". The last test EXECUTES that runbook step
# and proves it re-arms the gate to exactly one start: a recovery procedure nobody ever runs is
# indistinguishable from a wedge with no recovery.
# ---------------------------------------------------------------------------------------------


class _B1bSeam:
    """One B1b subject: a process key wired to a `FakeCibSevenTransport` whose FIRST
    `find_active_instance` raises — the exact seam between the durable emit and the engine start.

    The counters are shared by every delivery of the same business key, so `start_calls["n"]` is
    the suite's load-bearing "how many engine starts did this key EVER cause" assertion (the
    Fake's `already_existed` is not: see the note at the end of the NON_STRICT test).
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        process_key: str,
        tenant_id: str,
        route: str,
        marker: str,
    ) -> None:
        self.transport = FakeCibSevenTransport()
        self.process_key = process_key
        self.tenant_id = tenant_id
        self.business_key = f"{route}-{tenant_id}-{marker}"
        self.variables: dict[str, Any] = {"caso_id": marker}
        self.provenance = _provenance(tenant_id, route=route)
        #: The row an operator is told to delete — recomputed by the SAME composer production
        #: uses, never hand-spelled, so a change to the key format cannot silently pass this suite.
        self.dedup_key = start_dedup_key(tenant_id, process_key, self.business_key)
        self.find_calls = {"n": 0}
        self.start_calls = {"n": 0}

        real_find = FakeCibSevenTransport.find_active_instance
        real_start = FakeCibSevenTransport.start_process_instance
        find_calls = self.find_calls
        start_calls = self.start_calls

        async def _find_raise_once(inner: FakeCibSevenTransport, business_key: str) -> ProcessInstance | None:
            find_calls["n"] += 1
            if find_calls["n"] == 1:
                raise RuntimeError("SIMULATED CRASH — after emit, before find_active_instance/start (B1b)")
            return await real_find(inner, business_key)

        async def _start_counting(
            inner: FakeCibSevenTransport,
            process_key: str,
            business_key: str,
            variables: dict[str, Any],
        ) -> ProcessInstance:
            start_calls["n"] += 1
            return await real_start(inner, process_key, business_key, variables)

        monkeypatch.setattr(FakeCibSevenTransport, "find_active_instance", _find_raise_once)
        monkeypatch.setattr(FakeCibSevenTransport, "start_process_instance", _start_counting)

    async def deliver(self, sink: PostgresAuditSink) -> ProcessInstance:
        """One delivery of THIS start through the real chokepoint. A re-delivery is simply this
        called again — same process key, business key, variables and provenance."""
        return await self.deliver_through(start_process_idempotent, sink)

    async def deliver_through(
        self, chokepoint: Callable[..., Awaitable[ProcessInstance]], sink: PostgresAuditSink
    ) -> ProcessInstance:
        """The same delivery, through an arbitrary chokepoint-shaped callable — the seam the
        mutation-check companion uses to swap in a broken variant from `mutations.py` while
        keeping the inputs, the counters and the crash injection byte-identical to the green
        tests. Green tests always go through `deliver`."""
        return await chokepoint(
            self.transport,
            process_key=self.process_key,
            business_key=self.business_key,
            variables=self.variables,
            audit_sink=sink,
            provenance=self.provenance,
        )


async def _crash_the_first_delivery(
    seam: _B1bSeam, chaos_sink: PostgresAuditSink, chaos_pg_dsn: str, chaos_tenant_schema: str
) -> None:
    """Run the first delivery into the injected crash and assert the seam's POSTURE-INDEPENDENT
    half: the crash landed before the engine was touched, and the durable claim + chain link
    survived it anyway (emit-before-effect held even though the caller "died")."""
    with pytest.raises(RuntimeError, match="SIMULATED CRASH"):
        await seam.deliver(chaos_sink)

    assert seam.find_calls["n"] == 1
    assert seam.start_calls["n"] == 0, "the engine start was attempted despite the simulated crash"
    await assert_chain_valid(chaos_pg_dsn, chaos_tenant_schema, expected_records=1)
    assert await count_dedup_rows(chaos_pg_dsn, chaos_tenant_schema) == 1


async def test_b1b_crash_between_emit_and_engine_start_converges_to_one_start(
    chaos_pg_dsn: str,
    chaos_tenant_schema: str,
    chaos_sink: PostgresAuditSink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NON_STRICT subject (`SP-OP-NIP-001`). Crash BETWEEN the durable audit emit
    (`start_process_idempotent` step 1) and the engine start (steps 3-4, mocked via
    `FakeCibSevenTransport`): emit-before-effect must hold — the durable row survives the crash,
    and because this key's claim gates the AUDIT ROW ONLY, the retry converges to exactly ONE
    engine start."""
    seam = _B1bSeam(
        monkeypatch,
        process_key="SP-OP-NIP-001",
        tenant_id=chaos_tenant_schema,
        route="NIP",
        marker="N-1",
    )
    await _crash_the_first_delivery(seam, chaos_sink, chaos_pg_dsn, chaos_tenant_schema)

    # Retry — SAME business_key/process_key/variables/provenance (re-delivery).
    instance = await seam.deliver(chaos_sink)

    assert seam.start_calls["n"] == 1, "the retry did not start the engine exactly once"
    assert instance.already_existed is False  # the first REAL start (post-crash retry)
    assert instance.start_outcome is StartOutcome.STARTED
    # Still exactly ONE audit link — emit_once deduped the re-delivery, no double-audit.
    await assert_chain_valid(chaos_pg_dsn, chaos_tenant_schema, expected_records=1)

    # A THIRD call (a re-delivery of the NOW-started effect) must NOT double-start — the anti-dupla
    # / idempotency guarantee stays causally decisive across the crash, not vacuously true.
    instance_again = await seam.deliver(chaos_sink)
    assert seam.start_calls["n"] == 1, "a THIRD delivery double-started the engine"
    assert instance_again.instance_id == instance.instance_id
    # NOTE: `FakeCibSevenTransport.find_active_instance` returns the stored `ProcessInstance`
    # object VERBATIM (unlike `CibSevenHttpTransport.find_active_instance`, which explicitly sets
    # `already_existed=True` on an idempotent hit) — so `already_existed` on the Fake's return
    # value is not a reliable idempotent-hit signal. `start_calls["n"] == 1` above (no second
    # engine start across 3 deliveries) is the load-bearing assertion for this invariant.


@pytest.mark.parametrize(
    ("process_key", "route", "posture"),
    [
        ("SP-OP-CANCEL-001", "CANCEL", "EXCLUSIVE"),
        ("SP-OP-PAGTO-001", "PAGTO", "PERMANENT"),
    ],
    ids=["exclusive-cancel", "permanent-pagto"],
)
async def test_b1b_crash_between_emit_and_engine_start_wedges_loudly_for_gated_postures(
    chaos_pg_dsn: str,
    chaos_tenant_schema: str,
    chaos_sink: PostgresAuditSink,
    monkeypatch: pytest.MonkeyPatch,
    process_key: str,
    route: str,
    posture: str,
) -> None:
    """GATED subjects (`EXCLUSIVE` = `SP-OP-CANCEL-001`, `PERMANENT` = `SP-OP-PAGTO-001`). The
    SAME crash, and the same emit-before-effect guarantee — but the re-delivery must NOT converge
    to a start. The claim gates the effect for these keys, so "claim exists, engine has no
    instance anywhere" is undecidable (`_resolve_strict_dedup_hit` case 3) and the gate refuses,
    loudly and repeatably, with ZERO engine starts and the claim left intact.

    BOTH gated postures are asserted by ONE parametrized body ON PURPOSE: `EXCLUSIVE` and
    `PERMANENT` diverge only on a PROVEN-FINISHED instance, and this seam never produces one (the
    instance was never created). Their identical behaviour here is a claim of the design
    (`_resolve_strict_dedup_hit` case 3: "RAISE — for BOTH gated postures, and for the same
    reason"), so a future edit that makes only one of them fall through fails this test.
    """
    seam = _B1bSeam(
        monkeypatch,
        process_key=process_key,
        tenant_id=chaos_tenant_schema,
        route=route,
        marker="caso-1",
    )
    await _crash_the_first_delivery(seam, chaos_sink, chaos_pg_dsn, chaos_tenant_schema)

    # Re-delivery — SAME inputs. Under a gated posture this is the loud wedge, not a start.
    with pytest.raises(StartClaimWithoutInstanceError) as wedged:
        await seam.deliver(chaos_sink)

    # OPERATOR-ACTIONABLE, not merely loud: the error names the key AND the single row to clear.
    assert wedged.value.process_key == process_key
    assert wedged.value.business_key == seam.business_key
    assert wedged.value.dedup_key == seam.dedup_key
    assert seam.start_calls["n"] == 0, (
        f"{posture}: the re-delivery started an engine instance behind an unresolved claim"
    )
    # Nothing was consumed or repaired by the refusal: the claim is intact (so a racer's in-flight
    # POST is still excluded) and the chain gained no second link.
    assert await count_dedup_rows(chaos_pg_dsn, chaos_tenant_schema) == 1
    await assert_chain_valid(chaos_pg_dsn, chaos_tenant_schema, expected_records=1)

    # A THIRD delivery wedges IDENTICALLY. The gate must not "give up" into a start after N
    # retries — every re-delivery lands here until a human resolves it, which is the point.
    with pytest.raises(StartClaimWithoutInstanceError):
        await seam.deliver(chaos_sink)
    assert seam.start_calls["n"] == 0, f"{posture}: a THIRD delivery broke through the wedge"
    assert await count_dedup_rows(chaos_pg_dsn, chaos_tenant_schema) == 1
    await assert_chain_valid(chaos_pg_dsn, chaos_tenant_schema, expected_records=1)


@pytest.mark.skipif(
    not mutations.mutation_active("b1b_posture"),
    reason="mutation-check only runs when MAEZO_CHAOS_MUTATE=b1b_posture "
    "(see docs/design/T3.3-chaos-resilience.md §4 / tests/integration/chaos/mutations.py)",
)
async def test_b1b_posture_mutation_check_restart_on_missing_instance_turns_suite_red(
    chaos_pg_dsn: str,
    chaos_tenant_schema: str,
    chaos_sink: PostgresAuditSink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION-CHECK for the POSTURE SPLIT: reproduces "a gated key whose claim has no instance
    anywhere is reclaimable" via `mutations.broken_start_process_restart_on_claim_without_instance`
    — case 3 of `_resolve_strict_dedup_hit` auto-restarting instead of raising. `src/` is never
    edited. EXPECTED TO FAIL when run with `MAEZO_CHAOS_MUTATE=b1b_posture`.

    WHY THIS EXISTS SEPARATELY FROM `b1b`. The pre-existing `b1b` mutation reorders emit and
    effect, so it can only ever prove the audit-ORDERING invariant; it is structurally blind to
    WHICH POSTURES may restart after a crash, which is the whole claim the two gated tests above
    make. Without this companion those tests would assert a contract with no mutation behind it —
    exactly the vacuity this package's convention exists to forbid.

    The invariant re-asserted below is the SAME one the green gated test asserts: ZERO engine
    starts behind an unresolved claim. Under the mutation a second start occurs, which is the
    duplicate irreversible effect (a second concurrent `UT_AnaliseRescisao`) the `EXCLUSIVE`
    posture exists to prevent.
    """
    seam = _B1bSeam(
        monkeypatch,
        process_key="SP-OP-CANCEL-001",
        tenant_id=chaos_tenant_schema,
        route="CANCEL",
        marker="posture-mutation",
    )
    await _crash_the_first_delivery(seam, chaos_sink, chaos_pg_dsn, chaos_tenant_schema)

    # Re-delivery through the BROKEN variant instead of the real chokepoint.
    await seam.deliver_through(mutations.broken_start_process_restart_on_claim_without_instance, chaos_sink)

    assert seam.start_calls["n"] == 0, (
        "the gate restarted a GATED key whose durable claim had no engine instance behind it "
        "(mutation reproduced the absence-of-evidence-as-permission bug): a concurrent racer's "
        "in-flight POST would now have a SECOND instance racing it"
    )


async def test_b1b_operator_rearm_after_the_gated_wedge_starts_exactly_once(
    chaos_pg_dsn: str,
    chaos_tenant_schema: str,
    chaos_sink: PostgresAuditSink,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RECOVERY DRILL for the wedge above, on the `EXCLUSIVE` subject `SP-OP-CANCEL-001`.

    Executes the exact operator step the error message and `docs/runbooks/engine-processes.md`
    §5 ("Chave travada: claim duravel sem instancia") prescribe — confirm the engine has no
    instance, then delete THAT ONE `audit_emit_dedup` row — and proves the three things the
    runbook asserts on the operator's behalf:

      1. the next delivery starts the process EXACTLY ONCE (the key is un-wedged, not merely quiet);
      2. the audit chain stays VALID and simply GAINS a link (append-only: the re-emit adds the
         second record, it never rewrites the first — clearing a dedup row is not chain surgery,
         and `docs/runbooks/audit-recovery.md` §3's ban on that stays absolute);
      3. the gate is immediately RE-ARMED — the delivery after the successful start is deduped and
         resolved to the SAME live instance, never a second one.
    """
    seam = _B1bSeam(
        monkeypatch,
        process_key="SP-OP-CANCEL-001",
        tenant_id=chaos_tenant_schema,
        route="CANCEL",
        marker="C-rearm",
    )
    await _crash_the_first_delivery(seam, chaos_sink, chaos_pg_dsn, chaos_tenant_schema)
    with pytest.raises(StartClaimWithoutInstanceError):
        await seam.deliver(chaos_sink)

    # The runbook step. Its precondition is the runbook's FIRST step — a human confirming against
    # the engine that no instance exists, active or historic. This test SIMULATES that
    # precondition and does not substitute for it: the exhausted bounded re-poll inside
    # `_resolve_strict_dedup_hit` is only what makes the precondition TRUE here, in a fixture
    # where the Fake engine provably holds nothing. It is not evidence a human can skip step 1 in
    # production, where a racer's POST may land after the re-poll gives up — which is precisely
    # why the gate refuses to decide on its own and hands the call to an operator.
    assert await delete_dedup_row(chaos_pg_dsn, chaos_tenant_schema, seam.dedup_key) is True
    assert await count_dedup_rows(chaos_pg_dsn, chaos_tenant_schema) == 0

    instance = await seam.deliver(chaos_sink)
    assert seam.start_calls["n"] == 1, "the re-armed key did not start exactly once"
    assert instance.start_outcome is StartOutcome.STARTED
    assert instance.already_existed is False
    # (2) — a SECOND chain link, and the chain still verifies end to end.
    await assert_chain_valid(chaos_pg_dsn, chaos_tenant_schema, expected_records=2)
    assert await count_dedup_rows(chaos_pg_dsn, chaos_tenant_schema) == 1

    # (3) — the gate is armed again on the fresh claim: this delivery is deduped and RESOLVED
    # against engine evidence (a live instance), so it returns that instance instead of starting.
    resolved = await seam.deliver(chaos_sink)
    assert seam.start_calls["n"] == 1, "the re-delivery after the re-arm double-started the engine"
    assert resolved.instance_id == instance.instance_id
    assert resolved.already_existed is True
    assert resolved.start_outcome is StartOutcome.ALREADY_ACTIVE
    await assert_chain_valid(chaos_pg_dsn, chaos_tenant_schema, expected_records=2)


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

    The subject key is immaterial here and stays `SP-OP-CANCEL-001` (the mutation's original):
    the broken variant never calls `start_process_idempotent`, so it consults no
    `StartDedupPosture` and passes through no gate — it isolates the audit-ORDERING invariant
    alone, which is the same under all three postures.
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
