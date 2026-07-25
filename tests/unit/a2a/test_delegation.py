"""Unit tests for maezo.a2a.delegation — DelegationEnvelope/Budget guards (ADR-0003).

Ported (v2-adapted) from the donor `Maezo-Healthcare-Plan` reference implementation's structural
guard coverage (`delegation.py`'s own docstrings + `tests/unit/a2a/test_dispatcher.py`'s envelope
construction) as part of the T2.4 A2A W2 build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §1.2/§5. This is the FULL unit coverage of the 4
ADR-0003 anti-loop guards at the envelope layer (budget, depth/hops, cycle, and the PHI-ref guard);
Guard 4 (task_id idempotency) is exercised at the dispatcher layer in `test_idempotency.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from maezo.a2a import (
    Budget,
    BudgetExhaustedError,
    CyclicDelegationError,
    DelegationEnvelope,
    DelegationError,
    MaxHopsExceededError,
)
from maezo.a2a.delegation import origin_of


def _budget(**kw: object) -> Budget:
    base: dict[str, object] = {"tokens": 100, "time_ms": 100}
    base.update(kw)
    return Budget(**base)  # type: ignore[arg-type]


def _root(**kw: object) -> DelegationEnvelope:
    base: dict[str, object] = {
        "task_id": "t1",
        "task_type": "authorization.analyze",
        "origin": "helena",
        "target": "rafael",
        "tenant": "amh",
        "budget": _budget(),
        "payload_ref": "fhir://Patient/abc",
    }
    base.update(kw)
    return DelegationEnvelope.root(**base)  # type: ignore[arg-type]


# --- Budget ---------------------------------------------------------------------------------


class TestBudget:
    def test_charge_decrements_tokens_and_time(self) -> None:
        budget = Budget(tokens=10, time_ms=10, cost_per_hop=3)
        charged = budget.charge()
        assert charged.tokens == 7
        assert charged.time_ms == 7
        # Immutable: the original is unchanged.
        assert budget.tokens == 10

    def test_exhausted_when_tokens_below_cost(self) -> None:
        budget = Budget(tokens=2, time_ms=100, cost_per_hop=3)
        assert budget.exhausted is True

    def test_exhausted_when_time_below_cost(self) -> None:
        budget = Budget(tokens=100, time_ms=2, cost_per_hop=3)
        assert budget.exhausted is True

    def test_not_exhausted_when_exactly_enough(self) -> None:
        budget = Budget(tokens=3, time_ms=3, cost_per_hop=3)
        assert budget.exhausted is False

    def test_charge_raises_when_exhausted(self) -> None:
        budget = Budget(tokens=1, time_ms=100, cost_per_hop=3)
        with pytest.raises(BudgetExhaustedError):
            budget.charge()

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(DelegationError):
            Budget(tokens=-1, time_ms=10)

    def test_negative_time_ms_rejected(self) -> None:
        with pytest.raises(DelegationError):
            Budget(tokens=10, time_ms=-1)

    def test_non_positive_cost_per_hop_rejected(self) -> None:
        with pytest.raises(DelegationError):
            Budget(tokens=10, time_ms=10, cost_per_hop=0)


# --- DelegationEnvelope.root ------------------------------------------------------------------


class TestRoot:
    def test_root_builds_two_hop_chain(self) -> None:
        env = _root()
        assert env.delegation_chain == ("helena", "rafael")
        assert env.origin == "helena"
        assert env.target == "rafael"
        assert env.hops == 2

    def test_root_charges_one_hop_from_budget(self) -> None:
        env = _root(budget=_budget(tokens=10, time_ms=10, cost_per_hop=1))
        assert env.budget.tokens == 9
        assert env.budget.time_ms == 9

    def test_root_rejects_trivial_cycle(self) -> None:
        """origin == target is a cycle of length 0 — rejected at the root (Guard 1)."""
        with pytest.raises(CyclicDelegationError):
            _root(origin="helena", target="helena")

    def test_root_rejects_when_root_chain_exceeds_max_hops(self) -> None:
        """A 2-agent root chain already exceeds max_hops=1 (Guard 2)."""
        with pytest.raises(MaxHopsExceededError):
            _root(max_hops=1)

    def test_root_requires_task_id(self) -> None:
        with pytest.raises(DelegationError, match="task_id"):
            _root(task_id="")

    def test_root_requires_tenant(self) -> None:
        with pytest.raises(DelegationError, match="tenant"):
            _root(tenant="")

    def test_root_requires_max_hops_at_least_one(self) -> None:
        with pytest.raises(DelegationError, match="max_hops"):
            _root(max_hops=0)

    def test_root_requires_timezone_aware_deadline(self) -> None:
        naive = datetime.now()  # noqa: DTZ005 — deliberately naive, to prove it is rejected
        with pytest.raises(DelegationError, match="deadline"):
            _root(deadline=naive)

    def test_root_accepts_timezone_aware_deadline(self) -> None:
        future = datetime.now(tz=UTC) + timedelta(hours=1)
        env = _root(deadline=future)
        assert env.deadline == future

    @pytest.mark.parametrize("phi_like", ["12345678901", "123.456.789-01", "12345678901234"])
    def test_root_rejects_phi_looking_payload_ref(self, phi_like: str) -> None:
        """An 11-digit (CPF/CNS) or 14-digit (CNPJ) numeric-looking ref is rejected (ADR-0006)."""
        with pytest.raises(DelegationError, match="payload_ref"):
            _root(payload_ref=phi_like)

    def test_root_accepts_fhir_reference_payload_ref(self) -> None:
        env = _root(payload_ref="fhir://Patient/abc-123")
        assert env.payload_ref == "fhir://Patient/abc-123"

    def test_payload_meta_defaults_to_empty(self) -> None:
        assert _root().payload_meta == {}

    def test_payload_meta_is_preserved(self) -> None:
        env = _root(payload_meta={"foo": "bar"})
        assert env.payload_meta == {"foo": "bar"}


# --- DelegationEnvelope.extend (guards 1-3) ----------------------------------------------------


class TestExtend:
    def test_extend_appends_target_to_chain(self) -> None:
        env = _root()
        sub = env.extend(target="beatriz", task_id="t2")
        assert sub.delegation_chain == ("helena", "rafael", "beatriz")
        assert sub.target == "beatriz"
        assert sub.task_id == "t2"

    def test_extend_preserves_origin_tenant_max_hops_deadline(self) -> None:
        deadline = datetime.now(tz=UTC) + timedelta(hours=1)
        env = _root(deadline=deadline, max_hops=5)
        sub = env.extend(target="beatriz", task_id="t2")
        assert sub.origin == env.origin
        assert sub.tenant == env.tenant
        assert sub.max_hops == env.max_hops
        assert sub.deadline == env.deadline

    def test_extend_defaults_task_type_and_payload_ref_to_parent(self) -> None:
        env = _root(task_type="authorization.analyze", payload_ref="fhir://Patient/abc")
        sub = env.extend(target="beatriz", task_id="t2")
        assert sub.task_type == "authorization.analyze"
        assert sub.payload_ref == "fhir://Patient/abc"

    def test_extend_can_override_task_type_and_payload_ref(self) -> None:
        env = _root()
        sub = env.extend(
            target="beatriz",
            task_id="t2",
            task_type="fraude.investigate",
            payload_ref="fhir://Patient/xyz",
        )
        assert sub.task_type == "fraude.investigate"
        assert sub.payload_ref == "fhir://Patient/xyz"

    def test_extend_rejects_cycle_back_to_origin(self) -> None:
        """Guard 1 — extending back to an agent already in the chain is a cycle."""
        env = _root()  # chain: (helena, rafael)
        with pytest.raises(CyclicDelegationError):
            env.extend(target="helena", task_id="t2")

    def test_extend_rejects_cycle_back_to_current_target(self) -> None:
        env = _root()
        with pytest.raises(CyclicDelegationError):
            env.extend(target="rafael", task_id="t2")

    def test_extend_rejects_when_exceeding_max_hops(self) -> None:
        """Guard 2 — a 3-hop root (max_hops=3) cannot extend to a 4th hop."""
        env = _root(max_hops=3)  # chain: (helena, rafael) — 2 hops so far
        sub = env.extend(target="beatriz", task_id="t2")  # 3 hops — still within max_hops=3
        with pytest.raises(MaxHopsExceededError):
            sub.extend(target="carolina", task_id="t3")  # would be the 4th hop

    def test_extend_rejects_when_budget_exhausted(self) -> None:
        """Guard 3 — extend charges a hop; raises before constructing the new envelope."""
        env = _root(budget=_budget(tokens=1, time_ms=1, cost_per_hop=1), max_hops=10)
        with pytest.raises(BudgetExhaustedError):
            env.extend(target="beatriz", task_id="t2")

    def test_extend_charges_budget_per_hop(self) -> None:
        env = _root(budget=_budget(tokens=10, time_ms=10, cost_per_hop=1), max_hops=10)
        sub = env.extend(target="beatriz", task_id="t2")
        # root() already charged 1 hop (10->9); extend() charges another (9->8).
        assert sub.budget.tokens == 8
        assert sub.budget.time_ms == 8

    def test_extend_new_envelope_is_independent(self) -> None:
        env = _root(max_hops=10)
        env.extend(target="beatriz", task_id="t2")
        assert env.target == "rafael"  # the parent is unchanged (immutability)
        assert env.delegation_chain == ("helena", "rafael")


# --- expired() / hops -----------------------------------------------------------------------


class TestExpiry:
    def test_no_deadline_never_expires(self) -> None:
        assert _root().expired() is False

    def test_future_deadline_not_expired(self) -> None:
        future = datetime.now(tz=UTC) + timedelta(hours=1)
        assert _root(deadline=future).expired() is False

    def test_past_deadline_is_expired(self) -> None:
        past = datetime.now(tz=UTC) - timedelta(seconds=1)
        assert _root(deadline=past).expired() is True

    def test_expired_accepts_explicit_now(self) -> None:
        deadline = datetime(2020, 1, 1, tzinfo=UTC)
        before = datetime(2019, 12, 31, tzinfo=UTC)
        after = datetime(2020, 1, 2, tzinfo=UTC)
        env = _root(deadline=deadline)
        assert env.expired(now=before) is False
        assert env.expired(now=after) is True


# --- origin_of --------------------------------------------------------------------------------


class TestOriginOf:
    def test_origin_of_returns_first_agent(self) -> None:
        assert origin_of(("helena", "rafael", "beatriz")) == "helena"

    def test_origin_of_empty_chain_raises(self) -> None:
        with pytest.raises(DelegationError):
            origin_of(())
