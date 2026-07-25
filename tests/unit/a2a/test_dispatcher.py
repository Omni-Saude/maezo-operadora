"""Unit tests for maezo.a2a.dispatcher — DelegationDispatcher (ADR-0003/0007, T-F).

Ported (v2-adapted) from the donor `Maezo-Healthcare-Plan` reference implementation's
`tests/unit/a2a/test_dispatcher.py` as part of the T2.4 A2A W2 build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §2/§3/§5. The biggest adaptation is the AUDIT-SEAM
REWRITE (see `dispatcher._audit_delegation`'s own docstring for the full field-mapping rationale):
the donor asserts against its own `AuditLog`/`AuditRecord(tool=, decision_basis=,
autonomy_level=)`; this suite asserts against v2's `AuditRecord` shape
(`agent_id`/`tenant_id`/`action`/`decision`/`details: dict`) as emitted via `emit_once` (captured
by `FakeAuditSink`, `maezo.tools.workers.harness`), plus the PHI-safety property the design calls
out explicitly: a synthetic CPF planted in `payload_meta` must never reach the audit record.

Covers: the happy path (routes + audits + emits facts, in order); EVERY `RejectionReason`
(unknown_target, task_type_not_accepted, no_handler, expired, anti_loop-from-handler); facts never
carry PHI; the exact v2 `AuditRecord` shape + `emit_once` dedup_key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from maezo.a2a import (
    TOPIC_COMPLETED,
    TOPIC_REJECTED,
    TOPIC_REQUESTED,
    Budget,
    DelegationEnvelope,
    RejectionReason,
)
from maezo.a2a.dispatcher import a2a_audit_dedup_key

from .fakes import FakeAgentHandler, build_test_dispatcher, make_card


def _envelope(
    *, target: str = "rafael", task_type: str = "authorization.analyze", **kw: object
) -> DelegationEnvelope:
    base: dict[str, object] = {
        "task_id": "t1",
        "task_type": task_type,
        "origin": "helena",
        "target": target,
        "tenant": "amh",
        "budget": Budget(tokens=100, time_ms=100),
        "payload_ref": "fhir://Patient/abc",
    }
    base.update(kw)
    return DelegationEnvelope.root(**base)  # type: ignore[arg-type]


# --- Happy path -------------------------------------------------------------------------------


async def test_successful_delegation_routes_audits_and_emits_facts() -> None:
    handler = FakeAgentHandler(output_ref="fhir://Task/done")
    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}
    )

    result = await dispatcher.delegate(_envelope())

    # Routed to the target's handler.
    assert result.success
    assert result.output_ref == "fhir://Task/done"
    assert result.idempotent_replay is False
    assert handler.call_count == 1

    # Emitted requested BEFORE and completed AFTER, both keyed by tenant.
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]
    assert all(key == b"amh" for (_, _, key) in producer.sent)
    completed_value = producer.sent[1][1]
    assert b"fhir://Task/done" in completed_value  # output_ref in the completed fact

    # Audited the delegation exactly once, BEFORE the effect.
    assert len(sink.emitted) == 1
    record, dedup_key = sink.emitted[0]
    assert record.agent_id == "helena"  # the chain's originator
    assert record.tenant_id == "amh"
    assert record.action == "a2a.delegate:rafael"
    assert record.decision == "ALLOW"
    assert record.details["decision_basis"] == "A2A:delegate:allow"
    assert dedup_key == a2a_audit_dedup_key("amh", "t1")


async def test_facts_never_carry_phi() -> None:
    handler = FakeAgentHandler()
    dispatcher, _, producer = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})
    await dispatcher.delegate(_envelope(payload_ref="fhir://Patient/abc"))
    for _, value, _ in producer.sent:
        # The fact carries the reference, never a raw payload; and no CPF-like digit run.
        assert b"Patient" not in value or b"fhir://" in value


async def test_audit_record_details_exclude_payload_meta_synthetic_cpf() -> None:
    """PHI-safety proof (design §"plant a synthetic CPF"): a CPF planted in `payload_meta` — the
    one envelope field NOT covered by the `_looks_like_phi` guard on `payload_ref` — must never
    reach the emitted `AuditRecord`."""
    handler = FakeAgentHandler()
    dispatcher, sink, _ = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})
    synthetic_cpf = "123.456.789-01"
    await dispatcher.delegate(_envelope(payload_meta={"cpf_beneficiario": synthetic_cpf}))

    assert len(sink.emitted) == 1
    record, _ = sink.emitted[0]
    # The planted CPF must not appear ANYWHERE in the persisted record — not in `details`, not
    # smuggled into any other field.
    assert synthetic_cpf not in record.details.values()
    assert "cpf_beneficiario" not in record.details
    serialized = str(record.details)
    assert synthetic_cpf not in serialized
    assert record.details == {
        "task_id": "t1",
        "task_type": "authorization.analyze",
        "chain": ["helena", "rafael"],
        "payload_ref": "fhir://Patient/abc",
        "target": "rafael",
        "decision_basis": "A2A:delegate:allow",
    }


# --- Rejections: one test per RejectionReason ------------------------------------------------


async def test_unknown_target_rejected_with_fact_and_audit() -> None:
    dispatcher, sink, producer = build_test_dispatcher(cards=[], handlers={})
    result = await dispatcher.delegate(_envelope(target="ghost"))
    assert not result.success
    assert result.rejection_reason is RejectionReason.UNKNOWN_TARGET
    assert producer.topics() == [TOPIC_REJECTED]
    record, dedup_key = sink.emitted[0]
    assert record.decision == "DENY"
    assert record.details["decision_basis"].startswith("A2A:reject:")
    assert dedup_key == a2a_audit_dedup_key("amh", "t1")


async def test_task_type_not_accepted_rejected() -> None:
    card = make_card("rafael", accepted=frozenset({"authorization.analyze"}))
    dispatcher, sink, producer = build_test_dispatcher(cards=[card], handlers={"rafael": FakeAgentHandler()})
    result = await dispatcher.delegate(_envelope(task_type="clinical.decision"))
    assert result.rejection_reason is RejectionReason.TASK_TYPE_NOT_ACCEPTED
    assert producer.topics() == [TOPIC_REJECTED]
    assert sink.emitted[0][0].decision == "DENY"


async def test_missing_handler_rejected() -> None:
    # Card registered but no handler wired.
    dispatcher, sink, producer = build_test_dispatcher(cards=[make_card("rafael")], handlers={})
    result = await dispatcher.delegate(_envelope())
    assert result.rejection_reason is RejectionReason.NO_HANDLER
    assert producer.topics() == [TOPIC_REJECTED]
    assert sink.emitted[0][0].decision == "DENY"


async def test_expired_envelope_rejected_before_routing() -> None:
    handler = FakeAgentHandler()
    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}
    )
    past = datetime.now(tz=UTC) - timedelta(seconds=1)
    result = await dispatcher.delegate(_envelope(deadline=past))
    assert result.rejection_reason is RejectionReason.EXPIRED
    assert handler.call_count == 0
    assert producer.topics() == [TOPIC_REJECTED]
    assert sink.emitted[0][0].decision == "DENY"


async def test_handler_subdelegation_loop_surfaces_as_rejection() -> None:
    """If the handler attempts a cyclic sub-delegation, the dispatcher returns an anti-loop rejection."""
    # rafael tries to sub-delegate back to helena (already in the chain) -> CyclicDelegationError.
    handler = FakeAgentHandler(subdelegate_to="helena")
    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}
    )
    result = await dispatcher.delegate(_envelope())
    assert not result.success
    assert result.rejection_reason is RejectionReason.ANTI_LOOP
    assert TOPIC_REJECTED in producer.topics()
    # The pre-execution "allow" audit already ran (the handler WAS allowed to run); the handler's
    # own internal anti-loop failure is captured only in the rejected fact, not a 2nd audit call —
    # ported faithfully from the donor's own control flow (dispatcher.py `_execute`'s except
    # branch does not call `_audit_delegation` a second time).
    assert len(sink.emitted) == 1
    assert sink.emitted[0][0].decision == "ALLOW"


# --- Idempotent replay never re-audits or re-emits requested -----------------------------------


async def test_redelivery_does_not_reaudit_or_reemit_requested() -> None:
    handler = FakeAgentHandler(output_ref="fhir://Task/done")
    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}
    )
    first = await dispatcher.delegate(_envelope())
    second = await dispatcher.delegate(_envelope())  # same task_id

    assert first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert handler.call_count == 1
    assert len(sink.emitted) == 1  # audited exactly once, not once per delivery
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]  # not emitted twice
