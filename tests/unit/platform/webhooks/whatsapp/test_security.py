"""Unit tests for `maezo.platform.webhooks.whatsapp.security` (T1.6, real HMAC — no mock)."""

from __future__ import annotations

import hashlib
import hmac

from maezo.platform.webhooks.whatsapp.security import hash_phone, verify_hub_signature


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


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


def test_hash_phone_deterministic() -> None:
    h1 = hash_phone("+5511999999999", tenant="amh")
    h2 = hash_phone("+5511999999999", tenant="amh")
    assert h1 == h2
    assert h1 == hashlib.sha256(b"amh:+5511999999999").hexdigest()


def test_hash_phone_distinct_per_tenant() -> None:
    h_amh = hash_phone("+5511999999999", tenant="amh")
    h_other = hash_phone("+5511999999999", tenant="other")
    assert h_amh != h_other
