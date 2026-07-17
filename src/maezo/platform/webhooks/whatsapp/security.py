"""WhatsApp Cloud API webhook security — HMAC signature validation + phone pseudonymization.

Real, self-contained cryptography (constraint 3 — not a fabricated boundary): both functions are
pure, deterministic, and need no network/engine dependency, matching
`docs/runbooks/whatsapp-webhook.md` §2's documented contract exactly.
"""

from __future__ import annotations

import hashlib
import hmac

_SIGNATURE_PREFIX = "sha256="


def verify_hub_signature(payload: bytes, signature_header: str | None, app_secret: str) -> bool:
    """Validate Meta's `X-Hub-Signature-256: sha256=<hex_digest>` header.

    The digest is HMAC-SHA256 of the raw request body using the Meta app secret. Uses
    `hmac.compare_digest` (timing-safe) — never a plain `==` string comparison on a secret-derived
    value. Returns False (never raises) for a missing header, a malformed `sha256=` prefix, or a
    mismatched digest — the caller (app.py) is responsible for turning False into 401.
    """
    if not signature_header or not signature_header.startswith(_SIGNATURE_PREFIX):
        return False
    provided_hex = signature_header[len(_SIGNATURE_PREFIX) :]
    expected = hmac.new(app_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided_hex, expected)


def hash_phone(phone: str, tenant: str) -> str:
    """SHA-256 hex digest of `"{tenant}:{phone}"` — the raw phone number is NEVER logged,
    stored, or forwarded past this point (ADR-0006 General Zone pseudonymization)."""
    return hashlib.sha256(f"{tenant}:{phone}".encode()).hexdigest()
