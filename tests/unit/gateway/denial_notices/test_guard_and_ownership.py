"""`operadora.auth.send_denial_notice` — the custody-basis producer and the ONE owner.

History. WP-J1-06 added a second, dark owner for this topic (`NativeDenialNoticeWorker`,
switched by `MAEZO_DENIAL_NOTICE_OWNER`) that composed the denial record from the
human-decision basis in PHI custody instead of clinical strings in engine variables.
Forensic finding F-032 (roadmap R-004) found that owner outside the AUTH contract, and the
owner's decision DEC-01 (issue #1081) removed it: the contract's worker,
`SendDenialNoticeWorker`, is the single owner. The old switch is now a tombstone — ON, the
daemon refuses to register its workers instead of silently ignoring the request.

What stays, and is tested here: the pure `DenialNoticeProducer` (the guard that reads the
custody basis — completeness first, then human accountability). It is NOT installed as an
owner by anything; it is kept for the future written-notice channel, which must read the
basis from custody rather than clinical text (ADR-0006).
"""

from __future__ import annotations

import pytest

from maezo.gateway.denial_notices.models import (
    BASIS_VARIABLES,
    DenialIncompleteError,
    DenialNotHumanError,
)
from maezo.gateway.denial_notices.producer import TOPIC, DenialNoticeProducer, denial_record_ref
from maezo.runtime.worker_runtime import denial_notices as ownership
from maezo.runtime.worker_runtime.denial_notices import (
    RetiredDenialOwnerError,
    TopicOwnershipError,
    assert_single_owner,
    refuse_retired_second_owner,
)
from maezo.tools.workers.auth import SendDenialNoticeWorker, register_auth_workers
from maezo.tools.workers.base import ERR_AUTH_DENIAL_INCOMPLETE
from maezo.tools.workers.harness import FakeWorkerTransport, WorkerHarness

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


def _compose(**overrides: object) -> dict[str, object]:
    return DenialNoticeProducer().compose(_vars(**overrides)).variables()


# --- the producer: completeness, unconditionally first ------------------------------------------


def test_a_complete_human_basis_composes_the_formal_record():
    result = _compose()
    assert result["notice_type"] == "denial"
    assert result["human_approved"] is True
    assert result["auditor_id"] == AUDITOR
    assert result["human_decision_custody_ref"] == CUSTODY
    # No transmission is claimed: composing is not delivering.
    assert "status" not in result


def test_the_record_reference_is_stable_and_decision_specific():
    first = _compose()
    again = _compose()
    other = _compose(human_decision_command_ref="command-other")
    assert first["denial_record_ref"] == again["denial_record_ref"]
    assert first["denial_record_ref"] != other["denial_record_ref"]
    assert len(str(first["denial_record_ref"])) == 64


def test_no_clinical_field_can_leave_the_producer():
    result = _compose(
        justificativa_clinica="texto clinico", cid10_referencia="Z00.0", fundamentacao_dut="DUT 42"
    )
    assert not {"justificativa_clinica", "cid10_referencia", "fundamentacao_dut"} & set(result)
    assert "texto clinico" not in repr(result)


@pytest.mark.parametrize("name", BASIS_VARIABLES)
def test_every_missing_basis_reference_is_incomplete(name):
    with pytest.raises(DenialIncompleteError) as raised:
        DenialNoticeProducer().compose(_vars(**{name: None}))
    assert raised.value.missing == (name,)


@pytest.mark.parametrize("value", ["", "   ", "​", 7, True, None, [], {}])
def test_a_blank_or_non_string_reference_is_missing(value):
    with pytest.raises(DenialIncompleteError):
        DenialNoticeProducer().compose(_vars(human_decision_custody_ref=value))


def test_a_malformed_digest_is_not_a_basis():
    with pytest.raises(DenialIncompleteError):
        DenialNoticeProducer().compose(_vars(human_decision_content_digest="not-a-digest"))


def test_a_workload_may_never_be_the_deciding_principal():
    with pytest.raises(DenialIncompleteError):
        DenialNoticeProducer().compose(_vars(human_decision_principal_ref=WORKLOAD, auditor_id=WORKLOAD))


def test_completeness_is_checked_before_provenance():
    with pytest.raises(DenialIncompleteError):
        DenialNoticeProducer().compose(_vars(human_decision_custody_ref=None, auditor_id=""))


# --- the producer: human accountability ---------------------------------------------------------


@pytest.mark.parametrize("auditor", [None, "", "   ", "outro-principal", 7])
def test_a_denial_without_matching_human_accountability_is_refused(auditor):
    with pytest.raises(DenialNotHumanError):
        DenialNoticeProducer().compose(_vars(auditor_id=auditor))


def test_the_record_reference_is_computed_from_the_basis_alone():
    from maezo.gateway.denial_notices.producer import read_basis

    basis = read_basis(_vars())
    assert denial_record_ref(basis) == denial_record_ref(read_basis(_vars()))


def test_the_record_can_never_grow_a_clinical_field():
    from maezo.gateway.denial_notices.models import DenialNoticeRecord

    assert set(DenialNoticeRecord.model_fields) == {
        "notice_type",
        "event",
        "human_approved",
        "auditor_id",
        "denial_record_ref",
        "human_decision_custody_ref",
    }


def test_the_basis_still_carries_no_denial_discriminator():
    """V14 MINOR-7 tripwire: fails the day a basis field that discriminates denials lands."""
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
    }
    assert len(BASIS_VARIABLES) == len(fields)


# --- the reviewed D7-A row for this topic -------------------------------------------------------


def test_the_topic_declares_exactly_one_reportable_error():
    from maezo.gateway.engine_contracts import EngineOperation
    from maezo.gateway.engine_schemas import SCHEMAS, schema_by_id

    row = schema_by_id("auth.send_denial_notice.bpmn_error.v1")
    assert row in SCHEMAS
    assert row.topic == TOPIC
    assert row.operation is EngineOperation.BPMN_ERROR
    assert row.error_codes == (ERR_AUTH_DENIAL_INCOMPLETE,)
    assert not {"justificativa_clinica", "cid10_referencia", "fundamentacao_dut"} & set(row.read_projection)


# --- R-004 / F-032: ONE owner, the contract's worker --------------------------------------------


def _harness() -> WorkerHarness:
    return WorkerHarness(FakeWorkerTransport(), worker_id="r004-owner-probe")


def test_the_second_owner_no_longer_exists():
    assert not hasattr(ownership, "NativeDenialNoticeWorker")
    assert not hasattr(ownership, "DenialNoticeHost")
    assert not hasattr(ownership, "install_denial_notice_host")


def test_the_contract_worker_is_the_only_owner():
    harness = _harness()
    register_auth_workers(harness)
    assert type(harness.registry.get(TOPIC)) is SendDenialNoticeWorker
    assert harness.registered_topics.count(TOPIC) == 1
    assert_single_owner(harness)


def test_the_daemon_bootstrap_installs_only_the_contract_worker():
    from maezo.runtime.worker_runtime.service import register_default_workers

    harness = _harness()
    register_default_workers(harness)
    register_default_workers(harness)  # idempotent re-registration keeps the same single owner
    assert type(harness.registry.get(TOPIC)) is SendDenialNoticeWorker
    assert harness.registered_topics.count(TOPIC) == 1
    assert_single_owner(harness)


def test_a_second_owner_seam_is_refused_loudly():
    """Nobody can thread a second owner back in through the old seam name."""
    harness = _harness()
    with pytest.raises(TopicOwnershipError):
        register_auth_workers(harness, denial_notice_host=object())


def test_a_foreign_worker_on_the_topic_fails_the_ownership_check():
    from maezo.tools.workers.base import WorkerBase

    class _Impostor(WorkerBase):
        def __init__(self) -> None:
            super().__init__(topic=TOPIC, max_retries=1)

        def execute(self, process_vars):  # type: ignore[no-untyped-def]
            return {}

    harness = _harness()
    register_auth_workers(harness)
    harness.register_worker(_Impostor())
    with pytest.raises(TopicOwnershipError):
        assert_single_owner(harness)


def test_a_missing_owner_fails_the_ownership_check():
    with pytest.raises(TopicOwnershipError):
        assert_single_owner(_harness())


def test_the_retired_switch_is_a_tombstone(monkeypatch: pytest.MonkeyPatch):
    from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings

    monkeypatch.delenv("MAEZO_DENIAL_NOTICE_OWNER", raising=False)
    assert WorkerRuntimeSettings().denial_notice_owner is False
    refuse_retired_second_owner(False)
    with pytest.raises(RetiredDenialOwnerError):
        refuse_retired_second_owner(True)


def test_the_daemon_bootstrap_refuses_a_composition_without_the_contract_owner(
    monkeypatch: pytest.MonkeyPatch,
):
    """Mutation guard: `register_default_workers` itself asserts the single owner."""
    import maezo.runtime.worker_runtime.service as svc

    monkeypatch.setattr(svc, "register_all_workers", lambda harness, **seams: None)
    with pytest.raises(TopicOwnershipError):
        svc.register_default_workers(_harness())
