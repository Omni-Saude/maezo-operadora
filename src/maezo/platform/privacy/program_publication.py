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
