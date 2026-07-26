"""Unit tests for maezo.gateway.pseudonymizer — PHI Pseudonymization (ADR-0006, ADR-0035).

TDD London School: tests written BEFORE implementation.
"""

import hashlib
import hmac

import pytest
import structlog

from maezo.gateway.pseudonymizer import (
    Pseudonymizer,
    PseudonymizerKeyMissingError,
    _derive_dev_key,
)


def test_pseudonymize_removes_phi() -> None:
    """Pseudonymizer must replace PHI fields with SHA-256 pseudonyms."""
    p = Pseudonymizer()

    data = {
        "cpf": "12345678901",
        "nome": "João Silva",
        "telefone": "11999999999",
        "email": "joao@example.com",
        "procedimento": "consulta",
        "valor": 150.00,
    }

    result = p.pseudonymize(data)

    # PHI fields must NOT contain the original values
    assert result["cpf"] != "12345678901"
    assert result["nome"] != "João Silva"
    assert result["telefone"] != "11999999999"
    assert result["email"] != "joao@example.com"

    # Non-PHI fields must be preserved
    assert result["procedimento"] == "consulta"
    assert result["valor"] == 150.00

    # PHI fields must be valid SHA-256 hex digests (64 hex chars)
    for field in ["cpf", "nome", "telefone", "email"]:
        pseudonym = result[field]
        assert len(pseudonym) == 64
        assert all(c in "0123456789abcdef" for c in pseudonym)


def test_pseudonymize_deterministic() -> None:
    """Same input must produce the same pseudonym (deterministic SHA-256)."""
    p = Pseudonymizer()

    data = {"cpf": "12345678901", "nome": "Maria"}

    result1 = p.pseudonymize(dict(data))
    result2 = p.pseudonymize(dict(data))

    assert result1["cpf"] == result2["cpf"]
    assert result1["nome"] == result2["nome"]


def test_pseudonymize_different_inputs_different_pseudonyms() -> None:
    """Different PHI values must produce different pseudonyms."""
    p = Pseudonymizer()

    r1 = p.pseudonymize({"cpf": "11111111111"})
    r2 = p.pseudonymize({"cpf": "22222222222"})

    assert r1["cpf"] != r2["cpf"]


def test_pseudonymize_empty_phi_fields() -> None:
    """Fields with empty/None values should remain as-is (no crash)."""
    p = Pseudonymizer()

    data = {"cpf": "", "nome": None, "telefone": "11999999999"}

    result = p.pseudonymize(data)

    # Empty/None values should not be pseudonymized
    assert result["cpf"] == ""
    assert result["nome"] is None
    assert result["telefone"] != "11999999999"  # Non-empty PHI still pseudonymized


def test_pseudonymize_no_phi_fields() -> None:
    """Data without any PHI fields should be returned unchanged (except type)."""
    p = Pseudonymizer()

    data = {"procedimento": "consulta", "valor": 150.00}
    result = p.pseudonymize(dict(data))

    assert result == data


def test_pseudonymize_phi_fields_frozenset() -> None:
    """PHI_FIELDS must be a frozenset (immutable, CI-enforced)."""
    from maezo.gateway.pseudonymizer import PHI_FIELDS

    assert isinstance(PHI_FIELDS, frozenset)
    assert "cpf" in PHI_FIELDS
    assert "nome" in PHI_FIELDS
    assert "telefone" in PHI_FIELDS
    assert "email" in PHI_FIELDS


# --- ADR-0035: keyed HMAC-SHA256 + fail-closed policy ------------------------------------------

_CPF = "12345678901"


def _plain_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_keyed_output_is_not_plain_sha256() -> None:
    """SECURITY (ADR-0035): a keyed pseudonym must NOT equal the reversible unkeyed sha256(cpf).

    This is the reversibility-closed invariant: an attacker with a precomputed sha256 table of
    all ~10^9 valid CPFs must not be able to reverse the pseudonym.
    """
    keyed = Pseudonymizer(key=b"real-vault-key").pseudonymize({"cpf": _CPF})["cpf"]
    assert keyed != _plain_sha256(_CPF)
    # It IS an HMAC of the cpf under that key (proves keying, not some other transform).
    expected = hmac.new(b"real-vault-key", _CPF.encode("utf-8"), hashlib.sha256).hexdigest()
    assert keyed == expected


def test_keyed_output_differs_from_unkeyed_for_same_cpf() -> None:
    """Keyed HMAC output != plain sha256 output for the SAME cpf (the core weakness closed)."""
    keyed = Pseudonymizer(key=b"k").pseudonymize({"cpf": _CPF})["cpf"]
    assert keyed != _plain_sha256(_CPF)


def test_same_key_same_token() -> None:
    """Deterministic WITHIN a key: same key + same input -> same token (correlation preserved)."""
    a = Pseudonymizer(key=b"shared").pseudonymize({"cpf": _CPF})["cpf"]
    b = Pseudonymizer(key=b"shared").pseudonymize({"cpf": _CPF})["cpf"]
    assert a == b


def test_different_key_different_token() -> None:
    """Different key -> different token for the same input (proves it is actually keyed)."""
    a = Pseudonymizer(key=b"key-A").pseudonymize({"cpf": _CPF})["cpf"]
    b = Pseudonymizer(key=b"key-B").pseudonymize({"cpf": _CPF})["cpf"]
    assert a != b


def test_from_settings_keyed_uses_provided_key() -> None:
    """from_settings with a key -> HMAC under exactly that key."""
    p = Pseudonymizer.from_settings(phi_hmac_key="vault-key", production=True, tenant_id="amh")
    token = p.pseudonymize({"cpf": _CPF})["cpf"]
    assert token == hmac.new(b"vault-key", _CPF.encode("utf-8"), hashlib.sha256).hexdigest()


def test_prod_absent_key_fails_closed() -> None:
    """SECURITY-CRITICAL (ADR-0035): production + absent key MUST raise (fail-closed).

    Mutation-mind: revert `from_settings` to return a Pseudonymizer instead of raising here and
    this test goes RED — the reversible-in-prod weakness would ship silently otherwise.
    """
    with pytest.raises(PseudonymizerKeyMissingError):
        Pseudonymizer.from_settings(phi_hmac_key=None, production=True, tenant_id="amh")
    # Empty string is treated the same as absent (an env set to "" is not a key).
    with pytest.raises(PseudonymizerKeyMissingError):
        Pseudonymizer.from_settings(phi_hmac_key="", production=True, tenant_id="amh")


def test_prod_whitespace_only_key_fails_closed() -> None:
    """SECURITY (ADR-0035): a blank/whitespace-only PHI_HMAC_KEY is NOT a key — prod must treat
    it as ABSENT and fail closed, not silently accept whitespace as real key material (same
    hardening class as the repo's `_norm_str`/whitespace-bypass fixes).

    Mutation-mind: revert `from_settings` to branch on raw truthiness (`if phi_hmac_key:`)
    instead of the normalized value and this test goes RED.
    """
    for blank in ("   ", "\t\n", " \r\n "):
        with pytest.raises(PseudonymizerKeyMissingError):
            Pseudonymizer.from_settings(phi_hmac_key=blank, production=True, tenant_id="amh")


def test_local_whitespace_only_key_uses_dev_fallback_with_warning() -> None:
    """dev/local + whitespace-only key -> treated as absent: dev fallback + warning, NO raise."""
    with structlog.testing.capture_logs() as logs:
        p = Pseudonymizer.from_settings(phi_hmac_key="  \t", production=False, tenant_id="amh")
    assert any(
        e.get("event") == "phi_pseudonymizer_dev_fallback_key" and e.get("log_level") == "warning"
        for e in logs
    )
    # Same token as the absent-key dev fallback (whitespace == absent, one canonical dev key).
    absent = Pseudonymizer.from_settings(phi_hmac_key=None, production=False, tenant_id="amh")
    assert p.pseudonymize({"cpf": _CPF})["cpf"] == absent.pseudonymize({"cpf": _CPF})["cpf"]


def test_key_with_surrounding_whitespace_is_canonicalized() -> None:
    """A real key that arrives with a trailing newline (classic secret-file artifact) is
    normalized to the same key material as its clean form — no token drift between a clean and
    a newline-suffixed injection of the SAME key."""
    clean = Pseudonymizer.from_settings(phi_hmac_key="vault-key", production=True, tenant_id="amh")
    newline = Pseudonymizer.from_settings(phi_hmac_key="vault-key\n", production=True, tenant_id="amh")
    assert clean.pseudonymize({"cpf": _CPF})["cpf"] == newline.pseudonymize({"cpf": _CPF})["cpf"]


def test_local_absent_key_deterministic_fallback_with_warning() -> None:
    """dev/local + absent key -> deterministic per-tenant DEV key + loud warning, NO raise."""
    with structlog.testing.capture_logs() as logs:
        p1 = Pseudonymizer.from_settings(phi_hmac_key=None, production=False, tenant_id="amh")
    # Loud warning emitted (the ".env vazia em dev" convention, made visible).
    assert any(
        e.get("event") == "phi_pseudonymizer_dev_fallback_key" and e.get("log_level") == "warning"
        for e in logs
    )
    # Deterministic across constructions in the same tenant.
    p2 = Pseudonymizer.from_settings(phi_hmac_key=None, production=False, tenant_id="amh")
    assert p1.pseudonymize({"cpf": _CPF})["cpf"] == p2.pseudonymize({"cpf": _CPF})["cpf"]
    # ...and it is the deterministic per-tenant DEV key (HMAC, never plain sha256).
    dev_token = p1.pseudonymize({"cpf": _CPF})["cpf"]
    assert dev_token == hmac.new(_derive_dev_key("amh"), _CPF.encode("utf-8"), hashlib.sha256).hexdigest()
    assert dev_token != _plain_sha256(_CPF)


def test_dev_fallback_is_per_tenant() -> None:
    """Distinct tenants get distinct dev pseudonyms (no cross-tenant collision in dev)."""
    amh = Pseudonymizer.from_settings(phi_hmac_key=None, production=False, tenant_id="amh")
    other = Pseudonymizer.from_settings(phi_hmac_key=None, production=False, tenant_id="other")
    assert amh.pseudonymize({"cpf": _CPF})["cpf"] != other.pseudonymize({"cpf": _CPF})["cpf"]


def test_bare_constructor_default_is_not_plain_sha256() -> None:
    """Even the no-arg constructor (LogScrubber/tests default) is keyed HMAC, not reversible sha256."""
    token = Pseudonymizer().pseudonymize({"cpf": _CPF})["cpf"]
    assert token != _plain_sha256(_CPF)
    assert len(token) == 64 and all(c in "0123456789abcdef" for c in token)
