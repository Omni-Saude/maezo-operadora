"""ATTACK: crash-before-complete — a process death re-executes the delegation handler.

Threat (leg-2 disclosed window, `outbox.py` "Failure posture" + `dispatcher._delegate_durable`):
`_execute` emits the COMPLETED fact and returns BEFORE `store.complete()` seals the idempotency row.
A crash in that window leaves the row 'processing'. On retry, `claim_or_get` finds a 'processing'
row, polls, times out, and returns `None` (`idempotency.py:289-297`) — so the dispatcher
RE-EXECUTES the handler. If that re-execution repeats the downstream effect, the harm is a DOUBLE
process start / duplicated dossier.

Two defenses, at two layers (see `adversary.py`), and the honest picture needs both:

  Layer 1 — the durable store's seal: on the SEALED path (complete ran) a redelivery REPLAYS and the
            handler never re-runs. But in THIS window the row is 'processing', so Layer 1 cannot
            prevent re-execution — that is the disclosed residual, not a bug.
  Layer 2 — the engine business-key (`start_process_idempotent`): the LOAD-BEARING defense in this
            window. A re-executed handler presents the SAME stable business key, so the engine fires
            the effect AT MOST ONCE. This is what makes the disclosed re-execution SAFE.

RED controls:
  * crash window  -> neuter Layer 2 (`NonIdempotentEngine`): the re-executed handler DOUBLE-STARTS
    the process. Consequence-level harm, demonstrated red->green.
  * sealed path   -> neuter Layer 1 (`NeuteredClaimStore`): "neuter idempotency claim -> double
    execution" — the brief's literal RED for the seal.

And the outbox at-least-once redelivery re-emits the COMPLETED fact with a STABLE dedup key, so a
consumer can collapse the duplicate (leg-2 proved key stability; here it is a tested expectation in
the crash context).
"""

from __future__ import annotations

import json

import pytest

from maezo.a2a import (
    A2ARegistry,
    Budget,
    DelegationDispatcher,
    DelegationEnvelope,
    FactProducer,
    fact_dedup_key,
)
from maezo.a2a.facts import TOPIC_COMPLETED
from maezo.tools.workers.harness import FakeAuditSink
from tests.unit.a2a.attacks.adversary import (
    BusinessKeyEngine,
    BusinessKeyHandler,
    EffectSink,
    ModelIdempotencyStore,
    NeuteredClaimStore,
    NonIdempotentEngine,
    real_pk,
)
from tests.unit.a2a.fakes import FakeAgentHandler, RecordingProducer, make_card

_TENANT = "amh"
_TASK_ID = "task-crash-before-complete-1"
_TASK_TYPE = "authorization.analyze"
_BUSINESS_KEY = f"bk:{_TENANT}:{_TASK_ID}"  # what BusinessKeyHandler derives — stable across retries


def _envelope() -> DelegationEnvelope:
    return DelegationEnvelope.root(
        task_id=_TASK_ID,
        task_type=_TASK_TYPE,
        origin="helena",
        target="rafael",
        tenant=_TENANT,
        budget=Budget(tokens=64, time_ms=60_000, cost_per_hop=1),
        payload_ref="fhir://Coverage/crash-1",
    )


def _dispatcher(
    store: object, handler: object, *, producer: RecordingProducer | None = None
) -> DelegationDispatcher:
    registry = A2ARegistry()
    registry.register(make_card("rafael", tenant=_TENANT, accepted=frozenset({_TASK_TYPE})))
    return DelegationDispatcher(
        registry=registry,
        handlers={"rafael": handler},  # type: ignore[dict-item]
        audit=FakeAuditSink(),
        facts=FactProducer(producer if producer is not None else RecordingProducer()),
        idempotency=store,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Crash window — Layer 2 (engine business-key) is load-bearing
# ---------------------------------------------------------------------------


async def test_crash_before_complete_business_key_prevents_double_effect() -> None:
    """DEFENSE HOLDS (Layer 2). The pre-crash execution already started the process; the row is left
    'processing' (complete never ran). The retry RE-EXECUTES the handler (the disclosed window) — but
    the engine business-key makes the second start a no-op. Exactly ONE process start survives."""
    sink = EffectSink()
    engine = BusinessKeyEngine(sink)
    engine.start_process_idempotent(_BUSINESS_KEY)  # (1) the pre-crash effect
    assert sink.count == 1

    store = ModelIdempotencyStore(key_fn=real_pk)
    store.preseed_processing(tenant=_TENANT, task_id=_TASK_ID)  # crashed before complete -> 'processing'

    handler = BusinessKeyHandler(engine)
    result = await _dispatcher(store, handler).delegate(_envelope())  # (2) retry

    assert handler.calls == 1  # the handler WAS re-executed (the disclosed window is real)
    assert sink.count == 1  # ...but NO double effect — the business key held (the safety)
    assert result.success


async def test_red_control_crash_before_complete_without_business_key_double_starts() -> None:
    """RED CONTROL — the attack SUCCEEDS when Layer 2 is neutered. Same crash window, but the
    engine's business-key dedup is removed: the re-executed handler starts the process a SECOND time.
    Two process starts for one delegation — the observable harm. This is the demonstrated
    crash-before-complete neuter (red->green)."""
    sink = EffectSink()
    engine = NonIdempotentEngine(sink)  # RED: engine business-key NEUTERED
    engine.start_process_idempotent(_BUSINESS_KEY)  # (1) the pre-crash effect
    assert sink.count == 1

    store = ModelIdempotencyStore(key_fn=real_pk)
    store.preseed_processing(tenant=_TENANT, task_id=_TASK_ID)

    handler = BusinessKeyHandler(engine)
    await _dispatcher(store, handler).delegate(_envelope())  # (2) retry

    assert handler.calls == 1  # re-executed
    assert sink.count == 2  # THE HARM: double process start / duplicated dossier


# ---------------------------------------------------------------------------
# Sealed path — Layer 1 (the durable claim) prevents re-execution
# ---------------------------------------------------------------------------


async def test_sealed_row_replays_and_never_re_executes_the_handler() -> None:
    """DEFENSE HOLDS (Layer 1). When complete DID seal the row 'done', a redelivery REPLAYS: the
    handler is never re-executed, so the effect cannot repeat even for a NON-idempotent engine."""
    sink = EffectSink()
    handler = BusinessKeyHandler(NonIdempotentEngine(sink))  # deliberately non-idempotent...
    store = ModelIdempotencyStore(key_fn=real_pk)
    dispatcher = _dispatcher(store, handler)

    first = await dispatcher.delegate(_envelope())
    second = await dispatcher.delegate(_envelope())  # redelivery of the SAME task_id

    assert first.idempotent_replay is False
    assert second.idempotent_replay is True  # ...but the seal made it a replay
    assert handler.calls == 1  # the handler ran exactly once
    assert sink.count == 1  # so exactly one effect, despite the non-idempotent engine


async def test_red_control_neuter_the_claim_double_executes_the_handler() -> None:
    """RED CONTROL — "neuter idempotency claim -> double execution" (the brief's literal RED for the
    seal). With `claim_or_get` neutered to never dedup, the redelivery RE-EXECUTES the handler and
    the non-idempotent engine double-starts the process."""
    sink = EffectSink()
    handler = BusinessKeyHandler(NonIdempotentEngine(sink))
    dispatcher = _dispatcher(NeuteredClaimStore(), handler)  # RED: durable claim NEUTERED

    await dispatcher.delegate(_envelope())
    await dispatcher.delegate(_envelope())  # redelivery

    assert handler.calls == 2  # THE HARM: handler executed twice
    assert sink.count == 2  # ...and the effect fired twice


# ---------------------------------------------------------------------------
# The outbox redelivers the fact with a STABLE dedup key (at-least-once, collapsible)
# ---------------------------------------------------------------------------


async def test_re_executed_delegation_re_emits_completed_fact_with_stable_dedup_key() -> None:
    """The crash-retry re-emits the COMPLETED fact (at-least-once), and its dedup key — computed via
    the real `fact_dedup_key` from the fact's own fields — is the STABLE
    `{tenant}:a2a:delegate:{task_id}:completed`, so a consumer can collapse the duplicate. This ties
    the crash window to the outbox's redelivery contract."""
    sink = EffectSink()
    engine = BusinessKeyEngine(sink)
    engine.start_process_idempotent(_BUSINESS_KEY)
    store = ModelIdempotencyStore(key_fn=real_pk)
    store.preseed_processing(tenant=_TENANT, task_id=_TASK_ID)
    producer = RecordingProducer()

    await _dispatcher(store, BusinessKeyHandler(engine), producer=producer).delegate(_envelope())

    completed = [value for (topic, value, _key) in producer.sent if topic == TOPIC_COMPLETED]
    assert len(completed) == 1, "the retry re-emitted the COMPLETED fact (at-least-once)"
    decoded = json.loads(completed[0].decode("utf-8"))
    key = fact_dedup_key(tenant=decoded["tenant"], task_id=decoded["task_id"], kind=decoded["kind"])
    assert key == f"{_TENANT}:a2a:delegate:{_TASK_ID}:completed"  # stable, collapsible


async def test_non_vacuity_the_dispatcher_uses_the_durable_path_when_a_store_is_present() -> None:
    """Guards against a vacuous crash test: prove the dispatcher actually takes the DURABLE path
    (not the in-memory `_inflight` one) when a store is injected — otherwise `preseed_processing`
    above would be exercising nothing. A store whose 'processing' row is preseeded makes a FRESH
    dispatcher instance (no in-memory state) re-execute, which only the durable path consults."""
    sink = EffectSink()
    store = ModelIdempotencyStore(key_fn=real_pk)
    store.preseed_processing(tenant=_TENANT, task_id=_TASK_ID)
    handler = FakeAgentHandler(output_ref="process://durable-path")
    # A brand-new dispatcher: its in-memory _inflight is empty, so ONLY the durable store can be
    # consulted here — and it returns None for a 'processing' row, so the handler runs.
    result = await _dispatcher(store, handler).delegate(_envelope())
    assert handler.call_count == 1
    assert result.output_ref == "process://durable-path"
    _ = sink


if __name__ == "__main__":  # pragma: no cover - convenience for the R1 verifier
    raise SystemExit(pytest.main([__file__, "-v"]))
