"""Unit tests for maezo.tools.workers.ans_submit — SP-OP-ANS-SUBMIT-001.

TDD London School: tests exercise the external task contracts.
"""

import functools
from pathlib import Path

import pytest

from maezo.tools.workers.ans_gateway import (
    ANS_OUTCOME_ENVIADO,
    ANS_OUTCOME_NACK,
    MOCK_ANS_NACK_MOTIVO,
    MOCK_ANS_PROTOCOL_PREFIX,
    AnsGatewayUnavailableError,
    LabeledMockAnsGatewayTransport,
    RealAnsGatewayTransport,
    RefusingAnsGatewayTransport,
    resolve_ans_gateway,
)
from maezo.tools.workers.ans_submit import (
    ANS_SUBMIT_BPMN_ERROR_ALLOWLIST,
    AnsDatasetIncompletoError,
    AnsNotifyRegulatorioInput,
    AnsSubmissionData,
    AnsSubmitDecision,
    AnsSubmitInput,
    AnsSubmitNotHumanError,
    _ans_business_key,
    assemble_entry,
    handle_nack,
    make_notify_regulatorio_handler,
    notify_regulatorio,
    prepare_submission,
    publish_completed,
    publish_completed_entry,
    register_ans_submit_workers,
    retransmit_entry,
    retry_submission,
    submit_entry,
    track_protocol_entry,
    transmit_to_ans,
    validate_data,
    validate_entry,
)
from maezo.tools.workers.base import FunctionWorker
from maezo.tools.workers.dmn_transport import DmnEvaluationError, FakeDmnTransport
from maezo.tools.workers.harness import (
    ExternalTask,
    FakeKafkaPublisher,
    FakeWorkerTransport,
    WorkerBpmnError,
    WorkerHarness,
)
from maezo.tools.workers.tiss_schema import TissSchemaValidator

#: NOT a real ANS Padrão TISS version — a minimal, clearly-labeled FIXTURE used only to prove the
#: T2.6-2 validation seam end-to-end (design §2.B external-dependency finding: the real vendored
#: XSD set + pinned version are SME-gated / not sourced in this task).
_FIXTURE_TISS_VERSION = "FIXTURE-0"

_FIXTURE_XSD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="loteGuias">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="numeroLote" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
"""


def _fixture_validator(tmp_path: Path, report_type: str = "RN_124_SIP") -> TissSchemaValidator:
    """A `TissSchemaValidator` pinned at a temp schema root holding ONE fixture XSD — NOT a real
    TISS schema (see `_FIXTURE_TISS_VERSION` docstring)."""
    version_dir = tmp_path / _FIXTURE_TISS_VERSION
    version_dir.mkdir(parents=True, exist_ok=True)
    (version_dir / f"{report_type}.xsd").write_text(_FIXTURE_XSD, encoding="utf-8")
    return TissSchemaValidator(schema_root=tmp_path, version=_FIXTURE_TISS_VERSION)


def _write_dataset_xml(tmp_path: Path, name: str, content: str) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def _ans_retry_policy_fake(*, backoff: str, continue_retry: bool) -> FakeDmnTransport:
    """A FakeDmnTransport registered with ONE `ans_retry_policy` response — matches the real
    deployed table row-for-row (verified against `spec/processes/dmn/ans_retry_policy.dmn`)."""
    fake = FakeDmnTransport()
    fake.register("ans_retry_policy", [{"backoff": backoff, "continue_retry": continue_retry}])
    return fake


# ---------------------------------------------------------------------------
# prepare_submission
# ---------------------------------------------------------------------------


def test_prepare_submission() -> None:
    """prepare_submission builds a dataset stub."""
    inp = AnsSubmitInput(
        tenant_id="amh",
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_complete=True,
        schema_valid=True,
        lgpd_anonimizado=False,
    )
    result = prepare_submission(inp)
    assert result.dataset_ref is not None
    assert result.report_type == "RN_124_SIP"
    assert result.dataset_complete is True


def test_prepare_submission_incomplete() -> None:
    """prepare_submission with dataset_ref absent -> dataset_complete=False."""
    inp = AnsSubmitInput(
        tenant_id="amh",
        report_type="DIOPS_TRIMESTRAL",
        competencia="2026-Q2",
        dataset_ref="",
    )
    result = prepare_submission(inp)
    assert result.dataset_complete is False


# ---------------------------------------------------------------------------
# validate_data — T2.6-2 (design §2.B): real lxml.etree.XMLSchema validation, NOT an echo of the
# inbound `schema_valid` flag (the stub this replaces). See tests/unit/tools/workers/
# test_tiss_schema.py for the seam module's own exhaustive edge-case coverage (missing version,
# missing XSD, malformed XSD, malformed XML, valid/invalid XML); the tests here prove the WIRING
# into validate_data/validate_entry.
# ---------------------------------------------------------------------------


def test_validate_data_no_version_pinned_fails_closed() -> None:
    """T2.6-2: with NO `tiss_validator` injected, `validate_data` resolves a real
    `TissSchemaValidator()` — and `MAEZO_TISS_SCHEMA_VERSION` is unset (today, everywhere: the
    padrão-TISS version in force is SME-gated, design §2.B/§7). Fail-closed: `schema_valid=False`
    even for an otherwise-'complete' submission. This is the CURRENT production posture, and
    replaces the old stub's echo-True — the inbound `schema_valid=True` flag is now IGNORED
    (no longer trusted from the wire), the exact fix this task makes."""
    submission = AnsSubmissionData(
        dataset_ref="dataset-ok",
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_complete=True,
        schema_valid=True,
    )
    result = validate_data(submission)
    assert result["schema_valid"] is False
    assert result["tiss_schema_version"] is None


def test_validate_data_missing_ref() -> None:
    """validate_data flags missing dataset_ref (unchanged structural check, layered on top of the
    new real TISS validation)."""
    submission = AnsSubmissionData(
        dataset_ref="",
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_complete=True,
    )
    result = validate_data(submission)
    assert result["schema_valid"] is False
    assert any("dataset_ref" in e.lower() for e in result["errors"])


def test_validate_data_valid_xml_against_fixture_xsd_is_schema_valid(tmp_path: Path) -> None:
    """Design §2.B verification plan: 'a schema-valid XML → schema_valid=True'. Proves the
    mechanism genuinely validates (not another echo) against a FIXTURE XSD (NOT real TISS)."""
    validator = _fixture_validator(tmp_path)
    dataset_ref = _write_dataset_xml(
        tmp_path, "dataset-ok.xml", "<loteGuias><numeroLote>1</numeroLote></loteGuias>"
    )
    submission = AnsSubmissionData(
        dataset_ref=dataset_ref,
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_complete=True,
    )
    result = validate_data(submission, tiss_validator=validator)
    assert result["schema_valid"] is True
    assert result["errors"] == []
    assert result["tiss_schema_version"] == _FIXTURE_TISS_VERSION


def test_validate_data_corrupt_xml_fails_schema_routes_to_human(tmp_path: Path) -> None:
    """Design §2.B verification plan: 'corrupt a valid TISS XML → schema_valid=False → routes to
    human'. `validate_data` never raises — it returns `schema_valid=False` + structured errors,
    which the BPMN routes to `UT_CorrigirPendenciaEnvio`, never a reject."""
    validator = _fixture_validator(tmp_path)
    dataset_ref = _write_dataset_xml(
        tmp_path, "dataset-corrupt.xml", "<loteGuias><campoInexistente/></loteGuias>"
    )
    submission = AnsSubmissionData(
        dataset_ref=dataset_ref,
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_complete=True,
    )
    result = validate_data(submission, tiss_validator=validator)
    assert result["schema_valid"] is False
    assert result["errors"]


def test_validate_data_missing_vendored_xsd_fails_closed(tmp_path: Path) -> None:
    """Design §2.B: 'Missing/unvendored XSD for a report type → schema_valid=False → human (never
    pass on absence)'."""
    validator = TissSchemaValidator(schema_root=tmp_path, version="v-sem-xsd-vendorizado")
    dataset_ref = _write_dataset_xml(tmp_path, "dataset.xml", "<loteGuias/>")
    submission = AnsSubmissionData(
        dataset_ref=dataset_ref,
        report_type="RN_209_UTILIZACAO",
        competencia="2026-06",
        dataset_complete=True,
    )
    result = validate_data(submission, tiss_validator=validator)
    assert result["schema_valid"] is False


def test_validate_data_stub_dataset_ref_never_green_even_with_schema_pinned(tmp_path: Path) -> None:
    """Design §2.B: 'Because assemble is a stub today ... there is no real XML to validate →
    validation stays False → human, which is the correct fail-closed posture.' Today's stub
    `prepare_submission` produces a synthetic, non-file `dataset_ref`
    (`dataset-{report_type}-{competencia}`) — even WITH a version+XSD pinned, that never resolves
    to a real file, so it fails closed."""
    validator = _fixture_validator(tmp_path)
    submission = AnsSubmissionData(
        dataset_ref="dataset-RN_124_SIP-2026-06",  # prepare_submission's stub shape, not a real file
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_complete=True,
    )
    result = validate_data(submission, tiss_validator=validator)
    assert result["schema_valid"] is False


def test_validate_data_version_pin_surfaced_in_result(tmp_path: Path) -> None:
    """Design §2.B verification plan: 'Version pin surfaced in the audit record.'"""
    validator = _fixture_validator(tmp_path)
    dataset_ref = _write_dataset_xml(
        tmp_path, "dataset-ok.xml", "<loteGuias><numeroLote>1</numeroLote></loteGuias>"
    )
    submission = AnsSubmissionData(
        dataset_ref=dataset_ref, report_type="RN_124_SIP", competencia="2026-06", dataset_complete=True
    )
    result = validate_data(submission, tiss_validator=validator)
    assert result["tiss_schema_version"] == _FIXTURE_TISS_VERSION


# ---------------------------------------------------------------------------
# transmit_to_ans — GUARD tests
# ---------------------------------------------------------------------------


def test_transmit_to_ans_guard_not_approved() -> None:
    """transmit_to_ans raises if decisao_envio != APROVAR_ENVIO."""
    submission = AnsSubmissionData(
        dataset_ref="ds-1",
        report_type="RN_124_SIP",
        competencia="2026-06",
    )
    decision = AnsSubmitDecision(
        decisao_envio="ADIAR_ENVIO",
        revisor_id="reg-001",
    )
    with pytest.raises(AnsSubmitNotHumanError) as exc:
        transmit_to_ans(submission, decision)
    assert "decisao_envio" in str(exc.value)


def test_transmit_to_ans_guard_missing_revisor() -> None:
    """transmit_to_ans raises if revisor_id is missing."""
    submission = AnsSubmissionData(
        dataset_ref="ds-1",
        report_type="RN_124_SIP",
        competencia="2026-06",
    )
    decision = AnsSubmitDecision(
        decisao_envio="APROVAR_ENVIO",
        revisor_id="",
    )
    with pytest.raises(AnsSubmitNotHumanError) as exc:
        transmit_to_ans(submission, decision)
    assert "revisor_id" in str(exc.value)


def test_transmit_to_ans_success_labeled_mock() -> None:
    """transmit_to_ans succeeds with an approved decision + an injected LabeledMock gateway,
    returning a DETERMINISTIC, UNMISTAKABLY-SYNTHETIC protocol (T2.6-1, design §2.A).

    The old fabricated `ANSPROTO-{sha256(time_ns)}` is gone: the protocol now reads
    `MOCK-ANS-NAO-VINCULATIVO-{business_key}` and carries `synthetic`/`vinculativo` flags, so it
    can never be mistaken for a real ANS protocol."""
    submission = AnsSubmissionData(
        dataset_ref="ds-1",
        report_type="RN_124_SIP",
        competencia="2026-06",
    )
    decision = AnsSubmitDecision(
        decisao_envio="APROVAR_ENVIO",
        revisor_id="reg-001",
    )
    result = transmit_to_ans(
        submission,
        decision,
        gateway=LabeledMockAnsGatewayTransport(),
        business_key="ANSSUB-amh-RN_124_SIP-2026-06",
    )
    assert result["submitted"] is True
    assert result["protocolo_ans"] == f"{MOCK_ANS_PROTOCOL_PREFIX}ANSSUB-amh-RN_124_SIP-2026-06"
    assert not result["protocolo_ans"].startswith("ANSPROTO-")
    assert result["synthetic"] is True
    assert result["vinculativo"] is False
    assert result["status_envio"] == "enviado"


# ---------------------------------------------------------------------------
# NACK (ERR_ANS_PROTOCOLO_NACK) — deterministically inducible in dev/test, IMPOSSIBLE in prod.
# BPMN: BE_SubmitNack on ST_SubmeterEnvio -> SUB_RetryEnvio -> ... -> UT_TratarNack (human).
# ---------------------------------------------------------------------------


def _approved_submission() -> tuple[AnsSubmissionData, AnsSubmitDecision]:
    return (
        AnsSubmissionData(dataset_ref="ds-1", report_type="RN_124_SIP", competencia="2026-06"),
        AnsSubmitDecision(decisao_envio="APROVAR_ENVIO", revisor_id="reg-001"),
    )


def test_transmit_to_ans_nack_raises_allowlisted_bpmn_error() -> None:
    """A NACK from the gateway raises the MODELED, ALLOWLISTED ERR_ANS_PROTOCOLO_NACK.

    This is the whole point of the NACK capability: `WorkerBpmnError` with a code the harness's
    `bpmn_error_allowlist` admits is what makes BE_SubmitNack fire instead of the instance ending
    silently (the CIB Seven 2.1.0 hazard) or incidenting.
    """
    submission, decision = _approved_submission()
    with pytest.raises(WorkerBpmnError) as exc:
        transmit_to_ans(
            submission,
            decision,
            gateway=LabeledMockAnsGatewayTransport(),
            business_key="ANSSUB-amh-RN_124_SIP-2026-06",
            requested_outcome=ANS_OUTCOME_NACK,
        )
    assert exc.value.error_code == "ERR_ANS_PROTOCOLO_NACK"
    assert exc.value.error_code in ANS_SUBMIT_BPMN_ERROR_ALLOWLIST
    # The refusal reason must travel in the message — WorkerBpmnError has no variables channel.
    assert MOCK_ANS_NACK_MOTIVO in str(exc.value)


def test_submit_entry_seeded_status_envio_nack_drives_the_nack_branch() -> None:
    """The seeded `status_envio="nack"` process variable reaches the gateway (it used to be
    silently dropped by pick_fields, which is why the NACK was unproducible)."""
    with pytest.raises(WorkerBpmnError) as exc:
        submit_entry(
            {
                "tenant_id": "amh",
                "report_type": "RN_124_SIP",
                "competencia": "2026-06",
                "dataset_ref": "ds-1",
                "decisao_envio": "APROVAR_ENVIO",
                "revisor_id": "reg-001",
                "status_envio": "nack",
            },
            ans_gateway=LabeledMockAnsGatewayTransport(),
        )
    assert exc.value.error_code == "ERR_ANS_PROTOCOLO_NACK"


def test_nack_is_opt_in_only_default_still_succeeds() -> None:
    """Purely additive: with no directive the mock still returns the accepted filing."""
    submission, decision = _approved_submission()
    result = transmit_to_ans(
        submission, decision, gateway=LabeledMockAnsGatewayTransport(), business_key="bk-1"
    )
    assert result["submitted"] is True
    assert result["status_envio"] == ANS_OUTCOME_ENVIADO


def test_nacked_protocol_is_still_unmistakably_synthetic() -> None:
    """A mock NACK must not look like a real ANS refusal any more than a mock ACK looks like a
    real filing — same MOCK- prefix, same synthetic/vinculativo flags, mock-labelled motivo."""
    protocol = LabeledMockAnsGatewayTransport().submit(
        business_key="bk-1",
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_ref="ds-1",
        revisor_id="reg-001",
        requested_outcome=ANS_OUTCOME_NACK,
    )
    assert protocol.status_envio == ANS_OUTCOME_NACK
    assert protocol.protocolo_ans.startswith(MOCK_ANS_PROTOCOL_PREFIX)
    assert not protocol.protocolo_ans.startswith("ANSPROTO-")
    assert protocol.synthetic is True
    assert protocol.vinculativo is False
    assert protocol.nack_motivo == MOCK_ANS_NACK_MOTIVO


def test_production_transports_cannot_be_steered_into_a_nack() -> None:
    """PRODUCTION SAFETY: neither production-shaped transport honors `requested_outcome` — both
    refuse unconditionally, so no NACK (and no protocol at all) can be induced on them."""
    for transport in (RefusingAnsGatewayTransport(), RealAnsGatewayTransport()):
        with pytest.raises(AnsGatewayUnavailableError):
            transport.submit(
                business_key="bk-1",
                report_type="RN_124_SIP",
                competencia="2026-06",
                dataset_ref="ds-1",
                revisor_id="reg-001",
                requested_outcome=ANS_OUTCOME_NACK,
            )


def test_prod_wiring_refuses_even_when_a_nack_is_requested() -> None:
    """END-TO-END PRODUCTION FENCE: `submit_entry` with NO injected gateway is the production
    wiring (`register_all_workers` passes no `ans_gateway`). Even with the NACK directive seeded it
    resolves to RefusingAnsGatewayTransport and refuses — it can neither file nor fabricate a NACK.
    """
    with pytest.raises(AnsGatewayUnavailableError):
        submit_entry(
            {
                "tenant_id": "amh",
                "report_type": "RN_124_SIP",
                "competencia": "2026-06",
                "dataset_ref": "ds-1",
                "decisao_envio": "APROVAR_ENVIO",
                "revisor_id": "reg-001",
                "status_envio": "nack",
            }
        )


def test_human_guard_still_fires_before_any_nack_can_be_requested() -> None:
    """The NACK directive must not become a way around the HITL pre-filing guard: with no human
    approval the refusal is still ERR_ANS_SUBMIT_NOT_HUMAN, raised before the gateway is touched."""
    with pytest.raises(AnsSubmitNotHumanError):
        submit_entry(
            {"report_type": "RN_124_SIP", "status_envio": "nack"},
            ans_gateway=LabeledMockAnsGatewayTransport(),
        )


def test_unknown_gateway_status_fails_closed_not_treated_as_a_filing() -> None:
    """Fail-closed: a transport returning an uninterpretable status must incident, never yield
    `submitted=True`. Guards a future RealAnsGatewayTransport against silent fail-open."""

    class _WeirdStatusTransport:
        def submit(self, **_kwargs: object) -> object:
            from maezo.tools.workers.ans_gateway import AnsProtocol

            return AnsProtocol(protocolo_ans="X-1", status_envio="talvez", synthetic=False, vinculativo=True)

    submission, decision = _approved_submission()
    with pytest.raises(ValueError, match="status_envio desconhecido"):
        transmit_to_ans(submission, decision, gateway=_WeirdStatusTransport(), business_key="bk-1")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Assembly-failure guard (ERR_ANS_DATASET_INCOMPLETO) — BE_AssembleDatasetIncompleto on
# ST_AssembleDataset -> UT_CorrigirPendenciaEnvio (human). NEVER auto-rejects the filing.
# ---------------------------------------------------------------------------


def test_prepare_submission_raises_allowlisted_code_on_assembly_failure() -> None:
    inp = AnsSubmitInput(
        tenant_id="amh",
        report_type="RN_124_SIP",
        competencia="2026-06",
        dataset_ref="DATASET-TESTE-0001",
        dataset_assembly_failed=True,
    )
    with pytest.raises(WorkerBpmnError) as exc:
        prepare_submission(inp)
    assert exc.value.error_code == "ERR_ANS_DATASET_INCOMPLETO"
    assert exc.value.error_code in ANS_SUBMIT_BPMN_ERROR_ALLOWLIST


def test_assemble_entry_raises_on_assembly_failure_variable() -> None:
    with pytest.raises(WorkerBpmnError) as exc:
        assemble_entry(
            {
                "tenant_id": "amh",
                "report_type": "RN_124_SIP",
                "competencia": "2026-06",
                "dataset_assembly_failed": True,
            }
        )
    assert exc.value.error_code == "ERR_ANS_DATASET_INCOMPLETO"


def test_assemble_guard_does_not_fire_otherwise() -> None:
    """The guard is narrow: only an explicit origin-side assembly failure trips it. An absent flag,
    an explicit False, and an incomplete-but-existing dataset all still assemble normally — those
    route through the admissibility DMN, not through this boundary."""
    assert assemble_entry({"report_type": "RN_124_SIP", "competencia": "2026-06"})["dataset_ref"]
    assert assemble_entry(
        {"report_type": "RN_124_SIP", "competencia": "2026-06", "dataset_assembly_failed": False}
    )["dataset_ref"]
    # dataset_complete=False is NOT an assembly failure — it is a routable fact.
    incomplete = assemble_entry(
        {"report_type": "RN_124_SIP", "competencia": "2026-06", "dataset_complete": False}
    )
    assert incomplete["dataset_complete"] is False


def test_assemble_failure_is_a_modeled_error_not_the_valueerror_family() -> None:
    """It must be a `WorkerBpmnError`, NOT `AnsDatasetIncompletoError`: the ValueError family
    demotes to a raw incident and loses the modeled route to UT_CorrigirPendenciaEnvio."""
    with pytest.raises(WorkerBpmnError):
        prepare_submission(AnsSubmitInput(report_type="X", dataset_assembly_failed=True))
    # ...while the unrelated blank-protocolo_ans call sites keep their ValueError classification.
    with pytest.raises(AnsDatasetIncompletoError):
        track_protocol_entry({"protocolo_ans": "  "})


# ---------------------------------------------------------------------------
# ADR-0030 allowlist constant — exactly the intended codes, nothing more.
# ---------------------------------------------------------------------------


def test_ans_submit_allowlist_is_exactly_the_two_modeled_codes() -> None:
    """Census fence. ERR_ANS_RETRY_ESGOTADO is deliberately ABSENT: it is thrown by the MODEL
    (End_RetryEsgotado inside SUB_RetryEnvio), no worker raises it, and its only boundary is
    attached to a subProcess rather than an external task — so the boundary-proof gate could not
    admit a worker raise of it. ERR_ANS_SUBMIT_NOT_HUMAN is absent too: it has no boundary at all
    and stays a PermissionError -> incident (and would be T-E-gated regardless).
    """
    assert (
        frozenset({"ERR_ANS_PROTOCOLO_NACK", "ERR_ANS_DATASET_INCOMPLETO"}) == ANS_SUBMIT_BPMN_ERROR_ALLOWLIST
    )


def test_ans_submit_allowlist_carries_no_te_gated_code() -> None:
    """Neither code is a `*_NOT_HUMAN` guard or a denial-block code, so both may be enabled
    pre-T-E (ADR-0030 §4)."""
    from maezo.tools.workers.harness import is_guard_refusal_code

    assert not any(is_guard_refusal_code(c) for c in ANS_SUBMIT_BPMN_ERROR_ALLOWLIST)


# ---------------------------------------------------------------------------
# AnsGatewayTransport triple (T2.6-1, design §2.A) — Refusing prod-default / LabeledMock /
# Real creds-blocked; NO fabricated protocol anywhere.
# ---------------------------------------------------------------------------


def test_resolve_ans_gateway_defaults_to_refusing_not_mock() -> None:
    """Fail-closed selection: an unwired seam resolves to Refusing (prod default), NEVER the mock —
    the mock is unreachable in prod by construction (design §2.A)."""
    assert isinstance(resolve_ans_gateway(None), RefusingAnsGatewayTransport)
    mock = LabeledMockAnsGatewayTransport()
    assert resolve_ans_gateway(mock) is mock


def test_labeled_mock_is_deterministic_by_business_key() -> None:
    """LabeledMock returns a DETERMINISTIC, synthetic, non-binding protocol keyed on the business
    key — two calls with the same business key return the identical protocol (subsumes the T-H
    determinism requirement + fixes the latent retry idempotency bug, BPMN :461). No `ANSPROTO-`."""
    mock = LabeledMockAnsGatewayTransport()
    bk = "ANSSUB-amh-RN_124_SIP-2026-06"
    kwargs = {"report_type": "RN_124_SIP", "competencia": "2026-06", "dataset_ref": "ds", "revisor_id": "r"}
    p1 = mock.submit(business_key=bk, **kwargs)
    p2 = mock.submit(business_key=bk, **kwargs)
    assert p1 == p2  # frozen dataclass equality — fully deterministic
    assert p1.protocolo_ans == f"{MOCK_ANS_PROTOCOL_PREFIX}{bk}"
    assert not p1.protocolo_ans.startswith("ANSPROTO-")
    assert p1.synthetic is True
    assert p1.vinculativo is False
    # A different business key yields a different protocol.
    p3 = mock.submit(business_key="ANSSUB-amh-DIOPS_TRIMESTRAL-2026-Q2", **kwargs)
    assert p3.protocolo_ans != p1.protocolo_ans


def test_refusing_transport_never_issues_a_protocol() -> None:
    """Refusing (prod default) RAISES a fail-closed AnsGatewayUnavailableError — it NEVER returns a
    protocol (the anti-fabrication guarantee, design §2.A)."""
    with pytest.raises(AnsGatewayUnavailableError) as exc:
        RefusingAnsGatewayTransport().submit(
            business_key="ANSSUB-amh-RN_124_SIP-2026-06",
            report_type="RN_124_SIP",
            competencia="2026-06",
            dataset_ref="ds",
            revisor_id="r",
        )
    assert exc.value.code == "ERR_ANS_GATEWAY_UNAVAILABLE"


def test_real_transport_is_creds_blocked_stub() -> None:
    """Real is a documented, creds-blocked future integration point — `submit` fails closed, never
    fabricates (Plan §7, issue #16). Blocked ≠ done."""
    with pytest.raises(AnsGatewayUnavailableError):
        RealAnsGatewayTransport("https://ans.example/ws", auth_token="x").submit(
            business_key="ANSSUB-amh-RN_124_SIP-2026-06",
            report_type="RN_124_SIP",
            competencia="2026-06",
            dataset_ref="ds",
            revisor_id="r",
        )


def test_transmit_prod_default_refuses_no_protocol() -> None:
    """FAIL-CLOSED PROD-CONFIG: transmit_to_ans with NO gateway (the production wiring — the
    registered partial binds `ans_gateway=None`) refuses via the resolved Refusing transport and
    returns NO protocol at all, even for a fully-approved human decision."""
    submission = AnsSubmissionData(dataset_ref="ds-1", report_type="RN_124_SIP", competencia="2026-06")
    decision = AnsSubmitDecision(decisao_envio="APROVAR_ENVIO", revisor_id="reg-001")
    with pytest.raises(AnsGatewayUnavailableError):
        transmit_to_ans(submission, decision, business_key="ANSSUB-amh-RN_124_SIP-2026-06")


def test_submit_entry_prod_default_refuses() -> None:
    """The dict-boundary entry mirrors it: an approved payload with NO injected gateway refuses
    (prod default) rather than fabricating a protocol."""
    variables = {
        "tenant_id": "amh",
        "report_type": "RN_124_SIP",
        "competencia": "2026-06",
        "decisao_envio": "APROVAR_ENVIO",
        "revisor_id": "reg-001",
    }
    with pytest.raises(AnsGatewayUnavailableError):
        submit_entry(variables)


def test_submit_worker_refusal_reclassifies_to_failclosed_incident() -> None:
    """Through the FunctionWorker dispatch path (exactly how production registers the submit
    worker: `functools.partial(submit_entry, ans_gateway=None)`), the refusal reclassifies to a
    `ValueError` — which the harness maps to `failure(retries=0)`, an engine-guaranteed,
    human-visible incident (base.py §5), NEVER a transient engine-retry and NEVER a fabricated
    protocol."""
    worker = FunctionWorker("regulatorio.anssubmit.submit", functools.partial(submit_entry, ans_gateway=None))
    with pytest.raises(ValueError) as exc:
        worker.execute(
            {
                "tenant_id": "amh",
                "report_type": "RN_124_SIP",
                "competencia": "2026-06",
                "decisao_envio": "APROVAR_ENVIO",
                "revisor_id": "reg-001",
            }
        )
    assert "ERR_ANS_GATEWAY_UNAVAILABLE" in str(exc.value)


def test_guard_fires_before_gateway_even_with_refusing() -> None:
    """The ERR_ANS_SUBMIT_NOT_HUMAN guard is orthogonal to the gateway and fires FIRST: a missing
    human decision raises AnsSubmitNotHumanError BEFORE the gateway is ever consulted — even with a
    Refusing gateway wired, the error is the guard's, not the gateway's."""
    submission = AnsSubmissionData(dataset_ref="ds-1", report_type="RN_124_SIP", competencia="2026-06")
    decision = AnsSubmitDecision(decisao_envio="ADIAR_ENVIO", revisor_id="")
    with pytest.raises(AnsSubmitNotHumanError):
        transmit_to_ans(
            submission,
            decision,
            gateway=RefusingAnsGatewayTransport(),
            business_key="ANSSUB-amh-RN_124_SIP-2026-06",
        )


# ---------------------------------------------------------------------------
# handle_nack
# ---------------------------------------------------------------------------


def test_handle_nack_retryable() -> None:
    """handle_nack marks transient errors as retryable."""
    result = handle_nack("ANSPROTO-1", nack_motivo="Erro transiente", retry_attempt=1)
    assert result["retryable"] is True
    assert result["status"] == "nack"


def test_handle_nack_non_retryable() -> None:
    """handle_nack marks permanent errors as non-retryable."""
    result = handle_nack("ANSPROTO-1", nack_motivo="Schema invalido permanente", retry_attempt=1)
    assert result["retryable"] is False


# ---------------------------------------------------------------------------
# retry_submission — DMN ans_retry_policy
# ---------------------------------------------------------------------------


def test_retry_submission_attempt_1() -> None:
    """Retry attempt 1 -> PT5M, continue=True (via the dmn= seam, ADR-0028/T1.5)."""
    fake = _ans_retry_policy_fake(backoff="PT5M", continue_retry=True)
    result = retry_submission("ANSPROTO-1", retry_attempt=1, dmn=fake)
    assert result.backoff == "PT5M"
    assert result.continue_retry is True
    assert fake.calls == [("ans_retry_policy", {"retry_attempt": 1})]


def test_retry_submission_attempt_2() -> None:
    """Retry attempt 2 -> PT30M."""
    fake = _ans_retry_policy_fake(backoff="PT30M", continue_retry=True)
    result = retry_submission("ANSPROTO-1", retry_attempt=2, dmn=fake)
    assert result.backoff == "PT30M"
    assert result.continue_retry is True


def test_retry_submission_attempt_3() -> None:
    """Retry attempt 3 -> PT2H."""
    fake = _ans_retry_policy_fake(backoff="PT2H", continue_retry=True)
    result = retry_submission("ANSPROTO-1", retry_attempt=3, dmn=fake)
    assert result.backoff == "PT2H"
    assert result.continue_retry is True


def test_retry_submission_exhausted_reports_never_raises() -> None:
    """Retry attempt > 3 -> DMN catch-all (continue_retry=false) is REPORTED, never raised.

    The engine owns the exhaustion route (`BRT_RetryPolicy` -> `GW_ContinuarRetry` ->
    `End_RetryEsgotado` -> `BE_RetryEsgotado` -> `UT_TratarNack`). A raise here would fail the
    external task and strand the token on `ST_RetransmitirEnvio`, making the modeled terminal
    unreachable — the exact defect this replaced.
    """
    fake = _ans_retry_policy_fake(backoff="", continue_retry=False)
    result = retry_submission("ANSPROTO-1", retry_attempt=4, dmn=fake)
    assert result.continue_retry is False
    assert result.backoff == ""
    assert result.retry_attempt == 4


def test_retry_submission_dmn_unwired_raises_dmn_evaluation_error() -> None:
    """`require_dmn` fail-closed guard — an unwired seam must not silently skip the decision."""
    with pytest.raises(DmnEvaluationError):
        retransmit_entry({"protocolo_ans": "ANSPROTO-1", "retry_attempt": 1}, dmn=None)


# ---------------------------------------------------------------------------
# publish_completed
# ---------------------------------------------------------------------------


def test_publish_completed_anssubmit() -> None:
    """publish_completed emits domain event."""
    result = publish_completed(desfecho="enviado_ack")
    assert result["published"] is True
    assert result["payload"]["desfecho"] == "enviado_ack"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


def test_ans_submit_not_human_is_permission_error() -> None:
    """AnsSubmitNotHumanError must be a subclass of PermissionError."""
    assert issubclass(AnsSubmitNotHumanError, PermissionError)


def test_ans_dataset_incompleto_is_value_error() -> None:
    """AnsDatasetIncompletoError must be a subclass of ValueError."""
    assert issubclass(AnsDatasetIncompletoError, ValueError)


def test_no_worker_side_retry_esgotado_exception_exists() -> None:
    """Retry exhaustion is MODEL-owned — no worker exception may re-introduce it.

    `End_RetryEsgotado` (an error END event inside `SUB_RetryEnvio`) throws
    `Error_AnsRetryEsgotado` to `BE_RetryEsgotado`; the contract and the `ans_retry_policy` DMN
    description both name the subprocess as the thrower. The removed `AnsRetryEsgotadoError`
    (a `RuntimeError`, i.e. the harness's TRANSIENT family) actively PREVENTED that route: it
    failed `ST_RetransmitirEnvio` instead of completing it, so the token never advanced to
    `GW_RetransmissaoOk` -> `BRT_RetryPolicy` -> `GW_ContinuarRetry`. This fence keeps it gone.
    """
    import maezo.tools.workers.ans_submit as ans_submit_module

    assert not hasattr(ans_submit_module, "AnsRetryEsgotadoError")


# ---------------------------------------------------------------------------
# Dict-boundary entry functions (T1.2/ADR-0026 §2b) — round-trip vs calling the
# typed function directly; fail-closed marshalling on invalid/missing input.
# ---------------------------------------------------------------------------


def test_assemble_entry_round_trips_prepare_submission() -> None:
    variables = {
        "tenant_id": "amh",
        "report_type": "SIP",
        "competencia": "2026-06",
        "dataset_ref": "dataset-SIP-2026-06",
        "dataset_complete": True,
        "schema_valid": True,
        "lgpd_anonimizado": True,
        "unrelated_process_variable": "must be ignored",
    }
    direct = prepare_submission(
        AnsSubmitInput(**{k: v for k, v in variables.items() if k != "unrelated_process_variable"})
    )
    entry_result = assemble_entry(variables)

    assert entry_result["dataset_ref"] == direct.dataset_ref
    assert entry_result["dataset_complete"] == direct.dataset_complete


def test_validate_entry_round_trips_validate_data() -> None:
    variables = {"dataset_ref": "dataset-1", "dataset_complete": True, "schema_valid": True}
    assert validate_entry(variables) == validate_data(AnsSubmissionData(**variables))


def test_submit_entry_guards_missing_human_decision() -> None:
    """submit_entry raises the UNCHANGED AnsSubmitNotHumanError guard when decisao_envio/
    revisor_id are missing — the entry function only marshals, it never loosens the guard."""
    with pytest.raises(AnsSubmitNotHumanError):
        submit_entry({"report_type": "SIP", "decisao_envio": ""})


def test_submit_entry_happy_path() -> None:
    variables = {
        "tenant_id": "amh",
        "report_type": "SIP",
        "competencia": "2026-06",
        "decisao_envio": "APROVAR_ENVIO",
        "revisor_id": "revisor-1",
    }
    # Dev/test explicitly inject the LabeledMock gateway (design §2.A) — with NO gateway the
    # prod-default Refusing transport would fail closed (see the prod-config tests below).
    result = submit_entry(variables, ans_gateway=LabeledMockAnsGatewayTransport())
    assert result["submitted"] is True
    assert result["revisor_id"] == "revisor-1"
    # Deterministic synthetic protocol keyed on the derived business key.
    assert result["protocolo_ans"] == f"{MOCK_ANS_PROTOCOL_PREFIX}ANSSUB-amh-SIP-2026-06"
    assert result["synthetic"] is True and result["vinculativo"] is False


def test_ans_business_key_returns_stripped_explicit_key() -> None:
    """`_ans_business_key` returns the STRIPPED explicit `business_key` (item 2 fix) — mirrors
    `recurso.py`'s `_mint_protocolo_recurso` sibling (`business_key.strip() or f"RECURSO-..."`).

    Pre-fix this tested `bk.strip()` for non-blankness but then returned the UNSTRIPPED `bk` — a
    whitespace-padded engine business key would derive a DIFFERENT synthetic protocol than its
    stripped form, silently breaking the BPMN `:461` retransmit idempotency this function's
    docstring promises.
    """
    assert _ans_business_key({"business_key": "  ANSSUB-amh-SIP-2026-06  "}) == "ANSSUB-amh-SIP-2026-06"
    assert _ans_business_key({"business_key": "\tANSSUB-amh-SIP-2026-06\n"}) == "ANSSUB-amh-SIP-2026-06"
    # unpadded key is unaffected (idempotent strip)
    assert _ans_business_key({"business_key": "ANSSUB-amh-SIP-2026-06"}) == "ANSSUB-amh-SIP-2026-06"
    # whitespace-only key reads as ABSENT (same as pre-fix) -> reconstructed from parts. The
    # parts here are already clean, so this alone does not prove the reconstruct branch strips
    # them too — see test_ans_business_key_strips_reconstructed_parts below for that.
    whitespace_only = {
        "business_key": "   ",
        "tenant_id": "amh",
        "report_type": "SIP",
        "competencia": "2026-06",
    }
    assert _ans_business_key(whitespace_only) == "ANSSUB-amh-SIP-2026-06"


def test_ans_business_key_strips_reconstructed_parts() -> None:
    """`_ans_business_key`'s reconstruct branch (no usable explicit `business_key`) strips EACH
    part (`tenant_id`/`report_type`/`competencia`) individually before joining them — mirrors the
    explicit-key `.strip()` above so the two paths are equally immune to whitespace padding.

    Pre-fix, the reconstruct branch built the key from unstripped parts: a padded `tenant_id`
    of `"  amh  "` etc. produced the interior-whitespace key `"ANSSUB-  amh  - SIP - 2026-06 "`
    instead of the clean `"ANSSUB-amh-SIP-2026-06"` — a DIFFERENT string than the same logical
    identity's clean form, silently breaking the retransmit idempotency this function's docstring
    promises (same hazard the explicit-key `.strip()` fix above already closed for that path).
    """
    padded_parts = {
        "business_key": "",
        "tenant_id": "  amh  ",
        "report_type": " SIP ",
        "competencia": " 2026-06 ",
    }
    assert _ans_business_key(padded_parts) == "ANSSUB-amh-SIP-2026-06"


def test_submit_entry_retransmit_key_stability_with_padded_engine_key() -> None:
    """RETRANSMIT-KEY STABILITY (item 2): a retransmit carrying the SAME logical business key,
    whitespace-padded by the engine on the second delivery, mints the IDENTICAL synthetic
    protocol as the clean first delivery — otherwise a merely-padded retransmit would look like a
    brand-new submission to `LabeledMockAnsGatewayTransport`, defeating the BPMN `:461`
    idempotency `_ans_business_key`'s docstring promises.
    """
    variables = {
        "tenant_id": "amh",
        "report_type": "SIP",
        "competencia": "2026-06",
        "decisao_envio": "APROVAR_ENVIO",
        "revisor_id": "revisor-1",
    }
    clean = submit_entry(
        {**variables, "business_key": "ANSSUB-amh-SIP-2026-06"},
        ans_gateway=LabeledMockAnsGatewayTransport(),
    )
    padded_retransmit = submit_entry(
        {**variables, "business_key": "  ANSSUB-amh-SIP-2026-06  "},
        ans_gateway=LabeledMockAnsGatewayTransport(),
    )
    assert clean["protocolo_ans"] == padded_retransmit["protocolo_ans"]
    assert clean["protocolo_ans"] == f"{MOCK_ANS_PROTOCOL_PREFIX}ANSSUB-amh-SIP-2026-06"


def test_track_protocol_entry_raises_on_missing_protocolo() -> None:
    """Fail-closed (ADR-0026 §2b): a missing/blank protocolo_ans must raise
    AnsDatasetIncompletoError rather than silently tracking an empty protocol."""
    with pytest.raises(AnsDatasetIncompletoError):
        track_protocol_entry({"protocolo_ans": ""})
    with pytest.raises(AnsDatasetIncompletoError):
        track_protocol_entry({})


def test_track_protocol_entry_happy_path() -> None:
    result = track_protocol_entry({"protocolo_ans": "ANSPROTO-1", "nack_motivo": "timeout"})
    assert result["protocolo_ans"] == "ANSPROTO-1"
    assert result["retryable"] is True


def test_retransmit_entry_raises_on_missing_protocolo() -> None:
    with pytest.raises(AnsDatasetIncompletoError):
        retransmit_entry({"retry_attempt": 1})


def test_retransmit_entry_happy_path_round_trips_retry_submission() -> None:
    variables = {"protocolo_ans": "ANSPROTO-1", "retry_attempt": 1}
    direct = retry_submission(
        "ANSPROTO-1", 1, dmn=_ans_retry_policy_fake(backoff="PT5M", continue_retry=True)
    )
    result = retransmit_entry(variables, dmn=_ans_retry_policy_fake(backoff="PT5M", continue_retry=True))
    assert result["backoff"] == direct.backoff
    assert result["continue_retry"] == direct.continue_retry


def test_retransmit_entry_reports_exhaustion_without_raising() -> None:
    """Exhaustion COMPLETES the external task with continue_retry=False so the engine can route it.

    `GW_ContinuarRetry` reads `${retry.continue_retry == true}` off `BRT_RetryPolicy`'s own
    evaluation; the worker must complete for the token to ever reach that gateway.
    """
    fake = _ans_retry_policy_fake(backoff="", continue_retry=False)
    result = retransmit_entry({"protocolo_ans": "ANSPROTO-1", "retry_attempt": 4}, dmn=fake)
    assert result["continue_retry"] is False
    assert result["retry_attempt"] == 5


def test_retransmit_entry_increments_the_loop_counter() -> None:
    """The worker owns `retry_attempt` (DMN description) — without the increment the modeled
    `SUB_RetryEnvio` loop never reaches the table's `> 3` catch-all and spins forever."""
    fake = _ans_retry_policy_fake(backoff="PT5M", continue_retry=True)
    # Absent counter (first entry into SUB_RetryEnvio) => this invocation is attempt 1.
    first = retransmit_entry({"protocolo_ans": "ANSPROTO-1"}, dmn=fake)
    assert first["retry_attempt"] == 1
    assert fake.calls == [("ans_retry_policy", {"retry_attempt": 1})]

    # ...and each subsequent pass advances by exactly one, which is what BRT_RetryPolicy re-reads.
    fake_2 = _ans_retry_policy_fake(backoff="PT30M", continue_retry=True)
    second = retransmit_entry({"protocolo_ans": "ANSPROTO-1", "retry_attempt": 1}, dmn=fake_2)
    assert second["retry_attempt"] == 2
    assert fake_2.calls == [("ans_retry_policy", {"retry_attempt": 2})]


def test_publish_completed_entry_round_trips_publish_completed() -> None:
    variables = {"event_type": "anssubmit.submitted", "desfecho": "enviado"}
    assert publish_completed_entry(variables) == publish_completed(
        event_type="anssubmit.submitted", payload={}, desfecho="enviado"
    )


# ---------------------------------------------------------------------------
# notify_regulatorio (T3.1 phase-2 FINDING A — registration gap regression fix).
# BPMN topic `regulatorio.anssubmit.notify_regulatorio` (ST_PrepararDossie dossie
# prep + ST_NotificarDeadlineRisk) had NO registered worker — the process stalled
# at ST_PrepararDossie ("no handler registered for topic"). Raw async handler,
# mirrors recurso's make_notify_sla_risk_handler.
# ---------------------------------------------------------------------------


def _notify_task(*, business_key: str = "ANSSUB-amh-RN_124_SIP-2026-01", **variables: object) -> ExternalTask:
    return ExternalTask(
        task_id="task-notify-1",
        topic="regulatorio.anssubmit.notify_regulatorio",
        process_instance_id="proc-1",
        business_key=business_key,
        worker_id="w-1",
        variables=dict(variables),
    )


def test_notify_regulatorio_dossie_variant_no_deadline_risk() -> None:
    """ST_PrepararDossie: no `event_topic_deadline_risk` -> dossie variant, no adverse effect."""
    result = notify_regulatorio(
        AnsNotifyRegulatorioInput(tenant_id="amh", report_type="RN_124_SIP", competencia="2026-01")
    )
    assert result["notify_regulatorio_sent"] is True
    assert result["deadline_risk"] is False
    assert result["report_type"] == "RN_124_SIP"


def test_notify_regulatorio_deadline_risk_variant() -> None:
    """ST_NotificarDeadlineRisk: `event_topic_deadline_risk` present -> deadline-risk variant."""
    result = notify_regulatorio(
        AnsNotifyRegulatorioInput(tenant_id="amh", report_type="RN_124_SIP", competencia="2026-01"),
        event_topic_deadline_risk="agents.events.anssubmit.deadline_risk",
    )
    assert result["notify_regulatorio_sent"] is True
    assert result["deadline_risk"] is True


async def test_make_notify_regulatorio_handler_publishes_notification() -> None:
    """Handler emits the donor's `anssubmit.notify_regulatorio` notification to the internal
    channel (observable via notifications_of_type — the donor's happy-path assertion)."""
    kafka = FakeKafkaPublisher()
    handler = make_notify_regulatorio_handler(kafka)
    task = _notify_task(
        tenant_id="amh", report_type="RN_124_SIP", competencia="2026-01", periodicidade="mensal"
    )
    result = await handler(task)
    assert result["notify_regulatorio_sent"] is True
    assert len(kafka.published) == 1
    topic, payload, key = kafka.published[0]
    assert topic == "operadora.notifications.internal"
    assert payload["type"] == "anssubmit.notify_regulatorio"
    assert payload["report_type"] == "RN_124_SIP"
    assert payload["deadline_risk"] is False
    assert key == task.business_key
    # DECIDED KEEP (DL-0038, t2-notify-integrity): notify_regulatorio STAYS topic-default
    # best-effort BY DESIGN — advisory on both variants (dossie: the MAIN ANS-filing path must
    # not incident on a ping, engine timers guard the deadline; deadline-risk: the fail-closed
    # domain-event publish one step downstream incidents a real outage anyway). This pin goes
    # RED if someone flips the posture.
    assert kafka.best_effort_calls == [None]


async def test_make_notify_regulatorio_handler_deadline_risk_stamps_topic() -> None:
    kafka = FakeKafkaPublisher()
    handler = make_notify_regulatorio_handler(kafka)
    task = _notify_task(
        tenant_id="amh",
        report_type="RN_124_SIP",
        competencia="2026-01",
        event_topic_deadline_risk="agents.events.anssubmit.deadline_risk",
    )
    result = await handler(task)
    assert result["deadline_risk"] is True
    _topic, payload, _key = kafka.published[0]
    assert payload["deadline_risk"] is True
    assert payload["event_topic_deadline_risk"] == "agents.events.anssubmit.deadline_risk"


async def test_make_notify_regulatorio_handler_fail_closed_no_producer() -> None:
    """kafka=None: completes anyway (informational-only — blocking would starve the
    Flow_GW_Revisao happy path to UT_RevisarEnvio and the shared deadline-risk timers), never
    fabricates a publish."""
    result = await make_notify_regulatorio_handler(None)(_notify_task(report_type="RN_124_SIP"))
    assert result["notify_regulatorio_sent"] is True


def test_register_ans_submit_workers_registers_notify_regulatorio() -> None:
    """Regression fence for FINDING A: register_ans_submit_workers MUST wire all 7 BPMN topics
    it owns (6 FunctionWorker entries + the notify_regulatorio raw handler)."""
    harness = WorkerHarness(FakeWorkerTransport(), worker_id="probe")
    register_ans_submit_workers(harness, FakeKafkaPublisher())
    topics = set(harness.registered_topics)
    assert "regulatorio.anssubmit.notify_regulatorio" in topics, "notify_regulatorio not registered"
    for topic in (
        "regulatorio.anssubmit.assemble",
        "regulatorio.anssubmit.validate",
        "regulatorio.anssubmit.submit",
        "regulatorio.anssubmit.track_protocol",
        "regulatorio.anssubmit.retransmit",
        "regulatorio.anssubmit.publish_completed",
    ):
        assert topic in topics, f"{topic} regressed out of register_ans_submit_workers"
