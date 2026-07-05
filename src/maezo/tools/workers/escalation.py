"""Escalation workers — SP-OP-ESCALATION-001.

Provides BPMN external task handlers for the Escalonamento Humano Universal process.

Workers:
- NotifyTeamWorker: routes escalation to the correct human group
- NotifyFallbackWorker: fallback notification when primary channel fails
- NotifySupervisorWorker: supervisor alert on SLA breach

CRITICAL: Workers NEVER make adverse decisions (L0 hard). They only route
and notify. Escalation resolution is always a human decision.
"""

from __future__ import annotations

from typing import Any

from maezo.tools.workers.base import WorkerBase

# ---------------------------------------------------------------------------
# Routing maps (extracted from DMN escalation_routing — contract SP-OP-ESCALATION-001)
# ---------------------------------------------------------------------------

_SEVERITY_TO_GROUP: dict[str, str] = {
    "grave": "plantao-clinico",
    "moderada": "enfermagem-triagem",
    "leve": "atendimento-humano",
}

# Fallback default when severity is unknown (fail-safe, never P1)
_DEFAULT_GROUP: str = "atendimento-humano"


# ---------------------------------------------------------------------------
# NotifyTeamWorker
# ---------------------------------------------------------------------------


class NotifyTeamWorker(WorkerBase):
    """External task: operadora.escalation.notify_team

    Routes escalation to the correct human group based on severity and
    motivo_categoria. Publishes the escalation event to Kafka.

    Decision logic:
    - severidade=grave          -> plantao-clinico (P1)
    - severidade=moderada       -> enfermagem-triagem (P2)
    - severidade=leve/unknown   -> atendimento-humano (P3, fail-safe)

    NEVER decides the outcome of the escalation — only routes.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.escalation.notify_team")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Route escalation to the correct human group.

        Args:
            process_vars: BPMN variables including tenant_id, source_agent_id,
                          severity, motivo_categoria, beneficiario_pseudo_id.

        Returns:
            Dict with group, status, event, severity, and routing metadata.
        """
        severity = process_vars.get("severity", "leve")
        motivo = process_vars.get("motivo_categoria", "outro")
        tenant_id = process_vars.get("tenant_id", "")

        group = _SEVERITY_TO_GROUP.get(severity, _DEFAULT_GROUP)

        self.logger.info(
            "escalation_notify_team",
            tenant_id=tenant_id,
            severity=severity,
            motivo=motivo,
            group=group,
        )

        return {
            "status": "teams_notified",
            "group": group,
            "severity": severity,
            "motivo_categoria": motivo,
            "event": "agents.events.escalation.requested",
        }


# ---------------------------------------------------------------------------
# NotifyFallbackWorker
# ---------------------------------------------------------------------------


class NotifyFallbackWorker(WorkerBase):
    """External task: operadora.escalation.notify_fallback

    Fallback notification when the primary notification channel fails
    (e.g., ERR_ESC_NOTIFY_FAILED). Routes to supervisor for manual handling.

    NEVER escalates to an adverse decision — only notifies the supervisor
    that the primary channel failed.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.escalation.notify_fallback")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Notify supervisor when primary channel fails.

        Args:
            process_vars: Must include fallback_reason.

        Returns:
            Dict with fallback status and target group.
        """
        reason = process_vars.get("fallback_reason", "unknown")
        tenant_id = process_vars.get("tenant_id", "")

        self.logger.warning(
            "escalation_fallback_triggered",
            tenant_id=tenant_id,
            reason=reason,
        )

        return {
            "status": "fallback_triggered",
            "fallback_group": "supervisao-atendimento",
            "original_error": reason,
            "event": "agents.events.escalation.sla_breached",
        }


# ---------------------------------------------------------------------------
# NotifySupervisorWorker
# ---------------------------------------------------------------------------


class NotifySupervisorWorker(WorkerBase):
    """External task: operadora.escalation.notify_supervisor

    Alerts supervisor when SLA is breached (ack or resolution timer expired).

    NEVER makes any decision about the case — only notifies.
    The supervisor (human) decides the next action.
    """

    def __init__(self) -> None:
        super().__init__(topic="operadora.escalation.notify_supervisor")

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Alert supervisor on SLA breach.

        Args:
            process_vars: Must include sla_status.

        Returns:
            Dict with supervisor notification status.
        """
        tenant_id = process_vars.get("tenant_id", "")
        sla_status = process_vars.get("sla_status", "unknown")
        severity = process_vars.get("severity", "leve")

        self.logger.warning(
            "escalation_supervisor_notified",
            tenant_id=tenant_id,
            sla_status=sla_status,
            severity=severity,
        )

        return {
            "status": "supervisor_notified",
            "sla_status": sla_status,
            "alert_to": "supervisao-atendimento",
            "require_human_resolution": True,
            "event": "agents.events.escalation.sla_breached",
        }
