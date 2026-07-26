"""Escalation workers — SP-OP-ESCALATION-001.

Provides BPMN external task handlers for the Escalonamento Humano Universal process.

Handlers (DL-0034 — RAW ASYNC KAFKA HANDLERS, the #55 R-B / events.py sanctioned form, NOT
`WorkerBase`; see `register_escalation_workers`):
- notify_team: routes escalation to the correct human group + emits the internal notification.
- notify_supervisor: supervisor alert on SLA breach (also serves `ST_NotificarFallback` — the
  BPMN's fallback-channel task publishes to this SAME topic,
  `operadora.escalation.notify_supervisor`; there is no separate `notify_fallback` topic in the
  BPMN, `spec/processes/bpmn/SP-OP-ESCALATION-001_*.bpmn:112,203`).

CRITICAL: handlers NEVER make adverse decisions (L0 hard). They only route and notify.
Escalation resolution is always a human decision.

WHY RAW ASYNC HANDLERS (DL-0034, ratified by orchestrator 2026-07-26, built in t5): for
escalation, NOTIFYING *is* the business effect (there is no domain computation beyond
route+notify), and the BPMN models these as their OWN topics
(`operadora.escalation.notify_team`/`notify_supervisor`), not the generic
`operadora.events.publish`. A `WorkerBase.execute(dict)` boundary has NO async Kafka seam
(ADR-0026 §2), so the prior `NotifyTeamWorker`/`NotifySupervisorWorker` `WorkerBase` classes NEVER
actually published — they returned a `{"status": ...}` dict and the notification silently never
happened (live-confirmed on CIB Seven 2.1.0: `tests/integration/processes/
test_sp_op_escalation_001.py`'s `_NOTIFY_KAFKA_GAP_REASON` — `probe.notified_teams`/
`notified_supervisors` never observed an execution). This mirrors EXACTLY the #55 R-B precedent
(`operadora.lgpd.request_additional_proof`, `lgpd.py:449-510`) / `events.py`: a raw handler
registered via `harness.register(topic, handler)` with the async Kafka seam.

kafka=None (fail-closed, same decision as `events.py` / #55 R-B): the task MUST still complete —
the BPMN flow continues UNCONDITIONALLY past the notify task (`Flow_Notificar_UT` ->
`UT_TratarEscalonamento`); a notification-producer gap must never HANG the escalation. Log LOUDLY,
never fabricate a publish. NO adverse effect either way.

SCOPE NOTE: raising `ERR_ESC_NOTIFY_FAILED` to drive the `BE_FalhaNotificacao` / `BE_Notif*`
fallback boundaries is DELIBERATELY out of scope here (ADR-0030 Tier-1, "co-scheduled with the
Kafka-producer gap" — DL-0034; the ADR-0030 boundary table lists it "Worker raises it today? NO").
Mirroring #55 R-B, a publish failure propagates the RAW exception (harness failure/retry
ownership), never a modeled `bpmnError` — adding that modeled raise is a separate,
`bpmn_error_allowlist`-gated change.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from maezo.tools.workers.harness import ExternalTask, KafkaPublisher, TaskHandler, WorkerHarness

logger = structlog.get_logger(__name__)
# Stdlib logger mirror (events.py convention): the kafka=None loud log must surface even where the
# structlog chain is not wired.
_stdlib_logger = logging.getLogger(__name__)

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

# Topics + notification channel/types (mirrors lgpd.py / recurso.py's per-worker notification idiom;
# the notification `type` is what the integration probe's `notified_teams`/`notified_supervisors`
# match on — test_sp_op_escalation_001.py:198,202).
_NOTIFICATIONS_TOPIC = "operadora.notifications.internal"
_NOTIFY_TEAM_TOPIC = "operadora.escalation.notify_team"
_NOTIFY_SUPERVISOR_TOPIC = "operadora.escalation.notify_supervisor"
_NOTIFY_TEAM_NOTIFICATION_TYPE = "escalation.notify_team"
_NOTIFY_SUPERVISOR_NOTIFICATION_TYPE = "escalation.notify_supervisor"


def _resolve_group(severity: str) -> str:
    """Route severity -> human group (fail-safe default `atendimento-humano`, never P1)."""
    return _SEVERITY_TO_GROUP.get(severity, _DEFAULT_GROUP)


# ---------------------------------------------------------------------------
# notify_team (DL-0034 — raw async Kafka handler, mirrors #55 R-B)
# ---------------------------------------------------------------------------


def make_notify_team_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Create the handler for `operadora.escalation.notify_team` (DL-0034; mirrors #55 R-B).

    Serves `ST_NotificarTime`. Routes escalation to the correct human group by severity
    (grave -> plantao-clinico, moderada -> enfermagem-triagem, leve/unknown -> atendimento-humano
    fail-safe) and emits the internal notification (`type=escalation.notify_team`). NEVER decides
    the escalation OUTCOME — only routes+notifies. The routing `group` is returned for
    observability; the human task's `candidateGroups` is engine-set from
    `${roteamento.grupo_atendimento}` (the `escalation_routing` DMN), NOT this output (verified:
    `${group}` appears in NO BPMN expression), so this worker's return never drives assignment.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        severity = v.get("severity", "leve")
        motivo = v.get("motivo_categoria", "outro")
        tenant_id = v.get("tenant_id", "")
        group = _resolve_group(severity)
        result: dict[str, Any] = {
            "status": "teams_notified",
            "group": group,
            "severity": severity,
            "motivo_categoria": motivo,
            "event": "agents.events.escalation.requested",
        }

        if kafka is None:
            # No producer wired yet (T1.2/ADR-0026 gap, same reality as events.py). Log LOUDLY and
            # complete anyway — the task MUST complete so the flow reaches UT_TratarEscalonamento
            # (else the escalation HANGS in prod). NEVER fabricate a publish.
            logger.warning(
                "escalation_notify_team_no_producer",
                tenant_id=tenant_id,
                severity=severity,
                group=group,
                business_key=task.business_key,
            )
            _stdlib_logger.warning(
                "escalation_notify_team_no_producer business_key=%s — kafka=None (no producer "
                "wired); notification NOT published, completing so the flow reaches "
                "UT_TratarEscalonamento",
                task.business_key,
            )
            return result

        # No PHI: motivo_categoria/severity/group are bounded routing labels; beneficiario_pseudo_id
        # is a pseudonym and free-text fields are never copied into the notification (ADR-0006).
        notification = {
            "type": _NOTIFY_TEAM_NOTIFICATION_TYPE,
            "tenant_id": tenant_id,
            "severity": severity,
            "motivo_categoria": motivo,
            "group": group,
        }
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=task.business_key or None)
        logger.info(
            "escalation_notify_team_sent",
            tenant_id=tenant_id,
            severity=severity,
            group=group,
            business_key=task.business_key,
        )
        return result

    return handler


# ---------------------------------------------------------------------------
# notify_supervisor (DL-0034 — raw async Kafka handler, mirrors #55 R-B)
# ---------------------------------------------------------------------------


def make_notify_supervisor_handler(kafka: KafkaPublisher | None) -> TaskHandler:
    """Create the handler for `operadora.escalation.notify_supervisor` (DL-0034; mirrors #55 R-B).

    Serves BOTH `ST_NotificarSupervisor` (SLA breach — ack/resolution timer expired) and
    `ST_NotificarFallback` (channel fallback when `notify_team` fails) — the BPMN wires both tasks
    to this SAME topic (`:112,203`); there is no separate `notify_fallback` topic anywhere in
    spec/. Alerts the supervisor (`type=escalation.notify_supervisor`). NEVER makes any decision
    about the case — the supervisor (human) decides the next action.
    """

    async def handler(task: ExternalTask) -> Mapping[str, Any]:
        v = task.variables
        tenant_id = v.get("tenant_id", "")
        sla_status = v.get("sla_status", "unknown")
        severity = v.get("severity", "leve")
        result: dict[str, Any] = {
            "status": "supervisor_notified",
            "sla_status": sla_status,
            "alert_to": "supervisao-atendimento",
            "require_human_resolution": True,
            "event": "agents.events.escalation.sla_breached",
        }

        if kafka is None:
            logger.warning(
                "escalation_supervisor_notify_no_producer",
                tenant_id=tenant_id,
                sla_status=sla_status,
                business_key=task.business_key,
            )
            _stdlib_logger.warning(
                "escalation_supervisor_notify_no_producer business_key=%s — kafka=None (no "
                "producer wired); notification NOT published, completing so the escalation is not "
                "stalled",
                task.business_key,
            )
            return result

        notification = {
            "type": _NOTIFY_SUPERVISOR_NOTIFICATION_TYPE,
            "tenant_id": tenant_id,
            "sla_status": sla_status,
            "severity": severity,
            "alert_to": "supervisao-atendimento",
        }
        await kafka.publish(_NOTIFICATIONS_TOPIC, notification, key=task.business_key or None)
        logger.warning(
            "escalation_supervisor_notified",
            tenant_id=tenant_id,
            sla_status=sla_status,
            severity=severity,
            business_key=task.business_key,
        )
        return result

    return handler


# ---------------------------------------------------------------------------
# Bootstrap — donor contract (T1.2/ADR-0026 Decisao §3).
#
# t2.5-p2b-round2: the dead `NotifyFallbackWorker` (topic
# `operadora.escalation.notify_fallback`) was removed — `grep -rn notify_fallback spec/` (zero
# hits) confirmed no BPMN task ever declared that topic; `ST_NotificarFallback` actually declares
# `camunda:topic="operadora.escalation.notify_supervisor"` (BPMN :112), the SAME topic as
# `ST_NotificarSupervisor`. Both tasks are served by the single `notify_supervisor` handler.
# ---------------------------------------------------------------------------


def register_escalation_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the 2 SP-OP-ESCALATION-001 raw async Kafka handlers on `harness` (DL-0034).

    RAW `harness.register()` handlers (NOT `register_worker`) — DL-0034 (ratified by orchestrator
    2026-07-26, built in t5): emitting the escalation notification needs the async Kafka seam a
    `WorkerBase.execute` boundary cannot reach; this is the #55 R-B / `events.publish` sanctioned
    form, NOT an exception to ADR-0026 §2's dict-first purity (for this class of task, notifying IS
    the business effect and the BPMN gives it its OWN topic). `kafka` is threaded into BOTH
    handlers; production wires a real producer later (T1.2/ADR-0026), and both fail closed to
    "complete + loud log" while it is `None`. Because these are raw handlers, they populate the
    harness `_handlers` table but NOT the `WorkerRegistry` (see `test_bootstrap_registration.py`'s
    `raw_handler_topics`).
    """
    del seams  # unused — no dmn/other seam is needed by these notify handlers
    harness.register(_NOTIFY_TEAM_TOPIC, make_notify_team_handler(kafka))
    harness.register(_NOTIFY_SUPERVISOR_TOPIC, make_notify_supervisor_handler(kafka))
