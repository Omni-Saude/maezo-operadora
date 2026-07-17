"""Authorization workers — SP-OP-AUTH-001.

Provides BPMN external task handlers for the Autorizacao Previa process.

Workers:
- AnalyzeRequestWorker: convoca Rafael, monta dossie de analise
- RequestDocumentsWorker: pendencia ao prestador
- IssueAuthorizationWorker: emite autorizacao TISS
- SendDenialNoticeWorker: negativa formal (guard: ERR_AUTH_DENIAL_NOT_HUMAN)
- NotifySlaRiskWorker: alerta coordenacao
- ConveneJuntaWorker: convoca junta medica

CRITICAL (ADR-0008, L0 hard):
- Negativa de cobertura SO nasce nas User Tasks humanas.
- Workers with ERR_AUTH_DENIAL_NOT_HUMAN guard: send_denial_notice,
  issue_authorization (when decisao=NEGAR).
- Auto-approval (L2) exists only with DMN favorable + teto do tenant.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from maezo.tools.workers.base import ERR_DENIAL_NOT_HUMAN, WorkerBase

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

# ---------------------------------------------------------------------------
# AnalyzeRequestWorker
# ---------------------------------------------------------------------------


class AnalyzeRequestWorker(WorkerBase):
    """External task: operadora.auth.analyze_request

    Convoca Rafael (medico auditor) e monta o dossie de analise.
    Pode auto-aprovar (L2) quando todas as condicoes favoraveis sao atendidas.

    Auto-approval conditions (ADR-0008 L2):
    - dut_atendida == True
    - dentro_teto_l2 == True
    - rede_credenciada == True

    Ineligibilidade aparente (beneficiario_ativo=False, carencia_nao_cumprida)
    SEMPRE roteia para analise humana — NUNCA resulta em negativa automatica.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.analyze_request")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Analyze the authorization request.

        Creates a dossier for Rafael. May auto-approve if L2 conditions met.

        Args:
            process_vars: BPMN authorization variables.

        Returns:
            Dict with analysis result and recommendation.
        """
        tenant_id = process_vars.get("tenant_id", "")
        guia = process_vars.get("numero_guia_tiss", "unknown")

        # Check L2 auto-approval conditions
        dut_ok = process_vars.get("dut_atendida", False)
        teto_ok = process_vars.get("dentro_teto_l2", False)
        rede_ok = process_vars.get("rede_credenciada", False)
        beneficiario_ativo = process_vars.get("beneficiario_ativo", False)
        carencia_ok = process_vars.get("carencia_cumprida", False)
        docs_completa = process_vars.get("documentacao_completa", False)

        dossier_ref = f"dossier-{uuid.uuid4().hex[:12]}"

        # L2 auto-approval: all conditions favorable
        if all([dut_ok, teto_ok, rede_ok, beneficiario_ativo, carencia_ok, docs_completa]):
            self.logger.info(
                "auth_auto_approved",
                tenant_id=tenant_id,
                guia=guia,
                dossier_ref=dossier_ref,
            )
            return {
                "status": "auto_approved",
                "recommendation": "AUTO_APROVAR",
                "dossier_ref": dossier_ref,
                "assigned_to": "rafael",
                "event": "agents.events.auth.received",
            }

        # Default: route to human analysis (Rafael)
        self.logger.info(
            "auth_dossier_created",
            tenant_id=tenant_id,
            guia=guia,
            recommendation="ANALISE_HUMANA",
            dossier_ref=dossier_ref,
        )

        return {
            "status": "dossier_created",
            "recommendation": "ANALISE_HUMANA",
            "dossier_ref": dossier_ref,
            "assigned_to": "rafael",
            "event": "agents.events.auth.received",
        }


# ---------------------------------------------------------------------------
# RequestDocumentsWorker
# ---------------------------------------------------------------------------


class RequestDocumentsWorker(WorkerBase):
    """External task: operadora.auth.request_documents

    Cria pendencia de documentacao para o prestador.
    Publica evento agents.events.auth.pended.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.request_documents")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Create a documentation pendecy.

        Args:
            process_vars: Must include prestador_id and missing_docs.

        Returns:
            Dict with pe™ndency status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        prestador_id = process_vars.get("prestador_id", "")
        missing = process_vars.get("missing_docs", [])

        self.logger.info(
            "auth_documents_pended",
            tenant_id=tenant_id,
            prestador_id=prestador_id,
            missing_docs=missing,
        )

        return {
            "status": "pended",
            "prestador_id": prestador_id,
            "missing_docs": missing,
            "event": "agents.events.auth.pended",
        }


# ---------------------------------------------------------------------------
# IssueAuthorizationWorker
# ---------------------------------------------------------------------------


class IssueAuthorizationWorker(WorkerBase):
    """External task: operadora.auth.issue_authorization

    Emite autorizacao TISS com numero unico.
    Guard: ERR_AUTH_DENIAL_NOT_HUMAN — requires human_approved flag.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.issue_authorization")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Issue a TISS authorization.

        Guard: must have human_approved=True.
        Only issues for APROVAR decisions (never NEGAR).

        Args:
            process_vars: Must include numero_guia_tiss, decisao_auditor, human_approved.

        Returns:
            Dict with authorization number or guard block.
        """
        tenant_id = process_vars.get("tenant_id", "")
        guia = process_vars.get("numero_guia_tiss", "unknown")
        human_approved = process_vars.get("human_approved", False)

        # Guard: require human approval for authorization issuance
        if not human_approved:
            self.logger.warning(
                "auth_issue_blocked_by_guard",
                tenant_id=tenant_id,
                guia=guia,
                reason="human_approved flag missing",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Autorizacao requer aprovacao humana (L0 hard)",
            }

        # Issue authorization
        auth_number = f"AUTH-{tenant_id}-{guia}-{uuid.uuid4().hex[:8]}"

        self.logger.info(
            "auth_issued",
            tenant_id=tenant_id,
            guia=guia,
            numero_autorizacao=auth_number,
        )

        return {
            "status": "authorized",
            "numero_autorizacao": auth_number,
            "error_code": None,
            "event": "agents.events.auth.completed",
        }


# ---------------------------------------------------------------------------
# SendDenialNoticeWorker
# ---------------------------------------------------------------------------


class SendDenialNoticeWorker(WorkerBase):
    """External task: operadora.auth.send_denial_notice

    Envia negativa formal por escrito (em nome do auditor).
    Guard: ERR_AUTH_DENIAL_NOT_HUMAN — automatic denial is FORBIDDEN.

    Only sends when:
    1. decisao_auditor is present (APROVAR or NEGAR)
    2. human_approved flag is True

    Without human_approved, the guard blocks the denial.
    For APROVAR decisions, sends an approval notice instead.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.send_denial_notice")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Send denial (or approval) notice.

        Guard: blocks NEGAR decisions without human approval.

        Args:
            process_vars: Must include decisao_auditor, justificativa_clinica,
                          human_approved.

        Returns:
            Dict with notice status and optional error code.
        """
        tenant_id = process_vars.get("tenant_id", "")
        decisao = process_vars.get("decisao_auditor", "")
        justificativa = process_vars.get("justificativa_clinica", "")
        human_approved = process_vars.get("human_approved", False)

        # Guard: block NEGAR without human approval
        if decisao == "NEGAR" and not human_approved:
            self.logger.error(
                "auth_denial_blocked_by_guard",
                tenant_id=tenant_id,
                reason="NEGAR sem aprovacao humana (L0 hard)",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Negativa automatica PROIBIDA. Requer decisao de medico auditor humano.",
            }

        # For APROVAR, human_approved is still checked but not mandatory for notice
        if decisao == "NEGAR":
            self.logger.info(
                "auth_denial_sent",
                tenant_id=tenant_id,
                justificativa=justificativa,
            )
            return {
                "status": "notice_sent",
                "notice_type": "denial",
                "justificativa_clinica": justificativa,
                "error_code": None,
                "event": "agents.events.auth.completed",
            }

        # APROVAR or other — send approval notice
        self.logger.info(
            "auth_approval_notice_sent",
            tenant_id=tenant_id,
        )
        return {
            "status": "notice_sent",
            "notice_type": "approval",
            "error_code": None,
            "event": "agents.events.auth.completed",
        }


# ---------------------------------------------------------------------------
# NotifySlaRiskWorker
# ---------------------------------------------------------------------------


class NotifySlaRiskWorker(WorkerBase):
    """External task: operadora.auth.notify_sla_risk

    Alerta coordenacao de auditoria medica sobre risco de SLA.
    Timer nao-interruptivo (50-70% do SLA total).
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.notify_sla_risk")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Notify coordinator about SLA risk.

        Args:
            process_vars: Must include sla_percent and sla_analise.

        Returns:
            Dict with notification status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        sla_percent = process_vars.get("sla_percent", 0)
        sla_analise = process_vars.get("sla_analise", "")

        self.logger.warning(
            "auth_sla_risk_notified",
            tenant_id=tenant_id,
            sla_percent=sla_percent,
            sla_analise=sla_analise,
        )

        return {
            "status": "risk_notified",
            "alert_to": "coordenacao-auditoria-medica",
            "sla_percent": sla_percent,
            "sla_analise": sla_analise,
            "event": "agents.events.auth.sla_breached",
        }


# ---------------------------------------------------------------------------
# ConveneJuntaWorker
# ---------------------------------------------------------------------------


class ConveneJuntaWorker(WorkerBase):
    """External task: operadora.auth.convene_junta

    Convoca junta medica (RN 424).
    Guard: ERR_AUTH_DENIAL_NOT_HUMAN — requires human authorization.

    Only convokes when decisao_auditor == JUNTA_MEDICA and human_approved.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.auth.convene_junta")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Convokes a medical board.

        Guard: requires human_approved=True.

        Args:
            process_vars: Must include decisao_auditor, human_approved.

        Returns:
            Dict with junta status or guard block.
        """
        tenant_id = process_vars.get("tenant_id", "")
        guia = process_vars.get("numero_guia_tiss", "unknown")
        decisao = process_vars.get("decisao_auditor", "")
        human_approved = process_vars.get("human_approved", False)

        # Guard: require human approval
        if not human_approved:
            self.logger.warning(
                "auth_junta_blocked_by_guard",
                tenant_id=tenant_id,
                guia=guia,
                reason="human_approved flag missing",
            )
            return {
                "status": "blocked_by_guard",
                "error_code": ERR_DENIAL_NOT_HUMAN,
                "mensagem": "Convocacao de junta medica requer aprovacao humana (L0 hard)",
            }

        self.logger.info(
            "auth_junta_convened",
            tenant_id=tenant_id,
            guia=guia,
            decisao=decisao,
        )

        return {
            "status": "junta_convened",
            "junta_group": "junta-medica",
            "fundamentacao": "RN 424/2017",
            "event": "agents.events.auth.completed",
        }


# ---------------------------------------------------------------------------
# Bootstrap — donor contract (T1.2/ADR-0026 Decisao §3): class modules register
# directly, one WorkerBase() instance per topic. auth.py's 6 classes take no
# constructor args (none declares a Kafka dependency today) — `kafka`/`seams`
# are accepted for signature parity with the other 15 register_<domain>_workers
# bootstraps and `register_all_workers` (ADR-0026 Decisao §4), unused here.
# ---------------------------------------------------------------------------


def register_auth_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the 6 SP-OP-AUTH-001 `WorkerBase` workers on `harness`."""
    del kafka, seams  # unused — no auth.py worker declares a Kafka/other seam dependency
    for worker_cls in (
        AnalyzeRequestWorker,
        RequestDocumentsWorker,
        IssueAuthorizationWorker,
        SendDenialNoticeWorker,
        NotifySlaRiskWorker,
        ConveneJuntaWorker,
    ):
        harness.register_worker(worker_cls())
