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
from typing import Literal

import pytest

from maezo.a2a import (
    AgentCard,
    Budget,
    DelegationEnvelope,
    DelegationResult,
    HandlerOutput,
    RejectionReason,
    StoredResult,
)
from maezo.a2a.dispatcher import AgentHandler, DelegationDispatcher, FactProducer
from maezo.a2a.facts import TOPIC_COMPLETED, TOPIC_REQUESTED
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


class _FakeIdempotencyStoreNeverSeals:
    """Models a task_id whose FIRST claim is won but never sealed — a retryable handler failure
    (or a crash) that never reaches `complete`, exactly the durable-store window
    A2A-RETRY-REEMITS-REQUESTED-FACT reproduces. Mirrors `PostgresIdempotencyStore`'s real
    contract: `None` on the FIRST call for a `task_id` (fresh claim — the caller executes AND
    emits `requested`); `False` on every SUBSEQUENT call for the SAME `task_id` (the row exists,
    is not 'done', and this fake's `complete` is a no-op, so it never will be — a REDELIVERY the
    caller must still execute, but without re-emitting `requested`)."""

    def __init__(self) -> None:
        self._claimed: set[str] = set()

    async def claim_or_get(self, *, tenant: str, task_id: str) -> StoredResult | Literal[False] | None:
        if task_id in self._claimed:
            return False
        self._claimed.add(task_id)
        return None

    async def complete(self, *, tenant: str, task_id: str, result: DelegationResult) -> None:
        pass  # never seals, by design — this fake models the crash/retryable-failure window


async def test_durable_redelivery_of_unsealed_row_does_not_reemit_requested() -> None:
    """A2A-RETRY-REEMITS-REQUESTED-FACT (durable path): a `task_id` whose row is claimed but never
    sealed (a retryable handler failure, e.g. `StartProcessFailedError`) gets its handler
    RE-EXECUTED on redelivery (retryability preserved, RAF-02) but the `requested` fact fires
    exactly once across BOTH deliveries — not once per delivery.

    Mutation: in `dispatcher._delegate_durable`, treat `stored is False` the same as `stored is
    None` (drop the `skip_requested_fact=True` branch) -> this goes RED (2 `requested` facts for
    1 logical delegation)."""
    calls = 0

    async def _failing_handler(envelope: DelegationEnvelope) -> HandlerOutput:
        nonlocal calls
        calls += 1
        raise RuntimeError("engine indisponivel (probe A2A-RETRY-REEMITS-REQUESTED-FACT)")

    store = _FakeIdempotencyStoreNeverSeals()
    registry = A2ARegistry()
    registry.register(make_card("rafael"))
    producer = RecordingProducer()
    dispatcher = DelegationDispatcher(
        registry=registry,
        handlers={"rafael": _failing_handler},
        audit=FakeAuditSink(),
        facts=FactProducer(producer),
        idempotency=store,
    )

    with pytest.raises(RuntimeError):
        await dispatcher.delegate(_envelope("dur-retry-1"))
    with pytest.raises(RuntimeError):
        await dispatcher.delegate(_envelope("dur-retry-1"))  # redelivery, same task_id

    assert calls == 2, "a reentrega nao reexecutou o handler (retentabilidade RAF-02 quebrada)"
    assert producer.topics() == [TOPIC_REQUESTED], (
        f"reentrega reemitiu {TOPIC_REQUESTED!r} uma segunda vez (topics={producer.topics()!r})"
    )


class _ProducerFailingFirstSend(RecordingProducer):
    """Produtor cujo PRIMEIRO `send` falha (broker/outbox indisponivel) e os seguintes passam.

    Modela a falha real do caminho de producao: `PostgresOutboxFactProducer.send` faz um `INSERT`
    na outbox transacional e a falha PROPAGA por decisao explicita de `a2a/outbox.py`."""

    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        if not self.failed_once:
            self.failed_once = True
            raise RuntimeError("outbox indisponivel (falha da PRIMEIRA emissao)")
        await super().send(topic, value, key=key)


async def test_inmemory_failed_requested_emit_is_reemitted_on_redelivery() -> None:
    """A2A-RETRY-REEMITS-REQUESTED-FACT (caminho em memoria): se a EMISSAO do `requested` falha, a
    reentrega REEMITE — o fato nao se perde.

    A supressao da reemissao (o guard `skip_requested_fact`) so pode se apoiar num fato OBSERVADO.
    `_delegate_inflight` marca `entry.requested_emitted` pelo callback `on_requested_emitted`, que
    `_execute` chama DEPOIS de `_emit(REQUESTED)` retornar. Se a marca fosse escrita antes de
    `_execute` (como numa versao anterior desta correcao), uma emissao falha viraria PERDA
    PERMANENTE: a reentrega veria a marca `True`, pularia a emissao para sempre e publicaria um
    `completed` sem nenhum `requested` antes — uma duplicata o consumidor deduplica, uma perda nao.

    Mutacao que leva este teste a RED: em `_delegate_inflight`, voltar a marcar
    `entry.requested_emitted = True` ANTES da chamada a `_execute` (em vez de pelo callback)."""
    calls = 0

    async def _handler(envelope: DelegationEnvelope) -> HandlerOutput:
        nonlocal calls
        calls += 1
        return HandlerOutput(output_ref="fhir://Task/ok")

    producer = _ProducerFailingFirstSend()
    dispatcher, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": _handler}, producer=producer
    )

    with pytest.raises(RuntimeError):
        await dispatcher.delegate(_envelope("mem-emit-falha-1"))
    assert producer.topics() == [], (
        f"a emissao falhou; nada deveria ter sido publicado ({producer.topics()!r})"
    )
    assert calls == 0, "a emissao do `requested` precede o handler: ele nao deveria ter rodado"

    result = await dispatcher.delegate(_envelope("mem-emit-falha-1"))  # reentrega do MESMO task_id

    assert result.success, "a reentrega apos falha de emissao deveria executar normalmente"
    assert calls == 1, "a reentrega nao executou o handler"
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED], (
        f"o fato `requested` foi PERDIDO: a reentrega publicou {producer.topics()!r} "
        f"— um `completed` sem `requested` antes quebra a trilha de auditoria A2A"
    )
