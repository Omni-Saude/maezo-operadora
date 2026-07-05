"""MAEZO Workers — BPMN external task handlers (Phase 0-1).

Workers consume external tasks from the BPMN engine and implement
the business logic defined in process contracts (SP-OP-*).

Architecture:
- WorkerBase: base class with structlog, error handling, retry
- WorkerRegistry: registration by external task topic
- ERR_*_NOT_HUMAN: guards preventing automatic adverse actions

TDD London School: tests in tests/unit/tools/workers/
"""

from __future__ import annotations

from maezo.tools.workers.base import (
    ERR_DENIAL_NOT_HUMAN,
    ERR_ESCALATION_NOT_HUMAN,
    ERR_FRAUD_ACCUSATION_NOT_HUMAN,
    WorkerBase,
    WorkerRegistry,
    worker_registry,
)

__all__ = [
    "ERR_DENIAL_NOT_HUMAN",
    "ERR_ESCALATION_NOT_HUMAN",
    "ERR_FRAUD_ACCUSATION_NOT_HUMAN",
    "WorkerBase",
    "WorkerRegistry",
    "worker_registry",
]
