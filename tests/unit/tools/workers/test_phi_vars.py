"""Unit tests for the worker PHI-egress redaction seam (`phi_vars.redact_phi_vars`).

One-way, class-token redaction at the engine/Kafka-facing edge (ADR-0006, GAP-XPHI-1). See
`src/maezo/tools/workers/phi_vars.py` for the invariant.
"""

from __future__ import annotations

import pytest

from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS, REDACTED_PHI, redact_phi_vars


def test_redacted_phi_is_a_class_token() -> None:
    """The sentinel is a fixed, non-empty class token — never a real value."""
    assert REDACTED_PHI == "[REDACTED_PHI]"


def test_phi_process_vars_covers_the_denial_grounding_fields() -> None:
    """The three ANS grounding fields are all PHI-named (so the denial worker's egress is covered)."""
    assert {"justificativa_clinica", "cid10_referencia", "fundamentacao_dut"} <= PHI_PROCESS_VARS


@pytest.mark.parametrize("phi_key", sorted(PHI_PROCESS_VARS))
def test_every_phi_named_value_is_redacted(phi_key: str) -> None:
    """Every PHI-named key with a non-empty value is replaced by the class token."""
    out = redact_phi_vars({phi_key: "conteudo clinico sensivel do paciente"})
    assert out[phi_key] == REDACTED_PHI


def test_non_phi_keys_pass_through_untouched() -> None:
    """Correlation identifiers a fact MUST carry are never touched."""
    payload = {
        "tenant_id": "amh",
        "numero_guia_tiss": "GUIA-TESTE-0001",
        "business_key": "AUTH-amh-GUIA-TESTE-0001",
        "status": "notice_sent",
    }
    assert redact_phi_vars(payload) == payload


def test_empty_phi_values_pass_through() -> None:
    """None / empty / whitespace-only PHI values have nothing to redact — passed through as-is."""
    out = redact_phi_vars({"justificativa_clinica": None, "cid10_referencia": "", "fundamentacao_dut": "   "})
    assert out["justificativa_clinica"] is None
    assert out["cid10_referencia"] == ""
    assert out["fundamentacao_dut"] == "   "


def test_returns_a_copy_not_a_mutation() -> None:
    """The input mapping is never mutated (backstop on the hot path)."""
    src = {"justificativa_clinica": "texto cru"}
    out = redact_phi_vars(src)
    assert src["justificativa_clinica"] == "texto cru"  # original untouched
    assert out["justificativa_clinica"] == REDACTED_PHI


def test_mixed_payload_redacts_only_phi() -> None:
    """A realistic denial notice: clinical fields redacted, structural fields intact."""
    out = redact_phi_vars(
        {
            "status": "notice_sent",
            "notice_type": "denial",
            "tenant_id": "amh",
            "numero_guia_tiss": "GUIA-0001",
            "justificativa_clinica": "Paciente com CID C50.9",
            "cid10_referencia": "C50.9",
            "fundamentacao_dut": "DUT item 3.1",
        }
    )
    assert out["status"] == "notice_sent"
    assert out["tenant_id"] == "amh"
    assert out["numero_guia_tiss"] == "GUIA-0001"
    assert out["justificativa_clinica"] == REDACTED_PHI
    assert out["cid10_referencia"] == REDACTED_PHI
    assert out["fundamentacao_dut"] == REDACTED_PHI
    # Adversarial: no fragment of the planted clinical text survives anywhere in the output.
    blob = " ".join(str(v) for v in out.values())
    assert "C50.9" not in blob
    assert "DUT item 3.1" not in blob


def test_never_raises_on_non_string_phi_value() -> None:
    """A non-string PHI value is redacted fail-closed (never raises, never emitted raw)."""
    out = redact_phi_vars({"laudo": {"nested": "clinical"}, "diagnostico": 12345})
    assert out["laudo"] == REDACTED_PHI
    assert out["diagnostico"] == REDACTED_PHI
