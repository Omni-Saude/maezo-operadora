"""Unit tests for maezo.platform.erasure — Erasure Manager (ADR-0002).

FAIL-CLOSED honesty (T3.4-F3): the audited defect was that `erase()`/`verify()` reported
`status="completed"`/`"clean"` while executing ZERO SQL (proven with fake connections whose
executed-SQL log stayed empty). Real per-layer deletion is NOT buildable yet (gated on the DPO
legal-bases/retention matrix + the T3.4-F4 checkpoint-schema reconciliation). Until it lands,
`erase()` and `verify()` MUST refuse loudly — never fabricate success. These tests pin that
refusal.

CONSCIOUS-FLIP GUARD: a future implementor wiring real deletion MUST delete/replace the
`pytest.raises(ErasureNotImplementedError)` assertions below — they cannot silently start
passing. Do NOT weaken these to make a half-built implementation go green.
"""

from __future__ import annotations

import pytest

from maezo.platform.erasure import ErasureManager, ErasureNotImplementedError, ErasureResult


class _RecordingConn:
    """Fake DB connection recording every SQL statement it is asked to execute."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: object, *args: object, **kwargs: object) -> None:
        self.executed.append(str(sql))


def test_erase_fails_closed_not_implemented() -> None:
    """erase() MUST raise ErasureNotImplementedError — never report a completed erasure.

    Reproduces the auditor's probe: inject fake connections and prove ZERO SQL is executed
    AND no success value is returned.
    """
    working, episodic, semantic = _RecordingConn(), _RecordingConn(), _RecordingConn()
    manager = ErasureManager(working_db=working, episodic_db=episodic, semantic_db=semantic)

    with pytest.raises(ErasureNotImplementedError) as exc:
        manager.erase(tenant_id="amh", fhir_patient_id="patient-001")

    # LGPD art. 18, VI must be named in the refusal so on-call triage understands the stakes.
    assert "NOT IMPLEMENTED" in str(exc.value)
    assert "art. 18" in str(exc.value)
    executed = working.executed + episodic.executed + semantic.executed
    assert executed == [], (
        "FAIL-CLOSED GUARD (T3.4-F3): ErasureManager.erase must execute ZERO SQL while real "
        "deletion is unimplemented. If you implemented real per-layer deletion, this test must "
        f"be consciously rewritten — do not silently flip it to green. Saw SQL: {executed!r}"
    )


def test_erase_never_returns_completed_status() -> None:
    """There is NO input for which erase() returns a truthy ErasureResult today.

    Guards against a regression that re-adds a `return ErasureResult(status="completed")` path.
    """
    manager = ErasureManager()
    with pytest.raises(ErasureNotImplementedError):
        manager.erase("amh", "patient-002")


def test_verify_fails_closed_not_implemented() -> None:
    """verify() MUST raise — never certify a layer as 'clean' without a real SELECT."""
    manager = ErasureManager()

    with pytest.raises(ErasureNotImplementedError) as exc:
        manager.verify("amh", "patient-003")

    assert "NOT IMPLEMENTED" in str(exc.value)


def test_erasure_not_implemented_is_a_not_implemented_error() -> None:
    """The sentinel subclasses NotImplementedError so a bare `except Exception` still catches it,
    while the precise type lets the worker convert it into a non-retried incident."""
    assert issubclass(ErasureNotImplementedError, NotImplementedError)


# ---------------------------------------------------------------------------
# ErasureResult dataclass — a pure value object retained for the FUTURE real
# implementation (per-layer status accounting). These do NOT exercise the
# (fail-closed) manager; they pin the dataclass's own field/status semantics.
# ---------------------------------------------------------------------------


def test_erasure_result_fields() -> None:
    """ErasureResult must have all required fields with fail-safe defaults."""
    import datetime as dt

    result = ErasureResult(fhir_patient_id="p-001", tenant_id="amh")

    assert result.fhir_patient_id == "p-001"
    assert result.tenant_id == "amh"
    assert result.working_erased is False
    assert result.episodic_erased is False
    assert result.semantic_erased is False
    assert result.status == "pending"
    assert isinstance(result.errors, list)
    assert isinstance(result.timestamp, dt.datetime)


def test_erasure_result_holds_completed_status() -> None:
    """ErasureResult can represent a completed erasure (for the future real implementation)."""
    result = ErasureResult(fhir_patient_id="p-001", tenant_id="amh")
    result.working_erased = True
    result.episodic_erased = True
    result.semantic_erased = True
    result.status = "completed"

    assert result.status == "completed"


def test_erasure_result_holds_partial_status() -> None:
    """ErasureResult can represent a partial erasure outcome."""
    result = ErasureResult(fhir_patient_id="p-001", tenant_id="amh")
    result.working_erased = True
    result.status = "partial"

    assert result.status == "partial"
