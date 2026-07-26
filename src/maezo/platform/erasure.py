"""Erasure Manager — cascading data deletion by fhir_patient_id (ADR-0002).

Implements LGPD right to erasure (art. 18, VI) with a three-layer cascade:
1. Working layer: LangGraph checkpointer (PostgreSQL, schema `agents`)
2. Episodic layer: Transcripts/decisions/events partition by fhir_patient_id
3. Semantic layer: pgvector embeddings

The cascade is FAIL-SAFE: if any layer fails, the entire operation is rolled
back (or at minimum reported as incomplete). Verification runs after erasure
to confirm all three layers are clean.

FAIL-CLOSED (T3.4-F3): the per-layer deletion SQL is NOT YET IMPLEMENTED — it is
gated on the DPO legal-bases/retention matrix (human-decided) AND the checkpoint-
schema reconciliation (T3.4-F4). Until real deletion lands, ``erase()`` and
``verify()`` RAISE ``ErasureNotImplementedError`` rather than reporting a
``"completed"``/``"clean"`` outcome that no data movement backs. Reporting success
for an erasure that executed zero SQL would be a silent LGPD art. 18, VI violation
(the data subject is told their data is gone while it remains) — so the honest,
fail-closed behavior is to refuse loudly and force an incident.

Usage:
    manager = ErasureManager(langgraph_session, episodic_session, semantic_session)
    result = manager.erase("tenant-amh", "patient-123")  # raises ErasureNotImplementedError today
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ErasureNotImplementedError(NotImplementedError):
    """Fail-closed sentinel: real cascading deletion is not yet implemented (T3.4-F3).

    Raised by ``ErasureManager.erase()``/``verify()`` so NO caller can mistake the
    ABSENCE of a deletion effect for a completed erasure. The deletion/verification SQL
    is deliberately unwritten — it is gated on the DPO legal-bases/retention matrix
    (human-decided) and the checkpoint-schema reconciliation (T3.4-F4). A future
    implementor flipping this to real deletion MUST remove these raises and, in doing so,
    consciously update every test that currently asserts this fail-closed refusal.

    Subclasses ``NotImplementedError`` (a ``RuntimeError``) so a bare ``except Exception``
    still catches it, but the precise type lets callers (e.g. the LGPD erasure worker)
    convert it into an engine incident that is NEVER retried and NEVER reported as success.
    """


@dataclass
class ErasureResult:
    """Result of an erasure operation across the three layers.

    Attributes:
        fhir_patient_id: The patient whose data was erased.
        tenant_id: The tenant scope.
        working_erased: Whether working layer (LangGraph checkpoint) was erased.
        episodic_erased: Whether episodic layer (transcripts/decisions) was erased.
        semantic_erased: Whether semantic layer (pgvector embeddings) was erased.
        status: 'completed' (all layers), 'partial' (some layers), 'failed' (none).
        errors: List of error messages from any failed layers.
        timestamp: When the erasure was executed.
    """

    fhir_patient_id: str
    tenant_id: str
    working_erased: bool = False
    episodic_erased: bool = False
    semantic_erased: bool = False
    status: str = "pending"
    errors: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class ErasureManager:
    """Manages cascading data erasure across three storage layers.

    ADR-0002 mandates that erasure by fhir_patient_id must cascade through:
    1. Working (LangGraph checkpointer — PostgreSQL `agents` schema)
    2. Episodic (transcripts, decisions, events partitioned by fhir_patient_id)
    3. Semantic (pgvector embeddings referencing FHIR resources)

    The manager is FAIL-SAFE: partial erasure is detected and reported.
    Verification confirms all layers are clean before returning 'completed'.

    Typical usage:
        manager = ErasureManager(working_db, episodic_db, semantic_db)
        result = manager.erase("amh", "patient-456")
        if result.status != "completed":
            # Alert human operator — partial erasure detected
            ...
    """

    def __init__(
        self,
        working_db: Any = None,
        episodic_db: Any = None,
        semantic_db: Any = None,
    ) -> None:
        """Initialize the erasure manager with optional DB connections.

        In tests and development, connections may be None (workers simulated).
        In production, these are SQLAlchemy async sessions or connection pools.

        Args:
            working_db: Connection to the LangGraph checkpointer database.
            episodic_db: Connection to the episodic (transcripts/events) database.
            semantic_db: Connection to the pgvector semantic database.
        """
        self._working = working_db
        self._episodic = episodic_db
        self._semantic = semantic_db
        logger.info("erasure_manager_initialized")

    def erase(self, tenant_id: str, fhir_patient_id: str) -> ErasureResult:
        """Execute cascading erasure for a given fhir_patient_id.

        Erases data from all three layers. Returns an ErasureResult with
        per-layer status and any errors encountered.

        Args:
            tenant_id: The tenant scope (e.g. 'amh').
            fhir_patient_id: The FHIR patient ID whose data should be erased.

        Returns:
            ErasureResult with status and per-layer results.

        Raises:
            ErasureNotImplementedError: ALWAYS, today. The per-layer deletion SQL is not
                yet implemented (see module docstring / class ErasureNotImplementedError).
                This raise is fail-closed: it is the ONLY honest outcome while zero data
                movement is possible, and it precedes the status machine below so no code
                path can ever return ``status="completed"`` without a real effect.
        """
        # FAIL-CLOSED (T3.4-F3): refuse before touching the (unimplemented) layer helpers.
        # The `_erase_*`/`_verify_*` helpers below are intentionally unreachable until the
        # deletion SQL is written (gated on the DPO retention matrix + T3.4-F4 checkpoint-
        # schema reconciliation). Removing this raise WITHOUT implementing real deletion
        # would re-introduce the audited "false success" defect.
        logger.error(
            "erasure_refused_not_implemented",
            tenant_id=tenant_id,
            fhir_patient_id=fhir_patient_id,
            layer="all",
        )
        raise ErasureNotImplementedError(
            "ErasureManager.erase: cascading deletion is NOT IMPLEMENTED — refusing to "
            "report success for an erasure that executes zero SQL (LGPD art. 18, VI). "
            "Gated on the DPO legal-bases/retention matrix + T3.4-F4 checkpoint-schema "
            "reconciliation. Implement real per-layer deletion before removing this guard."
        )

    def verify(self, tenant_id: str, fhir_patient_id: str) -> dict[str, Any]:
        """Verify that all three layers are clean for a given fhir_patient_id.

        Returns a dict with per-layer verification results. This is run after
        erasure to confirm data was actually removed.

        Args:
            tenant_id: The tenant scope.
            fhir_patient_id: The FHIR patient ID to verify.

        Returns:
            Dict with keys: working_clean, episodic_clean, semantic_clean, status.

        Raises:
            ErasureNotImplementedError: ALWAYS, today. The per-layer verification SELECTs are
                not yet implemented, so ``verify()`` cannot honestly report ``"clean"``.
                Returning ``"clean"`` from a verify that executed zero SQL would falsely
                certify that erased data is gone — the fail-closed refusal is the only
                truthful outcome until real verification queries are written.
        """
        # FAIL-CLOSED (T3.4-F3): the `_verify_*` helpers below are intentionally unreachable
        # until the verification SELECTs are written. Never return "clean" without a real check.
        logger.error(
            "erasure_verify_refused_not_implemented",
            tenant_id=tenant_id,
            fhir_patient_id=fhir_patient_id,
            layer="all",
        )
        raise ErasureNotImplementedError(
            "ErasureManager.verify: erasure verification is NOT IMPLEMENTED — refusing to "
            "certify a layer as 'clean' without executing a real SELECT. Implement per-layer "
            "verification before removing this guard (T3.4-F3)."
        )

    def _erase_working(self, tenant_id: str, fhir_patient_id: str) -> None:
        """Erase working layer (LangGraph checkpoint) data.

        In production, this executes:
            DELETE FROM agents.checkpoints WHERE fhir_patient_id = :pid
            DELETE FROM agents.checkpoint_writes WHERE fhir_patient_id = :pid

        Args:
            tenant_id: The tenant scope.
            fhir_patient_id: The FHIR patient ID.

        Raises:
            Exception: If the deletion fails.
        """
        # In tests: always succeeds (DB is an interface, not a real connection here)
        if self._working is not None:
            # Production path: execute SQL delete
            pass

    def _erase_episodic(self, tenant_id: str, fhir_patient_id: str) -> None:
        """Erase episodic layer (transcripts, decisions, events).

        In production, this executes:
            DELETE FROM episodes.transcripts WHERE fhir_patient_id = :pid
            DELETE FROM episodes.decisions WHERE fhir_patient_id = :pid

        Args:
            tenant_id: The tenant scope.
            fhir_patient_id: The FHIR patient ID.

        Raises:
            Exception: If the deletion fails.
        """
        if self._episodic is not None:
            pass

    def _erase_semantic(self, tenant_id: str, fhir_patient_id: str) -> None:
        """Erase semantic layer (pgvector embeddings).

        In production, this executes:
            DELETE FROM semantic.embeddings
            WHERE fhir_patient_id = :pid OR source_ref LIKE '%' || :pid || '%'

        Args:
            tenant_id: The tenant scope.
            fhir_patient_id: The FHIR patient ID.

        Raises:
            Exception: If the deletion fails.
        """
        if self._semantic is not None:
            pass

    def _verify_working(self, tenant_id: str, fhir_patient_id: str) -> bool:
        """Verify working layer is clean.

        In production, this executes:
            SELECT COUNT(*) FROM agents.checkpoints WHERE fhir_patient_id = :pid

        Returns True if no rows remain.

        Args:
            tenant_id: The tenant scope.
            fhir_patient_id: The FHIR patient ID.

        Returns:
            True if the layer is clean (no data remaining).
        """
        # In tests: always returns True (simulated clean verify)
        return True

    def _verify_episodic(self, tenant_id: str, fhir_patient_id: str) -> bool:
        """Verify episodic layer is clean.

        Returns True if no rows remain.

        Args:
            tenant_id: The tenant scope.
            fhir_patient_id: The FHIR patient ID.

        Returns:
            True if the layer is clean.
        """
        return True

    def _verify_semantic(self, tenant_id: str, fhir_patient_id: str) -> bool:
        """Verify semantic layer is clean.

        Returns True if no embeddings remain.

        Args:
            tenant_id: The tenant scope.
            fhir_patient_id: The FHIR patient ID.

        Returns:
            True if the layer is clean.
        """
        return True
