"""Unit tests for SP-OP-AUTH-001 workers — TDD London School.

Tests: denial guard (ERR_AUTH_DENIAL_NOT_HUMAN), auto-approve, analyze_request,
request_documents, issue_authorization, notify_sla_risk, convene_junta.

CRITICAL: Workers must NEVER make adverse decisions (negativa, acusacao).
"""

from __future__ import annotations

from pathlib import Path

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

# Synthetic-core helper — pins the AUTH ceiling in isolation (mirrors test_ceilings) so these
# tests do not depend on the D-07 value (max_value_brl=0) in the real spec matrix.
_HARD_BLOCK = """\
  clinical_decision:      { level: L0, hard: true }
  authorization_denial:   { level: L0, hard: true }
  nip_manter_negativa:    { level: L0, hard: true }
  fraud_accusation:       { level: L0, hard: true }
  contract_termination:   { level: L0, hard: true }
"""


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
    T3.1 completeness guard passes and this exercises the human_approved guard specifically.
    """
    worker = SendDenialNoticeWorker()

    # Simulate a denial attempt without human approval marker (but with complete fundamentacao).
    process_vars = {
        "tenant_id": "amh",
        "beneficiario_pseudo_id": "pseudo-abc",
        "decisao_auditor": "NEGAR",
        **_COMPLETE_DENIAL_FUNDAMENTACAO,
        # Note: NO human_approved flag
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


def test_issue_authorization_only_with_human_approval() -> None:
    """issue_authorization only issues when human_approved flag is present."""
    worker = IssueAuthorizationWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "numero_guia_tiss": "G12345",
            "decisao_auditor": "APROVAR",
            # no human_approved
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


def test_convene_junta_guard_blocks_without_human() -> None:
    """convene_junta must not convene without human authorization."""
    worker = ConveneJuntaWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "JUNTA_MEDICA",
            # no human_approved
            "numero_guia_tiss": "G12345",
        }
    )

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN
