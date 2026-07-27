"""WhatsApp Cloud API webhook security — HMAC signature validation + phone pseudonymization.

Real, self-contained cryptography (constraint 3 — not a fabricated boundary): `verify_hub_signature`
is pure/deterministic and needs no dependency. `hash_phone` is now KEYED (ADR-0035): it derives the
conversation/thread identity through the SAME vault-keyed `Pseudonymizer` the gateway uses, so the
`telefone`-derived identity that egresses (conversation_id → checkpoint thread_id → CIB Seven
business key → INFO logs) is irreversible without `PHI_HMAC_KEY`.
"""

from __future__ import annotations

import hashlib
import hmac

from maezo.gateway.pseudonymizer import KEYED_PSEUDONYM_PREFIX, Pseudonymizer

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


def hash_phone(phone: str, tenant: str, pseudonymizer: Pseudonymizer) -> str:
    """KEYED pseudonym of `"{tenant}:{phone}"`, tagged with the `hk1_` keyed-scheme marker.

    The raw phone number is NEVER logged, stored, or forwarded past this point (ADR-0006 General
    Zone). CRITICAL (ADR-0035 extension): the digest is produced by the injected KEYED HMAC-SHA256
    `Pseudonymizer` — NOT a bare `sha256` — because this value becomes the `conversation_id` /
    checkpoint `thread_id` / `ESC-{tenant}-...` CIB Seven business key that is durably PERSISTED
    (Postgres checkpoint tables) and Cockpit-visible. A bare sha256 of a phone is trivially
    reversible via a precomputed table over the ~6.7e9 BR-mobile keyspace; a keyed HMAC is not,
    without the vault-synced `PHI_HMAC_KEY`.

    Fail-closed is INHERITED, not re-implemented: the `pseudonymizer` is always built via
    `Pseudonymizer.from_settings` at the composition root (`webhooks/service.py`), which raises
    `PseudonymizerKeyMissingError` in a production `runtime_mode` when the key is absent/blank —
    so there is NO code path here that can emit an unkeyed identity in production. In dev/CI the
    injected pseudonymizer carries a non-secret deterministic key (loud warning at construction).

    The `tenant:` prefix inside the HMAC input keeps the pseudonym distinct per tenant even under a
    single shared vault key; the `hk1_` marker on the output lets `assert_phi_safe_thread_id`
    require a keyed form and refuse the legacy `wa:{tenant}:{bare-sha256}` identity.
    """
    digest = pseudonymizer.pseudonymize({"telefone": f"{tenant}:{phone}"})["telefone"]
    return f"{KEYED_PSEUDONYM_PREFIX}{digest}"
