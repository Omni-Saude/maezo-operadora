"""WP-J1-06 row j — the portal NEGAR path reaches `End_NegadaAuditor`, guarded.

Before this WP, every denial submitted through the portal died at
`End_FundamentacaoIncompletaBloqueada`: `SendDenialNoticeWorker` demands the three ANS
grounding fields in process variables, and ADR-0006 is the reason a portal NEGAR never
puts them there (the clinical text goes to PHI custody; the engine gets references).

These tests prove the replacement owner keeps the SAME rule with the SAME modeled
error code, against the evidence that actually exists — and that exactly one owner
serves the topic.

The other half of row j — the PHI notice body delivered to provider and beneficiary —
is NOT built or tested here: `HumanDecisionCustody.resolve` authorizes only the
deciding principal and `EngineBackedPhiDecisionAuthorization` refuses once the task is
no longer current, so a post-completion consumer cannot read that text under any
ratified permission. That gap is reported, not papered over with a stub.
"""

from __future__ import annotations

import pytest

from maezo.gateway.denial_notices.models import (
    BASIS_VARIABLES,
    DenialIncompleteError,
    DenialNotHumanError,
)
from maezo.gateway.denial_notices.producer import TOPIC, DenialNoticeProducer, denial_record_ref
from maezo.runtime.worker_runtime.denial_notices import (
    DenialNoticeHost,
    NativeDenialNoticeWorker,
    TopicOwnershipError,
    install_denial_notice_host,
)
from maezo.tools.workers.auth import AUTH_BPMN_ERROR_ALLOWLIST, SendDenialNoticeWorker, register_auth_workers
from maezo.tools.workers.base import ERR_AUTH_DENIAL_INCOMPLETE, ERR_DENIAL_NOT_HUMAN
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerBpmnError, WorkerHarness

AUDITOR = "principal-auditor-7"
WORKLOAD = "portal-human"
CUSTODY = "phi-decision:6f1c5f1e-0a3d-4a2b-9a55-2c9a1f0d7e11"


def _vars(**overrides: object) -> dict[str, object]:
    """A portal NEGAR exactly as `AtomicHumanCommand` writes it into the engine."""
    payload: dict[str, object] = {
        "tenant_id": "tenant_j1",
        "decisao_auditor": "NEGAR",
        "auditor_id": AUDITOR,
        "human_decision_custody_ref": CUSTODY,
        "human_decision_content_digest": "a" * 64,
        "human_decision_request_digest": "b" * 64,
        "human_decision_binding_digest": "c" * 64,
        "human_decision_principal_ref": AUDITOR,
        "human_decision_workload_ref": WORKLOAD,
        "human_decision_command_ref": "command-9f2",
        "human_decision_audit_intent_ref": "intent-9f2",
    }
    payload.update(overrides)
    return payload


def _worker() -> NativeDenialNoticeWorker:
    return install_denial_notice_host().worker()


# --- the journey the WP exists to unblock ------------------------------------


def test_a_portal_negar_composes_the_formal_record():
    """The path that ended at the blocked terminal now produces a denial record."""
    result = _worker().execute(_vars())

    assert result["notice_type"] == "denial"
    assert result["error_code"] is None
    assert result["event"] == "agents.events.auth.completed"
    assert result["human_approved"] is True
    assert result["auditor_id"] == AUDITOR
    assert result["human_decision_custody_ref"] == CUSTODY
    # No transmission is claimed: this step composes, the secure channel is Fase 1.
    assert "status" not in result


def test_the_record_reference_is_stable_and_decision_specific():
    """Same decision, same reference; a different decision never collides with it."""
    first = _worker().execute(_vars())
    again = _worker().execute(_vars())
    other = _worker().execute(_vars(human_decision_command_ref="command-other"))

    assert first["denial_record_ref"] == again["denial_record_ref"]
    assert first["denial_record_ref"] != other["denial_record_ref"]
    assert len(first["denial_record_ref"]) == 64


def test_no_clinical_field_can_leave_this_worker():
    """ADR-0006: the composed record carries references, never grounding text.

    Even when clinical variables are present in engine scope (a legacy or forged
    payload), nothing clinical is read, echoed or returned.
    """
    result = _worker().execute(
        _vars(
            justificativa_clinica="texto clinico",
            cid10_referencia="Z00.0",
            fundamentacao_dut="DUT 42",
        )
    )
    assert not {"justificativa_clinica", "cid10_referencia", "fundamentacao_dut"} & set(result)
    assert "texto clinico" not in repr(result)


# --- guard 1: completeness, unconditionally first ----------------------------


@pytest.mark.parametrize("name", BASIS_VARIABLES)
def test_every_missing_basis_reference_blocks_the_send(name):
    """An ungrounded denial is never transmitted — one modeled boundary, unchanged."""
    with pytest.raises(WorkerBpmnError) as raised:
        _worker().execute(_vars(**{name: None}))
    assert raised.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE
    assert ERR_AUTH_DENIAL_INCOMPLETE in AUTH_BPMN_ERROR_ALLOWLIST


@pytest.mark.parametrize("value", ["", "   ", "​", 7, True, None, [], {}])
def test_a_blank_or_non_string_reference_is_missing(value):
    """Cannot decide that the grounding exists = it does not = refuse the send."""
    with pytest.raises(WorkerBpmnError) as raised:
        _worker().execute(_vars(human_decision_custody_ref=value))
    assert raised.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


def test_a_malformed_digest_is_not_a_basis():
    """A reference that cannot be resolved in PHI is not grounding evidence."""
    with pytest.raises(WorkerBpmnError) as raised:
        _worker().execute(_vars(human_decision_content_digest="not-a-digest"))
    assert raised.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


def test_a_workload_may_never_be_the_deciding_principal():
    """`ClassifiedDecision` refuses that projection; so does this owner."""
    with pytest.raises(WorkerBpmnError) as raised:
        _worker().execute(_vars(human_decision_principal_ref=WORKLOAD, auditor_id=WORKLOAD))
    assert raised.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


def test_completeness_is_checked_before_provenance():
    """A payload failing BOTH guards is barred as incomplete, never as non-human."""
    with pytest.raises(WorkerBpmnError) as raised:
        _worker().execute(_vars(human_decision_custody_ref=None, auditor_id=""))
    assert raised.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


def test_the_incomplete_error_names_fields_never_values():
    """PHI hygiene in diagnostics: names travel, values do not."""
    producer = DenialNoticeProducer()
    with pytest.raises(DenialIncompleteError) as raised:
        producer.compose(_vars(human_decision_command_ref=None))
    assert raised.value.missing == ("human_decision_command_ref",)


# --- guard 2: human accountability -------------------------------------------


@pytest.mark.parametrize("auditor", [None, "", "   ", "outro-principal", 7])
def test_a_denial_without_matching_human_accountability_is_blocked(auditor):
    """ERR_DENIAL_NOT_HUMAN is RETURNED: the BPMN declares no boundary for it."""
    result = _worker().execute(_vars(auditor_id=auditor))
    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN
    assert ERR_DENIAL_NOT_HUMAN not in AUTH_BPMN_ERROR_ALLOWLIST


def test_the_producer_raises_the_typed_provenance_refusal():
    """The domain refusal is typed; only the host decides how it reaches the engine."""
    with pytest.raises(DenialNotHumanError):
        DenialNoticeProducer().compose(_vars(auditor_id="outro-principal"))


def test_a_non_negar_outcome_composes_no_denial():
    """Defensive branch: `ST_EnviarNegativaFormal`'s only inbound flow is NEGAR."""
    result = _worker().execute(_vars(decisao_auditor="APROVAR"))
    assert result["notice_type"] == "approval"
    assert "human_decision_custody_ref" not in result


# --- one owner per topic ------------------------------------------------------


def test_without_the_seam_the_generic_worker_still_owns_the_topic():
    """Nothing regresses for a deployment that has not installed the new owner."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="j1-06-probe")
    register_auth_workers(harness)
    assert type(harness.registry.get(TOPIC)) is SendDenialNoticeWorker


def test_with_the_seam_the_native_owner_replaces_it_exactly_once():
    """Two workers on one topic would race for the task and skip a guard."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="j1-06-probe")
    register_auth_workers(harness, denial_notice_host=install_denial_notice_host())

    assert type(harness.registry.get(TOPIC)) is NativeDenialNoticeWorker
    assert harness.registered_topics.count(TOPIC) == 1
    assert not any(
        isinstance(harness.registry.get(topic), SendDenialNoticeWorker)
        for topic in harness.registered_topics
    )


def test_the_seam_does_not_disturb_the_other_auth_workers():
    """Same topic set either way: only the owner of this one topic changes."""
    generic = WorkerHarness(FakeWorkerTransport(), worker_id="a")
    native = WorkerHarness(FakeWorkerTransport(), worker_id="b")
    register_auth_workers(generic)
    register_auth_workers(native, denial_notice_host=install_denial_notice_host())
    assert set(generic.registered_topics) == set(native.registered_topics)


def test_installing_over_an_existing_owner_is_refused():
    """Exclusivity is asserted at installation, not assumed."""
    with pytest.raises(TopicOwnershipError):
        install_denial_notice_host(generic_topics=[TOPIC])
    with pytest.raises(TopicOwnershipError):
        DenialNoticeHost.assert_exclusive(["operadora.auth.issue_authorization", TOPIC])


def test_a_host_refuses_anything_that_is_not_the_real_producer():
    """No seam through which a stand-in producer can be installed."""
    with pytest.raises(TopicOwnershipError):
        DenialNoticeHost(producer=object())  # type: ignore[arg-type]


def test_the_record_reference_is_computed_from_the_basis_alone():
    """Determinism of `denial_record_ref` is a property of the basis, not the clock."""
    from maezo.gateway.denial_notices.producer import read_basis

    basis = read_basis(_vars())
    assert denial_record_ref(basis) == denial_record_ref(read_basis(_vars()))
