"""Unit tests for `maezo.platform.webhooks.whatsapp.security` (T1.6, real HMAC — no mock).

`hash_phone` is now KEYED (ADR-0035 extension, t9-phi-conversation-id): the conversation/thread
identity it produces MUST be irreversible without `PHI_HMAC_KEY`. These tests prove that a known
phone does NOT map to `sha256(tenant:phone)` (the old reversible scheme) and DOES map to the keyed
HMAC, plus the prod fail-closed matrix for an absent/blank/whitespace key.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from maezo.gateway.pseudonymizer import (
    KEYED_PSEUDONYM_PREFIX,
    Pseudonymizer,
    PseudonymizerKeyMissingError,
)
from maezo.platform.webhooks.whatsapp.security import hash_phone, verify_hub_signature


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _plain_sha256(tenant: str, phone: str) -> str:
    """The OLD reversible scheme — the exact value the fix must NOT reproduce."""
    return hashlib.sha256(f"{tenant}:{phone}".encode()).hexdigest()


def test_verify_hub_signature_accepts_correct_signature() -> None:
    payload = b'{"entry":[]}'
    secret = "dev-secret"
    header = _sign(payload, secret)
    assert verify_hub_signature(payload, header, secret) is True


def test_verify_hub_signature_rejects_wrong_secret() -> None:
    payload = b'{"entry":[]}'
    header = _sign(payload, "right-secret")
    assert verify_hub_signature(payload, header, "wrong-secret") is False


def test_verify_hub_signature_rejects_tampered_payload() -> None:
    secret = "dev-secret"
    header = _sign(b'{"entry":[]}', secret)
    assert verify_hub_signature(b'{"entry":["tampered"]}', header, secret) is False


def test_verify_hub_signature_rejects_missing_header() -> None:
    assert verify_hub_signature(b"payload", None, "secret") is False


def test_verify_hub_signature_rejects_malformed_prefix() -> None:
    assert verify_hub_signature(b"payload", "sha1=deadbeef", "secret") is False


def test_verify_hub_signature_rejects_empty_header() -> None:
    assert verify_hub_signature(b"payload", "", "secret") is False


# ---------------------------------------------------------------------------
# hash_phone — KEYED, irreversible identity (ADR-0035 extension, t9-phi-conversation-id)
# ---------------------------------------------------------------------------

_PHONE = "+5511999999999"


def test_hash_phone_deterministic_per_key() -> None:
    """Same (phone, tenant, key) -> same pseudonym (correlation preserved for multi-turn resume)."""
    p = Pseudonymizer()
    assert hash_phone(_PHONE, "amh", p) == hash_phone(_PHONE, "amh", p)


def test_hash_phone_is_keyed_hmac_never_plain_sha256() -> None:
    """IRREVERSIBILITY PROOF (the core of this fix): a known phone must NOT map to the reversible
    `sha256(tenant:phone)` the old scheme produced — it must be the KEYED HMAC of the same input,
    carrying the `hk1_` keyed-scheme marker."""
    p = Pseudonymizer()
    h = hash_phone(_PHONE, "amh", p)

    # NOT the old reversible digest (a precomputed sha256 table can no longer recover the phone).
    assert h != _plain_sha256("amh", _PHONE)
    assert _plain_sha256("amh", _PHONE) not in h

    # IS the keyed HMAC of `tenant:phone`, tagged with the keyed marker.
    assert h.startswith(KEYED_PSEUDONYM_PREFIX)
    expected_digest = p.pseudonymize({"telefone": f"amh:{_PHONE}"})["telefone"]
    assert h == f"{KEYED_PSEUDONYM_PREFIX}{expected_digest}"
    assert len(expected_digest) == 64 and all(c in "0123456789abcdef" for c in expected_digest)


def test_hash_phone_distinct_per_tenant() -> None:
    """Per-tenant distinctness holds even under a single shared vault key (tenant is salted in)."""
    p = Pseudonymizer()
    assert hash_phone(_PHONE, "amh", p) != hash_phone(_PHONE, "other", p)


def test_hash_phone_distinct_per_key() -> None:
    """Two different HMAC keys yield different pseudonyms for the same phone — the identity is bound
    to the secret, so it cannot be reproduced without `PHI_HMAC_KEY`."""
    p1 = Pseudonymizer(key=b"key-one")
    p2 = Pseudonymizer(key=b"key-two")
    assert hash_phone(_PHONE, "amh", p1) != hash_phone(_PHONE, "amh", p2)


# ---------------------------------------------------------------------------
# Fail-closed matrix (ADR-0035): a production pseudonymizer with an absent/blank/whitespace key
# raises at CONSTRUCTION, so `hash_phone` has no path to emit an unkeyed identity in prod. In dev
# it degrades to a non-secret deterministic keyed pseudonym (still HMAC, never plain sha256).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_key", [None, "", "   ", "\t\n"])
def test_hash_phone_prod_fails_closed_on_absent_or_blank_key(bad_key: str | None) -> None:
    """Production + absent/blank/whitespace key -> the keyed pseudonymizer refuses to construct, so
    no reversible identity can ever be produced in prod (fail-closed, no silent unkeyed fallback)."""
    with pytest.raises(PseudonymizerKeyMissingError):
        p = Pseudonymizer.from_settings(phi_hmac_key=bad_key, production=True, tenant_id="amh")
        hash_phone(_PHONE, "amh", p)


@pytest.mark.parametrize("dev_key", [None, "", "   "])
def test_hash_phone_dev_degrades_to_keyed_nonsecret_never_sha256(dev_key: str | None) -> None:
    """Dev/CI + absent/blank key -> a non-secret deterministic per-tenant DEV pseudonymizer (loud
    warning at construction). Still KEYED HMAC (never the reversible plain sha256)."""
    p = Pseudonymizer.from_settings(phi_hmac_key=dev_key, production=False, tenant_id="amh")
    h = hash_phone(_PHONE, "amh", p)
    assert h.startswith(KEYED_PSEUDONYM_PREFIX)
    assert _plain_sha256("amh", _PHONE) not in h


def test_hash_phone_prod_with_real_key_produces_keyed_identity() -> None:
    """Production + a real key -> a keyed identity, distinct from the reversible sha256."""
    p = Pseudonymizer.from_settings(phi_hmac_key="a-real-vault-key", production=True, tenant_id="amh")
    h = hash_phone(_PHONE, "amh", p)
    assert h.startswith(KEYED_PSEUDONYM_PREFIX)
    assert _plain_sha256("amh", _PHONE) not in h
