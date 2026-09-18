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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

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
        self._requested: set[str] = set()
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

    async def requested_emitted(self, *, tenant: str, task_id: str) -> bool:
        return task_id in self._requested

    async def mark_requested(self, *, tenant: str, task_id: str) -> None:
        self._requested.add(task_id)


def _dispatcher_with_store(
    store: IdempotencyStore,
    *,
    cards: list[AgentCard],
    handlers: dict[str, AgentHandler],
    producer: RecordingProducer | None = None,
) -> DelegationDispatcher:
    registry = A2ARegistry()
    for card in cards:
        registry.register(card)
    return DelegationDispatcher(
        registry=registry,
        handlers=handlers,
        audit=FakeAuditSink(),
        facts=FactProducer(producer if producer is not None else RecordingProducer()),
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


class _AtomicRetryTransactions:
    """Offline dispatcher seam only; real PG atomicity is covered by the live PG suites.

    Unlike the orphan False sentinel, an existing claim never implies a requested fact.
    This double records the enlisted admission/marker ordering and rolls back failed admission.
    """

    def __init__(self, *, claimed: bool = False, fail_send: bool = False) -> None:
        self.claimed = claimed
        self.marked = False
        self.fail_send = fail_send
        self.topics: list[str] = []
        self.events: list[str] = []

    @asynccontextmanager
    async def transaction(self, tenant: str) -> AsyncIterator[_AtomicRetryTransactions]:
        assert tenant == "amh"
        before = self.claimed, self.marked, list(self.topics)
        self.events.append("begin")
        try:
            yield self
        except BaseException:
            self.claimed, self.marked, self.topics = before
            self.events.append("rollback")
            raise
        else:
            self.events.append("commit")

    async def claim(self, task_id: str) -> tuple[bool, dict[str, Any]]:
        fresh = not self.claimed
        self.claimed = True
        self.events.append("claim")
        return fresh, {"status": "processing", "result": None, "requested_enqueued_at": self.marked or None}

    async def requested_exists(self, task_id: str, row: Any) -> bool:
        return self.marked

    async def mark_requested(self, task_id: str) -> None:
        assert TOPIC_REQUESTED in self.topics, "marker cannot precede observed enqueue"
        self.events.append("mark")
        self.marked = True

    async def emit_once(self, record: Any, *, dedup_key: str) -> str:
        self.events.append("audit")
        return "unit-audit-hash"

    async def send(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        if self.fail_send:
            self.fail_send = False
            raise RuntimeError("enqueue unavailable")
        self.events.append("enqueue")
        self.topics.append(topic)

    async def poll(self, task_id: str) -> None:
        return None


@pytest.mark.parametrize("preexisting_claim", [False, True])
async def test_durable_redelivery_of_unsealed_row_does_not_reemit_requested(
    preexisting_claim: bool,
) -> None:
    """Original requested-once/retry obligation, on the stronger atomic admission path.

    A pre-existing row WITHOUT a marker still enqueues requested; two handler failures
    retain exactly one observed requested. No claim of actual database persistence here.
    """
    calls = 0

    async def _failing_handler(envelope: DelegationEnvelope) -> HandlerOutput:
        nonlocal calls
        calls += 1
        raise RuntimeError("engine unavailable")

    transactions = _AtomicRetryTransactions(claimed=preexisting_claim)
    dispatcher, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": _failing_handler}
    )
    dispatcher._transactions = transactions  # type: ignore[assignment]
    for _ in range(2):
        with pytest.raises(RuntimeError, match="engine unavailable"):
            await dispatcher.delegate(_envelope("dur-retry-1"))
    assert calls == 2
    assert transactions.topics == [TOPIC_REQUESTED]
    assert transactions.marked
    assert transactions.events[:6] == ["begin", "claim", "audit", "enqueue", "mark", "commit"]


async def test_atomic_failed_enqueue_rolls_back_and_retries_before_handler() -> None:
    calls = 0

    async def failing_handler(envelope: DelegationEnvelope) -> HandlerOutput:
        nonlocal calls
        calls += 1
        raise RuntimeError("handler retry")

    transactions = _AtomicRetryTransactions(fail_send=True)
    dispatcher, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": failing_handler}
    )
    dispatcher._transactions = transactions  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="enqueue unavailable"):
        await dispatcher.delegate(_envelope("atomic-enqueue-retry"))
    assert not transactions.claimed and not transactions.marked
    assert transactions.topics == []
    assert transactions.events[-1] == "rollback"
    assert calls == 0
    with pytest.raises(RuntimeError, match="handler retry"):
        await dispatcher.delegate(_envelope("atomic-enqueue-retry"))
    assert calls == 1
    assert transactions.marked
    assert transactions.topics == [TOPIC_REQUESTED]


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


# --- Seam durável (`idempotency=` sem `transactions=`) ------------------------------------------
#
# A2A-RETRY-REEMITS-REQUESTED-FACT, a metade que o caminho atômico NÃO cobre. `_delegate_atomic`
# (raízes de produção) já fecha o buraco com o marcador durável `requested_enqueued_at` da migração
# 0011, inscrito na MESMA transação do enfileiramento na outbox. O seam `_delegate_durable` —
# `IdempotencyStore` injetada sem `PostgresDelegationTransactions` — não tinha marcador nenhum:
# `claim_or_get` devolvia `None` tanto para uma reivindicação INÉDITA quanto para a REENTREGA de
# uma linha já reivindicada e nunca selada, então a segunda entrega reemitia `requested`.


class _FakeSeamStoreNeverSeals:
    """Seam durável cuja linha é reivindicada e NUNCA selada.

    Modela exatamente a janela do defeito: a primeira tentativa ganha a reivindicação, o handler
    falha de forma RETENTÁVEL (a exceção salta por cima de `store.complete`) e a linha fica
    `processing` para sempre. Espelha o contrato do `PostgresIdempotencyStore` real:

      - `claim_or_get` -> `None` na primeira chamada (reivindicação vencida) E nas seguintes (a
        linha existe, não está `done`, e o poll limitado expira) — é justamente essa
        indistinguibilidade que o marcador durável resolve, e NÃO um sentinela de reivindicação;
      - `requested_emitted`/`mark_requested` -> o marcador `requested_enqueued_at` (migração 0011),
        escrito SOMENTE depois de `_emit(REQUESTED)` retornar.
    """

    def __init__(self) -> None:
        self._claimed: set[str] = set()
        self._requested: set[str] = set()

    async def claim_or_get(self, *, tenant: str, task_id: str) -> StoredResult | None:
        self._claimed.add(task_id)
        return None  # nunca sela: sempre "execute" — fresh ou reentrega, indistinguíveis aqui

    async def complete(self, *, tenant: str, task_id: str, result: DelegationResult) -> None:
        pass  # nunca sela, por construção — este fake É a janela

    async def requested_emitted(self, *, tenant: str, task_id: str) -> bool:
        return task_id in self._requested

    async def mark_requested(self, *, tenant: str, task_id: str) -> None:
        self._requested.add(task_id)


async def test_durable_seam_redelivery_of_unsealed_row_does_not_reemit_requested() -> None:
    """A2A-RETRY-REEMITS-REQUESTED-FACT no seam `_delegate_durable`.

    Uma `task_id` reivindicada mas nunca selada (falha RETENTÁVEL do handler, canal RAF-02) tem o
    handler REEXECUTADO na reentrega — retentabilidade preservada — mas o fato `requested` sai UMA
    vez ao longo das DUAS entregas, não uma por entrega.

    Mutação que leva a RED: em `_delegate_durable`, chamar `self._execute(envelope)` sem consultar
    `store.requested_emitted` (o estado de `main` antes desta correção) -> 2 fatos `requested`
    para 1 delegação lógica.
    """
    calls = 0

    async def _failing_handler(envelope: DelegationEnvelope) -> HandlerOutput:
        nonlocal calls
        calls += 1
        raise RuntimeError("engine indisponivel (sonda A2A-RETRY-REEMITS-REQUESTED-FACT)")

    producer = RecordingProducer()
    store = _FakeSeamStoreNeverSeals()
    dispatcher = _dispatcher_with_store(
        store, cards=[make_card("rafael")], handlers={"rafael": _failing_handler}, producer=producer
    )

    for _ in range(2):
        with pytest.raises(RuntimeError, match="engine indisponivel"):
            await dispatcher.delegate(_envelope("seam-retry-1"))

    assert calls == 2, "a reentrega nao reexecutou o handler (retentabilidade RAF-02 quebrada)"
    assert producer.topics() == [TOPIC_REQUESTED], (
        f"a reentrega reemitiu {TOPIC_REQUESTED!r} uma segunda vez (topics={producer.topics()!r})"
    )


async def test_durable_seam_failed_requested_emit_is_reemitted_on_redelivery() -> None:
    """O marcador durável do seam só pode registrar uma emissão OBSERVADA, nunca uma reivindicação.

    Se `_emit(REQUESTED)` falha, o marcador NÃO é escrito e a reentrega REEMITE: uma duplicata o
    consumidor colapsa pela `fact_dedup_key`, uma PERDA ninguém recupera. É esta assimetria que
    torna o marcador (fato observado) correto e um sentinela de reivindicação (fato inferido)
    errado — a mera existência da linha nasce ANTES da emissão.

    Mutação que leva a RED: em `_delegate_durable`, chamar `store.mark_requested` antes de
    `self._execute` em vez de pelo callback `on_requested_emitted` -> o `requested` some para
    sempre e sai um `completed` sem `requested` antes.
    """
    calls = 0

    async def _handler(envelope: DelegationEnvelope) -> HandlerOutput:
        nonlocal calls
        calls += 1
        return HandlerOutput(output_ref="fhir://Task/ok")

    producer = _ProducerFailingFirstSend()
    store = _FakeSeamStoreNeverSeals()
    dispatcher = _dispatcher_with_store(
        store, cards=[make_card("rafael")], handlers={"rafael": _handler}, producer=producer
    )

    with pytest.raises(RuntimeError, match="outbox indisponivel"):
        await dispatcher.delegate(_envelope("seam-emit-falha-1"))
    assert producer.topics() == [], f"a emissao falhou; nada deveria ter saido ({producer.topics()!r})"
    assert calls == 0, "a emissao do `requested` precede o handler: ele nao deveria ter rodado"

    result = await dispatcher.delegate(_envelope("seam-emit-falha-1"))  # reentrega do MESMO task_id

    assert result.success, "a reentrega apos falha de emissao deveria executar normalmente"
    assert calls == 1, "a reentrega nao executou o handler"
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED], (
        f"o fato `requested` foi PERDIDO: a reentrega publicou {producer.topics()!r} "
        f"— um `completed` sem `requested` antes quebra a trilha de auditoria A2A"
    )
