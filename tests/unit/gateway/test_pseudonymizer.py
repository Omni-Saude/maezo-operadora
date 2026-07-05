"""Unit tests for maezo.gateway.pseudonymizer — PHI Pseudonymization (ADR-0006).

TDD London School: tests written BEFORE implementation.
"""

from maezo.gateway.pseudonymizer import Pseudonymizer


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
