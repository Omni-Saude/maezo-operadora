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
        isinstance(harness.registry.get(topic), SendDenialNoticeWorker) for topic in harness.registered_topics
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


# --- the reviewed D7-A row for this topic ------------------------------------


def test_the_topic_declares_exactly_one_reportable_error():
    """The catalog row states the boundary set, and grants nothing by existing."""
    from maezo.gateway.engine_contracts import EngineOperation
    from maezo.gateway.engine_schemas import SCHEMAS, schema_by_id

    row = schema_by_id("auth.send_denial_notice.bpmn_error.v1")
    assert row in SCHEMAS
    assert row.topic == TOPIC
    assert row.operation is EngineOperation.BPMN_ERROR
    assert row.error_codes == (ERR_AUTH_DENIAL_INCOMPLETE,)
    # No clinical variable is readable through this row (ADR-0006).
    assert not {"justificativa_clinica", "cid10_referencia", "fundamentacao_dut"} & set(row.read_projection)


def test_the_record_can_never_grow_a_clinical_field():
    """The closed record IS the ADR-0006 enforcement point for this owner.

    This owner reads no clinical variable, so it needs no redactor — and adding one
    would make a third `redact_phi_vars` call site, invalidating the two-call-site
    measurement `PHI-DISPOSITIONS-RECOMMENDATION.md` §3.0 rests on. What replaces it
    is stronger and local: the emitted key set is fixed here, so a clinical field
    cannot be added without editing this assertion.
    """
    from maezo.gateway.denial_notices.models import DenialNoticeRecord

    assert set(DenialNoticeRecord.model_fields) == {
        "notice_type",
        "event",
        "human_approved",
        "auditor_id",
        "denial_record_ref",
        "human_decision_custody_ref",
    }
    assert set(_worker().execute(_vars())) == {
        "notice_type",
        "error_code",
        "event",
        "human_approved",
        "auditor_id",
        "denial_record_ref",
        "human_decision_custody_ref",
    }


def _harness():  # type: ignore[no-untyped-def]
    return WorkerHarness(FakeWorkerTransport(), worker_id="j1-06-owner-probe")


def test_the_production_daemon_installs_exactly_one_owner_and_is_dark_by_default():
    """V14 MAJOR-1 — the NEGAR owner now HAS a production installation path.

    Before this repair `denial_notice_host` was read by `register_auth_workers` and
    passed by no caller in `src/`: the live daemon threaded six seams and this was not
    among them, and no setting existed. So in every deployment the generic
    `SendDenialNoticeWorker` stayed the owner and the portal NEGAR journey was as dead
    as it is on `main` — while the PR read as delivered capability.

    The switch is `MAEZO_DENIAL_NOTICE_OWNER`, dark by default. This proves both
    positions and that the topic never has two owners.
    """
    from maezo.runtime.worker_runtime.denial_notices import TOPIC, NativeDenialNoticeWorker
    from maezo.runtime.worker_runtime.service import register_default_workers
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
    from maezo.tools.workers.auth import SendDenialNoticeWorker

    assert WorkerRuntimeSettings().denial_notice_owner is False, "dark by default"

    dark = _harness()
    register_default_workers(dark)
    assert type(dark.registry.get(TOPIC)) is SendDenialNoticeWorker

    live = _harness()
    register_default_workers(live, denial_notice_host=install_denial_notice_host())
    assert type(live.registry.get(TOPIC)) is NativeDenialNoticeWorker

    # Exactly one owner, and the served topic set is identical either way — the
    # daemon's readiness probe derives its expectation from this same function.
    assert sorted(dark.registered_topics) == sorted(live.registered_topics)
    assert live.registered_topics.count(TOPIC) == 1


def test_the_installed_owner_survives_a_later_generic_registration():
    """V14 MINOR-5 — exclusivity is durable, not a single moment in time.

    `assert_exclusive` ran at construction and once after `register_auth_workers`.
    Registering the generic worker afterwards silently won, because the registry warns
    and overwrites on a duplicate topic — and `register_all_workers` is documented as
    idempotent and safe to call again, so a second call without the seam handed the
    topic straight back.
    """
    from maezo.runtime.worker_runtime.denial_notices import TOPIC, NativeDenialNoticeWorker
    from maezo.runtime.worker_runtime.service import register_default_workers
    from maezo.tools.workers.auth import SendDenialNoticeWorker
    from maezo.tools.workers.harness import TopicSealError

    harness = _harness()
    register_default_workers(harness, denial_notice_host=install_denial_notice_host())
    assert type(harness.registry.get(TOPIC)) is NativeDenialNoticeWorker

    # The exact D9 mutation V14 observed: register the generic worker afterwards.
    with pytest.raises(TopicSealError):
        harness.register_worker(SendDenialNoticeWorker())
    # And a raw handler cannot take it either.
    with pytest.raises(TopicSealError):
        harness.register(TOPIC, _never)
    # A second seam-less bootstrap pass no longer hands the topic back.
    with pytest.raises(TopicSealError):
        register_default_workers(harness)
    assert type(harness.registry.get(TOPIC)) is NativeDenialNoticeWorker

    # Re-registering the SAME owner stays idempotent.
    register_default_workers(harness, denial_notice_host=install_denial_notice_host())
    assert type(harness.registry.get(TOPIC)) is NativeDenialNoticeWorker


async def _never(task):  # type: ignore[no-untyped-def]
    raise AssertionError("a sealed topic must never dispatch to a foreign handler")


def test_the_basis_still_carries_no_denial_discriminator():
    """V14 MINOR-7 tripwire — the known weakness, pinned so it cannot become permanent.

    `HumanDecisionBasis` is what `AtomicHumanCommand` writes for EVERY admitted human
    decision, APROVAR and NEGAR alike: no field says "denial", and none lets the
    worker verify the sealed `content_digest` covers the three clinical fields. The
    guard therefore proves a complete human decision exists in custody, not that a
    denial with complete grounding does.

    That is a property to know, not a defect this PR can fix — the discriminator has
    to be written engine-side by PR-C's `ClassifiedDecision`. This test fails the day
    one appears, which is exactly when this guard must start consuming it.
    """
    from maezo.gateway.denial_notices.models import HumanDecisionBasis

    fields = set(HumanDecisionBasis.model_fields)
    assert fields == {
        "custody_ref",
        "content_digest",
        "request_digest",
        "binding_digest",
        "principal_ref",
        "workload_ref",
        "command_ref",
        "audit_intent_ref",
    }, "a new basis field landed — if it discriminates denials, the guard must read it"
    assert len(BASIS_VARIABLES) == len(fields)
    # No field name hints at the decision's kind or at the clinical grounding itself.
    assert not [f for f in fields if any(t in f for t in ("deni", "negar", "outcome", "decis", "kind"))]

    # And the compensating control is real: the worker composes NO denial record for
    # anything but a NEGAR — the discriminator the basis lacks is supplied, for this
    # one decision, by the gateway variable the BPMN flow condition already sets.
    approval = _worker().execute(_vars(decisao_auditor="APROVAR"))
    assert approval["notice_type"] == "approval"
    assert approval["error_code"] is None
    assert "denial_record_ref" not in approval
