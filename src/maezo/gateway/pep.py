"""Policy Enforcement Point — evaluates autonomy matrix before every tool call (ADR-0005, ADR-0008, DL-0005).

The PEP is the structural enforcement of HITL guarantees. It evaluates each action
against the autonomy matrix L0-L3 and returns ALLOW, DENY, or REQUIRE_HUMAN.

HARD_ACTIONS (L0-hard) are DENY for agents regardless of level — CI-enforced frozenset.
"""

from __future__ import annotations

import enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# L0-hard actions — immutable, CI-enforced, never YAML-configurable (DL-0005)
# negativa_cobertura: RN 259 prohibits coverage denial without medical auditor
# decisao_clinica: clinical/diagnostic decisions by agents are prohibited, hard-coded
# acusacao_fraude: fraud accusation is L0-hard, structural evidence required before human decision
HARD_ACTIONS: frozenset[str] = frozenset(
    {
        "negativa_cobertura",
        "decisao_clinica",
        "acusacao_fraude",
    }
)


class Decision(enum.Enum):
    """PEP decision for a tool call."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"


class PEP:
    """Policy Enforcement Point.

    Evaluates each action against the autonomy matrix:
    - L0-hard → DENY (agent cannot perform under any circumstances)
    - L0-non-hard → REQUIRE_HUMAN (dossier path)
    - L1 → REQUIRE_HUMAN (agent proposes, human approves)
    - L2/L3 → ALLOW (agent executes autonomously within bounds)
    """

    # Autonomy matrix: action → level mapping (ADR-0008)
    # This is the default matrix; tenant-specific overlays come from YAML policies
    _AUTONOMY_MATRIX: dict[str, int] = {
        # L0: human-only
        "negativa_cobertura": 0,
        "decisao_clinica": 0,
        "acusacao_fraude": 0,
        "cancelamento_contrato": 0,  # L0 non-hard
        "descredenciamento": 0,  # L0 non-hard
        # L1: agent proposes, human approves
        "pagamento_alcada": 1,
        "resposta_nip": 1,
        "envio_ans": 1,
        "descredenciamento_formal": 1,
        # L2: agent executes, review by sampling
        "aprovacao_auth_dmn_favoravel": 2,
        "glosa_padrao": 2,
        "reembolso_calculo": 2,
        "analise_recurso": 2,
        # L3: autonomous with telemetry
        "triagem_whatsapp": 3,
        "agendamento": 3,
        "respostas_informativas": 3,
        "lembretes": 3,
        "autorizacao_automatica": 3,
    }

    def evaluate(self, action: str, agent_context: dict[str, Any] | None = None) -> Decision:
        """Evaluate whether an action is allowed for the given agent context.

        Args:
            action: The action being requested (e.g. 'negativa_cobertura', 'triagem_whatsapp').
            agent_context: Optional dict with agent_id, level, tenant, etc.

        Returns:
            Decision.ALLOW, Decision.DENY, or Decision.REQUIRE_HUMAN.
        """
        # L0-hard actions are structurally denied to agents (DL-0005)
        if action in HARD_ACTIONS:
            logger.warning(
                "pep_deny_l0_hard",
                action=action,
                agent_context=agent_context,
            )
            return Decision.DENY

        # Look up the action's autonomy level
        level = self._AUTONOMY_MATRIX.get(action)

        if level is None:
            # Unknown action: fail-closed (deny)
            logger.warning(
                "pep_deny_unknown_action",
                action=action,
                agent_context=agent_context,
            )
            return Decision.DENY

        # L0 non-hard and L1 → REQUIRE_HUMAN (DL-0005)
        if level <= 1:
            logger.info(
                "pep_require_human",
                action=action,
                level=level,
                agent_context=agent_context,
            )
            return Decision.REQUIRE_HUMAN

        # L2 and L3 → ALLOW
        logger.info(
            "pep_allow",
            action=action,
            level=level,
            agent_context=agent_context,
        )
        return Decision.ALLOW
