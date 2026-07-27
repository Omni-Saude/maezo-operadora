"""WorkerBase and WorkerRegistry — foundation for BPMN external task workers.

Provides:
- WorkerBase: base class with structlog logger, error handling, retry
- WorkerRegistry: registration/retrieval by external task topic
- FunctionWorker: adapter wrapping dict-first/typed-I/O entry functions (T1.2/ADR-0026)
- pick_fields: fail-closed explicit field selection for dict->dataclass marshalling
- ERR_*_NOT_HUMAN: guard constants preventing automatic adverse actions

Per ADR-0008 (autonomy levels): L0 hard actions (negativa, fraude accusation)
must NEVER be performed automatically by workers. The ERR_*_NOT_HUMAN guards
enforce this at the code level.
"""

from __future__ import annotations

import dataclasses
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any, ClassVar

import structlog

# ---------------------------------------------------------------------------
# Guard constants — ERR_*_NOT_HUMAN
# ---------------------------------------------------------------------------

ERR_DENIAL_NOT_HUMAN: str = "ERR_DENIAL_NOT_HUMAN"
"""Guard: automatic denials are FORBIDDEN. Only human auditors may deny."""

ERR_AUTH_DENIAL_INCOMPLETE: str = "ERR_AUTH_DENIAL_INCOMPLETE"
"""Guard: a formal denial notice with INCOMPLETE fundamentacao must never be transmitted.

Modeled in SP-OP-AUTH-001 (`bpmn:error@errorCode="ERR_AUTH_DENIAL_INCOMPLETE"`,
`Error_AuthDenialIncompleta`) with a matching boundary event `BE_NegativaIncompleta` on
`ST_EnviarNegativaFormal` routing to the neutral terminal `End_FundamentacaoIncompletaBloqueada`.
Raised (as `WorkerBpmnError`) by `SendDenialNoticeWorker` when a NEGAR decision reaches the worker
without every ANS-required grounding field (justificativa_clinica / cid10_referencia /
fundamentacao_dut). Because it is spec-modeled, it is registered in the auth harness's
`bpmn_error_allowlist` (`AUTH_BPMN_ERROR_ALLOWLIST`) so the harness dispatches it as a real
`bpmnError` instead of demoting it to a fail-closed incident (harness.py `_bpmn_error_allowlist`)."""

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
# FunctionWorker — adapter wrapping dict-first / typed-I/O entry functions
# ---------------------------------------------------------------------------

#: Exception types the harness (`tools/workers/harness.py:_handle`) already classifies
#: correctly on its own: `PermissionError` family -> `failure(retries=0)` (L0 guard, never
#: retried); `ValueError` family -> `failure(retries=0)` (bad/immutable input); the transient
#: infra family -> engine-side computed retry. `FunctionWorker.execute` never intercepts these.
_HARNESS_CLASSIFIED: tuple[type[Exception], ...] = (
    PermissionError,
    ValueError,
    RuntimeError,
    OSError,
    TimeoutError,
    ConnectionError,
)


class FunctionWorker(WorkerBase):
    """Adapter wrapping a dict-first callable `fn(variables: dict) -> dict` as a `WorkerBase`.

    ADR-0026 Decisao §1/§2. `fn` is either:
    - one of the 42 dict-first module functions (`fn(variables) -> dict`), wrapped directly, or
    - a typed-I/O module's dict-boundary **entry function** (ADR-0026 §2b) — explicit field
      selection -> typed dataclass -> the UNCHANGED typed function -> `dataclasses.asdict`.

    `max_retries` defaults to **1** (execute once) — per T1.1 design §9 the ENGINE owns durable
    retry; `WorkerBase`'s in-process retry stays opt-in per worker for provably-idempotent
    transient faults only (pass `max_retries>1` explicitly to opt in).

    Error reclassification (ADR-0026 Decisao §5 — the `classify_worker_error` provision,
    implemented here rather than in the harness so no harness/dispatch code changes): six of the
    16 modules (adequacao/credenciamento/fraude/inadimplencia/pagto/programa) raise a bespoke
    ``Exception`` subclass with a ``.code``/``.message`` pair for their GATED (human-decision or
    custody-integrity) failures — e.g. ``AdequacaoError(ERR_FALLBACK_COMMITMENT_NOT_HUMAN, ...)``
    — rather than subclassing ``PermissionError``/``ValueError`` (ADR-0026 census, "Heterogeneous
    error classes"). Left alone, the harness's generic `except Exception` branch would treat
    these as *unclassified* and apply the **transient, engine-retried** outcome — retrying an L0
    guard could drive an adverse action (ADR-0008), so this is a fail-closed defect on the
    registry path. `execute()` therefore re-raises any such "coded" exception (duck-typed: not
    already one of `_HARNESS_CLASSIFIED`, but exposing string `.code` and `.message` attributes)
    as a `ValueError`, which the harness's EXISTING classification already routes to
    `failure(retries=0)` — an engine-guaranteed, human-visible incident, never retried. The
    module's own exception type/tests are untouched (this only affects the FunctionWorker/harness
    dispatch path); the metrics `error_type` label reports `ValueError` for these, a deliberate,
    documented trade-off for correctness.
    """

    def __init__(
        self,
        topic: str,
        fn: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        max_retries: int = 1,
        retry_backoff: float = 1.0,
    ) -> None:
        super().__init__(topic=topic, max_retries=max_retries, retry_backoff=retry_backoff)
        self._fn = fn

    def execute(self, process_vars: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._fn(process_vars)
        except _HARNESS_CLASSIFIED:
            raise
        except Exception as exc:  # noqa: BLE001 — reclassified below, see class docstring.
            code = getattr(exc, "code", None)
            message = getattr(exc, "message", None)
            if isinstance(code, str) and isinstance(message, str):
                raise ValueError(f"{code}: {message}") from exc
            raise


def non_blank(value: Any) -> bool:
    """True iff `value` is a present, non-blank business-key anchor field (fail-closed).

    Single source of truth for anchor-field validation, shared by the NotificationBridge rule
    predicates (`platform/notification_bridge.py`) and the fenced-start handoff workers
    (`contas.start_recurso`, `fraude.start_credenciamento`/`start_contratual`) — EB-4 R1 finding:
    the workers' plain `bool(str(variables.get(k, "")))` truthiness let WHITESPACE-ONLY and
    EXPLICIT-`None` anchors slip through (`str(None) == "None"` is truthy; `"   "` is truthy),
    producing degenerate business keys like `CRED-{tenant}-None` / `RECURSO-{t}-G-   ` instead of
    the intended refusal.

    Fail-closed semantics: `None`, absent, empty, and whitespace-only are all rejected the same
    way; any other value round-trips through `str(...).strip()` and passes iff non-empty.
    """
    if value is None:
        return False
    return bool(str(value).strip())


def resolve_fraude_numero_caso(variables: dict[str, Any]) -> str:
    """Resolve the FRAUDE-001 `numero_caso` business-key anchor for a CONTAS→FRAUDE handoff
    (contract `SP-OP-FRAUDE-001.md` "Business key (idempotencia)"): use an already-assigned
    `numero_caso` when present, else fall back to `prestador_id` (the entity under
    investigation) — so repeated referrals of the SAME prestador converge on the SAME
    FRAUDE-001 instance instead of minting a fresh, non-deterministic case id on every forward.

    Single source of truth — shared by BOTH derivation sites, so they are byte-identical for
    EVERY input type (not just the string case):
    - `contas.start_fraude`'s in-flow worker (`contas._fraude_numero_caso_for_handoff`);
    - `notification_bridge`'s CONTAS→FRAUDE Kafka-mirror rule
      (`notification_bridge._fraude_numero_caso_for_contas_handoff`).

    Before this extraction the two sites re-implemented the SAME derivation independently and
    had drifted: the worker used the shared `non_blank` (accepts any type whose stringified form
    is non-blank, e.g. an int/float/bool `numero_caso`) while the bridge required
    `isinstance(numero_caso, str)` — so a non-string `numero_caso` would derive a DIFFERENT
    business key on each path (worker: `str(numero_caso)`; bridge: the `prestador_id` fallback),
    a latent divergent-double-start hazard. `numero_caso` is not a CONTAS process variable today
    (never reachable), but this single implementation makes that class of drift structurally
    impossible rather than relying on two docstrings staying in sync.

    Uses the SAME `non_blank` anchor-validation semantics as every other business-key field in
    this repo (fail-closed on `None`/blank/whitespace-only, via the stringified value) — an
    already-assigned `numero_caso` of ANY type is accepted and stringified; only a blank/None
    value falls back to `prestador_id`.
    """
    numero_caso = variables.get("numero_caso")
    if non_blank(numero_caso):
        return str(numero_caso)
    return str(variables.get("prestador_id", ""))


def pick_fields(variables: dict[str, Any], cls: type) -> dict[str, Any]:
    """Explicit field selection: keep only the keys `cls` (a dataclass) actually declares.

    ADR-0026 §2b marshalling rule for the six typed-I/O modules — inbound `variables` (the flat
    BPMN process-variable dict, which also carries fields unrelated to this dataclass) maps to a
    typed input dataclass by EXPLICIT selection, never `**variables` (which would raise
    `TypeError: unexpected keyword argument` the moment an unrelated process variable is present,
    or silently accept extras a naive constructor tolerates). Missing keys are simply omitted —
    the dataclass's own defaults (or absence thereof) decide whether that is fail-closed; a
    dataclass field with NO default left unset raises `TypeError` at construction, which entry
    functions must translate into the module's own `*Invalido*Error` (ADR-0026 §2b: "a
    missing/invalid required field raises the module's own *Invalido*Error").
    """
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in variables.items() if k in names}


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
