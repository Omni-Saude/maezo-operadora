"""Unit tests for maezo.gateway.log_scrubber — Log Scrubbing (ADR-0006 §logs).

TDD London School: tests written BEFORE implementation verification.
"""

from __future__ import annotations

from maezo.gateway.log_scrubber import LogScrubber
from maezo.gateway.pseudonymizer import PHI_FIELDS


def test_log_scrubber_removes_cpf() -> None:
    """LogScrubber must replace CPF with SHA-256 pseudonym in log event dicts."""
    scrubber = LogScrubber()

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
    scrubber = LogScrubber()

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
    scrubber = LogScrubber()

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
    scrubber = LogScrubber()

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
    scrubber = LogScrubber()
    event: dict[str, object] = {}

    result = scrubber(None, "info", event)

    assert result == {}


def test_log_scrubber_deterministic_scrubbing() -> None:
    """Same PHI value must produce the same pseudonym in logs (deterministic)."""
    scrubber = LogScrubber()

    event1 = {"cpf": "12345678901"}
    event2 = {"cpf": "12345678901"}

    result1 = scrubber(None, "info", event1)
    result2 = scrubber(None, "info", event2)

    assert result1["cpf"] == result2["cpf"]


def test_log_scrubber_scrub_dict_method() -> None:
    """LogScrubber.scrub_dict() must pseudonymize PHI fields."""
    scrubber = LogScrubber()

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
    scrubber = LogScrubber()

    event = {"cpf": "", "nome": None, "telefone": "11999999999"}

    result = scrubber(None, "info", event)

    assert result["cpf"] == ""
    assert result["nome"] is None
    # Non-empty PHI still scrubbed
    assert result["telefone"] != "11999999999"


def test_log_scrubber_phi_fields_match_pseudonymizer() -> None:
    """LogScrubber must use the same PHI_FIELDS as Pseudonymizer."""
    assert frozenset({"cpf", "nome", "telefone", "email"}) == PHI_FIELDS
