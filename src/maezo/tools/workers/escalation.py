"""Escalation workers — SP-OP-ESCALATION-001.

Provides BPMN external task handlers for the Escalonamento Humano Universal process.

Workers:
- NotifyTeamWorker: routes escalation to the correct human group
- NotifySupervisorWorker: supervisor alert on SLA breach (also serves
  `ST_NotificarFallback` — the BPMN's fallback-channel task publishes to
  this SAME topic, `operadora.escalation.notify_supervisor`; there is no
  separate `notify_fallback` topic in the BPMN)

CRITICAL: Workers NEVER make adverse decisions (L0 hard). They only route
and notify. Escalation resolution is always a human decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maezo.tools.workers.base import WorkerBase

if TYPE_CHECKING:
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

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
# NotifySupervisorWorker
# ---------------------------------------------------------------------------


class NotifySupervisorWorker(WorkerBase):
    """External task: operadora.escalation.notify_supervisor

    Alerts supervisor when SLA is breached (ack or resolution timer expired).
    Also serves `ST_NotificarFallback` (the channel-fallback task when
    `notify_team` fails) — the BPMN wires BOTH tasks to this SAME topic
    (`spec/processes/bpmn/SP-OP-ESCALATION-001_*.bpmn:112,203`); there is no
    separate `notify_fallback` topic anywhere in spec/. A prior
    `NotifyFallbackWorker` registered on a dead `operadora.escalation.
    notify_fallback` topic (no matching `camunda:topic` in the BPMN, no
    engine subscriber would ever dispatch to it) was removed — see
    docs/processes/test-specs/SP-OP-ESCALATION-001.md:65 ("`ST_NotificarFallback`
    (topic `notify_supervisor`) executa").

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


# ---------------------------------------------------------------------------
# Bootstrap — donor contract (T1.2/ADR-0026 Decisao §3).
#
# t2.5-p2b-round2: removed the dead `NotifyFallbackWorker` (topic
# `operadora.escalation.notify_fallback`) — verified via `grep -rn
# notify_fallback spec/` (zero hits) that no BPMN task ever declared that
# topic; `ST_NotificarFallback` (the only task with "fallback" in its name)
# actually declares `camunda:topic="operadora.escalation.notify_supervisor"`
# (BPMN lines 111-112), the SAME topic as `ST_NotificarSupervisor`. Both
# tasks are now correctly served by the single `NotifySupervisorWorker`.
# ---------------------------------------------------------------------------


def register_escalation_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the 2 SP-OP-ESCALATION-001 `WorkerBase` workers on `harness`."""
    del kafka, seams  # unused — no escalation.py worker declares a Kafka/other seam dependency
    for worker_cls in (NotifyTeamWorker, NotifySupervisorWorker):
        harness.register_worker(worker_cls())
