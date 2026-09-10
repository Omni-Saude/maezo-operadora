"""Finite SP-OP-PROGRAMA-001 publication contract (R228).

Five BPMN event declarations and four notification builders carry per-instance strings.
No population publication shape is contracted. Any extension (including an opaque field
or nested value) refuses before transport; policy ratification alone cannot add a sink.
New aggregate consumers require an explicit contract and verified population policy call.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from maezo.platform.privacy.population_policy import PopulationPolicyUnavailableError
from maezo.tools.process_allowlist import KNOWN_PROCESS_KEYS

# Source identity comes from fetchAndLock metadata, not from outgoing event inputs.
# SP-OP-ANS-CRON-001 is the contract/model family, not an engine process ID.
# Its five timer definitions publish facts but remain intentionally not agent-startable
# (SP-OP-ANS-CRON-001 contract: topology per report_type; ADR-0003/ADR-0016).
_PUBLICATION_PROCESS_KEYS = KNOWN_PROCESS_KEYS | {
    "SP-OP-ANS-CRON-001-RN124SIP",
    "SP-OP-ANS-CRON-001-RN209",
    "SP-OP-ANS-CRON-001-RN388",
    "SP-OP-ANS-CRON-001-RN424TISS",
    "SP-OP-ANS-CRON-001-DIOPS",
}
PROGRAM_PROCESS_KEY = "SP-OP-PROGRAMA-001"
PROGRAM_ACTIVITY_TOPICS = {
    "ST_PublishReceived": "agents.events.programa.received",
    "ST_PublishConsentBlocked": "agents.events.programa.consent_blocked",
    "ST_PublishSlaBreached": "agents.events.programa.sla_breached",
    "ST_PublishProcessingStopped": "agents.events.programa.processing_stopped",
    "ST_PublishCompleted": "agents.events.programa.completed",
}

_BASE = frozenset({"tenant_id", "programa_id", "beneficiario_pseudo_id", "ciclo"})
_META = frozenset({"_business_key", "_process_instance_id", "_worker_topic"})
PROGRAM_EVENT_FIELDS = {
    "agents.events.programa.received": _BASE | {"gatilho"},
    "agents.events.programa.consent_blocked": _BASE,
    "agents.events.programa.sla_breached": _BASE,
    "agents.events.programa.processing_stopped": _BASE,
    "agents.events.programa.completed": _BASE
    | {
        "decisao_programa",
        "elegivel_programa",
        "responsavel_clinico_id",
        "contato_gap",
        "enrollment_gap",
        "desfecho",
    },
}
PROGRAM_NOTIFICATION_FIELDS = {
    "programa.stratify_risk": (_BASE - {"ciclo"}) | {"type", "risco_estratificado"},
    "programa.stop_processing": (_BASE - {"ciclo"}) | {"type"},
    "programa.proactive_contact": (_BASE - {"ciclo"}) | {"type"},
    "programa.notify_sla_risk": (_BASE - {"ciclo"}) | {"type"},
}


def validate_program_publication_source(
    process_definition_key: str | None,
    activity_id: str | None,
    worker_topic: str,
    topic: str,
    payload: Mapping[str, Any],
) -> None:
    """Bind generic publication to the fetched engine source before partition/transport.

    This is an engine transport trust boundary, not authentication of caller-created
    Python objects. All generic tasks require known source metadata. Program activities
    have exactly the five BPMN destinations; other families keep their payload behavior
    but cannot impersonate program destinations. Direct program notification builders
    own their separate explicit contracts and do not use the generic task interface.
    """
    if (
        type(process_definition_key) is not str
        or process_definition_key not in _PUBLICATION_PROCESS_KEYS
        or type(activity_id) is not str
        or not activity_id.strip()
        or worker_topic != "operadora.events.publish"
    ):
        raise PopulationPolicyUnavailableError("publication_source_unavailable")
    if process_definition_key == PROGRAM_PROCESS_KEY:
        expected = PROGRAM_ACTIVITY_TOPICS.get(activity_id)
        if expected is None or topic != expected:
            raise PopulationPolicyUnavailableError("program_publication_source_mismatch")
    elif (
        topic == "agents.events.programa"
        or topic.startswith("agents.events.programa.")
        or (
            topic == "operadora.notifications.internal"
            and (payload.get("type") == "programa" or str(payload.get("type", "")).startswith("programa."))
        )
    ):
        raise PopulationPolicyUnavailableError("program_publication_source_mismatch")


def validate_program_publication(topic: str, payload: Mapping[str, Any]) -> None:
    """Validate at actual sink, not from event_payload_vars or a prose reference."""
    if topic.startswith("agents.events.programa."):
        allowed = PROGRAM_EVENT_FIELDS.get(topic)
        if allowed is not None:
            allowed = allowed | _META
    elif topic == "operadora.notifications.internal" and str(payload.get("type", "")).startswith("programa."):
        allowed = PROGRAM_NOTIFICATION_FIELDS.get(str(payload.get("type", "")))
        if allowed is not None:
            allowed = allowed | _META
    else:
        return
    if (
        allowed is None
        or not set(payload) <= allowed
        or any(value is not None and type(value) is not str for value in payload.values())
    ):
        raise PopulationPolicyUnavailableError("program_publication_not_contracted")
