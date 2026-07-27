"""Unit tests for maezo.gateway.log_scrubber — Log Scrubbing (ADR-0006 §logs).

TDD London School: tests written BEFORE implementation verification.
"""

from __future__ import annotations

import pytest

from maezo.gateway.log_scrubber import LogScrubber
from maezo.gateway.pseudonymizer import PHI_FIELDS, Pseudonymizer, PseudonymizerKeyMissingError


def test_log_scrubber_removes_cpf() -> None:
    """LogScrubber must replace CPF with SHA-256 pseudonym in log event dicts."""
    scrubber = LogScrubber(Pseudonymizer())

    event = {
        "event": "user_login",
        "cpf": "12345678901",
        "nome": "João Silva",
        "action": "auth",
    }

    result = scrubber(None, "info", event)

    # CPF must be scrubbed
    assert result["cpf"] != "12345678901"
    assert len(result["cpf"]) == 64
    assert all(c in "0123456789abcdef" for c in result["cpf"])

    # Non-PHI fields must be preserved
    assert result["event"] == "user_login"
    assert result["action"] == "auth"


def test_log_scrubber_removes_phone() -> None:
    """LogScrubber must replace phone/telefone with SHA-256 pseudonym."""
    scrubber = LogScrubber(Pseudonymizer())

    event = {
        "event": "sms_sent",
        "telefone": "11999999999",
        "message_id": "msg-001",
    }

    result = scrubber(None, "info", event)

    # Phone must be scrubbed
    assert result["telefone"] != "11999999999"
    assert len(result["telefone"]) == 64

    # Non-PHI must be preserved
    assert result["message_id"] == "msg-001"


def test_log_scrubber_removes_nome_and_email() -> None:
    """LogScrubber must replace nome and email fields."""
    scrubber = LogScrubber(Pseudonymizer())

    event = {
        "nome": "Maria Santos",
        "email": "maria@example.com",
        "procedimento": "consulta",
    }

    result = scrubber(None, "info", event)

    assert result["nome"] != "Maria Santos"
    assert len(result["nome"]) == 64
    assert result["email"] != "maria@example.com"
    assert len(result["email"]) == 64
    assert result["procedimento"] == "consulta"


def test_log_scrubber_preserves_non_phi() -> None:
    """LogScrubber must never alter non-PHI fields."""
    scrubber = LogScrubber(Pseudonymizer())

    event = {
        "level": "info",
        "agent_id": "helena",
        "tenant_id": "amh",
        "action": "triagem",
        "duration_ms": 150,
    }

    result = scrubber(None, "info", event)

    assert result == event


def test_log_scrubber_empty_event() -> None:
    """LogScrubber must handle empty event dicts gracefully."""
    scrubber = LogScrubber(Pseudonymizer())
    event: dict[str, object] = {}

    result = scrubber(None, "info", event)

    assert result == {}


def test_log_scrubber_deterministic_scrubbing() -> None:
    """Same PHI value must produce the same pseudonym in logs (deterministic)."""
    scrubber = LogScrubber(Pseudonymizer())

    event1 = {"cpf": "12345678901"}
    event2 = {"cpf": "12345678901"}

    result1 = scrubber(None, "info", event1)
    result2 = scrubber(None, "info", event2)

    assert result1["cpf"] == result2["cpf"]


def test_log_scrubber_scrub_dict_method() -> None:
    """LogScrubber.scrub_dict() must pseudonymize PHI fields."""
    scrubber = LogScrubber(Pseudonymizer())

    data = {"cpf": "11111111111", "nome": "Test User", "not_phi": "value"}

    result = scrubber.scrub_dict(data)

    assert result["cpf"] != "11111111111"
    assert result["nome"] != "Test User"
    assert result["not_phi"] == "value"


def test_has_phi_detects_phi_fields() -> None:
    """LogScrubber.has_phi() must return True when any PHI field is present."""
    assert LogScrubber.has_phi({"cpf": "123"}) is True
    assert LogScrubber.has_phi({"nome": "João"}) is True
    assert LogScrubber.has_phi({"telefone": "119"}) is True
    assert LogScrubber.has_phi({"email": "a@b.com"}) is True


def test_has_phi_returns_false_for_no_phi() -> None:
    """LogScrubber.has_phi() must return False when no PHI fields present."""
    assert LogScrubber.has_phi({"action": "test", "status": "ok"}) is False
    assert LogScrubber.has_phi({}) is False
    assert LogScrubber.has_phi({"procedimento": "consulta"}) is False


def test_log_scrubber_empty_phi_preserved() -> None:
    """Empty/None PHI values should be preserved (no crash)."""
    scrubber = LogScrubber(Pseudonymizer())

    event = {"cpf": "", "nome": None, "telefone": "11999999999"}

    result = scrubber(None, "info", event)

    assert result["cpf"] == ""
    assert result["nome"] is None
    # Non-empty PHI still scrubbed
    assert result["telefone"] != "11999999999"


def test_log_scrubber_phi_fields_match_pseudonymizer() -> None:
    """LogScrubber must use the same PHI_FIELDS as Pseudonymizer."""
    assert frozenset({"cpf", "nome", "telefone", "email"}) == PHI_FIELDS


# ---------------------------------------------------------------------------
# Fail-closed (t9 fold-in LOW): the bare-ctor dev-key default is REMOVED, and `from_settings`
# inherits the ADR-0035 fail-closed policy — a prod log config with no key can no longer scrub PHI
# with a reversible/non-secret pseudonym.
# ---------------------------------------------------------------------------


def test_constructor_requires_explicit_pseudonymizer() -> None:
    """The bare `LogScrubber()` dev-key fallback is gone — a pseudonymizer must be injected."""
    with pytest.raises(TypeError):
        LogScrubber()  # type: ignore[call-arg]


@pytest.mark.parametrize("bad_key", [None, "", "   ", "\t\n"])
def test_from_settings_prod_fails_closed_on_absent_or_blank_key(bad_key: str | None) -> None:
    """Production + absent/blank/whitespace key -> raise, never a silent non-secret dev key."""
    with pytest.raises(PseudonymizerKeyMissingError):
        LogScrubber.from_settings(phi_hmac_key=bad_key, production=True, tenant_id="amh")


def test_from_settings_dev_builds_keyed_scrubber() -> None:
    """Dev/CI + absent key -> a non-secret deterministic keyed scrubber (still HMAC, not sha256)."""
    scrubber = LogScrubber.from_settings(phi_hmac_key=None, production=False, tenant_id="amh")
    out = scrubber.scrub_dict({"cpf": "12345678901"})
    assert out["cpf"] != "12345678901"
    assert len(out["cpf"]) == 64


def test_from_settings_prod_with_key_builds_keyed_scrubber() -> None:
    """Production + a real key -> a keyed scrubber that pseudonymizes PHI."""
    scrubber = LogScrubber.from_settings(phi_hmac_key="real-key", production=True, tenant_id="amh")
    out = scrubber.scrub_dict({"telefone": "11999999999"})
    assert out["telefone"] != "11999999999"
