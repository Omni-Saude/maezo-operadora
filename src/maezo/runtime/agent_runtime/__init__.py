"""Agent-runtime daemon (T1.6, design §10/Q-6 — ratified).

Health-only scaffold: the per-agent Deployment factory (`deployment-agent-runtime.yaml`) already
renders one Deployment per enabled `values.yaml` agent entry, each running
`python -m maezo.runtime.agent_runtime` (see `__main__.py`). Before this build that module did
not exist, so every enabled agent pod CrashLoopBackOff'd. This build makes the pod healthy
(`/healthz`, `/readyz`, `/metrics`) with three honest readiness checks — agent definition
loadable, autonomy policies loadable, inference provider constructible — and explicitly does
**not** execute an agent's LangGraph (T1.11's job).

Importing this package has no side effect (no `asyncio.run` at import time).
"""

from __future__ import annotations

from maezo.runtime.agent_runtime.service import (
    AgentState,
    build_readiness_checks,
    run,
)
from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings

__all__ = [
    "AgentRuntimeSettings",
    "AgentState",
    "build_readiness_checks",
    "run",
]
