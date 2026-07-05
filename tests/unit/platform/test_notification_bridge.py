"""Unit tests for maezo.platform.notification_bridge — cross-process handoffs.

TDD London School: tests exercise handoff rules (evaluate) without
Kafka or CIB Seven. The starter is injected as a spy.
"""

from typing import Any

import pytest

from maezo.platform.notification_bridge import (
    HandoffEvent,
    HandoffResult,
    NotificationBridge,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bridge() -> NotificationBridge:
    """Create a bridge with the no-op starter for evaluate-only tests."""
    return NotificationBridge()


class StarterSpy:
    """Spy starter that records calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, process_key: str, variables: dict[str, Any]) -> str:
        self.calls.append((process_key, variables))
        return f"instance-{process_key}-spy"


def _make_bridge_with_spy() -> tuple[NotificationBridge, StarterSpy]:
    """Create a bridge with a spy starter for execute tests."""
    spy = StarterSpy()
    return NotificationBridge(cibseven_starter=spy), spy


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_bridge_initializes_with_default_handoffs() -> None:
    """NotificationBridge registers the 5 default handoff rules on init."""
    bridge = _make_bridge()
    handoffs = bridge.list_handoffs()
    # 5 rules: contas.glosa_confirmed, contas.encaminhar_fraude,
    # fraude.acusacao_registrada (x3: CRED, CANCEL, INADIMPLENCIA)
    assert len(handoffs) == 5

    targets = {h["target_process"] for h in handoffs}
    assert "SP-OP-RECURSO-001" in targets
    assert "SP-OP-FRAUDE-001" in targets
    assert "SP-OP-CRED-001" in targets
    assert "SP-OP-CANCEL-001" in targets
    assert "SP-OP-INADIMPLENCIA-001" in targets


def test_bridge_register_custom_handoff() -> None:
    """register_handoff allows adding custom handoff rules."""
    bridge = _make_bridge()
    bridge.register_handoff(
        event_type="custom.test",
        predicate=lambda p: p.get("go") is True,
        target_process="SP-CUSTOM-001",
        variables_fn=lambda p: {"value": p.get("x", 0)},
    )
    assert len(bridge.list_handoffs()) == 6

    result = bridge.evaluate(HandoffEvent(event_type="custom.test", payload={"go": True, "x": 42}))
    assert result.handoff_triggered is True
    assert result.target_process == "SP-CUSTOM-001"
    assert result.variables == {"value": 42}


# ---------------------------------------------------------------------------
# CONTAS → RECURSO
# ---------------------------------------------------------------------------


def test_bridge_contas_to_recurso_triggered() -> None:
    """Handoff CONTAS→RECURSO triggers when decisao_contas == RECORRER."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="contas.glosa_confirmed",
        payload={
            "decisao_contas": "RECORRER",
            "glosa_id": "GLOSA-001",
            "numero_guia_tiss": "GUIA-123",
            "glosa_type": "tecnica",
            "documentacao_anexa": True,
            "numero_lote_tiss": "LOTE-001",
        },
    )
    result = bridge.evaluate(event)
    assert result.evaluated is True
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-RECURSO-001"
    assert result.variables["glosa_id"] == "GLOSA-001"
    assert result.variables["numero_guia_tiss"] == "GUIA-123"
    assert result.variables["glosa_type"] == "tecnica"
    assert result.variables["glosa_existe"] is True
    assert result.variables["documentacao_anexa"] is True


def test_bridge_contas_to_recurso_not_triggered_when_not_recorrer() -> None:
    """Handoff CONTAS→RECURSO does NOT trigger when decisao_contas != RECORRER."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="contas.glosa_confirmed",
        payload={
            "decisao_contas": "ACEITAR_GLOSA",
            "glosa_id": "GLOSA-001",
        },
    )
    result = bridge.evaluate(event)
    assert result.evaluated is True
    assert result.handoff_triggered is False
    assert "No handoff rule matched" in result.reason


# ---------------------------------------------------------------------------
# CONTAS → FRAUDE
# ---------------------------------------------------------------------------


def test_bridge_contas_to_fraude_triggered() -> None:
    """Handoff CONTAS→FRAUDE triggers when encaminhar_fraude == true."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="contas.encaminhar_fraude",
        payload={
            "encaminhar_fraude": True,
            "analista_id": "auditor-001",
            "numero_lote_tiss": "LOTE-001",
            "prestador_id": "PREST-001",
            "evidencia_refs": ["ref-1", "ref-2"],
            "indicadores_presentes": ["score_elevado"],
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-FRAUDE-001"
    assert result.variables["origem_encaminhamento"] == "contas"
    assert result.variables["encaminhado_por_id"] == "auditor-001"
    assert result.variables["prestador_id"] == "PREST-001"
    assert result.variables["evidencia_refs"] == ["ref-1", "ref-2"]


def test_bridge_contas_to_fraude_not_triggered_when_false() -> None:
    """Handoff CONTAS→FRAUDE does NOT trigger when encaminhar_fraude is False."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="contas.encaminhar_fraude",
        payload={"encaminhar_fraude": False},
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is False


def test_bridge_contas_to_fraude_not_triggered_when_missing() -> None:
    """Handoff CONTAS→FRAUDE does NOT trigger when encaminhar_fraude is absent."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="contas.encaminhar_fraude",
        payload={},
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is False


# ---------------------------------------------------------------------------
# FRAUDE → CRED
# ---------------------------------------------------------------------------


def test_bridge_fraude_to_cred_triggered() -> None:
    """Handoff FRAUDE→CRED triggers when fraud confirmed against a prestador."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="fraude.acusacao_registrada",
        payload={
            "decisao_fraude": "ACUSAR_FRAUDE",
            "entidade_tipo": "prestador",
            "prestador_id": "PREST-001",
            "numero_caso": "FRAUDE-001",
            "bundle_root": "abc123def",
            "destino_referral": {"legal": True},
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-CRED-001"


def test_bridge_fraude_to_cred_not_triggered_when_not_prestador() -> None:
    """Handoff FRAUDE→CRED does NOT trigger for beneficiario fraud."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="fraude.acusacao_registrada",
        payload={
            "decisao_fraude": "ACUSAR_FRAUDE",
            "entidade_tipo": "beneficiario",
            "prestador_id": "PREST-001",
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True  # triggers CANCEL, not CRED
    assert result.target_process == "SP-OP-CANCEL-001"


# ---------------------------------------------------------------------------
# FRAUDE → CANCEL/INADIMPLENCIA
# ---------------------------------------------------------------------------


def test_bridge_fraude_to_cancel_triggered() -> None:
    """Handoff FRAUDE→CANCEL triggers when fraud confirmed against a beneficiario."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="fraude.acusacao_registrada",
        payload={
            "decisao_fraude": "ACUSAR_FRAUDE",
            "entidade_tipo": "beneficiario",
            "beneficiario_pseudo_id": "pseudo-b-001",
            "numero_contrato": "CTR-001",
            "numero_caso": "FRAUDE-001",
            "bundle_root": "abc123def",
        },
    )
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-CANCEL-001"
    assert result.variables["beneficiario_pseudo_id"] == "pseudo-b-001"


def test_bridge_fraude_to_inadimplencia_triggered() -> None:
    """Handoff FRAUDE→INADIMPLENCIA and CANCEL both trigger for contract fraud."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="fraude.acusacao_registrada",
        payload={
            "decisao_fraude": "ACUSAR_FRAUDE",
            "entidade_tipo": "contrato",
            "numero_contrato": "CTR-001",
            "beneficiario_pseudo_id": "pseudo-b-001",
            "numero_caso": "FRAUDE-001",
        },
    )
    # evaluate returns first match (CANCEL, registered before INADIMPLENCIA)
    result = bridge.evaluate(event)
    assert result.handoff_triggered is True
    assert result.target_process == "SP-OP-CANCEL-001"

    # evaluate_all returns both for contrato
    all_results = bridge.evaluate_all(event)
    assert len(all_results) == 2
    targets = {r.target_process for r in all_results}
    assert targets == {"SP-OP-CANCEL-001", "SP-OP-INADIMPLENCIA-001"}


# ---------------------------------------------------------------------------
# evaluate_all — multiple handoffs from same event
# ---------------------------------------------------------------------------


def test_evaluate_all_multiple_fraude_handoffs() -> None:
    """evaluate_all returns all matching rules for fraude.acusacao_registrada."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="fraude.acusacao_registrada",
        payload={
            "decisao_fraude": "ACUSAR_FRAUDE",
            "entidade_tipo": "contrato",
            "numero_contrato": "CTR-001",
            "beneficiario_pseudo_id": "pseudo-b-001",
            "numero_caso": "FRAUDE-001",
        },
    )
    results = bridge.evaluate_all(event)
    # For contrato: CANCEL + INADIMPLENCIA (2 rules match)
    assert len(results) == 2
    targets = {r.target_process for r in results}
    assert "SP-OP-CANCEL-001" in targets
    assert "SP-OP-INADIMPLENCIA-001" in targets
    for r in results:
        assert r.handoff_triggered is True


def test_evaluate_all_single_match() -> None:
    """evaluate_all returns one result when only one rule matches."""
    bridge = _make_bridge()
    event = HandoffEvent(
        event_type="contas.glosa_confirmed",
        payload={
            "decisao_contas": "RECORRER",
            "glosa_id": "GLOSA-001",
            "numero_guia_tiss": "GUIA-123",
            "glosa_type": "tecnica",
            "numero_lote_tiss": "LOTE-001",
        },
    )
    results = bridge.evaluate_all(event)
    assert len(results) == 1
    assert results[0].target_process == "SP-OP-RECURSO-001"


def test_evaluate_all_no_match() -> None:
    """evaluate_all returns a single not-triggered result when no rules match."""
    bridge = _make_bridge()
    event = HandoffEvent(event_type="nonexistent.event", payload={})
    results = bridge.evaluate_all(event)
    assert len(results) == 1
    assert results[0].handoff_triggered is False
    assert "No handoff rule matched" in results[0].reason


# ---------------------------------------------------------------------------
# Unknown event types
# ---------------------------------------------------------------------------


def test_bridge_unknown_event_type() -> None:
    """Evaluating an unregistered event type returns not-triggered."""
    bridge = _make_bridge()
    result = bridge.evaluate(HandoffEvent(event_type="nonexistent.event", payload={}))
    assert result.evaluated is True
    assert result.handoff_triggered is False
    assert "No handoff rule matched" in result.reason


# ---------------------------------------------------------------------------
# execute_handoff (async, with spy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_handoff_calls_starter() -> None:
    """execute_handoff calls the injected CIB Seven starter."""
    bridge, spy = _make_bridge_with_spy()
    result = HandoffResult(
        evaluated=True,
        handoff_triggered=True,
        target_process="SP-OP-RECURSO-001",
        variables={"glosa_id": "G-1"},
    )
    result = await bridge.execute_handoff(result)
    assert result.process_instance_id == "instance-SP-OP-RECURSO-001-spy"
    assert len(spy.calls) == 1
    assert spy.calls[0] == ("SP-OP-RECURSO-001", {"glosa_id": "G-1"})


@pytest.mark.asyncio
async def test_execute_handoff_skips_when_not_triggered() -> None:
    """execute_handoff skips the starter when handoff_triggered is False."""
    bridge, spy = _make_bridge_with_spy()
    result = HandoffResult(
        evaluated=True,
        handoff_triggered=False,
        reason="Nothing to do",
    )
    result = await bridge.execute_handoff(result)
    assert result.process_instance_id == ""
    assert len(spy.calls) == 0


@pytest.mark.asyncio
async def test_on_event_full_pipeline() -> None:
    """on_event evaluates and executes in one call."""
    bridge, spy = _make_bridge_with_spy()
    results = await bridge.on_event(
        event_type="contas.glosa_confirmed",
        payload={
            "decisao_contas": "RECORRER",
            "glosa_id": "GLOSA-002",
            "numero_guia_tiss": "GUIA-456",
            "glosa_type": "clinica",
            "documentacao_anexa": False,
            "numero_lote_tiss": "LOTE-002",
        },
    )
    assert len(results) == 1
    assert results[0].handoff_triggered is True
    assert results[0].target_process == "SP-OP-RECURSO-001"
    assert results[0].process_instance_id == "instance-SP-OP-RECURSO-001-spy"
    assert spy.calls[0][0] == "SP-OP-RECURSO-001"


@pytest.mark.asyncio
async def test_on_event_multiple_handoffs() -> None:
    """on_event starts multiple processes for multi-handoff events."""
    bridge, spy = _make_bridge_with_spy()
    results = await bridge.on_event(
        event_type="fraude.acusacao_registrada",
        payload={
            "decisao_fraude": "ACUSAR_FRAUDE",
            "entidade_tipo": "contrato",
            "numero_contrato": "CTR-001",
            "beneficiario_pseudo_id": "pseudo-b-001",
            "numero_caso": "FRAUDE-001",
        },
    )
    assert len(results) == 2
    targets = {r.target_process for r in results}
    assert targets == {"SP-OP-CANCEL-001", "SP-OP-INADIMPLENCIA-001"}
    for r in results:
        assert r.process_instance_id.startswith("instance-")
    assert len(spy.calls) == 2


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def test_list_handoffs_returns_all() -> None:
    """list_handoffs returns all registered handoff rules."""
    bridge = _make_bridge()
    handoffs = bridge.list_handoffs()
    assert isinstance(handoffs, list)
    assert len(handoffs) == 5
    assert all("event_type" in h and "target_process" in h for h in handoffs)


def test_get_handoff_returns_rules() -> None:
    """get_handoff returns all rules for an event type."""
    bridge = _make_bridge()
    rules = bridge.get_handoff("contas.glosa_confirmed")
    assert len(rules) == 1
    target, predicate = rules[0]
    assert target == "SP-OP-RECURSO-001"
    assert callable(predicate)
    assert predicate({"decisao_contas": "RECORRER"}) is True
    assert predicate({"decisao_contas": "ACEITAR"}) is False


def test_get_handoff_multiple_rules() -> None:
    """get_handoff returns multiple rules for fraude.acusacao_registrada."""
    bridge = _make_bridge()
    rules = bridge.get_handoff("fraude.acusacao_registrada")
    assert len(rules) == 3  # CRED, CANCEL, INADIMPLENCIA
    targets = {t for t, _ in rules}
    assert targets == {"SP-OP-CRED-001", "SP-OP-CANCEL-001", "SP-OP-INADIMPLENCIA-001"}


def test_get_handoff_unknown_returns_empty() -> None:
    """get_handoff returns empty list for unregistered event types."""
    bridge = _make_bridge()
    assert bridge.get_handoff("nonexistent.event") == []


def test_count_handoffs() -> None:
    """count_handoffs returns the number of registered rules."""
    bridge = _make_bridge()
    assert bridge.count_handoffs() == 5
    bridge.register_handoff(
        event_type="extra.rule.here",
        predicate=lambda p: True,
        target_process="SP-EXTRA-001",
        variables_fn=lambda p: {},
    )
    assert bridge.count_handoffs() == 6
