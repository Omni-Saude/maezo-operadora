"""MAEZO A2A Collaboration — Agent Card, Registry, Anti-Loop (ADR-0003, ADR-0015).

Provides the structural foundation for Agent-to-Agent delegation:
- AgentCard: identity and capability declaration for each agent.
- A2ARegistry: tenant-scoped registry of AgentCards (ADR-0004).
- AntiLoopGuard: structural loop prevention (max depth, cycle detection).
"""

from maezo.a2a.anti_loop import AntiLoopGuard, CyclicDelegationError, MaxDepthExceededError
from maezo.a2a.card import AgentCard
from maezo.a2a.registry import A2ARegistry

__all__ = [
    "AgentCard",
    "A2ARegistry",
    "AntiLoopGuard",
    "CyclicDelegationError",
    "MaxDepthExceededError",
]
