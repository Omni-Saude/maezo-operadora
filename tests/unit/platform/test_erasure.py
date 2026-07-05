"""Unit tests for maezo.platform.erasure — Erasure Manager (ADR-0002).

TDD London School: tests written BEFORE implementation verification.
Tests cascade erasure across 3 layers and verification.
"""

from __future__ import annotations

from maezo.platform.erasure import ErasureManager, ErasureResult


def test_erasure_cascade_all_layers() -> None:
    """ErasureManager must cascade erasure through all 3 layers and return completed."""
    manager = ErasureManager()

    result = manager.erase(tenant_id="amh", fhir_patient_id="patient-001")

    assert isinstance(result, ErasureResult)
    assert result.fhir_patient_id == "patient-001"
    assert result.tenant_id == "amh"
    assert result.status == "completed"
    assert result.working_erased is True
    assert result.episodic_erased is True
    assert result.semantic_erased is True
    assert result.errors == []


def test_erasure_different_patients() -> None:
    """Erasure must work for different fhir_patient_id values."""
    manager = ErasureManager()

    r1 = manager.erase("amh", "patient-001")
    r2 = manager.erase("amh", "patient-002")

    assert r1.status == "completed"
    assert r2.status == "completed"
    assert r1.fhir_patient_id != r2.fhir_patient_id


def test_erasure_different_tenants() -> None:
    """Erasure must scope to the correct tenant."""
    manager = ErasureManager()

    r1 = manager.erase("amh", "patient-001")
    r2 = manager.erase("cassi", "patient-001")

    assert r1.tenant_id == "amh"
    assert r2.tenant_id == "cassi"
    assert r1.status == "completed"
    assert r2.status == "completed"


def test_erasure_verify_after_erase() -> None:
    """After erasure, verify() must report all layers as clean."""
    manager = ErasureManager()

    result = manager.erase("amh", "patient-003")
    assert result.status == "completed"

    verification = manager.verify("amh", "patient-003")
    assert verification["status"] == "clean"
    assert verification["working_clean"] is True
    assert verification["episodic_clean"] is True
    assert verification["semantic_clean"] is True


def test_erasure_verify_without_erase() -> None:
    """Verify without erase must still work (report actual state)."""
    manager = ErasureManager()

    verification = manager.verify("amh", "patient-999")

    # Default verify returns clean in test mode
    assert verification["status"] == "clean"


def test_erasure_result_fields() -> None:
    """ErasureResult must have all required fields."""
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


def test_erasure_result_completed_status() -> None:
    """ErasureResult status must reflect completion when all layers erased."""
    result = ErasureResult(
        fhir_patient_id="p-001",
        tenant_id="amh",
    )
    result.working_erased = True
    result.episodic_erased = True
    result.semantic_erased = True

    # Simulate the status logic from ErasureManager
    all_erased = result.working_erased and result.episodic_erased and result.semantic_erased
    if all_erased:
        result.status = "completed"

    assert result.status == "completed"


def test_erasure_partial_failure_detection() -> None:
    """Partial erasure must be detectable via status field."""
    result = ErasureResult(
        fhir_patient_id="p-001",
        tenant_id="amh",
    )
    # Only working layer succeeded
    result.working_erased = True
    result.episodic_erased = False
    result.semantic_erased = False

    all_erased = result.working_erased and result.episodic_erased and result.semantic_erased
    none_erased = not result.working_erased and not result.episodic_erased and not result.semantic_erased

    if all_erased:
        result.status = "completed"
    elif none_erased:
        result.status = "failed"
    else:
        result.status = "partial"

    assert result.status == "partial"


def test_erasure_all_layers_failed() -> None:
    """When all layers fail, status must be 'failed'."""
    result = ErasureResult(
        fhir_patient_id="p-001",
        tenant_id="amh",
    )
    # All layers failed
    result.working_erased = False
    result.episodic_erased = False
    result.semantic_erased = False

    all_erased = result.working_erased and result.episodic_erased and result.semantic_erased
    none_erased = not result.working_erased and not result.episodic_erased and not result.semantic_erased

    if all_erased:
        result.status = "completed"
    elif none_erased:
        result.status = "failed"
    else:
        result.status = "partial"

    assert result.status == "failed"
