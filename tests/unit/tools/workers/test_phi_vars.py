"""Unit tests for the worker PHI-egress redaction seam (`phi_vars.redact_phi_vars`).

One-way, class-token redaction at the engine/Kafka-facing edge (ADR-0006, GAP-XPHI-1). See
`src/maezo/tools/workers/phi_vars.py` for the invariant.
"""

from __future__ import annotations

import pytest

from maezo.tools.workers.phi_vars import (
    PHI_PROCESS_VARS,
    REDACTED_DIGITS,
    REDACTED_PHI,
    redact_error_message,
    redact_phi_vars,
)


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


# ---------------------------------------------------------------------------
# redact_error_message (T3.4 F5) — exception-message backstop before the engine's
# Cockpit-visible incident store.
# ---------------------------------------------------------------------------


def test_bare_cpf_digit_run_is_redacted() -> None:
    """An unformatted 11-digit run (a raw CPF, or any other long numeric id) is redacted."""
    out = redact_error_message(ValueError("cpf invalido: 12345678901"))
    assert "12345678901" not in out
    assert REDACTED_DIGITS in out


def test_formatted_cpf_is_redacted() -> None:
    """A canonically-punctuated CPF is redacted even though no single digit run is 11+ long."""
    out = redact_error_message(ValueError("beneficiario cpf=123.456.789-01 nao encontrado"))
    assert "123.456.789-01" not in out
    assert REDACTED_DIGITS in out


def test_formatted_cnpj_is_redacted() -> None:
    out = redact_error_message(ValueError("prestador cnpj=12.345.678/0001-90 invalido"))
    assert "12.345.678/0001-90" not in out
    assert REDACTED_DIGITS in out


def test_error_class_is_preserved_as_a_stable_prefix() -> None:
    """Ops must still be able to tell WHAT kind of failure occurred from the redacted message."""
    out = redact_error_message(ValueError("cpf invalido: 12345678901"))
    assert out.startswith("ValueError: ")


def test_plain_message_with_no_phi_shape_passes_through_readable() -> None:
    """No false positives: an ordinary message (short ids, prose) is not mangled."""
    exc = RuntimeError("engine unreachable: connection refused on port 8080")
    out = redact_error_message(exc)
    assert out == "RuntimeError: engine unreachable: connection refused on port 8080"


def test_plain_string_with_no_exception_has_no_class_prefix() -> None:
    """A static harness message (not derived from an exception) is passed through un-prefixed."""
    out = redact_error_message("no handler registered for topic 'operadora.test'")
    assert out == "no handler registered for topic 'operadora.test'"


def test_short_numeric_ids_below_the_digit_run_threshold_survive() -> None:
    """A 10-digit (or shorter) numeric id is NOT a false-positive match — only 11+ is redacted."""
    out = redact_error_message(ValueError("task 1234567890 not found"))
    assert "1234567890" in out


def test_long_message_is_capped() -> None:
    """An unbounded/adversarial message length is capped before reaching the incident store."""
    out = redact_error_message(ValueError("x" * 2000))
    assert len(out) < 2000
    assert out.endswith("...[TRUNCATED]")


def test_never_raises_on_a_pathological_input() -> None:
    """The backstop must never itself raise onto the failure-reporting hot path."""

    class _Unstringable:
        def __str__(self) -> str:
            raise RuntimeError("boom")

    out = redact_error_message(_Unstringable())  # type: ignore[arg-type]
    assert out == "[REDACTED_ERROR]"
