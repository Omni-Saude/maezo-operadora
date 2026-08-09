"""Adversarial: no PHI-bearing business key survives the `scrub_only` egress (test (b)).

Every test here SEEDS the leak first — a real `CANCEL-{tenant}-{matricula}` key, the exact shape
`inadimplencia._cancel_business_key` mints for an individual/familiar plan — and then asserts the
matricula cannot be recovered from what comes out. Asserting "a scrubber ran" would prove nothing;
asserting "the value is gone" is the invariant.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maezo.gateway.pseudonymizer import (
    KEYED_PSEUDONYM_PREFIX,
    Pseudonymizer,
    PseudonymizerKeyMissingError,
)
from maezo.platform.privacy.key_scrubber import (
    BUSINESS_KEY_LOG_FIELDS,
    PHI_KEY_ANCHOR_LOG_FIELDS,
    PSEUDONYMIZED_MESSAGE_KEY_FAMILIES,
    BusinessKeyScrubber,
    egress_message_key,
    pseudonymize_message_key,
    scrub_key_value,
)
from tests.support.privacy_policy import phi_key_mode

_TENANT = "amh"
_MATRICULA = "mat-99887766"
_CANCEL_KEY = f"CANCEL-{_TENANT}-{_MATRICULA}"
_INAD_KEY = f"INAD-{_TENANT}-{_MATRICULA}"

_HK1 = re.compile(rf"^{re.escape(KEYED_PSEUDONYM_PREFIX)}[0-9a-f]{{64}}$")


@pytest.fixture
def pseudonymizer() -> Pseudonymizer:
    """Non-secret deterministic dev key — keyed HMAC, never plain SHA-256 (ADR-0035)."""
    return Pseudonymizer()


# ---------------------------------------------------------------------------
# scrub_key_value — the value-level invariant
# ---------------------------------------------------------------------------


def test_scrubbed_key_keeps_the_family_and_loses_everything_else(pseudonymizer: Pseudonymizer) -> None:
    scrubbed = scrub_key_value(_CANCEL_KEY, pseudonymizer)
    assert scrubbed.startswith("CANCEL-")
    assert _HK1.match(scrubbed.removeprefix("CANCEL-"))
    # The invariant, stated as the attacker would: nothing of the subject survives.
    assert _MATRICULA not in scrubbed
    assert _TENANT not in scrubbed


def test_scrubbed_key_is_deterministic_so_correlation_survives(pseudonymizer: Pseudonymizer) -> None:
    """Same logical key -> same pseudonym (log correlation, Kafka per-key ordering)."""
    assert scrub_key_value(_CANCEL_KEY, pseudonymizer) == scrub_key_value(_CANCEL_KEY, pseudonymizer)


def test_different_keys_get_different_pseudonyms(pseudonymizer: Pseudonymizer) -> None:
    assert scrub_key_value(_CANCEL_KEY, pseudonymizer) != scrub_key_value(_INAD_KEY, pseudonymizer)


def test_unknown_family_is_hashed_whole_not_partially_revealed(pseudonymizer: Pseudonymizer) -> None:
    """Fail-closed on shape: an unrecognized prefix is not assumed safe to leave in the clear."""
    scrubbed = scrub_key_value(f"WHATEVER-{_TENANT}-{_MATRICULA}", pseudonymizer)
    assert _HK1.match(scrubbed)
    assert "WHATEVER" not in scrubbed


@pytest.mark.parametrize("value", [None, "", 0, 42, [], {"a": 1}])
def test_non_string_and_empty_values_pass_through(pseudonymizer: Pseudonymizer, value: object) -> None:
    """`None` must stay `None` so a downstream `key=... or None` branch is unaffected."""
    assert scrub_key_value(value, pseudonymizer) is value


# ---------------------------------------------------------------------------
# BusinessKeyScrubber — the structlog processor
# ---------------------------------------------------------------------------


def test_every_key_bearing_field_is_scrubbed(pseudonymizer: Pseudonymizer) -> None:
    """Seed the matricula-form key into EVERY key-bearing field name; none may survive."""
    scrubber = BusinessKeyScrubber(pseudonymizer)
    event = {field: _CANCEL_KEY for field in BUSINESS_KEY_LOG_FIELDS}
    out = scrubber(None, "info", dict(event))
    for field in BUSINESS_KEY_LOG_FIELDS:
        assert _MATRICULA not in str(out[field]), field
        assert out[field] != _CANCEL_KEY, field


def test_raw_matricula_fields_are_scrubbed(pseudonymizer: Pseudonymizer) -> None:
    """`inadimplencia.notify_beneficiario` logs `matricula=`; `cancel.notify_beneficiario` logs
    `matricula_beneficiario=`. Both are raw `PHI_PROCESS_VARS` values on a log line today."""
    scrubber = BusinessKeyScrubber(pseudonymizer)
    out = scrubber(None, "info", {field: _MATRICULA for field in PHI_KEY_ANCHOR_LOG_FIELDS})
    for field in PHI_KEY_ANCHOR_LOG_FIELDS:
        assert _HK1.match(out[field]), field
        assert _MATRICULA not in out[field], field


def test_the_whole_rendered_line_is_free_of_the_matricula(pseudonymizer: Pseudonymizer) -> None:
    """End-to-end shape of the real leak: a `handoff_rescisao`-style INFO line for an
    individual/familiar plan carries the matricula TWICE (as `numero_contrato` fallback and
    inside `cancel_business_key`). After scrubbing, the serialized line must not contain it."""
    scrubber = BusinessKeyScrubber(pseudonymizer)
    out = scrubber(
        None,
        "info",
        {
            "event": "inadimplencia_handoff_rescisao",
            "cancel_business_key": _CANCEL_KEY,
            "matricula_beneficiario": _MATRICULA,
            "cancel_instance_id": "inst-1",
        },
    )
    assert _MATRICULA not in repr(out)


def test_non_key_fields_are_never_altered(pseudonymizer: Pseudonymizer) -> None:
    scrubber = BusinessKeyScrubber(pseudonymizer)
    event = {"event": "x", "tenant_id": _TENANT, "topic": "operadora.x", "count": 3}
    assert scrubber(None, "info", dict(event)) == event


def test_canonical_phi_fields_are_still_scrubbed(pseudonymizer: Pseudonymizer) -> None:
    """Composition, not replacement: `LogScrubber`'s PHI_FIELDS behaviour is preserved."""
    scrubber = BusinessKeyScrubber(pseudonymizer)
    out = scrubber(None, "info", {"cpf": "12345678901", "nome": "Fulano"})
    assert out["cpf"] != "12345678901"
    assert out["nome"] != "Fulano"


# ---------------------------------------------------------------------------
# Kafka MESSAGE KEY — only the two PHI-bearing families move
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("family", sorted(PSEUDONYMIZED_MESSAGE_KEY_FAMILIES))
def test_cancel_and_inad_message_keys_are_pseudonymized(pseudonymizer: Pseudonymizer, family: str) -> None:
    out = pseudonymize_message_key(f"{family}-{_TENANT}-{_MATRICULA}", pseudonymizer)
    assert out.startswith(f"{family}-")
    assert _MATRICULA not in out


@pytest.mark.parametrize(
    "key",
    [
        "RECURSO-amh-G-1-GL-2",
        "CRED-amh-prest-7",
        "FRAUDE-amh-caso-3",
        "PROG-amh-prog1-hk1_abc-2026",
        "ESC-amh-hk1_abc",
        "no-family-at-all",
        "",
    ],
)
def test_every_other_family_keeps_its_partitioning(pseudonymizer: Pseudonymizer, key: str) -> None:
    """The partitioning change is scoped: nothing outside CANCEL/INAD is re-keyed."""
    assert pseudonymize_message_key(key, pseudonymizer) == key


def test_egress_message_key_is_identity_under_modo_off(tmp_path: Path) -> None:
    with phi_key_mode(tmp_path, "off"):
        assert egress_message_key(_CANCEL_KEY) == _CANCEL_KEY


@pytest.mark.parametrize("modo", ["scrub_only", "pseudo_keys"])
def test_egress_message_key_pseudonymizes_once_ratified(
    tmp_path: Path, modo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUNTIME_MODE", "local")
    with phi_key_mode(tmp_path, modo):
        out = egress_message_key(_CANCEL_KEY)
    assert out != _CANCEL_KEY
    assert _MATRICULA not in out


def test_active_policy_without_a_provisioned_key_fails_closed_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0035 is INHERITED, not re-implemented: a production runtime with no `PHI_HMAC_KEY`
    RAISES rather than pseudonymizing business keys with the publicly-known dev key.

    Note the deliberate divergence from the settings default: an UNSET `RUNTIME_MODE` counts as
    production here. A pod that never declared its mode must not quietly get the dev key — that
    is exactly the fail-open `Pseudonymizer.from_settings` and `LogScrubber.from_settings` were
    hardened against. Costs nothing today: with the shipped `off` policy this path never runs.
    """
    monkeypatch.delenv("RUNTIME_MODE", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("PHI_HMAC_KEY", raising=False)
    with phi_key_mode(tmp_path, "scrub_only"), pytest.raises(PseudonymizerKeyMissingError):
        egress_message_key(_CANCEL_KEY)


def test_modo_off_never_even_builds_a_pseudonymizer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The inertness proof for the previous test's machinery: under `off` the key is returned
    untouched WITHOUT constructing a pseudonymizer, so an unprovisioned `PHI_HMAC_KEY` cannot
    break any daemon running today."""
    monkeypatch.delenv("RUNTIME_MODE", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("PHI_HMAC_KEY", raising=False)
    with phi_key_mode(tmp_path, "off"):
        assert egress_message_key(_CANCEL_KEY) == _CANCEL_KEY


# ---------------------------------------------------------------------------
# Coverage pin — the field-name set cannot silently fall behind the code
# ---------------------------------------------------------------------------


def test_every_key_bearing_log_field_in_src_is_covered() -> None:
    """Re-sweep `src/` for `*business_key=` kwargs on logger calls and require full coverage.

    Without this, a new worker logging `foo_business_key=` would be scrubbed by nobody and the
    hole would reopen silently. The sweep is deliberately textual and over-broad (any
    `<name>business_key=` kwarg anywhere in src), so it errs toward demanding coverage.
    """
    src = Path(__file__).resolve().parents[4] / "src" / "maezo"
    found: set[str] = set()
    pattern = re.compile(r"\b([a-z_]*business_key)\s*=")
    for path in src.rglob("*.py"):
        found.update(pattern.findall(path.read_text(encoding="utf-8")))
    # Local variables / parameters named `business_key` are the same NAME the log kwarg uses, so
    # the sweep and the allowlist agree on it; anything else must be explicitly covered.
    uncovered = found - BUSINESS_KEY_LOG_FIELDS
    assert not uncovered, (
        f"key-bearing field names not covered by BUSINESS_KEY_LOG_FIELDS: {sorted(uncovered)}"
    )
