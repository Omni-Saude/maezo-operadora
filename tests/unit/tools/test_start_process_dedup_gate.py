"""B-3 + F3 BLOCKER-1: the start gate that must never lie about a payment.

TWO defects, one chokepoint (`start_process_idempotent`), and the tests below separate them.

B-3 — the engine's ACTIVE query alone cannot gate a start:
  * **TOCTOU** — `find_active_instance` is a plain GET (`transport.py:278-284`); two concurrent
    callers with the same business key can both read "nothing active" and both start.
  * **`active=true`** — a COMPLETED instance is invisible to it, so a re-delivered start for an
    already-FINISHED business key looks brand new. For `SP-OP-PAGTO-001` that is a RE-RELEASE of
    an already-paid order.
The fix honours the durable claim's insert-conflict result (`EmitOnceOutcome.deduped`), decided
inside one per-tenant advisory-locked transaction and therefore windowless.

F3 BLOCKER-1 — the durable claim alone cannot gate one either. The claim is written BEFORE the
engine POST, so it proves an agent COMMITTED to a start, not that one HAPPENED. The first
revision conflated the two: on a claim hit with no active instance it returned
`instance_id=""` / `state="ALREADY_STARTED"` / `already_existed=True`, which
`agents/andre/graph.py` reported as `process_started: True` and shipped over A2A. If the engine
POST had failed, every retry took that branch forever — the payment order was never started and
the system reported success every time. The repair asks the ENGINE'S HISTORY what actually
happened, and RAISES when even that cannot decide:

    claim hit + ACTIVE instance   -> ALREADY_ACTIVE      (live, real id)
    claim hit + HISTORIC instance -> ALREADY_COMPLETED   (proven finished, real id, refuse)
    claim hit + NO instance ever  -> StartClaimWithoutInstanceError  (undecidable -> LOUD wedge)

Because a permanent claim CHANGES RESTART SEMANTICS, it is applied per process family via
`_START_DEDUP_POLICY`; the non-strict half of that policy is pinned here too, so a future blanket
"just make it strict" cannot land silently.

GAP-D3-02 — the THIRD posture, and why a boolean could not express it. `SP-OP-CANCEL-001`'s
re-delivery idempotency rested entirely on the TOCTOU-prone `find_active_instance`, and a second
CONCURRENT instance is the L0 double-termination risk the INADIMPLENCIA/CANCEL harmonization
exists to prevent — so it needs the claim as a mutual-exclusion token. But it is NOT one-shot
(three of its end events leave the contract alive, and one key serves all four `tipo_solicitacao`
triggers), so a PERMANENT gate would deny a legitimate second cancellation case with no human in
the loop. `StartDedupPosture.EXCLUSIVE` is those two facts held at once:

    claim hit + ACTIVE instance   -> ALREADY_ACTIVE      (same as PERMANENT)
    claim hit + FINISHED instance -> fall through, START  (PERMANENT would refuse — the ONE
                                                           branch where the postures diverge)
    claim hit + NO instance ever  -> StartClaimWithoutInstanceError  (same as PERMANENT)

Both halves are pinned below, plus a side-by-side test that fails if a refactor ever collapses
the two postures into one.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

import maezo.tools.mcp_cibseven.transport as transport_module
from maezo.gateway.audit import AuditRecord, EmitOnceOutcome
from maezo.tools.mcp_cibseven.transport import (
    AgentDecisionProvenance,
    AuditStartSink,
    CibSevenError,
    DedupReportingAuditSink,
    FakeCibSevenTransport,
    HistoryQueryingTransport,
    ProcessInstance,
    StartClaimWithoutInstanceError,
    StartDedupGateUnavailableError,
    StartDedupPosture,
    StartOutcome,
    is_strict_start_dedup,
    start_dedup_key,
    start_dedup_posture,
    start_process_idempotent,
)
from tests.support.audit_fakes import FakeStartAuditSink

PAGTO = "SP-OP-PAGTO-001"  # the one PERMANENT family
PAGTO_KEY = "PAGTO-amh-OP-001"
INAD = "SP-OP-INADIMPLENCIA-001"  # a classified NON_STRICT family
INAD_KEY = "INAD-amh-C-001"
CANCEL = "SP-OP-CANCEL-001"  # the one EXCLUSIVE family (GAP-D3-02)
CANCEL_KEY = "CANCEL-amh-C-001"


def _provenance(**overrides: Any) -> AgentDecisionProvenance:
    base: dict[str, Any] = {
        "agent_id": "andre",
        "agent_version": "andre@v0",
        "tenant_id": "amh",
        "decision_basis": {"route": "human_review"},
    }
    base.update(overrides)
    return AgentDecisionProvenance(**base)


async def _start(
    transport: FakeCibSevenTransport,
    sink: AuditStartSink,
    *,
    process_key: str = PAGTO,
    business_key: str = PAGTO_KEY,
) -> ProcessInstance:
    return await start_process_idempotent(
        transport,
        process_key=process_key,
        business_key=business_key,
        variables={"ordem_pagamento_id": "OP-001"},
        audit_sink=sink,
        provenance=_provenance(),
    )


class _CountingTransport(FakeCibSevenTransport):
    """Counts REAL engine starts — the number that must never exceed 1 for a strict key."""

    def __init__(self) -> None:
        super().__init__()
        self.starts: list[str] = []

    async def start_process_instance(self, *a: Any, **k: Any) -> ProcessInstance:
        business_key = k.get("business_key") or (a[1] if len(a) > 1 else "")
        self.starts.append(str(business_key))
        return await super().start_process_instance(*a, **k)


@pytest.fixture
def instant_gate_repoll(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse `_resolve_strict_dedup_hit`'s bounded re-poll delay to zero.

    The re-poll exists to absorb a concurrent racer's in-flight POST; every test that asserts the
    UNDECIDABLE outcome has no racer at all, so the wall-clock wait buys nothing but a slower
    suite. The ATTEMPT COUNT is left untouched — the retry loop still runs for real, so a
    regression that skipped the history lookup on later attempts would still be caught.
    """
    monkeypatch.setattr(transport_module, "_STRICT_GATE_RESOLVE_DELAY_S", 0.0)


# ---------------------------------------------------------------------------
# The `active=true` hole: a COMPLETED instance must not be restartable (strict)
# ---------------------------------------------------------------------------


async def test_strict_family_refuses_to_restart_a_completed_business_key() -> None:
    """THE duplicate-payment defect. The order was released and its instance COMPLETED; a
    re-delivered start finds nothing ACTIVE, but claim + engine HISTORY prove it already ran."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start(transport, sink)
    assert first.already_existed is False
    assert first.start_outcome is StartOutcome.STARTED
    assert transport.starts == [PAGTO_KEY]

    # The engine finishes the instance: `find_active_instance` now sees NOTHING (active=true),
    # but the engine's HISTORY still does — which is what makes the refusal provable.
    transport.seed_instance(
        ProcessInstance(
            instance_id=first.instance_id,
            process_key=PAGTO,
            business_key=PAGTO_KEY,
            state="COMPLETED",
        )
    )
    assert await transport.find_active_instance(PAGTO_KEY) is None

    second = await _start(transport, sink)

    assert transport.starts == [PAGTO_KEY], "a PAID order was re-released — the gate is dead"
    assert second.already_existed is True
    assert second.start_outcome is StartOutcome.ALREADY_COMPLETED
    assert second.business_key == PAGTO_KEY
    # F3 BLOCKER-1 — HONESTY. The refusal reports the REAL historic instance and the engine's own
    # state token; the old synthetic `instance_id=""` / `state="ALREADY_STARTED"` shape is gone.
    assert second.instance_id == first.instance_id != ""
    assert second.state == "COMPLETED"


async def test_strict_family_refuses_to_restart_an_externally_terminated_key() -> None:
    """ "Finished" is not only COMPLETED. A cancelled/terminated payment instance is equally proof
    that the start HAPPENED, so restarting it is equally a second release. The engine's own state
    token is passed through verbatim — this module never re-labels an engine state."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start(transport, sink)
    transport.seed_instance(
        ProcessInstance(
            instance_id=first.instance_id,
            process_key=PAGTO,
            business_key=PAGTO_KEY,
            state="EXTERNALLY_TERMINATED",
        )
    )

    second = await _start(transport, sink)

    assert transport.starts == [PAGTO_KEY]
    assert second.start_outcome is StartOutcome.ALREADY_COMPLETED
    assert second.state == "EXTERNALLY_TERMINATED"


async def test_strict_gate_prefers_the_live_instance_when_the_engine_still_has_one() -> None:
    """A dedup hit while the instance is still ACTIVE returns the REAL instance (with its id) —
    the engine query is retained precisely because the claim stores only a record hash."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start(transport, sink)
    second = await _start(transport, sink)

    assert transport.starts == [PAGTO_KEY]
    assert second.instance_id == first.instance_id != ""
    assert second.state == "ACTIVE"
    assert second.already_existed is True
    assert second.start_outcome is StartOutcome.ALREADY_ACTIVE


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_strict_gate_never_restarts_a_key_whose_history_belongs_to_another_definition() -> None:
    """The dedup key is `(tenant, process_key, business_key)`; the engine query is business-key
    only. A history hit for a DIFFERENT definition is not evidence about THIS one, so the gate
    must not read it as "already paid" — it falls through to the undecidable branch and wedges
    loudly rather than inventing either answer."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    await _start(transport, sink)
    transport.seed_instance(
        ProcessInstance(
            instance_id="other-definition",
            process_key="SP-OP-CONTAS-001",  # same business key, different definition
            business_key=PAGTO_KEY,
            state="COMPLETED",
        )
    )

    with pytest.raises(StartClaimWithoutInstanceError):
        await _start(transport, sink)

    assert transport.starts == [PAGTO_KEY]


# ---------------------------------------------------------------------------
# F3 BLOCKER-1: the claim proves a COMMITMENT, not an EFFECT
# ---------------------------------------------------------------------------


class _FailingStartTransport(_CountingTransport):
    """Engine that refuses the start POST for the first `failures` attempts (a real outage or a
    4xx — `CibSevenError` either way), then behaves normally."""

    def __init__(self, *, failures: int) -> None:
        super().__init__()
        self._remaining = failures
        self.attempted: list[str] = []

    async def start_process_instance(self, *a: Any, **k: Any) -> ProcessInstance:
        business_key = k.get("business_key") or (a[1] if len(a) > 1 else "")
        self.attempted.append(str(business_key))
        if self._remaining > 0:
            self._remaining -= 1
            raise CibSevenError("engine start refused (simulated)")
        return await super().start_process_instance(*a, **k)


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_engine_failure_then_retry_never_reports_a_phantom_success() -> None:
    """THE F3 BLOCKER. Claim written -> engine start FAILS -> the claim outlives the failure.

    The pre-repair code answered every subsequent retry with `instance_id=""` +
    `state="ALREADY_STARTED"` + `already_existed=True`, which `andre/graph.py` reported as
    `process_started: True`. The order was NEVER started and the system said "started" forever.

    Post-repair the retry cannot be answered from the claim alone: the engine has no instance in
    history either, so the outcome is UNDECIDABLE and says so, loudly, every time. Never silent,
    never a false success — and still never a second engine start.
    """
    transport = _FailingStartTransport(failures=1)
    sink = FakeStartAuditSink()

    # Attempt 1: the winner writes the claim, the engine refuses. Honest transient.
    with pytest.raises(CibSevenError):
        await _start(transport, sink)
    assert transport.attempted == [PAGTO_KEY]
    assert transport.starts == []  # nothing actually started

    # Attempt 2 (re-delivery): the claim is a hit, but nothing exists engine-side.
    with pytest.raises(StartClaimWithoutInstanceError) as exc:
        await _start(transport, sink)

    assert transport.starts == [], "the gate started nothing — correct"
    assert transport.attempted == [PAGTO_KEY], "the gate must not even ATTEMPT a second start"
    # Actionable: the operator gets the exact row to resolve, not a shrug.
    assert exc.value.dedup_key == start_dedup_key("amh", PAGTO, PAGTO_KEY)
    assert exc.value.business_key == PAGTO_KEY
    # And it can never be mistaken for a routine engine outage by the agents' `except CibSevenError`.
    assert not isinstance(exc.value, CibSevenError)

    # Attempt 3: STABLE. The wedge is loud and repeatable, never degrading into a silent success
    # and never drifting into a duplicate start.
    with pytest.raises(StartClaimWithoutInstanceError):
        await _start(transport, sink)
    assert transport.starts == []


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_undecidable_gate_hit_is_a_raise_not_a_blank_instance() -> None:
    """Regression pin for the exact defective SHAPE. No caller may ever receive a `ProcessInstance`
    with a blank id from this chokepoint again — the branch that used to synthesise one now
    raises, so `process_started: True` cannot be derived from a phantom."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink(already_audited=True)  # claim pre-exists, engine knows nothing

    with pytest.raises(StartClaimWithoutInstanceError):
        await _start(transport, sink)

    assert transport.starts == []


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_undecidable_branch_is_reached_only_after_the_bounded_repoll() -> None:
    """The re-poll must actually re-poll: a regression that decided on the first miss would make
    every concurrent re-delivery an operator ticket. Both engine reads run on every attempt."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink(already_audited=True)
    lookups: list[str] = []

    async def _spy_active(business_key: str) -> ProcessInstance | None:
        lookups.append("active")
        return None

    async def _spy_any(business_key: str, *, process_key: str = "") -> ProcessInstance | None:
        lookups.append("history")
        return None

    transport.find_active_instance = _spy_active  # type: ignore[method-assign]
    transport.find_any_instance = _spy_any  # type: ignore[method-assign]

    with pytest.raises(StartClaimWithoutInstanceError):
        await _start(transport, sink)

    attempts = transport_module._STRICT_GATE_RESOLVE_ATTEMPTS
    assert lookups == ["active", "history"] * attempts


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_repoll_resolves_a_racer_that_started_between_attempts() -> None:
    """The benign case the re-poll exists for: the claim winner's POST lands DURING the loser's
    resolution. The loser must report the now-visible live instance, not raise."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink(already_audited=True)
    seen = 0

    real_active = transport.find_active_instance

    async def _late_active(business_key: str) -> ProcessInstance | None:
        nonlocal seen
        seen += 1
        if seen == 1:
            return None  # winner has not POSTed yet
        return await real_active(business_key)

    async def _blind_history(business_key: str, *, process_key: str = "") -> ProcessInstance | None:
        # History is equally blind on attempt 1 — only the ACTIVE re-read resolves this case.
        return None if seen <= 1 else None

    transport.find_active_instance = _late_active  # type: ignore[method-assign]
    transport.find_any_instance = _blind_history  # type: ignore[method-assign]
    # The winner's instance is visible from attempt 2 onward.
    transport.seed_instance(
        ProcessInstance(
            instance_id="winner-instance", process_key=PAGTO, business_key=PAGTO_KEY, state="ACTIVE"
        )
    )

    result = await _start(transport, sink)

    assert result.start_outcome is StartOutcome.ALREADY_ACTIVE
    assert result.instance_id == "winner-instance"
    assert transport.starts == []


async def test_engine_failure_on_a_strict_start_leaves_the_claim_and_says_so() -> None:
    """The orphan claim is announced when it is CREATED, not only on the next retry — the operator
    should not have to wait for a re-delivery to learn a payment key is wedged. The claim is
    deliberately NOT released: a POST that timed out may still have taken effect, and releasing on
    that guess is exactly the duplicate payment this gate exists to prevent."""
    transport = _FailingStartTransport(failures=1)
    sink = FakeStartAuditSink()

    with pytest.raises(CibSevenError):
        await _start(transport, sink)

    # The claim survives (it is the mutual-exclusion token, not a retry counter).
    assert sink.dedup_keys == [start_dedup_key("amh", PAGTO, PAGTO_KEY)]


async def test_non_strict_family_is_untouched_by_an_engine_start_failure() -> None:
    """Scope guard: the wedge is a STRICT-family property. A non-strict family keeps today's
    behaviour — the claim deduped only the audit row, so a retry legitimately starts."""
    transport = _FailingStartTransport(failures=1)
    sink = FakeStartAuditSink()

    with pytest.raises(CibSevenError):
        await _start(transport, sink, process_key=INAD, business_key=INAD_KEY)
    second = await _start(transport, sink, process_key=INAD, business_key=INAD_KEY)

    assert transport.starts == [INAD_KEY]
    assert second.start_outcome is StartOutcome.STARTED


# ---------------------------------------------------------------------------
# The TOCTOU hole: concurrency-shaped — two racers, exactly one winner
# ---------------------------------------------------------------------------


class _BlockingTransport(_CountingTransport):
    """Forces the TOCTOU interleaving: EVERY racer completes `find_active_instance` (seeing
    nothing) before ANY of them may start. Under the pre-B-3 code both would then start.

    Only the FIRST `racers` lookups pass through the barrier. The gate's bounded re-poll
    (`_resolve_strict_dedup_hit`) legitimately looks a second time, and an `asyncio.Barrier`
    resets after release — a re-poll rejoining it would wait for parties that have already gone
    home. Barrier-once keeps the interleaving the test wants without inventing a deadlock.
    """

    def __init__(self, racers: int) -> None:
        super().__init__()
        self._all_looked = asyncio.Barrier(racers)
        self._barrier_budget = racers
        self.lookups = 0

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        found = await super().find_active_instance(business_key)
        self.lookups += 1
        if self._barrier_budget > 0:
            self._barrier_budget -= 1
            await self._all_looked.wait()
        return found


async def test_two_racers_on_a_strict_key_produce_exactly_one_engine_start() -> None:
    """The property the durable claim buys that the engine read cannot: mutual exclusion.

    Both racers are held at the barrier until both have seen "no active instance", so the engine
    check is guaranteed useless. Only the durable claim can break the tie — and exactly one racer
    can observe `deduped=False`.
    """
    transport = _BlockingTransport(racers=2)
    sink = FakeStartAuditSink()

    results = await asyncio.gather(_start(transport, sink), _start(transport, sink))

    assert transport.starts == [PAGTO_KEY], (
        f"expected exactly ONE engine start, got {transport.starts} — the TOCTOU window is open"
    )
    # F3 MINOR-6(b): `sink.calls` counts EMIT ATTEMPTS, not chain links — both racers emitted,
    # and the claim is what made exactly one of them write a link. The chain-link count is the
    # durable sink's own invariant (`test_audit_postgres.py`), not observable on this fake.
    assert len(sink.calls) == 2
    assert sum(1 for r in results if r.start_outcome is StartOutcome.STARTED) == 1
    assert sum(1 for r in results if r.start_outcome is StartOutcome.ALREADY_ACTIVE) == 1


async def test_many_racers_on_a_strict_key_still_produce_exactly_one_engine_start() -> None:
    transport = _BlockingTransport(racers=8)
    sink = FakeStartAuditSink()

    results = await asyncio.gather(*(_start(transport, sink) for _ in range(8)))

    assert transport.starts == [PAGTO_KEY]
    assert transport.lookups >= 8  # every racer really did consult the engine first
    assert sum(1 for r in results if r.start_outcome is StartOutcome.STARTED) == 1
    assert all(r.instance_id for r in results), "no racer may be told about a phantom instance"


async def test_racers_on_different_business_keys_are_not_serialized_into_one_start() -> None:
    """Sanity/mutation guard: the gate keys on the business key, so two DIFFERENT payment orders
    must BOTH start. A gate that collapsed distinct keys would silently swallow real payments."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    await asyncio.gather(
        _start(transport, sink, business_key="PAGTO-amh-OP-001"),
        _start(transport, sink, business_key="PAGTO-amh-OP-002"),
    )

    assert sorted(transport.starts) == ["PAGTO-amh-OP-001", "PAGTO-amh-OP-002"]


# ---------------------------------------------------------------------------
# NON-strict families keep today's restart semantics (the "do not guess" line)
# ---------------------------------------------------------------------------


async def test_non_strict_family_may_restart_a_completed_business_key() -> None:
    """`INAD-{tenant}-{numero_contrato}` carries no cycle component: a contract that cured a
    delinquency can go delinquent AGAIN. Gating it would swallow the second, real case."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start(transport, sink, process_key=INAD, business_key=INAD_KEY)
    transport.seed_instance(
        ProcessInstance(
            instance_id=first.instance_id, process_key=INAD, business_key=INAD_KEY, state="COMPLETED"
        )
    )

    second = await _start(transport, sink, process_key=INAD, business_key=INAD_KEY)

    assert transport.starts == [INAD_KEY, INAD_KEY], "a legitimate re-run was blocked"
    assert second.already_existed is False


async def test_non_strict_family_still_dedupes_the_audit_row() -> None:
    """The claim keeps doing its ORIGINAL job everywhere: one chain link per logical effect."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    await _start(transport, sink, process_key=INAD, business_key=INAD_KEY)
    await _start(transport, sink, process_key=INAD, business_key=INAD_KEY)

    assert len(sink.calls) == 2  # both attempts emitted...
    dedup_keys = {k for _, k in sink.calls}
    assert dedup_keys == {start_dedup_key("amh", INAD, INAD_KEY)}  # ...under ONE dedup key


# ---------------------------------------------------------------------------
# EXCLUSIVE (GAP-D3-02): mutual exclusion WITHOUT a permanent gate — SP-OP-CANCEL-001
#
# The gap: CANCEL-001's re-delivery idempotency rested ENTIRELY on `find_active_instance`, whose
# TOCTOU window two concurrent deliveries can walk straight through — and a second CONCURRENT
# CANCEL-001 instance is the BLOCKING L0 double-termination risk
# (docs/processes/harmonization-inadimplencia-cancel.md §1): two parallel `UT_AnaliseRescisao`
# reviews for one contract.
#
# The trap the fix must NOT fall into: `PERMANENT` (PAGTO's posture) would also close that window,
# but it would additionally refuse the key FOREVER. CANCEL-001 is not one-shot — three of its end
# events leave the contract ALIVE (`End_ContratoMantido`, `End_PedidoCancelamentoNegado`,
# `End_ManterNaoConfirmado`) and one key serves all four `tipo_solicitacao` triggers — so a titular
# whose cancellation request was denied, or a contract kept alive by a human MANTER, legitimately
# produces a SECOND case under the SAME key. `PERMANENT` would deny it with no human in the loop.
#
# These tests pin BOTH halves: the window is closed, and the second case still gets through.
# ---------------------------------------------------------------------------


async def _start_cancel(
    transport: FakeCibSevenTransport,
    sink: AuditStartSink,
    *,
    business_key: str = CANCEL_KEY,
) -> ProcessInstance:
    return await start_process_idempotent(
        transport,
        process_key=CANCEL,
        business_key=business_key,
        variables={"numero_contrato": "C-001"},
        audit_sink=sink,
        provenance=_provenance(agent_id="inadimplencia-worker"),
    )


async def test_exclusive_family_returns_the_live_instance_on_a_redelivery() -> None:
    """The dominant real case: an external-task lock expires and the SAME handoff is dispatched
    again while the CANCEL-001 instance is still running. Exactly one instance, one claim."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start_cancel(transport, sink)
    second = await _start_cancel(transport, sink)

    assert transport.starts == [CANCEL_KEY], "a re-delivery started a SECOND rescisao review"
    assert first.start_outcome is StartOutcome.STARTED
    assert second.start_outcome is StartOutcome.ALREADY_ACTIVE
    assert second.instance_id == first.instance_id != ""
    assert second.already_existed is True


async def test_two_racers_on_the_exclusive_cancel_key_produce_exactly_one_engine_start() -> None:
    """THE GAP-D3-02 FIX, stated as the property it buys. Both racers are held at the barrier
    until both have seen "no active instance", so `find_active_instance` is guaranteed useless —
    exactly the TOCTOU interleaving that used to produce two CANCEL-001 instances. Only the
    durable claim can break the tie."""
    transport = _BlockingTransport(racers=2)
    sink = FakeStartAuditSink()

    results = await asyncio.gather(_start_cancel(transport, sink), _start_cancel(transport, sink))

    assert transport.starts == [CANCEL_KEY], (
        f"expected exactly ONE engine start, got {transport.starts} — the TOCTOU window is open "
        "and two rescisao reviews can run for one contract"
    )
    assert sum(1 for r in results if r.start_outcome is StartOutcome.STARTED) == 1
    assert sum(1 for r in results if r.start_outcome is StartOutcome.ALREADY_ACTIVE) == 1
    assert all(r.instance_id for r in results), "no racer may be told about a phantom instance"


async def test_exclusive_family_is_not_a_permanent_gate_on_a_finished_key() -> None:
    """THE ANTI-SWALLOW PIN — the discriminator between `EXCLUSIVE` and `PERMANENT`.

    The first CANCEL-001 instance ended with the contract ALIVE (the human decided MANTER, or the
    beneficiary's request was denied). A LATER, genuinely new cancellation case for the same
    contract mints the SAME business key. It MUST start. Under `PERMANENT` this test goes red with
    `ALREADY_COMPLETED` — which is precisely the defect a plain "flip CANCEL to strict" would have
    shipped: an adverse outcome against a beneficiary produced by an idempotency gate, no human
    anywhere in it."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start_cancel(transport, sink)
    # The instance ends — contract kept alive (`End_ContratoMantido`). `find_active_instance` now
    # sees nothing; the engine's HISTORY still proves the first generation happened and ended.
    transport.seed_instance(
        ProcessInstance(
            instance_id=first.instance_id,
            process_key=CANCEL,
            business_key=CANCEL_KEY,
            state="COMPLETED",
        )
    )
    assert await transport.find_active_instance(CANCEL_KEY) is None

    second = await _start_cancel(transport, sink)

    assert transport.starts == [CANCEL_KEY, CANCEL_KEY], (
        "a legitimate SECOND cancellation case was swallowed by the dedup gate"
    )
    assert second.start_outcome is StartOutcome.STARTED
    assert second.already_existed is False
    assert second.instance_id != ""


async def test_exclusive_family_permits_the_next_case_after_an_externally_terminated_one() -> None:
    """ "Finished" is not only COMPLETED. A terminated CANCEL-001 instance equally proves the
    claimed generation is over, so the next legitimate case may start — same reasoning, and the
    engine's own state token is what decides it."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start_cancel(transport, sink)
    transport.seed_instance(
        ProcessInstance(
            instance_id=first.instance_id,
            process_key=CANCEL,
            business_key=CANCEL_KEY,
            state="EXTERNALLY_TERMINATED",
        )
    )

    second = await _start_cancel(transport, sink)

    assert transport.starts == [CANCEL_KEY, CANCEL_KEY]
    assert second.start_outcome is StartOutcome.STARTED


# ---------------------------------------------------------------------------
# GK MAJOR-1: the ACTIVE-rescue branch, on the MULTI-ROW history `EXCLUSIVE` creates by design
#
# `EXCLUSIVE` falls through on a finished generation and starts the next one, so a CANCEL key's
# history is normally a LIST — `[gen-1 COMPLETED, gen-2 ACTIVE]`. The gatekeeper proved that
# `find_any_instance` returned the FIRST matching row, so on exactly that history the rescue read
# COMPLETED, the gate concluded "the claimed generation ended", and a SECOND CONCURRENT instance
# started — the double `UT_AnaliseRescisao` this posture exists to prevent, produced BY the
# posture. The single-cell fake could not even express that history, so no test could catch it.
# ---------------------------------------------------------------------------


class _ActiveBlindTransport(_CountingTransport):
    """`find_active_instance` always answers "nothing", whatever the history holds.

    This is not a contrivance: it is the code's own stated reason for having a history rescue at
    all (`transport.py` — "replication lag / a racer's POST landing between the two reads"). The
    rescue branch is UNREACHABLE while the active query works, so this double is the only way to
    exercise it, and therefore the only way its correctness is provable.
    """

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        return None


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_exclusive_rescue_finds_the_live_generation_in_a_multi_row_history() -> None:
    """THE GK MAJOR-1 REGRESSION. History `[gen-1 COMPLETED, gen-2 ACTIVE]`, active query blind:
    the gate must converge on the LIVE generation. Returning the finished row here starts a second
    concurrent rescisao review for one contract."""
    transport = _ActiveBlindTransport()
    transport.seed_instance(
        ProcessInstance(instance_id="pi-gen1", process_key=CANCEL, business_key=CANCEL_KEY, state="COMPLETED")
    )
    transport.seed_instance(
        ProcessInstance(instance_id="pi-gen2", process_key=CANCEL, business_key=CANCEL_KEY, state="ACTIVE"),
        append=True,
    )
    sink = FakeStartAuditSink(already_audited=True)  # the claim for this key already exists

    result = await _start_cancel(transport, sink)

    assert transport.starts == [], (
        "the gate started a SECOND CONCURRENT CANCEL-001 while gen-2 was live — the ACTIVE-rescue "
        "branch was handed an arbitrary history row"
    )
    assert result.start_outcome is StartOutcome.ALREADY_ACTIVE
    assert result.instance_id == "pi-gen2"
    assert result.state == "ACTIVE"
    assert result.already_existed is True


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_exclusive_rescue_does_not_read_a_suspended_generation_as_closed() -> None:
    """A SUSPENDED instance has NOT ended. Reading it as "generation over" would start a second
    instance beside a suspended one — under-matching permits a duplicate, over-matching only
    refuses a start, and only one of those two is recoverable."""
    transport = _ActiveBlindTransport()
    transport.seed_instance(
        ProcessInstance(instance_id="pi-gen1", process_key=CANCEL, business_key=CANCEL_KEY, state="COMPLETED")
    )
    transport.seed_instance(
        ProcessInstance(
            instance_id="pi-gen2", process_key=CANCEL, business_key=CANCEL_KEY, state="SUSPENDED"
        ),
        append=True,
    )
    sink = FakeStartAuditSink(already_audited=True)

    result = await _start_cancel(transport, sink)

    assert transport.starts == []
    assert result.start_outcome is StartOutcome.ALREADY_ACTIVE
    assert result.state == "SUSPENDED", "the engine's own state token is reported verbatim"


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_permanent_rescue_also_prefers_the_live_row_over_a_finished_one() -> None:
    """The same read serves `PERMANENT`. A PAGTO key whose history holds a finished row AND a live
    one must report the LIVE instance — `ALREADY_COMPLETED` there would name the wrong instance id
    to an operator asking "was this order paid?"."""
    transport = _ActiveBlindTransport()
    transport.seed_instance(
        ProcessInstance(instance_id="pi-old", process_key=PAGTO, business_key=PAGTO_KEY, state="COMPLETED")
    )
    transport.seed_instance(
        ProcessInstance(instance_id="pi-live", process_key=PAGTO, business_key=PAGTO_KEY, state="ACTIVE"),
        append=True,
    )
    sink = FakeStartAuditSink(already_audited=True)

    result = await _start(transport, sink)

    assert transport.starts == []
    assert result.start_outcome is StartOutcome.ALREADY_ACTIVE
    assert result.instance_id == "pi-live"


async def test_exclusive_multi_generation_lifecycle_starts_once_per_generation() -> None:
    """END TO END over TWO generations, with the history accumulating for real (no blindness).

    gen-1 runs and ends -> a genuinely new case starts gen-2 (the anti-swallow property) -> a
    re-delivery of gen-2 converges on it (the exclusion property). Exactly two starts, ever, and
    the third delivery names the LIVE instance, not the finished one."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start_cancel(transport, sink)
    # gen-1 ends (`End_ContratoMantido` — contract kept alive by the human MANTER decision).
    transport.seed_instance(
        ProcessInstance(
            instance_id=first.instance_id, process_key=CANCEL, business_key=CANCEL_KEY, state="COMPLETED"
        )
    )
    second = await _start_cancel(transport, sink)  # a new, legitimate cancellation case
    third = await _start_cancel(transport, sink)  # a re-delivery of THAT case

    assert transport.starts == [CANCEL_KEY, CANCEL_KEY], (
        "one start per legitimate generation — no more, no fewer"
    )
    assert second.start_outcome is StartOutcome.STARTED
    assert second.instance_id != first.instance_id != ""
    assert third.start_outcome is StartOutcome.ALREADY_ACTIVE
    assert third.instance_id == second.instance_id, (
        "the re-delivery was pointed at the FINISHED gen-1 instance instead of the live gen-2 one"
    )
    # The history really does hold both rows now, and the live one is what the rescue would read.
    historic = await transport.find_any_instance(CANCEL_KEY, process_key=CANCEL)
    assert historic is not None and historic.instance_id == second.instance_id


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_two_racers_over_a_multi_row_history_both_converge_on_the_live_generation() -> None:
    """The barrier property, re-checked on the history shape `EXCLUSIVE` actually produces. Two
    concurrent deliveries, a claim already held, `[gen-1 COMPLETED, gen-2 ACTIVE]` in history and
    an active query that sees nothing: BOTH must converge on gen-2 and NEITHER may start. Under
    the first-matching-row read both read COMPLETED and both started — two new instances beside a
    live one, from a single re-delivery burst."""
    transport = _ActiveBlindTransport()
    transport.seed_instance(
        ProcessInstance(instance_id="pi-gen1", process_key=CANCEL, business_key=CANCEL_KEY, state="COMPLETED")
    )
    transport.seed_instance(
        ProcessInstance(instance_id="pi-gen2", process_key=CANCEL, business_key=CANCEL_KEY, state="ACTIVE"),
        append=True,
    )
    sink = FakeStartAuditSink(already_audited=True)

    results = await asyncio.gather(_start_cancel(transport, sink), _start_cancel(transport, sink))

    assert transport.starts == []
    assert all(r.start_outcome is StartOutcome.ALREADY_ACTIVE for r in results)
    assert {r.instance_id for r in results} == {"pi-gen2"}


async def test_the_durable_claim_is_minted_once_per_key_never_once_per_generation() -> None:
    """THE MECHANISM BEHIND THE DISCLOSED RESIDUAL (DL-0046). The dedup key is
    `(tenant, process_key, business_key)` with NO generation component, so the claim is minted
    exactly ONCE in a business key's lifetime and every later generation runs with the claim
    already held. Consequence, stated plainly rather than implied: `EXCLUSIVE` supplies mutual
    exclusion for generation 1 only — from generation 2 onward, FOREVER, concurrent starts are
    back to being separated by `find_active_instance` alone (i.e. by nothing, under TOCTOU).

    Closing that needs a generation-scoped durable token (the XRD-10/MZO-060 outbox), which this
    module does not build. This test pins the MECHANISM, so it stays true and meaningful when the
    outbox lands — at which point the disclosure in DL-0046 and the policy block is what changes.
    """
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start_cancel(transport, sink)
    transport.seed_instance(
        ProcessInstance(
            instance_id=first.instance_id, process_key=CANCEL, business_key=CANCEL_KEY, state="COMPLETED"
        )
    )
    await _start_cancel(transport, sink)

    expected_key = start_dedup_key("amh", CANCEL, CANCEL_KEY)
    assert sink.dedup_keys == [expected_key, expected_key]
    assert len(set(sink.dedup_keys)) == 1, (
        "a generation-scoped dedup key would appear here as a SECOND distinct key — its absence "
        "is exactly the residual DL-0046 discloses"
    )


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_exclusive_family_wedges_loudly_when_the_claim_has_no_instance_anywhere() -> None:
    """ABSENCE OF EVIDENCE IS NOT PERMISSION. A claim with no instance active OR historic is the
    undecidable case: the winner's POST may still be in flight, and starting into that window is
    the second concurrent rescisao review this posture exists to prevent. `EXCLUSIVE` treats it
    EXACTLY like `PERMANENT` does — raise, never guess.

    This is the branch that separates `EXCLUSIVE` from "just go back to non-strict": non-strict
    would have started a duplicate here, silently."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink(already_audited=True)  # claim pre-exists, engine knows nothing

    with pytest.raises(StartClaimWithoutInstanceError) as exc:
        await _start_cancel(transport, sink)

    assert transport.starts == []
    assert exc.value.process_key == CANCEL
    assert exc.value.dedup_key == start_dedup_key("amh", CANCEL, CANCEL_KEY)
    # Never swallowed by an agent/worker `except CibSevenError` as a routine engine outage.
    assert not isinstance(exc.value, CibSevenError)


@pytest.mark.usefixtures("instant_gate_repoll")
async def test_exclusive_family_start_failure_then_retry_never_reports_a_phantom_success() -> None:
    """The F3 BLOCKER-1 property, now also live for CANCEL: claim written -> engine start FAILS ->
    the retry cannot be answered from the claim alone and says so, loudly, instead of reporting a
    rescisao review that was never opened."""
    transport = _FailingStartTransport(failures=1)
    sink = FakeStartAuditSink()

    with pytest.raises(CibSevenError):
        await _start_cancel(transport, sink)
    assert transport.starts == []

    with pytest.raises(StartClaimWithoutInstanceError):
        await _start_cancel(transport, sink)
    assert transport.starts == [], "the gate must not even ATTEMPT a second start"


async def test_exclusive_family_keys_are_not_collapsed_across_contracts() -> None:
    """Mutation guard: the gate keys on the business key. Two DIFFERENT contracts must BOTH start —
    a gate that collapsed distinct keys would silently swallow a real termination case."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    await asyncio.gather(
        _start_cancel(transport, sink, business_key="CANCEL-amh-C-001"),
        _start_cancel(transport, sink, business_key="CANCEL-amh-C-002"),
    )

    assert sorted(transport.starts) == ["CANCEL-amh-C-001", "CANCEL-amh-C-002"]


async def test_exclusive_family_refuses_to_start_behind_a_sink_that_cannot_report_dedup() -> None:
    """FAIL CLOSED, NEVER FALL BACK. A composition root whose sink cannot report the dedup flag
    cannot gate a CANCEL start — so the start does not run at all. Silently degrading to the
    TOCTOU-only path is the behaviour GAP-D3-02 exists to remove, and it must not be reachable by
    mis-wiring."""
    transport = _CountingTransport()

    with pytest.raises(StartDedupGateUnavailableError) as exc:
        await _start_cancel(transport, _HashOnlySink())

    assert transport.starts == []
    assert not isinstance(exc.value, CibSevenError)


async def test_exclusive_family_refuses_to_start_behind_a_history_blind_transport() -> None:
    """The other half of the same fail-closed rule: without `find_any_instance` the gate cannot
    tell "the claimed generation ended" from "the start never happened", so it refuses rather than
    guessing either. Raised BEFORE the claim is written — a mis-wired root leaves no orphan."""
    sink = FakeStartAuditSink()

    with pytest.raises(StartDedupGateUnavailableError):
        await start_process_idempotent(
            _HistoryBlindTransport(),
            process_key=CANCEL,
            business_key=CANCEL_KEY,
            variables={},
            audit_sink=sink,
            provenance=_provenance(),
        )

    assert sink.calls == [], "the refusal must precede the durable claim (no orphan claim)"


# ---------------------------------------------------------------------------
# PAGTO REGRESSION: `PERMANENT` behaviour is byte-for-byte what it was before GAP-D3-02
# ---------------------------------------------------------------------------


async def test_permanent_and_exclusive_diverge_only_on_the_finished_instance_branch() -> None:
    """Side-by-side discriminator. Same fixture shape, same claim hit, same finished instance —
    and the ONLY difference in the whole gate is the verdict on that one branch. If a refactor
    ever made `PERMANENT` fall through (or `EXCLUSIVE` refuse), this is the test that names it."""
    pagto_transport = _CountingTransport()
    pagto_sink = FakeStartAuditSink()
    first_pagto = await _start(pagto_transport, pagto_sink)
    pagto_transport.seed_instance(
        ProcessInstance(
            instance_id=first_pagto.instance_id,
            process_key=PAGTO,
            business_key=PAGTO_KEY,
            state="COMPLETED",
        )
    )

    cancel_transport = _CountingTransport()
    cancel_sink = FakeStartAuditSink()
    first_cancel = await _start_cancel(cancel_transport, cancel_sink)
    cancel_transport.seed_instance(
        ProcessInstance(
            instance_id=first_cancel.instance_id,
            process_key=CANCEL,
            business_key=CANCEL_KEY,
            state="COMPLETED",
        )
    )

    pagto_second = await _start(pagto_transport, pagto_sink)
    cancel_second = await _start_cancel(cancel_transport, cancel_sink)

    assert pagto_second.start_outcome is StartOutcome.ALREADY_COMPLETED
    assert pagto_transport.starts == [PAGTO_KEY], "PAGTO's permanent gate regressed"
    assert cancel_second.start_outcome is StartOutcome.STARTED
    assert cancel_transport.starts == [CANCEL_KEY, CANCEL_KEY]


# ---------------------------------------------------------------------------
# The policy map itself
# ---------------------------------------------------------------------------


def test_pagto_is_the_only_permanent_family_and_cancel_the_only_exclusive_one() -> None:
    """Pins the deliberate scope of BOTH gated postures. `PERMANENT` (a key that may never start
    again) is PAGTO and PAGTO alone — money release is one-shot. `EXCLUSIVE` (concurrent starts
    mutually excluded, but NEVER a permanent gate) is CANCEL and CANCEL alone since GAP-D3-02.
    Everything else stays `NON_STRICT`, because those keys either legitimately re-run across time
    or raise a domain question an engineering change may not answer.

    Widening either set is a REVIEWED decision, not a refactor — and MOVING a key from `EXCLUSIVE`
    to `PERMANENT` is the one that needs the most review: it converts an idempotency gate into a
    denial of a legitimate second case with no human in the loop."""
    from maezo.tools.mcp_cibseven.transport import _START_DEDUP_POLICY

    by_posture = {
        posture: sorted(k for k, p in _START_DEDUP_POLICY.items() if p is posture)
        for posture in StartDedupPosture
    }
    assert by_posture[StartDedupPosture.PERMANENT] == [PAGTO]
    assert by_posture[StartDedupPosture.EXCLUSIVE] == [CANCEL]
    assert len(by_posture[StartDedupPosture.NON_STRICT]) == len(_START_DEDUP_POLICY) - 2

    assert start_dedup_posture(PAGTO) is StartDedupPosture.PERMANENT
    assert start_dedup_posture(CANCEL) is StartDedupPosture.EXCLUSIVE
    assert start_dedup_posture(INAD) is StartDedupPosture.NON_STRICT

    # `is_strict_start_dedup` is the "does the claim gate the EFFECT?" predicate the chokepoint
    # branches on — TRUE for both gated postures, and it must not silently narrow back to PAGTO.
    assert is_strict_start_dedup(PAGTO) is True
    assert is_strict_start_dedup(CANCEL) is True
    assert is_strict_start_dedup(INAD) is False


@pytest.mark.parametrize(
    "process_key",
    [
        "SP-OP-INADIMPLENCIA-001",
        "SP-OP-ESCALATION-001",
        "SP-OP-CRED-001",
        "SP-OP-FRAUDE-001",
        "SP-OP-CANCEL-001",
        "SP-OP-CONTAS-001",
        "SP-OP-RECURSO-001",
        "SP-OP-AUTH-001",
        "SP-OP-ANS-SUBMIT-001",
        "SP-OP-NIP-001",
        "SP-OP-PROGRAMA-001",
    ],
)
def test_every_started_process_key_is_explicitly_classified(process_key: str) -> None:
    """Every process key reachable through the chokepoint has an EXPLICIT entry — the audit is
    recorded in code, not in a review comment. A new start site must classify itself."""
    from maezo.tools.mcp_cibseven.transport import _START_DEDUP_POLICY

    assert process_key in _START_DEDUP_POLICY


def test_unclassified_process_key_defaults_to_todays_behaviour() -> None:
    """A surprise permanent gate on an unknown flow would silently swallow real cases, which is
    the worse of the two failures — so an unclassified key defaults NON-strict (and warns)."""
    assert is_strict_start_dedup("SP-OP-SOMETHING-BRAND-NEW-999") is False


# ---------------------------------------------------------------------------
# Fail-closed: a sink that cannot report its claim may not run a strict start
# ---------------------------------------------------------------------------


class _HashOnlySink:
    """A legacy sink implementing ONLY `emit_once` — it cannot report the dedup flag."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def emit_once(self, record: AuditRecord, *, dedup_key: str) -> str:
        self.calls.append(dedup_key)
        return "hash"


async def test_strict_family_refuses_to_start_behind_a_sink_that_cannot_report_dedup() -> None:
    """No silent degradation: an un-gateable payment start FAILS rather than falling back to the
    TOCTOU-only path. The raise is deliberately NOT a `CibSevenError`, so the agents' `except
    CibSevenError` cannot swallow it into a misleading "engine unavailable"."""
    from maezo.tools.mcp_cibseven.transport import CibSevenError

    transport = _CountingTransport()

    with pytest.raises(StartDedupGateUnavailableError) as exc:
        await _start(transport, _HashOnlySink())

    assert not isinstance(exc.value, CibSevenError)
    assert transport.starts == []


async def test_non_strict_family_still_works_behind_a_hash_only_sink() -> None:
    """Back-compat: the fail-closed raise is scoped to strict families only."""
    transport = _CountingTransport()
    sink = _HashOnlySink()

    inst = await _start(transport, sink, process_key=INAD, business_key=INAD_KEY)

    assert inst.already_existed is False
    assert sink.calls == [start_dedup_key("amh", INAD, INAD_KEY)]


def test_protocol_probe_recognizes_every_production_sink_shape() -> None:
    """The gate is only real if the sinks actually wired in production satisfy it.

    `PostgresAuditSink` is what the two PAGTO-capable composition roots inject
    (`runtime/agent_runtime/service.py:316` for Andre's graph,
    `platform/webhooks/service.py:108` for the notification bridge). `FreshSinkAuditEmitter` is
    the loop-agnostic adapter the SYNC worker handoff seams use — and since GAP-D3-02 it is no
    longer a future-proofing nicety: it fronts the `EXCLUSIVE` CANCEL-001 starts of
    `inadimplencia.handoff_rescisao` / `fraude.start_contratual`
    (`runtime/worker_runtime/service.py:787-790`), so losing `emit_once_status` here would fail
    those handoffs closed.
    """
    from maezo.gateway.audit_postgres import FreshSinkAuditEmitter, PostgresAuditSink

    assert issubclass(PostgresAuditSink, DedupReportingAuditSink)
    assert issubclass(FreshSinkAuditEmitter, DedupReportingAuditSink)
    assert isinstance(FakeStartAuditSink(), DedupReportingAuditSink)
    assert not isinstance(_HashOnlySink(), DedupReportingAuditSink)


def test_protocol_probe_recognizes_every_production_transport_shape() -> None:
    """Sink half above, TRANSPORT half here — both are gate inputs and both fail closed.

    Every transport a composition root can inject in front of a gated family must satisfy
    `HistoryQueryingTransport`, INCLUDING through the Onda-1 gating decorator: `gate_cibseven`
    (`gateway/seams/cibseven.py:128`) picks `GatedHistoryQueryingCibSevenTransport` precisely so
    the capability survives the wrap. A decorator that silently dropped it would turn every
    CANCEL-001 / PAGTO-001 start into a fail-closed refusal in production.
    """
    from maezo.gateway.seams._base import SeamContext
    from maezo.gateway.seams.cibseven import GatedHistoryQueryingCibSevenTransport, gate_cibseven
    from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
    from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport

    http = CibSevenHttpTransport("http://engine.invalid")
    fresh = FreshClientCibSevenTransport("http://engine.invalid")
    assert isinstance(http, HistoryQueryingTransport)
    assert isinstance(fresh, HistoryQueryingTransport)

    seam = SeamContext(tenant="amh", principal="teste_gate_probe")
    for inner in (http, fresh):
        gated = gate_cibseven(inner, seam)
        assert isinstance(gated, GatedHistoryQueryingCibSevenTransport)
        assert isinstance(gated, HistoryQueryingTransport), (
            "the gating decorator dropped `find_any_instance` — every gated start would refuse"
        )

    # And the negative: a history-blind inner must NOT be dressed up as history-querying.
    assert not isinstance(gate_cibseven(_HistoryBlindTransport(), seam), HistoryQueryingTransport)


# ---------------------------------------------------------------------------
# Emit-before-effect is UNCHANGED by the gate
# ---------------------------------------------------------------------------


async def test_gate_never_reorders_audit_after_effect() -> None:
    events: list[str] = []

    class _Spy(FakeStartAuditSink):
        async def emit_once_status(self, record: AuditRecord, *, dedup_key: str) -> EmitOnceOutcome:
            events.append("emit")
            return await super().emit_once_status(record, dedup_key=dedup_key)

    class _T(FakeCibSevenTransport):
        async def start_process_instance(self, *a: Any, **k: Any) -> ProcessInstance:
            events.append("start")
            return await super().start_process_instance(*a, **k)

    await _start(_T(), _Spy())

    assert events == ["emit", "start"]


async def test_gate_hit_performs_no_engine_start_at_all() -> None:
    """Mutation guard: on a dedup hit the engine start must not be reached even once."""

    class _ExplodingStart(FakeCibSevenTransport):
        async def start_process_instance(self, *a: Any, **k: Any) -> ProcessInstance:
            raise AssertionError("engine start ran on a dedup-gated call — fail-OPEN!")

    transport = _ExplodingStart()
    sink = FakeStartAuditSink(already_audited=True)  # claim pre-exists
    transport.seed_instance(
        ProcessInstance(
            instance_id="prior-instance", process_key=PAGTO, business_key=PAGTO_KEY, state="COMPLETED"
        )
    )

    inst = await _start(transport, sink)

    assert inst.start_outcome is StartOutcome.ALREADY_COMPLETED
    assert inst.already_existed is True
    assert inst.instance_id == "prior-instance"


# ---------------------------------------------------------------------------
# Fail-closed: a transport that cannot see engine history may not run a strict start
# ---------------------------------------------------------------------------


class _HistoryBlindTransport:
    """A `CibSevenTransport` WITHOUT `find_any_instance` — the pre-F3 seam shape."""

    def __init__(self) -> None:
        self.starts: list[str] = []

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        return None

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> ProcessInstance:
        self.starts.append(business_key)
        return ProcessInstance(
            instance_id="x", process_key=process_key, business_key=business_key, state="ACTIVE"
        )

    async def correlate_message(self, *a: Any, **k: Any) -> None:
        return None

    async def get_process_status(self, business_key: str) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        return None


async def test_strict_family_refuses_to_start_behind_a_history_blind_transport() -> None:
    """Without engine history a dedup hit can only be checked against `active=true`, which is the
    blindness that produced the phantom `ALREADY_STARTED`. Refusing is the only honest option, and
    it happens BEFORE the claim is written so a mis-wired root leaves no orphan behind."""
    transport = _HistoryBlindTransport()
    sink = FakeStartAuditSink()

    with pytest.raises(StartDedupGateUnavailableError):
        await _start(transport, sink)  # type: ignore[arg-type]

    assert transport.starts == []
    assert sink.calls == [], "the durable claim must not exist — the refusal precedes the emit"


async def test_non_strict_family_still_works_behind_a_history_blind_transport() -> None:
    """Back-compat: the new seam requirement is scoped to strict families only."""
    transport = _HistoryBlindTransport()
    sink = FakeStartAuditSink()

    inst = await _start(transport, sink, process_key=INAD, business_key=INAD_KEY)  # type: ignore[arg-type]

    assert inst.start_outcome is StartOutcome.STARTED
    assert transport.starts == [INAD_KEY]


def test_production_transports_can_all_answer_the_history_question() -> None:
    """The gate is only real if the transports actually wired in production satisfy it.
    `CibSevenHttpTransport` is what the agent/bridge composition roots build;
    `FreshClientCibSevenTransport` is the worker daemon's decorator around it (a decorator that
    DROPPED the capability would silently downgrade a future strict worker start to a refusal)."""
    from maezo.tools.mcp_cibseven.transport import CibSevenHttpTransport
    from maezo.tools.workers.cibseven_engine import FreshClientCibSevenTransport

    assert issubclass(CibSevenHttpTransport, HistoryQueryingTransport)
    assert issubclass(FreshClientCibSevenTransport, HistoryQueryingTransport)
    assert isinstance(FakeCibSevenTransport(), HistoryQueryingTransport)
    assert not isinstance(_HistoryBlindTransport(), HistoryQueryingTransport)


# ---------------------------------------------------------------------------
# Tier 2 (F3 MINOR-6a) — the mutual exclusion against a REAL Postgres
#
# Everything above races coroutines against `FakeStartAuditSink`'s in-process dict, which is
# serialized by the event loop and therefore cannot exhibit the thing being claimed: that
# `PostgresAuditSink.emit_once_status` gives exactly ONE winner under genuine parallelism. That
# claim rests on `pg_advisory_xact_lock` + `ON CONFLICT (tenant, dedup_key) DO NOTHING`, and only
# a real database can falsify it.
#
# Placed HERE rather than under `tests/integration/` for the same reason as
# `tests/unit/runtime/test_worker_runtime_audit_sink_pg.py` and
# `tests/unit/platform/webhooks/whatsapp/test_dispatch_live_pg.py`: it needs a REAL Postgres but
# explicitly NOT the CIB Seven engine. Skips LOUDLY (never silently, never a fake pass) when no
# Postgres is reachable.
#
# Run for real:
#   docker compose --profile core up -d postgres
#   MAEZO_TEST_DATABASE_URL=postgresql://maezo:maezo@localhost:5433/maezo \
#     uv run pytest tests/unit/tools/test_start_process_dedup_gate.py -q -m integration
# ---------------------------------------------------------------------------

_LIVE_PG_RACERS = 8


def _default_test_dsn() -> str:
    return os.environ.get("MAEZO_TEST_DATABASE_URL", "postgresql://maezo:maezo@localhost:5433/maezo")


async def _postgres_reachable(dsn: str) -> bool:
    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

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
            f"Postgres not reachable at {dsn!r} (override with MAEZO_TEST_DATABASE_URL) — the "
            "LIVE start-gate concurrency proof is SKIPPED (visible, not silent). Start it with "
            "`docker compose --profile core up -d postgres`."
        )
    return dsn


# Mirrors platform/migrations/versions/0002_audit_chain.py + 0005_audit_emit_dedup.py upgrade()
# DDL exactly — duplicated per file for test speed/isolation, the same convention
# `tests/unit/gateway/test_audit_postgres.py:156-191` and
# `tests/unit/runtime/test_worker_runtime_audit_sink_pg.py:71-100` already follow.
_AUDIT_CHAIN_DDL = """
    CREATE TABLE audit_chain (
        id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        timestamp       timestamptz NOT NULL DEFAULT now(),
        tenant_id       text NOT NULL,
        agent_id        text NOT NULL,
        agent_version   text NOT NULL,
        action          text NOT NULL,
        decision        text NOT NULL,
        input_hash      text NOT NULL,
        decision_basis  jsonb NOT NULL DEFAULT '{}'::jsonb,
        dmn_versions    jsonb NOT NULL DEFAULT '{}'::jsonb,
        model_id        text,
        prompt_version  text,
        record_hash     text NOT NULL,
        prev_record_hash text,
        created_at      timestamptz NOT NULL DEFAULT now(),

        CONSTRAINT uq_audit_chain_prev_hash UNIQUE (prev_record_hash)
    )
"""
_AUDIT_EMIT_DEDUP_DDL = """
    CREATE TABLE audit_emit_dedup (
        tenant       text NOT NULL,
        dedup_key    text NOT NULL,
        record_hash  text NOT NULL,
        created_at   timestamptz NOT NULL DEFAULT now(),

        PRIMARY KEY (tenant, dedup_key)
    )
"""


@pytest.fixture
async def live_tenant_schema(pg_dsn: str) -> AsyncIterator[str]:
    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn

    tenant_id = f"sg{uuid.uuid4().hex[:16]}"  # schema_for_tenant requires [a-z][a-z0-9_]*
    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'CREATE SCHEMA "{tenant_id}"')
        await conn.execute(f'SET search_path TO "{tenant_id}"')
        await conn.execute(_AUDIT_CHAIN_DDL)
        await conn.execute(_AUDIT_EMIT_DEDUP_DDL)
    finally:
        await conn.close()

    yield tenant_id

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'DROP SCHEMA "{tenant_id}" CASCADE')
    finally:
        await conn.close()


@pytest.mark.integration
async def test_live_pg_n_racers_on_one_dedup_key_yield_exactly_one_deduped_false(
    pg_dsn: str, live_tenant_schema: str
) -> None:
    """`_LIVE_PG_RACERS` INDEPENDENT connections, ONE dedup key, exactly one `deduped=False`.

    This is the load-bearing premise of the whole strict gate: `start_process_idempotent` treats
    `deduped=False` as "I hold the EXCLUSIVE right to release this payment". If two racers could
    both observe it, two payments could both be released, and every other test in this file would
    be proving a property the database does not actually provide.

    Each racer gets its OWN `PostgresAuditSink` (hence its own pool/connection), so they contend
    in the server, not in one event loop's dict.
    """
    from maezo.gateway.audit_postgres import PostgresAuditSink

    dedup_key = start_dedup_key(live_tenant_schema, PAGTO, PAGTO_KEY)
    sinks = [PostgresAuditSink(pg_dsn, live_tenant_schema) for _ in range(_LIVE_PG_RACERS)]

    async def _claim(index: int) -> bool:
        record = AuditRecord(
            agent_id="andre",
            tenant_id=live_tenant_schema,
            agent_version="andre@v0",
            action=f"start_process:{PAGTO}",
            decision="START_PROCESS",
            # Deliberately DIFFERENT per racer: the dedup key alone must decide the winner, never
            # an accidental record-identity collision.
            details={"process_key": PAGTO, "racer": index},
        )
        outcome = await sinks[index].emit_once_status(record, dedup_key=dedup_key)
        return outcome.deduped

    try:
        deduped_flags = await asyncio.gather(*(_claim(i) for i in range(_LIVE_PG_RACERS)))
    finally:
        for sink in sinks:
            await sink.aclose()

    winners = [flag for flag in deduped_flags if flag is False]
    assert len(winners) == 1, (
        f"expected exactly ONE racer to observe deduped=False, got {len(winners)} of "
        f"{_LIVE_PG_RACERS} — the mutual exclusion the strict payment gate relies on is BROKEN"
    )

    import asyncpg  # type: ignore[import-untyped]

    from maezo.gateway.audit_postgres import normalize_dsn, verify_chain

    conn = await asyncpg.connect(normalize_dsn(pg_dsn))
    try:
        await conn.execute(f'SET search_path TO "{live_tenant_schema}"')
        links = await conn.fetchval("SELECT count(*) FROM audit_chain")
        claims = await conn.fetchval("SELECT count(*) FROM audit_emit_dedup")
    finally:
        await conn.close()

    # One logical effect -> exactly one chain link and exactly one claim, whatever the parallelism.
    assert links == 1
    assert claims == 1
    result = await verify_chain(pg_dsn, live_tenant_schema)
    assert result.valid


@pytest.mark.integration
async def test_live_pg_racers_through_the_full_chokepoint_start_exactly_once(
    pg_dsn: str, live_tenant_schema: str
) -> None:
    """The same race driven through `start_process_idempotent` end to end, with the real durable
    sink and a counting engine double: exactly one engine start, and every loser gets a REAL
    instance id with an honest `ALREADY_ACTIVE` — never the blank-id phantom (F3 BLOCKER-1)."""
    from maezo.gateway.audit_postgres import PostgresAuditSink

    transport = _CountingTransport()
    sinks = [PostgresAuditSink(pg_dsn, live_tenant_schema) for _ in range(_LIVE_PG_RACERS)]
    provenance = _provenance(tenant_id=live_tenant_schema)

    async def _race(index: int) -> ProcessInstance:
        return await start_process_idempotent(
            transport,
            process_key=PAGTO,
            business_key=PAGTO_KEY,
            variables={"ordem_pagamento_id": "OP-001"},
            audit_sink=sinks[index],
            provenance=provenance,
        )

    try:
        results = await asyncio.gather(*(_race(i) for i in range(_LIVE_PG_RACERS)))
    finally:
        for sink in sinks:
            await sink.aclose()

    assert transport.starts == [PAGTO_KEY], (
        f"expected exactly ONE engine start, got {transport.starts} — a duplicate payment release"
    )
    assert sum(1 for r in results if r.start_outcome is StartOutcome.STARTED) == 1
    assert all(r.instance_id for r in results), "a racer was handed a phantom (blank-id) instance"
