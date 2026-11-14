"""Unit tests for maezo.platform.erasure — Erasure Manager (ADR-0002), postura PADRAO (flag OFF).

FAIL-CLOSED honesty (T3.4-F3): the audited defect was that `erase()`/`verify()` reported
`status="completed"`/`"clean"` while executing ZERO SQL. R-013 (F-008) replaced the always-raise
stub with the DARK cascade orchestrator (`tests/unit/platform/test_erasure_cascade.py` covers the
flag-ON chain). This file pins the DEFAULT posture of every deployment — `MAEZO_ERASURE_CASCADE`
unset — which must stay a loud refusal with zero effect.

CONSCIOUS-FLIP GUARD (rewritten in R-013, as the original guard demanded): the old
`pytest.raises(ErasureNotImplementedError)` assertions were REPLACED, not weakened — the flag-OFF
refusal is `ErasureCascadeDisabledError`, which still IS-A `ErasureNotImplementedError` (and a
`NotImplementedError`), so every `except` written against the old refusal still treats the dark
state as a refusal, never as success.
"""

from __future__ import annotations

import pytest

from maezo.platform.erasure import (
    CASCADE_FLAG_ENV,
    ErasureCascadeDisabledError,
    ErasureManager,
    ErasureNotImplementedError,
    ErasureResult,
)


@pytest.fixture(autouse=True)
def _flag_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CASCADE_FLAG_ENV, raising=False)


class _ExplodingExecutor:
    """Executor that records any call — flag OFF must never reach it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def apply(self, tenant_id: str, fhir_patient_id: str, decisao_dpo: str) -> int:
        self.calls.append("apply")
        return 0

    def remaining(self, tenant_id: str, fhir_patient_id: str, decisao_dpo: str) -> int:
        self.calls.append("remaining")
        return 0


def test_erase_fails_closed_when_cascade_flag_off() -> None:
    """Flag OFF (default): erase() refuses before ANY effect — no executor is ever touched."""
    executor = _ExplodingExecutor()
    manager = ErasureManager(executors={"agent_memory": executor})

    with pytest.raises(ErasureCascadeDisabledError) as exc:
        manager.erase(tenant_id="amh", fhir_patient_id="patient-001", titular_pseudo_id="ps-1")

    assert exc.value.reason == "cascade_disabled"
    assert CASCADE_FLAG_ENV in str(exc.value)
    assert executor.calls == [], (
        "FAIL-CLOSED GUARD: with MAEZO_ERASURE_CASCADE off, ErasureManager.erase must touch "
        f"ZERO executors. Saw: {executor.calls!r}"
    )


def test_erase_never_returns_a_result_with_flag_off() -> None:
    """There is NO input for which erase() returns an ErasureResult while the flag is off."""
    manager = ErasureManager()
    with pytest.raises(ErasureNotImplementedError):
        manager.erase("amh", "patient-002")


def test_verify_fails_closed_when_cascade_flag_off() -> None:
    """verify() MUST refuse with the flag off — never certify a layer as 'clean'."""
    executor = _ExplodingExecutor()
    manager = ErasureManager(executors={"agent_memory": executor})

    with pytest.raises(ErasureCascadeDisabledError):
        manager.verify("amh", "patient-003")
    assert executor.calls == []


def test_disabled_refusal_keeps_the_not_implemented_ancestry() -> None:
    """Compat: the dark refusal IS-A ErasureNotImplementedError IS-A NotImplementedError."""
    assert issubclass(ErasureCascadeDisabledError, ErasureNotImplementedError)
    assert issubclass(ErasureNotImplementedError, NotImplementedError)


@pytest.mark.parametrize("raw", ["1", "yes", "on", "", " ", "TRUE1", "enabled"])
def test_ambiguous_flag_spellings_never_enable(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """Only the literal `true` enables — a typo cannot half-enable an LGPD erasure."""
    monkeypatch.setenv(CASCADE_FLAG_ENV, raw)
    with pytest.raises(ErasureCascadeDisabledError):
        ErasureManager().erase("amh", "patient-004", titular_pseudo_id="ps-4")


def test_enabled_override_only_accepts_the_boolean_true() -> None:
    """`enabled=` truthy garbage never enables."""
    for garbage in ("true", 1, [1]):
        with pytest.raises(ErasureCascadeDisabledError):
            ErasureManager(enabled=garbage).erase("amh", "patient-005", titular_pseudo_id="ps-5")  # type: ignore[arg-type]


def test_result_repr_never_carries_the_patient_id() -> None:
    """ADR-0006: the patient id is excluded from the result's repr."""
    result = ErasureResult(fhir_patient_id="pat-sentinel-repr-0001", tenant_id="amh")
    assert "pat-sentinel-repr-0001" not in repr(result)


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
