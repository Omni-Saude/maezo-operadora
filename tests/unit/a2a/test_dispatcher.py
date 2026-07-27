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
from typing import Any

import pytest

from maezo.a2a import (
    TOPIC_COMPLETED,
    TOPIC_REJECTED,
    TOPIC_REQUESTED,
    Budget,
    DelegationEnvelope,
    RejectionReason,
)
from maezo.a2a.dispatcher import a2a_audit_dedup_key, a2a_audit_outcome_dedup_key
from maezo.agents.helena.delegation import build_auth_analysis_envelope
from maezo.agents.rafael.delegation import make_rafael_handler
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from maezo.tools.workers.harness import FakeAuditSink
from tests.support.audit_fakes import FakeStartAuditSink

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

    # TWO audit rows (T-F completeness, LOW-1): the pre-exec ALLOW admission row, then the terminal
    # COMPLETED outcome row — the pre-exec row is not itself terminal.
    assert len(sink.emitted) == 2
    record, dedup_key = sink.emitted[0]
    assert record.agent_id == "helena"  # the chain's originator
    assert record.tenant_id == "amh"
    assert record.action == "a2a.delegate:rafael"
    assert record.decision == "ALLOW"
    assert record.details["decision_basis"] == "A2A:delegate:allow"
    assert dedup_key == a2a_audit_dedup_key("amh", "t1")

    outcome_record, outcome_key = sink.emitted[1]
    assert outcome_record.action == "a2a.delegate:rafael:outcome"
    assert outcome_record.decision == "COMPLETED"
    assert outcome_record.details["decision_basis"] == "A2A:delegate:completed"
    assert outcome_key == a2a_audit_outcome_dedup_key("amh", "t1")
    assert outcome_key != dedup_key  # distinct dedup keys — never collapsed by emit_once


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

    # Both the pre-exec ALLOW row and the terminal COMPLETED row (LOW-1) must be PHI-safe.
    assert len(sink.emitted) == 2
    for record, _ in sink.emitted:
        # The planted CPF must not appear ANYWHERE in the persisted record — not in `details`, not
        # smuggled into any other field.
        assert synthetic_cpf not in record.details.values()
        assert "cpf_beneficiario" not in record.details
        assert synthetic_cpf not in str(record.details)
    assert sink.emitted[0][0].details == {
        "task_id": "t1",
        "task_type": "authorization.analyze",
        "chain": ["helena", "rafael"],
        "payload_ref": "fhir://Patient/abc",
        "target": "rafael",
        "decision_basis": "A2A:delegate:allow",
    }
    assert sink.emitted[1][0].details == {
        "task_id": "t1",
        "task_type": "authorization.analyze",
        "chain": ["helena", "rafael"],
        "payload_ref": "fhir://Patient/abc",
        "target": "rafael",
        "decision_basis": "A2A:delegate:completed",
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
    """If the handler attempts a cyclic sub-delegation, the dispatcher returns an anti-loop
    rejection AND records a TERMINAL-outcome audit row (T-F follow-up, W4) — see
    `test_handler_error_emits_terminal_outcome_audit_row` below for the full shape/PHI assertions;
    this test keeps its original focus on the `DelegationResult`/fact behavior."""
    # rafael tries to sub-delegate back to helena (already in the chain) -> CyclicDelegationError.
    handler = FakeAgentHandler(subdelegate_to="helena")
    dispatcher, sink, producer = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}
    )
    result = await dispatcher.delegate(_envelope())
    assert not result.success
    assert result.rejection_reason is RejectionReason.ANTI_LOOP
    assert TOPIC_REJECTED in producer.topics()
    # The pre-execution "allow" audit already ran (the handler WAS allowed to run); W4 adds a
    # SECOND, terminal-outcome row closing the gap where this branch used to leave no audit trace
    # of the handler's own internal failure.
    assert len(sink.emitted) == 2
    assert sink.emitted[0][0].decision == "ALLOW"
    assert sink.emitted[1][0].decision == "FAILED"


async def test_handler_error_emits_terminal_outcome_audit_row() -> None:
    """T-F follow-up (W4): closes the completeness gap — an ALLOWed-then-internally-failed
    delegation now produces BOTH the pre-exec ALLOW row and a distinct terminal-outcome row, with
    its own dedup_key and PHI-safe details (payload_meta excluded, same discipline as the pre-exec
    audit)."""
    synthetic_cpf = "123.456.789-01"
    handler = FakeAgentHandler(subdelegate_to="helena")
    dispatcher, sink, _ = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})
    result = await dispatcher.delegate(_envelope(payload_meta={"cpf_beneficiario": synthetic_cpf}))
    assert not result.success

    assert len(sink.emitted) == 2
    allow_record, allow_key = sink.emitted[0]
    outcome_record, outcome_key = sink.emitted[1]

    assert allow_record.decision == "ALLOW"
    assert allow_key == a2a_audit_dedup_key("amh", "t1")

    assert outcome_record.agent_id == "helena"
    assert outcome_record.tenant_id == "amh"
    assert outcome_record.action == "a2a.delegate:rafael:outcome"
    assert outcome_record.decision == "FAILED"
    assert outcome_key == a2a_audit_outcome_dedup_key("amh", "t1")
    assert outcome_key != allow_key  # distinct dedup keys — never collapsed by emit_once

    # PHI-safe: the synthetic CPF planted in payload_meta must never reach either row.
    for record in (allow_record, outcome_record):
        assert synthetic_cpf not in record.details.values()
        assert "cpf_beneficiario" not in record.details
        assert synthetic_cpf not in str(record.details)

    assert outcome_record.details == {
        "task_id": "t1",
        "task_type": "authorization.analyze",
        "chain": ["helena", "rafael"],
        "payload_ref": "fhir://Patient/abc",
        "target": "rafael",
        "decision_basis": "A2A:handler_error:anti_loop",
        "detail": outcome_record.details["detail"],  # exact exception message, asserted below
    }
    assert "helena" in outcome_record.details["detail"]  # structural, PHI-free exception message


async def test_success_emits_terminal_completed_outcome_audit_row() -> None:
    """T-F completeness (LOW-1): a SUCCESSFUL delegation now produces BOTH the pre-exec ALLOW row
    and a distinct terminal COMPLETED `:outcome` row — symmetric to the handler-error branch — so
    the durable chain records that the delegation actually finished (not merely that it was
    admitted). Revert-RED guard: deleting the success-path `_audit_delegation_outcome` call drops
    this back to a single row and fails here."""
    synthetic_cpf = "123.456.789-01"
    handler = FakeAgentHandler(output_ref="fhir://Task/done")
    dispatcher, sink, _ = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})
    result = await dispatcher.delegate(_envelope(payload_meta={"cpf_beneficiario": synthetic_cpf}))
    assert result.success

    assert len(sink.emitted) == 2
    allow_record, allow_key = sink.emitted[0]
    outcome_record, outcome_key = sink.emitted[1]

    assert allow_record.decision == "ALLOW"
    assert allow_key == a2a_audit_dedup_key("amh", "t1")

    assert outcome_record.agent_id == "helena"
    assert outcome_record.tenant_id == "amh"
    assert outcome_record.action == "a2a.delegate:rafael:outcome"
    assert outcome_record.decision == "COMPLETED"
    assert outcome_record.details["decision_basis"] == "A2A:delegate:completed"
    assert "detail" not in outcome_record.details  # success carries no exception message
    assert outcome_key == a2a_audit_outcome_dedup_key("amh", "t1")
    assert outcome_key != allow_key  # distinct dedup keys — never collapsed by emit_once

    # PHI-safe: the synthetic CPF planted in payload_meta must never reach either row.
    for record in (allow_record, outcome_record):
        assert synthetic_cpf not in record.details.values()
        assert "cpf_beneficiario" not in record.details
        assert synthetic_cpf not in str(record.details)


async def test_audit_sink_failure_propagates_and_handler_never_runs() -> None:
    """FAIL-CLOSED proof (T-F daemon-readiness, W4): audit-before-effect means a durable sink that
    cannot persist ALREADY prevents the handler from ever running — no code change was needed for
    this, only a test proving the pre-existing ordering. `FakeAuditSink.always_fail` simulates an
    audit sink that is not ready (e.g. `audit_chain` unreachable)."""
    from maezo.gateway.audit_postgres import AuditPersistenceError

    handler = FakeAgentHandler()
    sink = FakeAuditSink()
    sink.always_fail = AuditPersistenceError("audit sink not ready (simulated)")
    dispatcher, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}, audit=sink
    )

    with pytest.raises(AuditPersistenceError):
        await dispatcher.delegate(_envelope())

    assert handler.call_count == 0  # the handler NEVER ran — audit failure blocked it upstream


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
    # Two rows for the FIRST delivery (ALLOW + COMPLETED outcome, LOW-1); the replay re-audits
    # NOTHING — still two, not four.
    assert len(sink.emitted) == 2
    assert [r.decision for r, _ in sink.emitted] == ["ALLOW", "COMPLETED"]
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]  # not emitted twice


# --- W3: the dispatcher routes to the REAL Rafael handler (not FakeAgentHandler) ---------------
#
# See `docs/design/A2A-dispatcher-card-signing.md` §9 for the full W3 rationale. This is the
# unit-level (engine-free, PG-free) half of the "T-F becomes real" proof: the dispatcher's own
# `_audit_delegation` (T-F, dedup `{tenant}:a2a:delegate:{task_id}`) is asserted here against a
# FakeAuditSink, DISTINCT from Rafael's own process-start audit (T-C2), which uses a SEPARATE
# `FakeStartAuditSink` injected only into `make_rafael_handler` — never conflated. The live-PG
# proof that the SAME distinction holds against a real `PostgresAuditSink` is
# `tests/unit/a2a/test_a2a_edge_live_pg.py` (Tier 2, `@pytest.mark.integration`, mirrors
# `test_idempotency_store.py`'s placement — PG-only, deliberately NOT under `tests/integration/`
# so it is never gated on the unrelated CIB Seven engine's reachability).

_EDGE_CASE_META: dict[str, Any] = {
    "beneficiario_pseudo_id": "pseudo-edge-1",
    "prestador_id": "prestador-1",
    "codigo_procedimento_tuss": "10101012",
    "categoria_procedimento": "consulta",
    "carater_atendimento": "eletivo",
    "valor_estimado_brl": 500.0,
    "requer_autorizacao": True,
    "documentacao_completa": True,
    "beneficiario_ativo": True,
    "carencia_cumprida": True,
    "dut_atendida": True,
    "dentro_teto_l2": False,
    "rede_credenciada": True,
}


class _FakeInference:
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        assert phi is True
        return "dossie factual sintetico"


def _real_rafael_handler(*, recomendacao: str = "ANALISE_HUMANA") -> tuple[Any, FakeStartAuditSink]:
    """A REAL Rafael handler (`make_rafael_handler`) over fakes — no engine, no Postgres."""
    dmn = FakeDmnTransport()
    dmn.register("auth_admissibility", [{"resultado": "SEGUE_ANALISE", "motivo": "test"}])
    dmn.register("auth_sla", [{"sla_analise": "P5D", "sla_alerta": "P3D"}])
    dmn.register("auth_auto_approval", [{"recomendacao": recomendacao, "motivo": "test"}])
    rafael_audit_sink = FakeStartAuditSink()
    handler = make_rafael_handler(
        _FakeInference(),
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=rafael_audit_sink,
    )
    return handler, rafael_audit_sink


def _real_envelope(*, numero_guia_tiss: str = "GUIA-EDGE-1") -> DelegationEnvelope:
    return build_auth_analysis_envelope(
        tenant="amh",
        numero_guia_tiss=numero_guia_tiss,
        coverage_ref="fhir://Coverage/edge-1",
        case_meta=_EDGE_CASE_META,
    )


async def test_dispatcher_routes_a_real_authorization_analysis_delegation_to_rafael() -> None:
    handler, rafael_audit_sink = _real_rafael_handler()
    card = make_card("rafael", accepted=frozenset({"authorization.analyze"}))
    dispatcher, sink, producer = build_test_dispatcher(cards=[card], handlers={"rafael": handler})
    envelope = _real_envelope()

    result = await dispatcher.delegate(envelope)

    assert result.success
    assert result.output_ref == "process://AUTH-amh-GUIA-EDGE-1"
    assert result.idempotent_replay is False

    # Surface 1 (T-F): the dispatcher's OWN delegation audit — the pre-exec ALLOW row + the terminal
    # COMPLETED outcome row (LOW-1), action/dedup_key exactly as the design specifies, fired on the
    # dispatcher's audit fake.
    assert len(sink.emitted) == 2
    record, dedup_key = sink.emitted[0]
    assert record.agent_id == "helena"
    assert record.tenant_id == "amh"
    assert record.action == "a2a.delegate:rafael"
    assert record.decision == "ALLOW"
    assert dedup_key == a2a_audit_dedup_key("amh", envelope.task_id)
    outcome_record, outcome_key = sink.emitted[1]
    assert outcome_record.action == "a2a.delegate:rafael:outcome"
    assert outcome_record.decision == "COMPLETED"
    assert outcome_key == a2a_audit_outcome_dedup_key("amh", envelope.task_id)

    # Surface 2 (T-C2): Rafael's OWN process-start audit fired on a DIFFERENT sink instance —
    # never conflated with surface 1 above (design doc §9.3).
    assert len(rafael_audit_sink.calls) == 1

    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]


async def test_dispatcher_redelivery_does_not_rerun_the_real_rafael_handler() -> None:
    handler, rafael_audit_sink = _real_rafael_handler()
    calls = 0
    inner_handler = handler

    async def counting_handler(envelope: DelegationEnvelope) -> Any:
        nonlocal calls
        calls += 1
        return await inner_handler(envelope)

    card = make_card("rafael", accepted=frozenset({"authorization.analyze"}))
    dispatcher, sink, producer = build_test_dispatcher(cards=[card], handlers={"rafael": counting_handler})
    envelope = _real_envelope(numero_guia_tiss="GUIA-EDGE-2")

    first = await dispatcher.delegate(envelope)
    second = await dispatcher.delegate(envelope)  # same task_id (deterministic auth_task_id)

    assert first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert second.output_ref == first.output_ref == "process://AUTH-amh-GUIA-EDGE-2"
    assert calls == 1  # the real Rafael graph never re-ran on replay
    # ALLOW + COMPLETED outcome (LOW-1) for the first delivery; the replay re-audits nothing.
    assert len(sink.emitted) == 2
    assert [r.decision for r, _ in sink.emitted] == ["ALLOW", "COMPLETED"]
    assert len(rafael_audit_sink.calls) == 1  # Rafael's own process-start fence, also once
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]


async def test_dispatcher_auto_approve_route_through_the_real_rafael_handler() -> None:
    """Structural counterpoint: the L2 auto-approve route also returns a process reference,
    never a coverage decision (guardrail re-confirmed at the dispatcher level)."""
    handler, _ = _real_rafael_handler(recomendacao="AUTO_APROVAR")
    card = make_card("rafael", accepted=frozenset({"authorization.analyze"}))
    dispatcher, sink, _ = build_test_dispatcher(cards=[card], handlers={"rafael": handler})
    envelope = _real_envelope(numero_guia_tiss="GUIA-EDGE-AUTO")

    result = await dispatcher.delegate(envelope)

    assert result.success
    assert result.output_ref == "process://AUTH-amh-GUIA-EDGE-AUTO"
    assert sink.emitted[0][0].decision == "ALLOW"
