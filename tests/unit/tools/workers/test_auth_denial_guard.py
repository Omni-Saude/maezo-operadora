"""Unit tests for SP-OP-AUTH-001 workers — TDD London School.

Tests: denial guard (ERR_AUTH_DENIAL_NOT_HUMAN), auto-approve, analyze_request,
request_documents, issue_authorization, notify_sla_risk, convene_junta.

CRITICAL: Workers must NEVER make adverse decisions (negativa, acusacao).
"""

from __future__ import annotations

from maezo.tools.workers.auth import (
    AnalyzeRequestWorker,
    ConveneJuntaWorker,
    IssueAuthorizationWorker,
    NotifySlaRiskWorker,
    RequestDocumentsWorker,
    SendDenialNoticeWorker,
)
from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN

# ---------------------------------------------------------------------------
# send_denial_notice — denial guard (ERR_AUTH_DENIAL_NOT_HUMAN)
# ---------------------------------------------------------------------------


def test_send_denial_notice_topic() -> None:
    """send_denial_notice worker must have topic 'operadora.auth.send_denial_notice'."""
    worker = SendDenialNoticeWorker()
    assert worker.topic == "operadora.auth.send_denial_notice"


def test_send_denial_notice_guard_prevents_automatic_denial() -> None:
    """send_denial_notice MUST refuse to send denial without human authorization.

    The ERR_AUTH_DENIAL_NOT_HUMAN guard prevents automatic adverse actions.
    """
    worker = SendDenialNoticeWorker()

    # Simulate a denial attempt without human approval marker
    process_vars = {
        "tenant_id": "amh",
        "beneficiario_pseudo_id": "pseudo-abc",
        "decisao_auditor": "NEGAR",
        "justificativa_clinica": "Fora do ROL",
        # Note: NO human_approved flag
    }

    result = worker.run(process_vars)

    assert result["status"] == "blocked_by_guard"
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN


def test_send_denial_notice_allows_when_human_approved() -> None:
    """send_denial_notice allows denial when human_approved flag is present."""
    worker = SendDenialNoticeWorker()

    process_vars = {
        "tenant_id": "amh",
        "beneficiario_pseudo_id": "pseudo-abc",
        "decisao_auditor": "NEGAR",
        "justificativa_clinica": "Fora do ROL",
        "human_approved": True,  # Human auditor approved
    }

    result = worker.run(process_vars)

    assert result["status"] == "notice_sent"
    assert result["error_code"] is None


def test_send_denial_notice_guard_activates_on_denial() -> None:
    """The guard activates specifically when decisao_auditor == 'NEGAR'."""
    worker = SendDenialNoticeWorker()

    # Test with NEGAR without human
    result = worker.run(
        {
            "tenant_id": "amh",
            "decisao_auditor": "NEGAR",
            "justificativa_clinica": "teste",
        }
    )
    assert result["error_code"] == ERR_DENIAL_NOT_HUMAN

    # Test with APROVAR (guard should not fire)
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


def test_analyze_request_auto_approve_eligible() -> None:
    """analyze_request auto-approves when L2 conditions are met.

    L2 auto-approval requires: dut_atendida + dentro_teto_l2 + rede_credenciada.
    """
    worker = AnalyzeRequestWorker()

    process_vars = {
        "tenant_id": "amh",
        "numero_guia_tiss": "G12345",
        "codigo_procedimento_tuss": "10101012",
        "categoria_procedimento": "consulta",
        "carater_atendimento": "eletivo",
        "beneficiario_pseudo_id": "pseudo-abc",
        "dut_atendida": True,
        "dentro_teto_l2": True,
        "rede_credenciada": True,
        "beneficiario_ativo": True,
        "carencia_cumprida": True,
        "requer_autorizacao": True,
        "documentacao_completa": True,
    }

    result = worker.run(process_vars)

    assert result["status"] == "auto_approved"
    assert result["recommendation"] == "AUTO_APROVAR"


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
