"""CardSigner — HMAC-SHA256 signing/verification of Agent Cards (ADR-0003/0007).

Ported from the donor `Maezo-Healthcare-Plan` reference implementation
(`src/maezo/a2a/registry.py:183-236`) as part of the T2.4 A2A W1 (card-signing) build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §1.2/§5. The algorithm is ported EXACTLY (this is the
security guarantee): HMAC-SHA256 over `AgentCard.signing_payload()`, fail-closed on an empty
signing key, constant-time verification.
"""

from __future__ import annotations

import hmac
from hashlib import sha256

from maezo.a2a.card import SIGNATURE_SCHEME, AgentCard, CardSignatureError


class CardSigner:
    """Signs/verifies Agent Cards with HMAC-SHA256 (ADR-0003/0007).

    Mirrors the key-injection contract of `gateway.pseudonymizer.Pseudonymizer`: the key arrives by
    INJECTION (vault/KMS), never hard-coded in source, and the constructor REFUSES an empty key
    (fail-closed) — there is no path where a Card is silently signed under a default key in
    production. Real key population in the vault/KMS is BLOCKED (external dependency — see
    `docs/design/A2A-dispatcher-card-signing.md` §6.2); the injection seam is this constructor plus
    `maezo.a2a.assembly.card_signer_from_key` / `build_agent_cards(..., signer=...)`.

    HMAC (std-lib, already vendored) is symmetric: the same secret signs and verifies. This suits
    an in-process, co-located A2A runtime (Option A) where the Card's producer and verifier share
    the same tenant trust boundary. The A2A remote boundary (mTLS) orthogonally covers transport
    authenticity; the versioned `scheme` tag lets a future migration to asymmetric signing happen
    without breaking the `signature` field's shape.
    """

    def __init__(self, signing_key: bytes) -> None:
        if not signing_key:
            raise CardSignatureError(
                "empty signing_key: inject the Card-signing key from vault/KMS, never use a "
                "default (ADR-0003)"
            )
        self._key = signing_key

    def _digest(self, card: AgentCard) -> str:
        """HMAC-SHA256 hex digest of the Card's canonical bytes, as `scheme:hexdigest`."""
        mac = hmac.new(self._key, card.signing_payload(), sha256).hexdigest()
        return f"{SIGNATURE_SCHEME}:{mac}"

    def sign(self, card: AgentCard) -> AgentCard:
        """Return a copy of the Card with an HMAC signature over its canonical bytes.

        Idempotent: the signature covers `signing_payload()` (which EXCLUDES the signature
        itself), so re-signing an already-signed Card produces the same signature.
        """
        return card.signed_copy(self._digest(card))

    def verify(self, card: AgentCard) -> bool:
        """True iff the Card carries a valid signature produced by THIS key.

        An unsigned Card (`signature is None`) -> False (not trusted under this key). An unknown
        scheme -> False (fail-closed): `_digest` always embeds the CURRENT `SIGNATURE_SCHEME`, so a
        signature tagged with a different/forged scheme can never equal it. Comparison is
        constant-time (`hmac.compare_digest`) to avoid leaking timing information. Never raises —
        the caller (registry/dispatcher) decides the rejection policy.
        """
        if card.signature is None:
            return False
        return hmac.compare_digest(card.signature, self._digest(card))

    def require_valid(self, card: AgentCard) -> AgentCard:
        """Like `verify`, but raises `CardSignatureError` if invalid (fail-closed path)."""
        if not self.verify(card):
            raise CardSignatureError(
                f"invalid Card signature for agent_id={card.agent_id!r} tenant={card.tenant!r}"
            )
        return card
