"""Guard 4 (ADR-0003): task_id idempotency — re-delivery never re-executes the handler.

Ported (v2-adapted) from the donor `Maezo-Healthcare-Plan` reference implementation's
`tests/unit/a2a/test_idempotency.py` as part of the T2.4 A2A W2 build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §5 W2. Covers the dispatcher's IN-MEMORY `_inflight`
path (no `idempotency=` store injected) plus its integration with a FAKE durable `IdempotencyStore`
(no Postgres) — the real Postgres-backed store's SQL mapping is unit-tested separately in
`test_idempotency_store.py`.
"""

from __future__ import annotations

import asyncio

from maezo.a2a import AgentCard, Budget, DelegationEnvelope, DelegationResult, RejectionReason, StoredResult
from maezo.a2a.dispatcher import AgentHandler, DelegationDispatcher, FactProducer
from maezo.a2a.idempotency import IdempotencyStore
from maezo.a2a.registry import A2ARegistry
from maezo.tools.workers.harness import FakeAuditSink

from .fakes import FakeAgentHandler, RecordingProducer, build_test_dispatcher, make_card


def _envelope(task_id: str = "t1", *, task_type: str = "authorization.analyze") -> DelegationEnvelope:
    return DelegationEnvelope.root(
        task_id=task_id,
        task_type=task_type,
        origin="helena",
        target="rafael",
        tenant="amh",
        budget=Budget(tokens=100, time_ms=100),
        payload_ref="fhir://Patient/abc",
    )


# --- In-memory `_inflight` path (idempotency=None, the default) --------------------------------


async def test_redelivery_returns_prior_result_without_reexecuting() -> None:
    handler = FakeAgentHandler(output_ref="fhir://Task/result")
    dispatcher, _, _ = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})

    first = await dispatcher.delegate(_envelope("t1"))
    second = await dispatcher.delegate(_envelope("t1"))  # same task_id

    assert first.success and first.output_ref == "fhir://Task/result"
    assert first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert second.output_ref == first.output_ref
    assert handler.call_count == 1  # the handler ran ONCE


async def test_distinct_task_ids_execute_independently() -> None:
    handler = FakeAgentHandler()
    dispatcher, _, _ = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})
    await dispatcher.delegate(_envelope("t1"))
    await dispatcher.delegate(_envelope("t2"))
    assert handler.call_count == 2


async def test_concurrent_redelivery_executes_once() -> None:
    """Concurrent delivery of the same task_id: the lock guarantees a single execution."""
    handler = FakeAgentHandler()
    dispatcher, _, _ = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})
    results = await asyncio.gather(
        dispatcher.delegate(_envelope("t1")),
        dispatcher.delegate(_envelope("t1")),
        dispatcher.delegate(_envelope("t1")),
    )
    assert all(r.success for r in results)
    assert handler.call_count == 1
    assert sum(1 for r in results if r.idempotent_replay) == 2


async def test_rejection_is_also_cached_and_replayed() -> None:
    """A rejection is terminal too (ADR-0003: rejection never retries) — replay returns it as-is."""
    dispatcher, _, _ = build_test_dispatcher(cards=[], handlers={})  # unknown target
    first = await dispatcher.delegate(_envelope("t1"))
    second = await dispatcher.delegate(_envelope("t1"))
    assert first.success is False
    assert first.rejection_reason is RejectionReason.UNKNOWN_TARGET
    assert second.idempotent_replay is True
    assert second.rejection_reason is RejectionReason.UNKNOWN_TARGET


# --- Durable path (fake in-memory IdempotencyStore, satisfying the Protocol) --------------------


class _FakeIdempotencyStore:
    """In-memory store satisfying `IdempotencyStore` (atomic claim + complete) — no Postgres."""

    def __init__(self) -> None:
        self._done: dict[str, StoredResult] = {}
        self._claimed: set[str] = set()
        self.claim_calls = 0

    async def claim_or_get(self, *, tenant: str, task_id: str) -> StoredResult | None:
        self.claim_calls += 1
        if task_id in self._done:
            return self._done[task_id]
        if task_id in self._claimed:
            return None
        self._claimed.add(task_id)
        return None

    async def complete(self, *, tenant: str, task_id: str, result: DelegationResult) -> None:
        self._done[task_id] = StoredResult.from_result(result)


def _dispatcher_with_store(
    store: IdempotencyStore, *, cards: list[AgentCard], handlers: dict[str, AgentHandler]
) -> DelegationDispatcher:
    registry = A2ARegistry()
    for card in cards:
        registry.register(card)
    return DelegationDispatcher(
        registry=registry,
        handlers=handlers,
        audit=FakeAuditSink(),
        facts=FactProducer(RecordingProducer()),
        idempotency=store,
    )


async def test_durable_store_replay_does_not_reexecute_handler() -> None:
    handler = FakeAgentHandler(output_ref="fhir://Task/durable")
    store = _FakeIdempotencyStore()
    dispatcher = _dispatcher_with_store(store, cards=[make_card("rafael")], handlers={"rafael": handler})

    env = _envelope("dur-1")
    first = await dispatcher.delegate(env)
    second = await dispatcher.delegate(env)  # same task_id -> durable replay

    assert first.success and first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert second.output_ref == first.output_ref
    assert handler.call_count == 1  # the handler ran ONCE (durable Guard 4)
    assert store.claim_calls == 2


async def test_durable_store_persists_rejection_as_terminal() -> None:
    """A rejection is terminal (never retried): replay returns the same rejection."""
    handler = FakeAgentHandler()
    store = _FakeIdempotencyStore()
    card = make_card("rafael", accepted=frozenset({"authorization.analyze"}))
    dispatcher = _dispatcher_with_store(store, cards=[card], handlers={"rafael": handler})

    env = _envelope("dur-rej", task_type="nip.instruct")
    first = await dispatcher.delegate(env)  # rafael doesn't accept nip.instruct -> rejection
    second = await dispatcher.delegate(env)  # replay of the terminal rejection

    assert first.success is False
    assert second.idempotent_replay is True
    assert second.success is False
    assert second.rejection_reason is RejectionReason.TASK_TYPE_NOT_ACCEPTED
    assert handler.call_count == 0
