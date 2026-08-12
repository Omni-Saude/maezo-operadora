"""ATTACK: duplicate fact delivery — the outbox is AT-LEAST-ONCE, so duplicates reach a naive consumer.

Threat (the consumer-side contract leg-2 left to the downstream): the transactional outbox
guarantees at-least-once, not exactly-once (`outbox.py` "at-least-once by lease expiry" +
`test_outbox_live_pg.py::test_an_unmarked_claim_is_reclaimed_after_its_lease_expires`). A relay that
crashes between publishing and marking a row re-publishes the SAME fact after the lease expires. An
operator who assumes exactly-once DOWNSTREAM is wrong, and the harm is a duplicated downstream
effect (a doubled reactive action off one delegation).

What the outbox DOES provide is a STABLE dedup key (`fact_dedup_key`, a pure function of the payload)
that a consumer can use to collapse the redelivery. This suite makes the at-least-once / consumer-
dedup contract an EXPLICIT, tested expectation:

  * a consumer that USES the stable key collapses the duplicate (defense holds);
  * a consumer that IGNORES it double-processes (RED — the disclosed at-least-once consequence);
  * an UNSTABLE key defeats even a dedup-aware consumer (RED — why the key must be payload-derived,
    not attempt/timestamp-derived).

The redelivery is driven from the REAL fact encoding (`build_fact(...).to_value()`) and the REAL
dedup-key derivation (`outbox_row_params`/`fact_dedup_key`), so the contract binds against the
actual bytes a relay republishes, not a stand-in.
"""

from __future__ import annotations

import pytest

from maezo.a2a import fact_dedup_key
from maezo.a2a.facts import DelegationFactKind, build_fact
from maezo.a2a.outbox import outbox_row_params
from tests.unit.a2a.attacks.adversary import DedupConsumer, NoDedupConsumer

_TENANT = "amh"
_TASK_ID = "task-dup-fact-1"
_TOPIC = "agents.events.delegation.completed"


def _fact_value() -> bytes:
    return build_fact(
        DelegationFactKind.COMPLETED,
        task_id=_TASK_ID,
        task_type="authorization.analyze",
        tenant=_TENANT,
        origin="helena",
        target="rafael",
        delegation_chain=("helena", "rafael"),
        output_ref="process://RECURSO-dup-1",
    ).to_value()


def _redeliver_twice(consumer: DedupConsumer | NoDedupConsumer) -> None:
    """Simulate the at-least-once redelivery: a crashed relay re-publishes the SAME row (identical
    payload => identical dedup_key, via the REAL `outbox_row_params`) after its lease expires."""
    value = _fact_value()
    _tenant, dedup_key, _topic, _pk, payload = outbox_row_params(_TOPIC, value, key=b"amh")
    consumer.consume(dedup_key=dedup_key, payload=payload)  # first delivery
    consumer.consume(dedup_key=dedup_key, payload=payload)  # lease-expiry REDELIVERY (same key)


# ---------------------------------------------------------------------------
# The consumer contract — collapse vs duplicate
# ---------------------------------------------------------------------------


def test_consumer_that_dedups_on_the_stable_key_collapses_the_redelivery() -> None:
    """DEFENSE HOLDS. A consumer that dedups on the stable `fact_dedup_key` sees the at-least-once
    redelivery as ONE downstream effect — the duplicate is collapsed."""
    consumer = DedupConsumer()
    _redeliver_twice(consumer)
    assert len(consumer.effects) == 1  # exactly one downstream effect for one logical fact


def test_red_control_a_consumer_without_dedup_double_processes_the_redelivery() -> None:
    """RED CONTROL — the DISCLOSED at-least-once consequence. A consumer that ignores the dedup key
    processes the redelivery as a fresh fact: TWO downstream effects off one delegation. This is the
    contract made explicit — at-least-once means a naive consumer WILL see duplicates."""
    consumer = NoDedupConsumer()
    _redeliver_twice(consumer)
    assert len(consumer.effects) == 2  # THE HARM: duplicate reached downstream


# ---------------------------------------------------------------------------
# Why the key must be STABLE (payload-derived), not attempt/timestamp-derived
# ---------------------------------------------------------------------------


def test_the_dedup_key_is_stable_across_redelivery_by_construction() -> None:
    """The enabling mechanism (leg-2 proved it; here it is the consumer-contract anchor): the REAL
    `outbox_row_params` derives the SAME dedup key from the SAME payload on every redelivery, and it
    equals the documented `{tenant}:a2a:delegate:{task_id}:{kind}` (outbox.py:245)."""
    value = _fact_value()
    _t1, key_first, _tp1, _pk1, _p1 = outbox_row_params(_TOPIC, value, key=b"amh")
    _t2, key_redelivered, _tp2, _pk2, _p2 = outbox_row_params(_TOPIC, value, key=b"amh")
    assert key_first == key_redelivered == f"{_TENANT}:a2a:delegate:{_TASK_ID}:completed"


def test_red_control_an_unstable_key_defeats_even_a_dedup_aware_consumer() -> None:
    """RED CONTROL — if the dedup key were NOT payload-stable (e.g. derived from the delivery
    attempt count, as a naive relay might), a dedup-aware consumer STILL double-processes, because
    the two deliveries carry different keys. This is why the key is a pure function of the payload."""
    consumer = DedupConsumer()
    value = _fact_value()
    _tenant, _stable_key, _topic, _pk, payload = outbox_row_params(_TOPIC, value, key=b"amh")

    def _unstable_key(attempt: int) -> str:
        return f"{fact_dedup_key(tenant=_TENANT, task_id=_TASK_ID, kind='completed')}:attempt:{attempt}"

    consumer.consume(dedup_key=_unstable_key(1), payload=payload)  # first delivery
    consumer.consume(dedup_key=_unstable_key(2), payload=payload)  # redelivery, DIFFERENT key
    assert len(consumer.effects) == 2  # THE HARM: the dedup-aware consumer could not collapse it


# ---------------------------------------------------------------------------
# Non-vacuity — a repeated dedup_key is a LEGITIMATE, expected event, never an error
# ---------------------------------------------------------------------------


def test_at_least_once_means_a_repeated_dedup_key_is_expected_not_an_error() -> None:
    """The contract, stated as an expectation: two deliveries carrying the SAME dedup key is the
    normal at-least-once event a crashed-relay lease-expiry produces
    (`test_outbox_live_pg.py::test_an_unmarked_claim_is_reclaimed_after_its_lease_expires`). The
    dedup-aware consumer treats the second as a duplicate to collapse — it never raises, never drops
    the FIRST, and processes exactly once."""
    consumer = DedupConsumer()
    value = _fact_value()
    _tenant, dedup_key, _topic, _pk, payload = outbox_row_params(_TOPIC, value, key=b"amh")
    assert consumer.consume(dedup_key=dedup_key, payload=payload) == "processed"
    assert consumer.consume(dedup_key=dedup_key, payload=payload) == "duplicate_collapsed"
    assert consumer.effects == [payload]  # the first delivery's payload, exactly once


if __name__ == "__main__":  # pragma: no cover - convenience for the R1 verifier
    raise SystemExit(pytest.main([__file__, "-v"]))
