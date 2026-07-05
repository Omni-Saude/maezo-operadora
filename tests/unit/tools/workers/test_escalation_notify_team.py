"""Unit tests for SP-OP-ESCALATION-001 workers — TDD London School.

Tests escalation workers: notify_team, notify_fallback, notify_supervisor.
Workers must never make adverse decisions — only route and notify.
"""

from __future__ import annotations

from maezo.tools.workers.escalation import (
    NotifyFallbackWorker,
    NotifySupervisorWorker,
    NotifyTeamWorker,
)

# ---------------------------------------------------------------------------
# notify_team
# ---------------------------------------------------------------------------


def test_notify_team_topic() -> None:
    """notify_team worker must have topic 'operadora.escalation.notify_team'."""
    worker = NotifyTeamWorker()
    assert worker.topic == "operadora.escalation.notify_team"


def test_notify_team_reads_process_vars() -> None:
    """notify_team reads BPMN variables and produces routing decision."""
    worker = NotifyTeamWorker()

    process_vars = {
        "tenant_id": "amh",
        "source_agent_id": "helena",
        "severity": "grave",
        "motivo_categoria": "red_flag_clinico",
        "beneficiario_pseudo_id": "pseudo-abc123",
    }

    result = worker.run(process_vars)

    assert result["status"] == "teams_notified"
    assert result["group"] == "plantao-clinico"
    assert result["severity"] == "grave"


def test_notify_team_returns_routing_group() -> None:
    """notify_team returns the correct routing group based on severity."""
    worker = NotifyTeamWorker()

    # grave -> plantao-clinico
    result = worker.run(
        {
            "tenant_id": "amh",
            "severity": "grave",
            "motivo_categoria": "red_flag_clinico",
        }
    )
    assert result["group"] == "plantao-clinico"

    # moderada -> enfermagem-triagem
    result = worker.run(
        {
            "tenant_id": "amh",
            "severity": "moderada",
            "motivo_categoria": "risco_psicossocial",
        }
    )
    assert result["group"] == "enfermagem-triagem"

    # leve -> atendimento-humano
    result = worker.run(
        {
            "tenant_id": "amh",
            "severity": "leve",
            "motivo_categoria": "solicitacao_humano",
        }
    )
    assert result["group"] == "atendimento-humano"


def test_notify_team_unknown_severity_falls_to_default() -> None:
    """Unknown severity defaults to atendimento-humano group (fail-safe)."""
    worker = NotifyTeamWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "severity": "unknown",
            "motivo_categoria": "outro",
        }
    )

    assert result["group"] == "atendimento-humano"


def test_notify_team_never_decides_adverse_action() -> None:
    """notify_team ONLY produces routing info — never a decision about the case."""
    worker = NotifyTeamWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "severity": "grave",
            "motivo_categoria": "red_flag_clinico",
            "beneficiario_pseudo_id": "pseudo-abc",
        }
    )

    # Must NOT contain any decision fields
    assert "decisao" not in result
    assert "resultado" not in result
    assert "notas_resolucao" not in result
    # Must contain routing info only
    assert "group" in result
    assert "status" in result


def test_notify_team_publishes_event() -> None:
    """notify_team must include a published event reference in the result."""
    worker = NotifyTeamWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "severity": "grave",
            "motivo_categoria": "red_flag_clinico",
        }
    )

    assert result["event"] == "agents.events.escalation.requested"


# ---------------------------------------------------------------------------
# notify_fallback
# ---------------------------------------------------------------------------


def test_notify_fallback_topic() -> None:
    """notify_fallback worker must have the correct topic."""
    worker = NotifyFallbackWorker()
    assert worker.topic == "operadora.escalation.notify_fallback"


def test_notify_fallback_notifies_supervisor_group() -> None:
    """notify_fallback must route to supervisor when primary notification fails."""
    worker = NotifyFallbackWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "fallback_reason": "ERR_ESC_NOTIFY_FAILED",
        }
    )

    assert result["status"] == "fallback_triggered"
    assert result["fallback_group"] == "supervisao-atendimento"
    assert result["original_error"] == "ERR_ESC_NOTIFY_FAILED"


def test_notify_fallback_never_escalates_to_adverse() -> None:
    """notify_fallback must NOT make any adverse decisions."""
    worker = NotifyFallbackWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "fallback_reason": "timeout",
        }
    )

    assert "negativa" not in str(result).lower()
    assert "deny" not in str(result).lower()


# ---------------------------------------------------------------------------
# notify_supervisor
# ---------------------------------------------------------------------------


def test_notify_supervisor_topic() -> None:
    """notify_supervisor worker must have the correct topic."""
    worker = NotifySupervisorWorker()
    assert worker.topic == "operadora.escalation.notify_supervisor"


def test_notify_supervisor_sla_breach() -> None:
    """notify_supervisor alerts supervisor on SLA breach."""
    worker = NotifySupervisorWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "sla_status": "breached",
            "severity": "grave",
        }
    )

    assert result["status"] == "supervisor_notified"
    assert result["sla_status"] == "breached"


def test_notify_supervisor_no_human_decision() -> None:
    """notify_supervisor must not make any human-authority decisions."""
    worker = NotifySupervisorWorker()

    result = worker.run(
        {
            "tenant_id": "amh",
            "sla_status": "breached",
        }
    )

    assert "decision" not in result
    assert "approve" not in str(result).lower()
    assert "deny" not in str(result).lower()
