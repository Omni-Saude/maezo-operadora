"""Unit tests for SP-OP-AUTH-001 workers — TDD London School.

Tests: denial guard (ERR_AUTH_DENIAL_NOT_HUMAN), auto-approve, analyze_request,
request_documents, issue_authorization, notify_sla_risk, convene_junta.

CRITICAL: Workers must NEVER make adverse decisions (negativa, acusacao).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from maezo.tools.workers.auth import (
    _REQUIRED_DENIAL_FIELDS,
    AnalyzeRequestWorker,
    ConveneJuntaWorker,
    IssueAuthorizationWorker,
    NotifySlaRiskWorker,
    RequestDocumentsWorker,
    SendDenialNoticeWorker,
)
from maezo.tools.workers.base import ERR_AUTH_DENIAL_INCOMPLETE, ERR_DENIAL_NOT_HUMAN
from maezo.tools.workers.ceilings import CeilingResolver
from maezo.tools.workers.harness import WorkerBpmnError
from maezo.tools.workers.phi_vars import REDACTED_PHI

# A fully-grounded NEGAR dossier (all three ANS RN-395 fields present). Tests that exercise the
# human_approved guard or the happy notice_sent path must be COMPLETE first, otherwise the
# completeness guard (checked first, T3.1) short-circuits with ERR_AUTH_DENIAL_INCOMPLETE.
_COMPLETE_DENIAL_FUNDAMENTACAO = {
    "justificativa_clinica": "Procedimento fora do ROL vigente (sintetico para teste)",
    "cid10_referencia": "Z00.0",
    "fundamentacao_dut": "DUT item 3.1 — exclusao sintetica para teste",
}

# T3.1 (mirrors ADR-0031's `identidade_verificada` fail-closed matrix, test_lgpd_erasure.py):
# the EXPLICIT `human_approved` signal is pinned to the boolean `True` — NOT bare truthiness.
# Absent / False / None / any truthy junk (string incl. whitespace-only, int, list, dict) must
# NEVER be read as human approval. Shared vectors reused across issue_authorization /
# send_denial_notice / convene_junta's own parametrized fail-closed tests below — each test
# supplies NO other provenance evidence (no decisao_auditor route literal, no auditor_id, no
# auto_aprovacao sanction), so the explicit-signal channel is proven fail-closed in isolation
# (item-9 bucket-3 Class-C: the guards now ALSO derive provenance from the real human-decision
# evidence — covered by their own dedicated tests below).
_HUMAN_APPROVED_NON_TRUE_VECTORS: list[dict[str, object]] = [
    {},  # human_approved absent
    {"human_approved": False},  # explicit False
    {"human_approved": None},  # explicit None
    {"human_approved": "true"},  # garbage: truthy string, not the bool True
    {"human_approved": " "},  # garbage: whitespace-only truthy string
    {"human_approved": 1},  # garbage: truthy int, not the bool True
    {"human_approved": [1]},  # garbage: truthy list, not the bool True
    {"human_approved": {"ok": True}},  # garbage: truthy dict, not the bool True
]

# Synthetic-core helper — pins the AUTH ceiling in isolation (mirrors test_ceilings) so these
# tests do not depend on the D-07 value (max_value_brl=0) in the real spec matrix.
_HARD_BLOCK = """\
  clinical_decision:      { level: L0, hard: true }
  authorization_denial:   { level: L0, hard: true }
  nip_manter_negativa:    { level: L0, hard: true }
  fraud_accusation:       { level: L0, hard: true }
  contract_termination:   { level: L0, hard: true }
"""


# Spec fence (GK-w4) — the human-provenance guard above only works if the MODEL actually asks the
# human for `auditor_id`. tests/unit/tools/workers/<file> -> parents[4] == repo root (same idiom as
# test_bootstrap_registration.py). Namespaces mirror scripts/ci/check_bpmn_error_allowlist.py.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_AUTH_BPMN = _REPO_ROOT / "spec" / "processes" / "bpmn" / "SP-OP-AUTH-001_Autorizacao_Previa.bpmn"
_BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
_CAMUNDA_NS = "http://camunda.org/schema/1.0/bpmn"

# The three User Tasks where a NEGAR can be born (SP-OP-AUTH-001 L0 invariant).
_HUMAN_DECISION_UTS = (
    "UT_AnaliseMedicoAuditor",
    "UT_CoordenacaoAssume",
    "UT_RegistrarParecerJunta",
)

# The full declared formField set of each of those UTs: the four decision/grounding fields plus
# `auditor_id`. Pinned as an exact set so neither a silent removal nor a silent rename regresses
# the model out from under the worker guards.
_EXPECTED_UT_FORM_FIELDS = {
    "decisao_auditor",
    "justificativa_clinica",
    "cid10_referencia",
    "fundamentacao_dut",
    "auditor_id",
}


def _pin_resolver(tmp_path: Path, max_value_brl: int) -> CeilingResolver:
    """Return a CeilingResolver pinned to a synthetic core with a given AUTH ceiling."""
    core = tmp_path / "L0-core.yaml"
    core.write_text(
        "version: 1\nactions:\n"
        f"{_HARD_BLOCK}"
        f"  authorization_approval: {{ level: L2, params: {{ max_value_brl: {max_value_brl} }} }}\n",
        encoding="utf-8",
    )
    return CeilingResolver(core_path=core)


# ---------------------------------------------------------------------------
# send_denial_notice — denial guard (ERR_AUTH_DENIAL_NOT_HUMAN)
# ---------------------------------------------------------------------------


def test_send_denial_notice_topic() -> None:
    """send_denial_notice worker must have topic 'operadora.auth.send_denial_notice'."""
    worker = SendDenialNoticeWorker()
    assert worker.topic == "operadora.auth.send_denial_notice"


def test_send_denial_notice_guard_prevents_automatic_denial() -> None:
    """send_denial_notice MUST refuse to send denial without human authorization.

    The ERR_DENIAL_NOT_HUMAN guard prevents automatic adverse actions. Dossier is COMPLETE so the
    T3.1 completeness guard passes and this exercises the human-provenance guard specifically:
    NO explicit human_approved AND NO auditor_id accountability -> blocked.
    """
    worker = SendDenialNoticeWorker()

    # Simulate a denial attempt without any human provenance (but with complete fundamentacao).
    process_vars = {
        "tenant_id": "amh",
        "beneficiario_pseudo_id": "pseudo-abc",
        "decisao_auditor": "NEGAR",
        **_COMPLETE_DENIAL_FUNDAMENTACAO,
        # Note: NO human_approved flag and NO auditor_id
    }

    result = worker.run(process_vars)

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


def test_send_denial_notice_allows_when_human_approved() -> None:
    """send_denial_notice allows denial when human_approved flag is present (and dossier complete)."""
    worker = SendDenialNoticeWorker()

    process_vars = {
        "tenant_id": "amh",
        "beneficiario_pseudo_id": "pseudo-abc",
        "decisao_auditor": "NEGAR",
        **_COMPLETE_DENIAL_FUNDAMENTACAO,
        "human_approved": True,  # Human auditor approved
    }

    result = worker.run(process_vars)

    assert result["status"] == "notice_sent"
    assert result["error_code"] is None
    # PHI egress enforcement: clinical fields are redacted one-way in the emitted payload.
    assert result["justificativa_clinica"] == REDACTED_PHI
    assert result["cid10_referencia"] == REDACTED_PHI
    assert result["fundamentacao_dut"] == REDACTED_PHI


def test_send_denial_notice_guard_activates_on_denial() -> None:
    """The human_approved guard activates specifically when decisao_auditor == 'NEGAR'."""
    worker = SendDenialNoticeWorker()

    # NEGAR, complete fundamentacao, no human -> human_approved guard fires (not completeness).
    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "NEGAR",
            **_COMPLETE_DENIAL_FUNDAMENTACAO,
        }
    )
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN

    # APROVAR (neither guard fires — no clinical fields, no completeness requirement).
    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "APROVAR",
            "human_approved": True,
        }
    )
    assert result["status"] == "notice_sent"
    assert result["error_code"] is None


@pytest.mark.parametrize("vars_extra", _HUMAN_APPROVED_NON_TRUE_VECTORS)
def test_send_denial_notice_fail_closed_rejects_non_true_human_approved(
    vars_extra: dict[str, object],
) -> None:
    """FAIL-CLOSED (T3.1): GUARD 2 only accepts the literal `human_approved is True`.

    Dossier is COMPLETE so the T3.1 completeness guard passes and this exercises the human_approved
    guard specifically. Absent/False/None/truthy-junk (string incl. whitespace-only, int, list,
    dict) must NEVER be read as human approval — closes the fail-OPEN class this change fixes.
    """
    worker = SendDenialNoticeWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "NEGAR",
            **_COMPLETE_DENIAL_FUNDAMENTACAO,
            **vars_extra,
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


# ---------------------------------------------------------------------------
# send_denial_notice — human-provenance derivation via auditor_id (item-9 bucket-3 Class-C:
# the human_approved threading fix. The live NEGAR payloads of the human UTs carry auditor_id
# — the ADR-0007 accountability field — and nothing in the model ever sets human_approved.)
# ---------------------------------------------------------------------------


def test_send_denial_notice_allows_with_auditor_id_accountability() -> None:
    """The exact live human-NEGAR shape (UT payload: NEGAR + grounding + auditor_id, NO
    human_approved flag) transmits — and threads the resolved provenance back."""
    worker = SendDenialNoticeWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "NEGAR",
            **_COMPLETE_DENIAL_FUNDAMENTACAO,
            "auditor_id": "dr-auditor-sintetico-001",
        }
    )

    assert result["status"] == "notice_sent"
    assert result["error_code"] is None
    assert result["human_approved"] is True  # threaded provenance (engine-visible)
    # PHI egress unchanged: clinical fields still redacted one-way.
    for field in _REQUIRED_DENIAL_FIELDS:
        assert result[field] == REDACTED_PHI


@pytest.mark.parametrize(
    "bad_auditor_id",
    ["", "   ", "\t\n", None, 123, True, ["dr-x"], {"id": "dr-x"}],
)
def test_send_denial_notice_fail_closed_rejects_junk_auditor_id(bad_auditor_id: object) -> None:
    """FAIL-CLOSED: whitespace-only/non-string auditor_id normalizes to "" and is NEVER read as
    human accountability (mirrors the sibling guards' _norm_str discipline) — blocked."""
    worker = SendDenialNoticeWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "NEGAR",
            **_COMPLETE_DENIAL_FUNDAMENTACAO,
            "auditor_id": bad_auditor_id,
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


def test_send_denial_notice_completeness_still_precedes_auditor_id_provenance() -> None:
    """Ordering preserved: an INCOMPLETE NEGAR raises ERR_AUTH_DENIAL_INCOMPLETE even with full
    auditor_id accountability — provenance never licenses an ungrounded denial."""
    worker = SendDenialNoticeWorker()
    with pytest.raises(WorkerBpmnError) as exc:
        worker.execute(
            {
                "tenant_id": "amh",
                "decisao_auditor": "NEGAR",
                "auditor_id": "dr-auditor-sintetico-001",
                # grounding fields MISSING
            }
        )
    assert exc.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


# ---------------------------------------------------------------------------
# SPEC FENCE (GK-w4 major) — `auditor_id` must be MODELED, not just consumed.
#
# The provenance guard above accepts a non-blank `auditor_id` as the ADR-0007 human-accountability
# evidence. That only holds if SP-OP-AUTH-001 actually asks the human for it: before this fence the
# three UTs declared exactly four formFields and `auditor_id` appeared nowhere in the BPMN, so a
# model-conforming Tasklist client submitting only the declared fields had its genuinely-human NEGAR
# blocked (ERR_DENIAL_NOT_HUMAN), and the variable was start-seedable with no modeled task
# overwriting it. These tests pin the model so it cannot silently regress under the worker.
# ---------------------------------------------------------------------------


def _ut_form_fields(user_task_id: str) -> dict[str, ET.Element]:
    """Return {formField id: element} declared by a User Task of the AUTH spec BPMN."""
    root = ET.parse(_AUTH_BPMN).getroot()
    for user_task in root.iter(f"{{{_BPMN_NS}}}userTask"):
        if user_task.get("id") == user_task_id:
            return {field.get("id", ""): field for field in user_task.iter(f"{{{_CAMUNDA_NS}}}formField")}
    raise AssertionError(f"userTask {user_task_id!r} not found in {_AUTH_BPMN}")


def _field_properties(form_field: ET.Element) -> dict[str, str]:
    """Return {camunda:property name: value} of a formField."""
    return {
        prop.get("name", ""): prop.get("value", "") for prop in form_field.iter(f"{{{_CAMUNDA_NS}}}property")
    }


def test_auth_spec_bpmn_exists() -> None:
    """Guards the fence below against a silently-vacuous pass (fail loud, never fabricate)."""
    assert _AUTH_BPMN.is_file(), f"AUTH spec BPMN not found at {_AUTH_BPMN}"


@pytest.mark.parametrize("user_task_id", _HUMAN_DECISION_UTS)
def test_spec_declares_auditor_id_on_every_human_decision_ut(user_task_id: str) -> None:
    """Each UT where a NEGAR can be born declares `auditor_id` as a string formField."""
    fields = _ut_form_fields(user_task_id)
    assert "auditor_id" in fields, (
        f"{user_task_id} does not declare auditor_id — a model-conforming Tasklist client cannot "
        "supply the ADR-0007 provenance the send_denial_notice guard requires"
    )
    assert fields["auditor_id"].get("type") == "string"


@pytest.mark.parametrize("user_task_id", _HUMAN_DECISION_UTS)
def test_spec_ut_form_field_set_is_exactly_pinned(user_task_id: str) -> None:
    """Exact formField set per UT — catches a removal OR a rename of any declared field."""
    assert set(_ut_form_fields(user_task_id)) == _EXPECTED_UT_FORM_FIELDS


@pytest.mark.parametrize("user_task_id", _HUMAN_DECISION_UTS)
def test_spec_auditor_id_is_required_if_negar_and_enforced_by_the_worker(user_task_id: str) -> None:
    """`auditor_id` carries the file's own conditional-requiredness idiom, pointing at the guard."""
    props = _field_properties(_ut_form_fields(user_task_id)["auditor_id"])
    assert props.get("requiredIf") == "decisao_auditor == NEGAR"
    assert ERR_DENIAL_NOT_HUMAN in props.get("enforcedBy", "")
    assert "ADR-0007" in props.get("adr", "")


@pytest.mark.parametrize("user_task_id", _HUMAN_DECISION_UTS)
def test_spec_auditor_id_carries_no_unconditional_required_constraint(user_task_id: str) -> None:
    """No Camunda `required` constraint on auditor_id: it is required only on the NEGAR branch, and
    an unconditional constraint would wrongly block APROVAR/SOLICITAR_INFO/JUNTA_MEDICA, which share
    these UTs (the GAP-AUTH-2 rationale in the spec, and the sibling idiom of SP-OP-RECURSO-001 /
    SP-OP-REEMBOLSO-001: plain string identity field, enforcement in the worker guard)."""
    constraints = [
        constraint.get("name")
        for constraint in _ut_form_fields(user_task_id)["auditor_id"].iter(f"{{{_CAMUNDA_NS}}}constraint")
    ]
    assert constraints == []


# ---------------------------------------------------------------------------
# send_denial_notice — completeness guard (ERR_AUTH_DENIAL_INCOMPLETE, T3.1)
#
# SP-OP-AUTH-001 marks justificativa_clinica / cid10_referencia / fundamentacao_dut as
# requiredIf="decisao_auditor == NEGAR" + enforcedBy="worker send_denial_notice guard
# ERR_AUTH_DENIAL_INCOMPLETE" on all three human decision tasks (9 formField annotations, 3
# distinct fields). An incomplete negativa fundamentada (RN 395 art. 10) must NEVER be transmitted.
# ---------------------------------------------------------------------------


def test_required_denial_fields_match_spec() -> None:
    """The guard's required set is exactly the three spec-annotated grounding fields."""
    assert _REQUIRED_DENIAL_FIELDS == (
        "justificativa_clinica",
        "cid10_referencia",
        "fundamentacao_dut",
    )


@pytest.mark.parametrize("missing_field", _REQUIRED_DENIAL_FIELDS)
def test_denial_incomplete_each_field_individually_missing_raises(missing_field: str) -> None:
    """Completeness matrix: EACH required field individually absent -> ERR_AUTH_DENIAL_INCOMPLETE.

    Fail-closed even WITH human_approved=True: a human deciding NEGAR does not license transmitting
    an ungrounded denial. Absent = key not present at all.
    """
    worker = SendDenialNoticeWorker()
    process_vars = {
        "tenant_id": "amh",
        "decisao_auditor": "NEGAR",
        "human_approved": True,
        **{f: v for f, v in _COMPLETE_DENIAL_FUNDAMENTACAO.items() if f != missing_field},
    }

    with pytest.raises(WorkerBpmnError) as exc:
        worker.execute(process_vars)

    assert exc.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE
    # The BpmnError names the missing field but NEVER leaks a clinical VALUE.
    assert missing_field in str(exc.value)


@pytest.mark.parametrize("blank_value", [None, "", "   ", "\t\n"])
@pytest.mark.parametrize("blanked_field", _REQUIRED_DENIAL_FIELDS)
def test_denial_incomplete_blank_values_raise(blanked_field: str, blank_value: object) -> None:
    """None / empty / whitespace-only grounding is INCOMPLETE (inability to decide => DENY)."""
    worker = SendDenialNoticeWorker()
    process_vars = {
        "tenant_id": "amh",
        "decisao_auditor": "NEGAR",
        "human_approved": True,
        **_COMPLETE_DENIAL_FUNDAMENTACAO,
        blanked_field: blank_value,
    }

    with pytest.raises(WorkerBpmnError) as exc:
        worker.execute(process_vars)

    assert exc.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


@pytest.mark.parametrize("non_str_value", [0, 1, False, True, [], {}, ["texto"], {"k": "v"}, 3.14])
@pytest.mark.parametrize("field", _REQUIRED_DENIAL_FIELDS)
def test_denial_incomplete_non_string_grounding_raises(field: str, non_str_value: object) -> None:
    """A required grounding field that is not a non-empty STRING is incomplete (fail-closed).

    Closes the class the donor's `not v` half-covered: 0/[]/{}/False are blocked, and so are
    truthy-but-non-textual values (1, [texto], {k:v}, 3.14) that `not v` would wrongly admit — the
    BPMN types these fields `string`, a non-str grounding value is unusable.
    """
    worker = SendDenialNoticeWorker()
    process_vars = {
        "tenant_id": "amh",
        "decisao_auditor": "NEGAR",
        "human_approved": True,
        **_COMPLETE_DENIAL_FUNDAMENTACAO,
        field: non_str_value,
    }
    with pytest.raises(WorkerBpmnError) as exc:
        worker.execute(process_vars)
    assert exc.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


@pytest.mark.parametrize(
    "zero_width_value",
    [
        "\u200b",  # zero-width space alone
        "\ufeff",  # BOM / zero-width no-break space alone
        "\u200b\u200c\u200d\u2060\ufeff",  # the whole zero-width family
        "  \u200b\t\ufeff\n ",  # zero-width mixed with ordinary whitespace
    ],
)
@pytest.mark.parametrize("field", _REQUIRED_DENIAL_FIELDS)
def test_denial_incomplete_zero_width_only_grounding_raises(field: str, zero_width_value: str) -> None:
    """A grounding field of only zero-width / BOM chars is empty (str.strip() would miss them)."""
    worker = SendDenialNoticeWorker()
    process_vars = {
        "tenant_id": "amh",
        "decisao_auditor": "NEGAR",
        "human_approved": True,
        **_COMPLETE_DENIAL_FUNDAMENTACAO,
        field: zero_width_value,
    }
    with pytest.raises(WorkerBpmnError) as exc:
        worker.execute(process_vars)
    assert exc.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


def test_denial_incomplete_all_fields_missing_raises() -> None:
    """A bare NEGAR (no grounding at all) is incomplete -> ERR_AUTH_DENIAL_INCOMPLETE."""
    worker = SendDenialNoticeWorker()
    with pytest.raises(WorkerBpmnError) as exc:
        worker.execute({"tenant_id": "amh", "decisao_auditor": "NEGAR", "human_approved": True})
    assert exc.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


def test_denial_complete_all_present_sends_redacted_notice() -> None:
    """All three fields present + human_approved -> notice_sent, clinical fields redacted."""
    worker = SendDenialNoticeWorker()
    result = worker.execute(
        {
            "tenant_id": "amh",
            "decisao_auditor": "NEGAR",
            "human_approved": True,
            **_COMPLETE_DENIAL_FUNDAMENTACAO,
        }
    )
    assert result["status"] == "notice_sent"
    assert result["notice_type"] == "denial"
    for field in _REQUIRED_DENIAL_FIELDS:
        assert result[field] == REDACTED_PHI


def test_denial_completeness_guard_is_not_retried() -> None:
    """max_retries=1: a deterministic completeness WorkerBpmnError propagates on first attempt.

    Proven by driving the full retry-wrapped run() path (not just execute()): it must raise
    immediately, never swallow/retry the guard.
    """
    worker = SendDenialNoticeWorker()
    assert worker.max_retries == 1
    with pytest.raises(WorkerBpmnError) as exc:
        worker.run({"tenant_id": "amh", "decisao_auditor": "NEGAR", "human_approved": True})
    assert exc.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


def test_denial_completeness_precedes_human_guard() -> None:
    """Ordering: an INCOMPLETE denial raises completeness even when human_approved is absent.

    This is the exact live-flow shape (human_approved is never wired) — the incomplete denial must
    reach the ERR_AUTH_DENIAL_INCOMPLETE boundary, NOT fall through to a human_approved dict-return
    (which would let the incomplete denial proceed to End_NegadaAuditor).
    """
    worker = SendDenialNoticeWorker()
    with pytest.raises(WorkerBpmnError) as exc:
        worker.execute(
            {
                "tenant_id": "amh",
                "decisao_auditor": "NEGAR",
                "justificativa_clinica": "presente",
                "cid10_referencia": "Z00.0",
                # fundamentacao_dut MISSING; human_approved MISSING
            }
        )
    assert exc.value.error_code == ERR_AUTH_DENIAL_INCOMPLETE


# ---------------------------------------------------------------------------
# send_denial_notice — PHI egress enforcement (adversarial)
# ---------------------------------------------------------------------------


def test_denial_adversarial_phi_never_appears_in_emitted_variables() -> None:
    """PHI planted in EVERY clinical field must never appear in any emitted variable value.

    Plants uniquely-identifiable PHI (CPF, CNS, name, CID) in each grounding field and asserts none
    of it survives into the worker's emitted output — proving the enforcement point is structural,
    not incidental.
    """
    worker = SendDenialNoticeWorker()
    planted = {
        "justificativa_clinica": "Paciente JOAO DA SILVA, CPF 123.456.789-00, quadro grave",
        "cid10_referencia": "C50.9",  # a real CID-10 code = clinical PHI
        "fundamentacao_dut": "CNS 700123456789012, laudo detalhado do oncologista",
    }
    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "NEGAR",
            "human_approved": True,
            **planted,
        }
    )

    emitted = " ".join(str(v) for v in result.values())
    for phi_snippet in ("JOAO DA SILVA", "123.456.789-00", "C50.9", "700123456789012", "oncologista"):
        assert phi_snippet not in emitted, f"PHI leaked into emitted variables: {phi_snippet!r}"
    # And the clinical keys, if present, hold only the class token.
    for field in _REQUIRED_DENIAL_FIELDS:
        assert result[field] == REDACTED_PHI


# ---------------------------------------------------------------------------
# Auto-approve (L2 autonomy)
# ---------------------------------------------------------------------------


def test_analyze_request_topic() -> None:
    """analyze_request worker must have the correct topic."""
    worker = AnalyzeRequestWorker()
    assert worker.topic == "operadora.auth.analyze_request"


def test_analyze_request_convoca_rafael() -> None:
    """analyze_request must produce a dossier for Rafael (human auditor)."""
    worker = AnalyzeRequestWorker()

    process_vars = {
        "tenant_id": "amh",
        "numero_guia_tiss": "G12345",
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "carater_atendimento": "eletivo",
        "beneficiario_pseudo_id": "pseudo-abc",
    }

    result = worker.run(process_vars)

    assert result["status"] == "dossier_created"
    assert result["assigned_to"] == "rafael"
    assert "dossier_ref" in result


def test_analyze_request_auto_approve_eligible(tmp_path: Path) -> None:
    """analyze_request auto-approves when L2 conditions are met AND value is within the teto.

    L2 auto-approval requires: dut_atendida + (COMPUTED) dentro_teto_l2 + rede_credenciada +
    beneficiario_ativo + carencia_cumprida + documentacao_completa. After T1.9 the ceiling is
    computed from policy (not the inbound flag), so this test pins a positive ceiling (R$500)
    and supplies `valor_estimado_brl` within it. The inbound `dentro_teto_l2` is now irrelevant.
    """
    worker = AnalyzeRequestWorker(resolver=_pin_resolver(tmp_path, max_value_brl=500))

    process_vars = {
        "tenant_id": "amh",
        "numero_guia_tiss": "G12345",
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "carater_atendimento": "eletivo",
        "beneficiario_pseudo_id": "pseudo-abc",
        "dut_atendida": True,
        "valor_estimado_brl": 100.0,  # R$100 -> 10000 cents, within the R$500 (50000 cents) teto
        "rede_credenciada": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": True,
        "requer_autorizacao": True,
        "documentacao_completa": True,
    }

    result = worker.run(process_vars)

    assert result["status"] == "auto_approved"
    assert result["recommendation"] == "AUTO_APROVAR"
    assert result["dentro_teto_l2"] is True  # the COMPUTED fact is written back for the DMN


# ---------------------------------------------------------------------------
# T1.9 ceiling enforcement (defect B3) — AUTH recomputes dentro_teto_l2 from policy
# ---------------------------------------------------------------------------


def test_auth_recomputes_dentro_teto_l2_ignoring_inbound(tmp_path: Path) -> None:
    """A seeded inbound `dentro_teto_l2=True` is IGNORED — ceiling 0 (D-07) blocks auto-approval.

    All other auto-approve facts are True and `valor_estimado_brl` is set, but the pinned
    ceiling is 0, so teto_ok=False -> the request is NOT auto-approved (routes to Rafael).
    """
    worker = AnalyzeRequestWorker(resolver=_pin_resolver(tmp_path, max_value_brl=0))

    process_vars = {
        "tenant_id": "amh",
        "numero_guia_tiss": "G12345",
        "dut_atendida": True,
        "dentro_teto_l2": True,  # attacker/upstream seed — must be ignored
        "valor_estimado_brl": 100.0,
        "rede_credenciada": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": True,
        "documentacao_completa": True,
    }

    result = worker.run(process_vars)

    assert result["status"] != "auto_approved"
    assert result["status"] == "dossier_created"
    assert result["dentro_teto_l2"] is False


def test_auth_absent_valor_estimado_fails_closed(tmp_path: Path) -> None:
    """Missing / None / non-numeric `valor_estimado_brl` -> teto_ok=False under a POSITIVE ceiling.

    Mandated by design Rev 4 (§2.4/§5.5a): a defaulted 0 would read as "within ceiling" under a
    positive teto (fail-OPEN). The hardened behavior routes such requests to human review.
    """
    worker = AnalyzeRequestWorker(resolver=_pin_resolver(tmp_path, max_value_brl=500))

    base_vars = {
        "tenant_id": "amh",
        "numero_guia_tiss": "G12345",
        "dut_atendida": True,
        "rede_credenciada": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": True,
        "documentacao_completa": True,
    }

    # (a) absent valor_estimado_brl
    result_absent = worker.run(dict(base_vars))
    assert result_absent["status"] != "auto_approved"
    assert result_absent["dentro_teto_l2"] is False

    # (b) None valor_estimado_brl
    result_none = worker.run({**base_vars, "valor_estimado_brl": None})
    assert result_none["status"] != "auto_approved"

    # (c) non-numeric valor_estimado_brl
    result_bad = worker.run({**base_vars, "valor_estimado_brl": "nao-e-numero"})
    assert result_bad["status"] != "auto_approved"

    # control: a numeric value WITHIN the teto DOES auto-approve (proves the ceiling is live)
    result_ok = worker.run({**base_vars, "valor_estimado_brl": 100.0})
    assert result_ok["status"] == "auto_approved"


def test_analyze_request_requires_human_for_non_auto() -> None:
    """analyze_request routes to human analysis when auto conditions not met."""
    worker = AnalyzeRequestWorker()

    process_vars = {
        "tenant_id": "amh",
        "numero_guia_tiss": "G12345",
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "internacao",
        "carater_atendimento": "urgencia",
        "beneficiario_pseudo_id": "pseudo-abc",
        "dut_atendida": False,  # DUT not met
        "dentro_teto_l2": True,
        "rede_credenciada": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": True,
        "requer_autorizacao": True,
        "documentacao_completa": True,
    }

    result = worker.run(process_vars)

    assert result["status"] == "dossier_created"
    assert result["recommendation"] == "ANALISE_HUMANA"


def test_analyze_request_never_auto_denies() -> None:
    """analyze_request MUST never produce a denial recommendation.

    Ineligibilidade aparente routes to human analysis, never auto-deny.
    """
    worker = AnalyzeRequestWorker()

    process_vars = {
        "tenant_id": "amh",
        "numero_guia_tiss": "G12345",
        "beneficiario_ativo": False,
        "carencia_cumprida": False,
        "requer_autorizacao": True,
        "documentacao_completa": True,
    }

    result = worker.run(process_vars)

    # Must route to human, never deny
    assert result["status"] == "dossier_created"
    assert "NEGAR" not in str(result)
    assert "deny" not in str(result).lower()
    assert "auto" not in result.get("recommendation", "").lower()


# ---------------------------------------------------------------------------
# request_documents
# ---------------------------------------------------------------------------


def test_request_documents_topic() -> None:
    """request_documents worker topic."""
    worker = RequestDocumentsWorker()
    assert worker.topic == "operadora.auth.request_documents"


def test_request_documents_creates_pendency() -> None:
    """request_documents creates a pending state for missing docs."""
    worker = RequestDocumentsWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "prestador_id": "prest-123",
            "missing_docs": ["guia_assinada", "pedido_medico"],
        }
    )

    assert result["status"] == "pended"
    assert result["event"] == "agents.events.auth.pended"


# ---------------------------------------------------------------------------
# issue_authorization
# ---------------------------------------------------------------------------


def test_issue_authorization_topic() -> None:
    """issue_authorization worker topic."""
    worker = IssueAuthorizationWorker()
    assert worker.topic == "operadora.auth.issue_authorization"


def test_issue_authorization_emits_tiss_authorization() -> None:
    """issue_authorization emits a TISS authorization number."""
    worker = IssueAuthorizationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "G12345",
            "decisao_auditor": "APROVAR",
            "human_approved": True,
        }
    )

    assert result["status"] == "authorized"
    assert "numero_autorizacao" in result
    assert result["numero_autorizacao"].startswith("AUTH-")


def test_issue_authorization_issues_on_human_aprovar_decision_alone() -> None:
    """human_approved threading (item-9 bucket-3 Class-C): the exact live human-APROVAR shape —
    the UT completes with ONLY decisao_auditor=APROVAR (nothing in the model ever sets a
    human_approved flag) — issues, and threads the resolved provenance back as True."""
    worker = IssueAuthorizationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "G12345",
            "decisao_auditor": "APROVAR",
            # no human_approved flag — decisao_auditor IS the human evidence (UT-only variable)
        }
    )

    assert result["status"] == "authorized"
    assert result["numero_autorizacao"].startswith("AUTH-")
    assert result["human_approved"] is True  # threaded provenance (engine-visible)


def test_issue_authorization_issues_on_modeled_auto_l2_sanction() -> None:
    """The modeled ST_EmitirAutorizacaoAuto route: no human exists by design — the DMN sanction
    (`auto_aprovacao.recomendacao == AUTO_APROVAR`, the exact Flow_GW_AutoAprovar condition)
    licenses the FAVORABLE L2 issuance (ADR-0008); the threaded provenance is truthfully False."""
    worker = IssueAuthorizationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "G12345",
            "auto_aprovacao": {"recomendacao": "AUTO_APROVAR", "motivo": "dut+teto+rede ok"},
            # no decisao_auditor (no UT on this route), no human_approved
        }
    )

    assert result["status"] == "authorized"
    assert result["numero_autorizacao"].startswith("AUTH-")
    assert result["human_approved"] is False  # truthful: no human decided the auto-L2 issuance


@pytest.mark.parametrize(
    "junk_auto",
    [
        "AUTO_APROVAR",  # raw string, not the DMN result map
        {"recomendacao": "ANALISE_HUMANA"},  # the DMN's own catch-all — NOT a sanction
        {"recomendacao": ""},
        {"recomendacao": 1},
        {"recomendacao": None},
        {"outra_chave": "AUTO_APROVAR"},
        ["AUTO_APROVAR"],
        None,
    ],
)
def test_issue_authorization_fail_closed_rejects_junk_auto_sanction(junk_auto: object) -> None:
    """FAIL-CLOSED parse of the auto sanction: only a Mapping whose recomendacao is exactly
    AUTO_APROVAR sanctions — junk shapes never license an issuance."""
    worker = IssueAuthorizationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "G12345",
            "auto_aprovacao": junk_auto,
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


def test_issue_authorization_blocked_without_any_sanction_evidence() -> None:
    """No decisao_auditor, no human_approved, no auto sanction -> blocked (fail-closed)."""
    worker = IssueAuthorizationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "G12345",
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


@pytest.mark.parametrize(
    "vars_extra",
    [
        {},
        {"human_approved": True},  # explicit flag never converts a NEGAR into a grant
        {"auto_aprovacao": {"recomendacao": "AUTO_APROVAR"}},  # stale auto sanction neither
        {"human_approved": True, "auto_aprovacao": {"recomendacao": "AUTO_APROVAR"}},
    ],
)
def test_issue_authorization_never_issues_on_negar(vars_extra: dict[str, object]) -> None:
    """Defense-in-depth (L0 hard): decisao_auditor=NEGAR NEVER issues an authorization — not
    even with the explicit flag or a residual auto sanction present."""
    worker = IssueAuthorizationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "G12345",
            "decisao_auditor": "NEGAR",
            **vars_extra,
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN
    assert "numero_autorizacao" not in result


@pytest.mark.parametrize("vars_extra", _HUMAN_APPROVED_NON_TRUE_VECTORS)
def test_issue_authorization_fail_closed_rejects_non_true_human_approved(
    vars_extra: dict[str, object],
) -> None:
    """FAIL-CLOSED (T3.1): the EXPLICIT signal channel only accepts `human_approved is True`.

    No decisao_auditor route literal and no auto sanction are supplied, so the explicit-flag
    channel is exercised in isolation: absent/False/None/truthy-junk (string incl.
    whitespace-only, int, list, dict) must NEVER be read as human approval.
    """
    worker = IssueAuthorizationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "G12345",
            **vars_extra,
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


# ---------------------------------------------------------------------------
# notify_sla_risk
# ---------------------------------------------------------------------------


def test_notify_sla_risk_topic() -> None:
    """notify_sla_risk worker topic."""
    worker = NotifySlaRiskWorker()
    assert worker.topic == "operadora.auth.notify_sla_risk"


def test_notify_sla_risk_alerts_coordinator() -> None:
    """notify_sla_risk alerts coordination when SLA approaches breach."""
    worker = NotifySlaRiskWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "sla_percent": 75,
            "sla_analise": "PT2H",
        }
    )

    assert result["status"] == "risk_notified"
    assert result["alert_to"] == "coordenacao-auditoria-medica"


# ---------------------------------------------------------------------------
# convene_junta
# ---------------------------------------------------------------------------


def test_convene_junta_topic() -> None:
    """convene_junta worker topic."""
    worker = ConveneJuntaWorker()
    assert worker.topic == "operadora.auth.convene_junta"


def test_convene_junta_convokes_junta() -> None:
    """convene_junta convokes the medical board when needed."""
    worker = ConveneJuntaWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "JUNTA_MEDICA",
            "human_approved": True,
            "numero_guia_tiss": "G12345",
        }
    )

    assert result["status"] == "junta_convened"
    assert result["junta_group"] == "junta-medica"


def test_convene_junta_convokes_on_human_junta_decision_alone() -> None:
    """human_approved threading (item-9 bucket-3 Class-C): the exact live shape — the auditor UT
    completes with ONLY decisao_auditor=JUNTA_MEDICA (the literal Flow_GWDec_Junta requires;
    nothing in the model ever sets a human_approved flag) — convokes."""
    worker = ConveneJuntaWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "JUNTA_MEDICA",
            # no human_approved flag — decisao_auditor IS the human evidence (UT-only variable)
            "numero_guia_tiss": "G12345",
        }
    )

    assert result["status"] == "junta_convened"
    assert result["junta_group"] == "junta-medica"


def test_convene_junta_never_writes_human_approved_back() -> None:
    """POISONING FENCE: convene_junta runs BEFORE UT_RegistrarParecerJunta — it must NEVER
    persist a human_approved process variable (a worker-written True would let a downstream
    junta-NEGAR lacking auditor_id slip send_denial_notice's explicit-signal channel)."""
    worker = ConveneJuntaWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "JUNTA_MEDICA",
            "human_approved": True,
            "numero_guia_tiss": "G12345",
        }
    )

    assert result["status"] == "junta_convened"
    assert "human_approved" not in result


def test_convene_junta_guard_blocks_without_human() -> None:
    """convene_junta must not convene without human-decision evidence: no JUNTA_MEDICA decision
    literal (a decisao that never routes here) and no explicit human_approved -> blocked."""
    worker = ConveneJuntaWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "SOLICITAR_INFO",  # not the junta decision; no human_approved
            "numero_guia_tiss": "G12345",
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


@pytest.mark.parametrize(
    "junk_decisao", ["", "   ", "junta_medica", "JUNTA_MEDICA_X", None, 1, ["JUNTA_MEDICA"]]
)
def test_convene_junta_fail_closed_rejects_junk_decisao(junk_decisao: object) -> None:
    """FAIL-CLOSED: only the exact JUNTA_MEDICA literal (strip-normalized, no case folding) is
    human-decision evidence — junk/case-variant/non-string decisao blocks."""
    worker = ConveneJuntaWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": junk_decisao,
            "numero_guia_tiss": "G12345",
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


@pytest.mark.parametrize("vars_extra", _HUMAN_APPROVED_NON_TRUE_VECTORS)
def test_convene_junta_fail_closed_rejects_non_true_human_approved(
    vars_extra: dict[str, object],
) -> None:
    """FAIL-CLOSED (T3.1): the EXPLICIT signal channel only accepts `human_approved is True`.

    No JUNTA_MEDICA decision literal is supplied, so the explicit-flag channel is exercised in
    isolation: absent/False/None/truthy-junk must NEVER be read as human approval.
    """
    worker = ConveneJuntaWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "G12345",
            **vars_extra,
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN
