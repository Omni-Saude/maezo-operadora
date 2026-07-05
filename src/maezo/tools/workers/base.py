"""WorkerBase and WorkerRegistry — foundation for BPMN external task workers.

Provides:
- WorkerBase: base class with structlog logger, error handling, retry
- WorkerRegistry: registration/retrieval by external task topic
- ERR_*_NOT_HUMAN: guard constants preventing automatic adverse actions

Per ADR-0008 (autonomy levels): L0 hard actions (negativa, fraude accusation)
must NEVER be performed automatically by workers. The ERR_*_NOT_HUMAN guards
enforce this at the code level.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import structlog

# ---------------------------------------------------------------------------
# Guard constants — ERR_*_NOT_HUMAN
# ---------------------------------------------------------------------------

ERR_DENIAL_NOT_HUMAN: str = "ERR_DENIAL_NOT_HUMAN"
"""Guard: automatic denials are FORBIDDEN. Only human auditors may deny."""

ERR_ESCALATION_NOT_HUMAN: str = "ERR_ESCALATION_NOT_HUMAN"
"""Guard: automatic escalation closing is FORBIDDEN. Only human supervisors may resolve."""

ERR_FRAUD_ACCUSATION_NOT_HUMAN: str = "ERR_FRAUD_ACCUSATION_NOT_HUMAN"
"""Guard: automatic fraud accusation is FORBIDDEN. Only human review may accuse."""


# ---------------------------------------------------------------------------
# WorkerBase
# ---------------------------------------------------------------------------


class WorkerBase(ABC):
    """Base class for all BPMN external task workers.

    Features:
    - structlog logger (per ADR-0007 audit requirements)
    - Retry with backoff on failure
    - Synchronous and async execution paths
    - Topic-based routing in WorkerRegistry

    Subclasses must implement execute() and may override topic, max_retries,
    and retry_backoff.

    WorkerBase.execute() is synchronous by design (London School TDD):
    workers are pure functions that transform process_vars -> result dict.
    Async I/O is handled by the engine/message layer, not by the worker logic.
    """

    # Default topic derived from class name (snake_case)
    _DEFAULT_TOPIC: ClassVar[str | None] = None  # overridden in __init_subclass__

    def __init__(
        self,
        topic: str | None = None,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
    ) -> None:
        """Initialize the worker.

        Args:
            topic: External task topic for this worker. Defaults to
                   class name in snake_case (e.g. NotifyTeamWorker -> "notify_team_worker").
            max_retries: Maximum retry attempts on failure (default: 3).
            retry_backoff: Backoff multiplier in seconds between retries (default: 1.0).
        """
        self._topic = topic or self._derive_topic()
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.logger = structlog.get_logger(f"maezo.workers.{self._topic}")

    @property
    def topic(self) -> str:
        """External task topic this worker consumes."""
        return self._topic

    def _derive_topic(self) -> str:
        """Derive a default topic from the class name (CamelCase -> snake_case)."""
        import re

        name = type(self).__name__
        # Insert underscore before uppercase letters, convert to lowercase
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    @abstractmethod
    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Execute the worker's business logic.

        Args:
            process_vars: Variables from the BPMN process instance.

        Returns:
            Result dictionary to be merged back into process variables.

        This is the ONLY method subclasses need to implement.
        """
        ...

    def run(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Execute the worker with retry logic.

        Args:
            process_vars: Variables from the BPMN process instance.

        Returns:
            Result dictionary.

        Raises:
            Exception: If all retry attempts fail.

        M11: Emits worker_execution_time_seconds on success and
        worker_error_count_total on failure via the observability layer.
        """
        import time as time_module

        last_error: Exception | None = None
        start_time = time_module.monotonic()

        for attempt in range(1, self.max_retries + 1):
            try:
                self.logger.debug(
                    "worker_executing",
                    topic=self._topic,
                    attempt=attempt,
                )
                result = self.execute(process_vars)
                self.logger.debug(
                    "worker_completed",
                    topic=self._topic,
                    attempt=attempt,
                )

                # M11: Record successful execution time
                duration = time_module.monotonic() - start_time
                try:
                    from maezo.platform.observability import record_worker_execution

                    record_worker_execution(
                        worker_name=type(self).__name__,
                        topic=self._topic,
                        duration_seconds=duration,
                    )
                except Exception:
                    pass  # Metrics are best-effort; never break worker execution

                return result
            except Exception as e:
                last_error = e
                self.logger.warning(
                    "worker_failed",
                    topic=self._topic,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    error=str(e),
                )

                # M11: Record error count on each failure
                try:
                    from maezo.platform.observability import record_worker_error

                    record_worker_error(
                        worker_name=type(self).__name__,
                        topic=self._topic,
                        error_type=type(e).__name__,
                    )
                except Exception:
                    pass  # Metrics are best-effort

                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff * attempt)

        self.logger.error(
            "worker_exhausted_retries",
            topic=self._topic,
            max_retries=self.max_retries,
        )
        raise last_error  # type: ignore[misc]

    def execute_sync(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Synchronous wrapper around execute() — alias for run().

        Provided for clarity when using workers synchronously in tests.
        """
        return self.run(process_vars)

    async def run_async(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        """Async wrapper around run().

        Currently delegates to the synchronous run() method. Override in
        subclasses that need true async I/O.

        Args:
            process_vars: Variables from the BPMN process instance.

        Returns:
            Result dictionary.
        """
        return self.run(process_vars)


# ---------------------------------------------------------------------------
# WorkerRegistry
# ---------------------------------------------------------------------------


class WorkerRegistry:
    """Registry mapping external task topics to WorkerBase instances.

    The Camunda/CIB Seven external task client uses this registry to
    dispatch tasks to the correct worker.

    Usage:
        registry = WorkerRegistry()
        registry.register("operadora.escalation.notify_team", NotifyTeamWorker())
        worker = registry.get("operadora.escalation.notify_team")
    """

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._workers: dict[str, WorkerBase] = {}
        self._logger = structlog.get_logger("maezo.workers.registry")

    def register(self, topic: str, worker: WorkerBase) -> None:
        """Register a worker for a topic.

        Args:
            topic: The external task topic (e.g. 'operadora.escalation.notify_team').
            worker: The WorkerBase instance that handles this topic.

        If a worker is already registered for this topic, it is replaced
        (with a warning log).
        """
        if topic in self._workers:
            self._logger.warning(
                "worker_registry_replace",
                topic=topic,
                previous=type(self._workers[topic]).__name__,
                new=type(worker).__name__,
            )
        self._workers[topic] = worker
        self._logger.debug(
            "worker_registered",
            topic=topic,
            worker_type=type(worker).__name__,
        )

    def get(self, topic: str) -> WorkerBase | None:
        """Retrieve a worker by topic.

        Args:
            topic: The external task topic.

        Returns:
            The WorkerBase instance, or None if not registered.
        """
        return self._workers.get(topic)

    def list_topics(self) -> list[str]:
        """Return all registered topic names."""
        return list(self._workers.keys())

    def count(self) -> int:
        """Return the number of registered workers."""
        return len(self._workers)

    def clear(self) -> None:
        """Remove all registered workers (useful for testing)."""
        self._workers.clear()
        self._logger.debug("worker_registry_cleared")


# Singleton instance for the default registry
worker_registry = WorkerRegistry()
