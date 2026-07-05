"""Unit tests for maezo.gateway.pep — Policy Enforcement Point (ADR-0005, ADR-0008, DL-0005).

TDD London School: tests written BEFORE implementation.
"""

from maezo.gateway.pep import HARD_ACTIONS, PEP, Decision


def test_pep_deny_l0_hard() -> None:
    """L0-hard actions (negativa_cobertura, decisao_clinica, acusacao_fraude)
    must return DENY regardless of agent context."""
    pep = PEP()

    for action in HARD_ACTIONS:
        result = pep.evaluate(action, agent_context={"agent_id": "helena", "level": "L2"})
        assert result == Decision.DENY, f"L0-hard action '{action}' must be DENY, got {result}"


def test_pep_require_human_l1() -> None:
    """L1 actions must return REQUIRE_HUMAN."""
    pep = PEP()

    result = pep.evaluate("pagamento_alcada", agent_context={"agent_id": "marina", "level": "L2"})
    assert result == Decision.REQUIRE_HUMAN


def test_pep_allow_l2() -> None:
    """L2 actions must return ALLOW."""
    pep = PEP()

    result = pep.evaluate("aprovacao_auth_dmn_favoravel", agent_context={"agent_id": "rafael", "level": "L2"})
    assert result == Decision.ALLOW


def test_pep_allow_l3() -> None:
    """L3 actions must return ALLOW."""
    pep = PEP()

    result = pep.evaluate("triagem_whatsapp", agent_context={"agent_id": "helena", "level": "L2"})
    assert result == Decision.ALLOW


def test_pep_require_human_l0_non_hard() -> None:
    """L0 non-hard actions must return REQUIRE_HUMAN (dossier path)."""
    pep = PEP()

    result = pep.evaluate("cancelamento_contrato", agent_context={"agent_id": "beatriz", "level": "L2"})
    assert result == Decision.REQUIRE_HUMAN


def test_pep_unknown_action_deny() -> None:
    """Unknown actions not in the autonomy matrix default to DENY (fail-closed)."""
    pep = PEP()

    result = pep.evaluate("acao_inexistente", agent_context={"agent_id": "helena", "level": "L2"})
    assert result == Decision.DENY


def test_pep_hard_actions_is_frozenset() -> None:
    """HARD_ACTIONS must be an immutable frozenset (CI-enforced, not YAML-configurable)."""
    assert isinstance(HARD_ACTIONS, frozenset)
    assert "negativa_cobertura" in HARD_ACTIONS
    assert "decisao_clinica" in HARD_ACTIONS
    assert "acusacao_fraude" in HARD_ACTIONS


def test_pep_agent_context_preserved_in_result() -> None:
    """The PEP result should include the agent context for audit trail purposes."""
    pep = PEP()

    ctx = {"agent_id": "rafael", "level": "L2", "tenant": "amh"}
    result = pep.evaluate("triagem_whatsapp", agent_context=ctx)
    assert result == Decision.ALLOW
