"""B-3: the durable `audit_emit_dedup` claim as an ATOMIC start gate (the duplicate-payment class).

`start_process_idempotent` used to DISCARD the `emit_once` result and gate the engine effect on
`find_active_instance` alone. That check has two holes, both of which these tests exercise
directly:

  * **TOCTOU** — `find_active_instance` is a plain GET (`transport.py:206-211`); two concurrent
    callers with the same business key can both read "nothing active" and both start.
  * **`active=true`** — a COMPLETED instance is invisible to it, so a re-delivered start for an
    already-FINISHED business key looks brand new. For `SP-OP-PAGTO-001` that is a RE-RELEASE of
    an already-paid order.

The fix honours the durable claim's insert-conflict result (`EmitOnceOutcome.deduped`), which is
decided inside one per-tenant advisory-locked transaction and therefore has no window. Because a
permanent claim CHANGES RESTART SEMANTICS, it is applied per process family via
`_START_DEDUP_POLICY`; the non-strict half of that policy is pinned here too, so a future blanket
"just make it strict" cannot land silently.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from maezo.gateway.audit import AuditRecord, EmitOnceOutcome
from maezo.tools.mcp_cibseven.transport import (
    STATE_ALREADY_STARTED,
    AgentDecisionProvenance,
    AuditStartSink,
    DedupReportingAuditSink,
    FakeCibSevenTransport,
    ProcessInstance,
    StartDedupGateUnavailableError,
    is_strict_start_dedup,
    start_dedup_key,
    start_process_idempotent,
)
from tests.support.audit_fakes import FakeStartAuditSink

PAGTO = "SP-OP-PAGTO-001"  # the one STRICT family
PAGTO_KEY = "PAGTO-amh-OP-001"
INAD = "SP-OP-INADIMPLENCIA-001"  # a classified NON-strict family
INAD_KEY = "INAD-amh-C-001"


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


# ---------------------------------------------------------------------------
# The `active=true` hole: a COMPLETED instance must not be restartable (strict)
# ---------------------------------------------------------------------------


async def test_strict_family_refuses_to_restart_a_completed_business_key() -> None:
    """THE duplicate-payment defect. The order was released and its instance COMPLETED; a
    re-delivered start finds nothing ACTIVE, but the durable claim proves it already happened."""
    transport = _CountingTransport()
    sink = FakeStartAuditSink()

    first = await _start(transport, sink)
    assert first.already_existed is False
    assert transport.starts == [PAGTO_KEY]

    # The engine finishes the instance: `find_active_instance` now sees NOTHING (active=true).
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
    assert second.state == STATE_ALREADY_STARTED
    assert second.business_key == PAGTO_KEY


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


# ---------------------------------------------------------------------------
# The TOCTOU hole: concurrency-shaped — two racers, exactly one winner
# ---------------------------------------------------------------------------


class _BlockingTransport(_CountingTransport):
    """Forces the TOCTOU interleaving: EVERY racer completes `find_active_instance` (seeing
    nothing) before ANY of them may start. Under the pre-B-3 code both would then start."""

    def __init__(self, racers: int) -> None:
        super().__init__()
        self._all_looked = asyncio.Barrier(racers)
        self.lookups = 0

    async def find_active_instance(self, business_key: str) -> ProcessInstance | None:
        found = await super().find_active_instance(business_key)
        self.lookups += 1
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
    # Exactly one racer wrote a chain link; the loser saw the claim.
    assert len(sink.calls) == 2
    assert sum(1 for r in results if r.already_existed) == 1
    assert sum(1 for r in results if not r.already_existed) == 1


async def test_many_racers_on_a_strict_key_still_produce_exactly_one_engine_start() -> None:
    transport = _BlockingTransport(racers=8)
    sink = FakeStartAuditSink()

    results = await asyncio.gather(*(_start(transport, sink) for _ in range(8)))

    assert transport.starts == [PAGTO_KEY]
    assert transport.lookups >= 8  # every racer really did consult the engine first
    assert sum(1 for r in results if not r.already_existed) == 1


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
# The policy map itself
# ---------------------------------------------------------------------------


def test_pagto_is_strict_and_is_the_only_strict_family() -> None:
    """Pins the deliberate scope: PAGTO (money release) is gated; nothing else is, because the
    other keys either legitimately re-run across time or raise a domain question an engineering
    change may not answer. Widening this set is a REVIEWED decision, not a refactor."""
    from maezo.tools.mcp_cibseven.transport import _START_DEDUP_POLICY

    assert [k for k, strict in _START_DEDUP_POLICY.items() if strict] == [PAGTO]
    assert is_strict_start_dedup(PAGTO) is True


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
    the loop-agnostic adapter the SYNC worker handoff seams use — it fronts no strict family
    today, but it must not become a trap for whichever seam is wired through it next.
    """
    from maezo.gateway.audit_postgres import FreshSinkAuditEmitter, PostgresAuditSink

    assert issubclass(PostgresAuditSink, DedupReportingAuditSink)
    assert issubclass(FreshSinkAuditEmitter, DedupReportingAuditSink)
    assert isinstance(FakeStartAuditSink(), DedupReportingAuditSink)
    assert not isinstance(_HashOnlySink(), DedupReportingAuditSink)


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

    inst = await _start(transport, sink)

    assert inst.state == STATE_ALREADY_STARTED
    assert inst.already_existed is True
