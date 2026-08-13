"""CardSigner — HMAC-SHA256 signing/verification of Agent Cards (ADR-0003/0007).

Ported from the donor `Maezo-Healthcare-Plan` reference implementation
(`src/maezo/a2a/registry.py:183-236`) as part of the T2.4 A2A W1 (card-signing) build wave — see
`docs/design/A2A-dispatcher-card-signing.md` §1.2/§5. The algorithm is ported EXACTLY (this is the
security guarantee): HMAC-SHA256 over `AgentCard.signing_payload()`, fail-closed on an empty
signing key, constant-time verification.

v2 hardening on top of the donor (R1 crypto-review findings, same-branch fixes):
- key validation additionally rejects WHITESPACE-ONLY and TOO-SHORT keys (`MIN_SIGNING_KEY_BYTES`),
  not just empty — same `CardSignatureError`, same fail-closed posture;
- `verify()` ASCII-encodes both comparison operands so a non-ASCII (definitionally invalid)
  signature returns False instead of leaking a `TypeError` out of `hmac.compare_digest` —
  preserving the "verify never raises" contract that W2's dispatcher will rely on.
"""

from __future__ import annotations

import hmac
from hashlib import sha256

from maezo.a2a.card import SIGNATURE_SCHEME, AgentCard, CardSignatureError

#: Minimum accepted signing-key length in bytes, measured AFTER stripping ASCII whitespace.
#: 16 bytes = a 128-bit floor. Rationale: neither this repo nor the donor has a pre-existing
#: min-key-length convention (the donor and `gateway.pseudonymizer` only reject EMPTY keys), so
#: this adopts the common 128-bit security-strength floor (NIST SP 800-107: an HMAC key's security
#: strength is capped by its length; 112-128 bits is the accepted minimum) to fail-closed on
#: degenerate keys ("x", "test", a stray shell character) that an empty-only check silently
#: accepts. RFC 2104 recommends keys >= the digest length (32 bytes for SHA-256) — real vault/KMS
#: keys SHOULD be 32+ random bytes; this constant is the fail-closed FLOOR, not the target.
MIN_SIGNING_KEY_BYTES = 16


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
        # Fail-closed key validation: empty, whitespace-only, and too-short keys are all refused
        # with the SAME error class. Validation is on the whitespace-STRIPPED view (so b"\n",
        # b"   ", or a 15-byte key padded with blanks cannot sneak past an emptiness check), but
        # the key MATERIAL used for HMAC is the caller's bytes untouched — this constructor never
        # silently transforms a key. Transport-artifact normalization (e.g. a trailing newline
        # from a mounted secret file) belongs to the injection seam
        # (`assembly.card_signing_key_from_env`), not here.
        stripped = signing_key.strip() if signing_key else b""
        if not stripped:
            raise CardSignatureError(
                "empty or whitespace-only signing_key: inject the Card-signing key from "
                "vault/KMS, never use a default (ADR-0003)"
            )
        if len(stripped) < MIN_SIGNING_KEY_BYTES:
            raise CardSignatureError(
                f"signing_key too short ({len(stripped)} bytes after strip, minimum "
                f"{MIN_SIGNING_KEY_BYTES}): a degenerate key gives no real HMAC security — "
                "inject a proper key from vault/KMS (ADR-0003)"
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
        signature tagged with a different/forged scheme can never equal it. A valid signature is
        `scheme:hexdigest` — ASCII by construction — so a NON-ASCII signature string is
        definitionally not-a-valid-signature -> False (both operands are ASCII-encoded to bytes
        first; `hmac.compare_digest` on str operands raises TypeError for non-ASCII, which would
        otherwise escape a caller catching only `CardSignatureError`). Comparison is constant-time
        (`hmac.compare_digest`) to avoid leaking timing information. Never raises — the caller
        (registry/dispatcher) decides the rejection policy.

        That never-raises promise is TOTAL over the signature slot's own TYPE, not just its
        encoding. `AgentCard.signature` is typed `str | None` and validated nowhere, so a card
        rebuilt off a wire can carry a dict/int/list/bool there — none of which has `.encode`, which
        used to raise `AttributeError` straight out of this method and out of
        `A2ARegistry.register`'s documented `CardSignatureError` contract. A non-str signature is
        definitionally not a valid signature, so it REFUSES (no coercion, no `str()` of the
        container). Mirrors the envelope surface's `mac` guard in `envelope_signing.verify`.
        """
        if card.signature is None:
            return False
        try:
            provided = card.signature.encode("ascii")
        except (UnicodeEncodeError, AttributeError):
            return False  # a non-ASCII / non-str signature is definitionally not a valid signature
        return hmac.compare_digest(provided, self._digest(card).encode("ascii"))

    def require_valid(self, card: AgentCard) -> AgentCard:
        """Like `verify`, but raises `CardSignatureError` if invalid (fail-closed path)."""
        if not self.verify(card):
            raise CardSignatureError(
                f"invalid Card signature for agent_id={card.agent_id!r} tenant={card.tenant!r}"
            )
        return card
