"""Unit tests for maezo.gateway.pep — Policy Enforcement Point (ADR-0005, ADR-0008, ADR-0025, DL-0005).

Vocabulary migrated to the English canonical action names of
``spec/policies/autonomy/L0-core.yaml`` (ADR-0025 D1). The former Portuguese
names are retired and now fail-close to DENY (proven in
``test_pep_policy_unification.py``). The matrix is loaded from YAML via
``build_pep`` rather than a hard-coded dict.
"""

from maezo.gateway.pep import HARD_ACTIONS, Decision, build_pep


def test_pep_deny_l0_hard() -> None:
    """The frozen-5 L0-hard actions must return DENY regardless of agent context."""
    pep = build_pep()

    for action in HARD_ACTIONS:
        result = pep.evaluate(action, agent_context={"agent_id": "helena", "level": "L2"})
        assert result == Decision.DENY, f"L0-hard action '{action}' must be DENY, got {result}"


def test_pep_require_human_l1() -> None:
    """L1 actions must return REQUIRE_HUMAN."""
    pep = build_pep()

    result = pep.evaluate("high_value_payment", agent_context={"agent_id": "marina", "level": "L2"})
    assert result == Decision.REQUIRE_HUMAN


def test_pep_allow_l2() -> None:
    """L2 actions must return ALLOW."""
    pep = build_pep()

    result = pep.evaluate("authorization_approval", agent_context={"agent_id": "rafael", "level": "L2"})
    assert result == Decision.ALLOW


def test_pep_allow_l3() -> None:
    """L3 actions must return ALLOW."""
    pep = build_pep()

    result = pep.evaluate("triage_and_routing", agent_context={"agent_id": "helena", "level": "L2"})
    assert result == Decision.ALLOW


def test_pep_contract_termination_hardened_to_deny() -> None:
    """contract_termination (formerly the L0-non-hard `cancelamento_contrato`) is now L0 HARD → DENY.

    ADR-0025 §3 migration table, Δ HARDEN row: the closest PT predecessor was coded
    L0 non-hard (REQUIRE_HUMAN); the frozen canonical action is L0 hard, so it must DENY.
    """
    pep = build_pep()

    result = pep.evaluate("contract_termination", agent_context={"agent_id": "beatriz", "level": "L2"})
    assert result == Decision.DENY


def test_pep_unknown_action_deny() -> None:
    """Unknown actions not in the autonomy matrix default to DENY (fail-closed)."""
    pep = build_pep()

    result = pep.evaluate("acao_inexistente", agent_context={"agent_id": "helena", "level": "L2"})
    assert result == Decision.DENY


def test_pep_hard_actions_is_frozenset() -> None:
    """HARD_ACTIONS must be an immutable frozenset of the English canonical 5 (ADR-0025 D3)."""
    assert isinstance(HARD_ACTIONS, frozenset)
    assert (
        frozenset(
            {
                "clinical_decision",
                "authorization_denial",
                "nip_manter_negativa",
                "fraud_accusation",
                "contract_termination",
            }
        )
        == HARD_ACTIONS
    )


def test_pep_agent_context_preserved_in_result() -> None:
    """Passing agent_context must not change the decision (context is audit-only)."""
    pep = build_pep()

    ctx = {"agent_id": "rafael", "level": "L2", "tenant": "amh"}
    result = pep.evaluate("triage_and_routing", agent_context=ctx)
    assert result == Decision.ALLOW
