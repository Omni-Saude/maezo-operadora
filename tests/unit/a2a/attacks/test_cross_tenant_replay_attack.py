"""ATTACK: cross-tenant replay — tenant B replays tenant A's `task_id`.

Threat (ADR-0039 §4.5 "Cross-tenant replay" row, defense-in-depth half): an envelope captured for
tenant A is re-presented under tenant B, hoping to make B serve A's stored delegation result (a
cross-tenant leak) or to collide the two tenants onto one idempotency row.

Defense under attack (BUILT NOW, no signing needed): the durable store's composite primary key
`(task_id, tenant)` (`idempotency.py:38` CLAIM_SQL/SELECT_SQL/COMPLETE_SQL all filter on BOTH
columns) and the outbox dedup key `{tenant}:a2a:delegate:{task_id}:{kind}` (`outbox.fact_dedup_key`)
both carry the tenant, so A's and B's rows/keys are DISTINCT by construction.

RED control: neuter the tenant component out of the key (`tenant_blind_pk`, the donor's single-column
`PRIMARY KEY (task_id)` the module docstring warns against). Then B COLLIDES with A and is SERVED
A's result — the observable harm. Every assertion pins that consequence (B receives A's process
reference), never merely that the key differs.
"""

from __future__ import annotations

import pytest

from maezo.a2a import (
    A2ARegistry,
    Budget,
    DelegationDispatcher,
    DelegationEnvelope,
    DelegationResult,
    FactProducer,
    HandlerOutput,
    fact_dedup_key,
)
from maezo.tools.workers.harness import FakeAuditSink
from tests.unit.a2a.attacks.adversary import ModelIdempotencyStore, real_pk, tenant_blind_pk
from tests.unit.a2a.fakes import RecordingProducer, make_card

_TASK_ID = "task-cross-tenant-1"
_TENANT_A = "amha"
_TENANT_B = "amhb"
_TASK_TYPE = "authorization.analyze"


def _envelope(tenant: str) -> DelegationEnvelope:
    return DelegationEnvelope.root(
        task_id=_TASK_ID,  # THE ATTACK: the SAME task_id presented under two tenants
        task_type=_TASK_TYPE,
        origin="helena",
        target="rafael",
        tenant=tenant,
        budget=Budget(tokens=64, time_ms=60_000, cost_per_hop=1),
        payload_ref=f"fhir://Coverage/{tenant}-1",
    )


class _TenantEchoHandler:
    """Returns a tenant-SPECIFIC process reference, so a cross-tenant SERVE is observable: if B is
    handed a reference whose tenant is A, the leak is unambiguous."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, envelope: DelegationEnvelope) -> HandlerOutput:
        self.calls += 1
        return HandlerOutput(output_ref=f"process://{envelope.tenant}/RECURSO-1")


def _dispatcher(store: ModelIdempotencyStore, handler: _TenantEchoHandler) -> DelegationDispatcher:
    """One dispatcher over ONE shared idempotency store (the single `a2a_idempotency` table both
    tenant replicas write to), with a tenant-scoped registry carrying a Card for BOTH tenants."""
    registry = A2ARegistry()
    registry.register(make_card("rafael", tenant=_TENANT_A, accepted=frozenset({_TASK_TYPE})))
    registry.register(make_card("rafael", tenant=_TENANT_B, accepted=frozenset({_TASK_TYPE})))
    return DelegationDispatcher(
        registry=registry,
        handlers={"rafael": handler},
        audit=FakeAuditSink(),
        facts=FactProducer(RecordingProducer()),
        idempotency=store,
    )


# ---------------------------------------------------------------------------
# Consequence attack — the durable store, driven end-to-end through delegate()
# ---------------------------------------------------------------------------


async def test_real_composite_pk_isolates_tenants_no_cross_tenant_serve() -> None:
    """DEFENSE HOLDS. With the real `(task_id, tenant)` PK, B's delegation of A's task_id executes
    INDEPENDENTLY — B is served B's own reference, never A's. No collision, no leak."""
    store = ModelIdempotencyStore(key_fn=real_pk)
    handler = _TenantEchoHandler()
    dispatcher = _dispatcher(store, handler)

    a = await dispatcher.delegate(_envelope(_TENANT_A))
    b = await dispatcher.delegate(_envelope(_TENANT_B))

    # Provenance: idempotency.py:38 — PK is (task_id, tenant); distinct tenant => distinct row.
    assert a.output_ref == f"process://{_TENANT_A}/RECURSO-1"
    assert b.output_ref == f"process://{_TENANT_B}/RECURSO-1"
    assert b.idempotent_replay is False  # B executed its own; it did NOT replay A
    assert handler.calls == 2  # both tenants ran — no cross-tenant collapse
    # Two distinct rows persist — the composite PK kept them apart.
    assert set(store.rows) == {(_TASK_ID, _TENANT_A), (_TASK_ID, _TENANT_B)}


async def test_red_control_tenant_blind_key_serves_tenant_a_result_to_tenant_b() -> None:
    """RED CONTROL — the attack SUCCEEDS when the defense is neutered. Drop the tenant from the key
    and B COLLIDES with A: B is served tenant A's process reference (`process://amha/...`) as an
    idempotent replay. That cross-tenant serve is the observable harm the composite PK prevents."""
    store = ModelIdempotencyStore(key_fn=tenant_blind_pk)
    handler = _TenantEchoHandler()
    dispatcher = _dispatcher(store, handler)

    a = await dispatcher.delegate(_envelope(_TENANT_A))
    b = await dispatcher.delegate(_envelope(_TENANT_B))

    assert a.output_ref == f"process://{_TENANT_A}/RECURSO-1"
    # THE HARM: B received tenant A's reference, and did so as a REPLAY (B's handler never ran).
    assert b.output_ref == f"process://{_TENANT_A}/RECURSO-1"
    assert b.idempotent_replay is True
    assert handler.calls == 1  # B's execution was collapsed into A's row — the collision
    assert set(store.rows) == {_TASK_ID}  # one row for two tenants — the leak's mechanism


# ---------------------------------------------------------------------------
# The store's own PK semantics — modeled directly (no dispatcher), consequence-level
# ---------------------------------------------------------------------------


async def test_store_composite_pk_second_tenant_claim_is_a_fresh_claim() -> None:
    """Directly at the store: A seals its row 'done'; B's `claim_or_get` for the same task_id is a
    FRESH claim (`None`) under the composite PK — B never sees A's `StoredResult`."""
    store = ModelIdempotencyStore(key_fn=real_pk)
    assert await store.claim_or_get(tenant=_TENANT_A, task_id=_TASK_ID) is None
    await store.complete(
        tenant=_TENANT_A, task_id=_TASK_ID, result=DelegationResult.ok(_TASK_ID, "process://amha/RECURSO-1")
    )
    # B claims the SAME task_id: a fresh claim, not A's sealed result.
    assert await store.claim_or_get(tenant=_TENANT_B, task_id=_TASK_ID) is None


async def test_red_control_store_tenant_blind_pk_leaks_tenant_a_stored_result() -> None:
    """RED CONTROL at the store: with the tenant dropped, B's `claim_or_get` returns tenant A's
    sealed `StoredResult` — the leak, isolated from the dispatcher."""
    store = ModelIdempotencyStore(key_fn=tenant_blind_pk)
    assert await store.claim_or_get(tenant=_TENANT_A, task_id=_TASK_ID) is None
    await store.complete(
        tenant=_TENANT_A, task_id=_TASK_ID, result=DelegationResult.ok(_TASK_ID, "process://amha/RECURSO-1")
    )
    leaked = await store.claim_or_get(tenant=_TENANT_B, task_id=_TASK_ID)
    assert leaked is not None
    assert leaked.output_ref == "process://amha/RECURSO-1"  # tenant A's reference, served to tenant B


# ---------------------------------------------------------------------------
# The outbox dedup key — the fact-side tenant isolation (pure, real function)
# ---------------------------------------------------------------------------


def test_fact_dedup_key_binds_tenant_so_cross_tenant_facts_never_collapse() -> None:
    """`fact_dedup_key` (the real function under test) carries the tenant, so tenant A's and tenant
    B's facts for the SAME task_id produce DIFFERENT keys — a consumer can never collapse B's fact
    onto A's. Expected keys hardcoded with provenance (outbox.py:245), not derived from the code."""
    key_a = fact_dedup_key(tenant=_TENANT_A, task_id=_TASK_ID, kind="completed")
    key_b = fact_dedup_key(tenant=_TENANT_B, task_id=_TASK_ID, kind="completed")
    assert key_a == "amha:a2a:delegate:task-cross-tenant-1:completed"
    assert key_b == "amhb:a2a:delegate:task-cross-tenant-1:completed"
    assert key_a != key_b


def test_red_control_a_tenant_blind_dedup_key_collapses_two_tenants_facts() -> None:
    """RED CONTROL: a dedup key that DROPS the tenant (the neuter) collapses tenant A's and tenant
    B's facts onto ONE key — a consumer would dedup B's fact as a duplicate of A's, dropping it.
    This is the fact-side twin of the store collision above."""

    def _tenant_blind_dedup_key(*, tenant: str, task_id: str, kind: str) -> str:
        _ = tenant
        return f"a2a:delegate:{task_id}:{kind}"

    key_a = _tenant_blind_dedup_key(tenant=_TENANT_A, task_id=_TASK_ID, kind="completed")
    key_b = _tenant_blind_dedup_key(tenant=_TENANT_B, task_id=_TASK_ID, kind="completed")
    assert key_a == key_b  # THE HARM: two tenants' facts now share one key


if __name__ == "__main__":  # pragma: no cover - convenience for the R1 verifier
    raise SystemExit(pytest.main([__file__, "-v"]))
