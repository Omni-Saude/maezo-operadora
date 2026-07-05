"""Unit tests for WorkerRegistry — TDD London School.

Tests the WorkerBase base class and WorkerRegistry for external task routing.
"""

from __future__ import annotations

import pytest

from maezo.tools.workers.base import (
    ERR_DENIAL_NOT_HUMAN,
    ERR_ESCALATION_NOT_HUMAN,
    ERR_FRAUD_ACCUSATION_NOT_HUMAN,
    WorkerBase,
    WorkerRegistry,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class StubWorker(WorkerBase):
    """Minimal worker for testing the base class."""

    def execute(self, process_vars: dict) -> dict:
        return {"status": "ok"}


class FailingWorker(WorkerBase):
    """Worker that always raises in execute()."""

    def execute(self, process_vars: dict) -> dict:
        raise RuntimeError("simulated failure")


# ---------------------------------------------------------------------------
# WorkerBase tests
# ---------------------------------------------------------------------------


def test_worker_base_topic_default() -> None:
    """WorkerBase.topic defaults to the class name in snake_case."""
    worker = StubWorker()
    assert worker.topic == "stub_worker"


def test_worker_base_topic_explicit() -> None:
    """WorkerBase accepts an explicit topic via constructor."""

    class ExplicitWorker(WorkerBase):
        async def execute(self, process_vars: dict) -> dict:
            return {}

    worker = ExplicitWorker(topic="operadora.workers.custom")
    assert worker.topic == "operadora.workers.custom"


def test_worker_base_max_retries_default() -> None:
    """WorkerBase.max_retries defaults to 3."""
    worker = StubWorker()
    assert worker.max_retries == 3


def test_worker_base_logger() -> None:
    """WorkerBase must have a structlog logger."""
    worker = StubWorker()
    assert hasattr(worker, "logger")
    assert worker.logger is not None


def test_worker_base_execute_stub() -> None:
    """A simple worker returns the dict from execute()."""
    worker = StubWorker()
    result = worker.execute_sync({"tenant_id": "amh"})
    assert result == {"status": "ok"}


def test_worker_base_run_delegates_to_execute() -> None:
    """WorkerBase.run() calls execute() and returns result."""
    worker = StubWorker()
    result = worker.run({"tenant_id": "amh"})
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_worker_base_run_async() -> None:
    """WorkerBase.run_async() calls the async execute()."""
    worker = StubWorker()
    result = await worker.run_async({"tenant_id": "amh"})
    assert result == {"status": "ok"}


def test_worker_base_run_retries_on_failure() -> None:
    """WorkerBase.run() retries up to max_retries times on failure."""
    worker = FailingWorker(max_retries=2)
    with pytest.raises(RuntimeError, match="simulated failure"):
        worker.run({"tenant_id": "amh"})


def test_worker_base_has_retry_backoff_attribute() -> None:
    """WorkerBase must have a retry_backoff attribute (seconds)."""
    worker = StubWorker()
    assert hasattr(worker, "retry_backoff")
    assert isinstance(worker.retry_backoff, (int, float))


# ---------------------------------------------------------------------------
# ERR_*_NOT_HUMAN guard constants
# ---------------------------------------------------------------------------


def test_err_denial_not_human_present() -> None:
    """ERR_DENIAL_NOT_HUMAN constant must exist and be a string."""
    assert isinstance(ERR_DENIAL_NOT_HUMAN, str)
    assert "NOT_HUMAN" in ERR_DENIAL_NOT_HUMAN


def test_err_escalation_not_human_present() -> None:
    """ERR_ESCALATION_NOT_HUMAN constant must exist and be a string."""
    assert isinstance(ERR_ESCALATION_NOT_HUMAN, str)
    assert "NOT_HUMAN" in ERR_ESCALATION_NOT_HUMAN


def test_err_fraud_accusation_not_human_present() -> None:
    """ERR_FRAUD_ACCUSATION_NOT_HUMAN constant must exist and be a string."""
    assert isinstance(ERR_FRAUD_ACCUSATION_NOT_HUMAN, str)
    assert "NOT_HUMAN" in ERR_FRAUD_ACCUSATION_NOT_HUMAN


# ---------------------------------------------------------------------------
# WorkerRegistry tests
# ---------------------------------------------------------------------------


def test_worker_registry_register_and_get() -> None:
    """WorkerRegistry.register() and get() must round-trip correctly."""
    registry = WorkerRegistry()
    worker = StubWorker()

    registry.register("operadora.workers.stub", worker)

    assert registry.get("operadora.workers.stub") is worker


def test_worker_registry_get_missing_returns_none() -> None:
    """WorkerRegistry.get() returns None for unregistered topics."""
    registry = WorkerRegistry()
    assert registry.get("nonexistent.topic") is None


def test_worker_registry_list_topics() -> None:
    """WorkerRegistry.list_topics() returns all registered topic names."""
    registry = WorkerRegistry()
    worker_a = StubWorker()
    worker_b = StubWorker()

    registry.register("operadora.escalation.notify_team", worker_a)
    registry.register("operadora.auth.analyze_request", worker_b)

    topics = registry.list_topics()
    assert "operadora.escalation.notify_team" in topics
    assert "operadora.auth.analyze_request" in topics
    assert len(topics) == 2


def test_worker_registry_register_duplicate_replaces() -> None:
    """Registering the same topic twice replaces the previous worker."""
    registry = WorkerRegistry()
    worker_a = StubWorker()
    worker_b = StubWorker()

    registry.register("operadora.workers.test", worker_a)
    registry.register("operadora.workers.test", worker_b)

    assert registry.get("operadora.workers.test") is worker_b


def test_worker_registry_count() -> None:
    """WorkerRegistry.count() returns the number of registered workers."""
    registry = WorkerRegistry()

    assert registry.count() == 0

    registry.register("topic.a", StubWorker())
    registry.register("topic.b", StubWorker())

    assert registry.count() == 2


def test_worker_registry_clear() -> None:
    """WorkerRegistry.clear() removes all registered workers."""
    registry = WorkerRegistry()
    registry.register("topic.a", StubWorker())
    registry.register("topic.b", StubWorker())

    registry.clear()

    assert registry.count() == 0
    assert registry.list_topics() == []


# ---------------------------------------------------------------------------
# Default singleton registry
# ---------------------------------------------------------------------------


def test_worker_registry_singleton_exists() -> None:
    """There must be a module-level singleton WorkerRegistry."""
    from maezo.tools.workers.base import worker_registry

    assert isinstance(worker_registry, WorkerRegistry)
