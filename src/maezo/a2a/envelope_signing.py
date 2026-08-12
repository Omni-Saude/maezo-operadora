"""Delegation-envelope signing + verification — the REAL ADR-0039 digest v2 (Accepted).

This is leg E3 of the envelope-signing train: the production implementation of ADR-0039's canonical
digest, HMAC signer, and fail-closed verifier. It mirrors the CONTRACT of the labeled reference
verifier the E4 adversarial attack suite exercises (the quarantined double under
tests/unit/a2a/attacks/) — same HMAC-SHA256 over a canonical JSON digest, a trusted per-tenant
KEYSET (not a single scalar, §4.2 rotation), a `replay_epoch` cutover, and a max-signature-age
bound — but differs in three ratified ways:

  1. **Digest v2 (ADR-0039 §4.1, owner decisions 1-2, 2026-08-12).** The signed field set is the v1
     mandated set PLUS `payload_meta_hash` (binding the instruction content — TUSS/CID-10/
     `valor_estimado_brl` on Helena's edge, the CRED `prestador_id` business key on Carolina's edge,
     `valor_pagamento_cents` on Andre's edge — without persisting or disclosing a byte of it) and
     `task_type` (bound directly). `signed_at` is bound in too, carrying the timestamp the §4.3.2
     max-age bound measures against (so an attacker cannot move it without the key).
  2. **Per-tenant custody (§4.4, decision 4).** Keys resolve through the SAME per-tenant seam Card
     signing uses (`maezo.a2a.keyset`), never a repo-wide or cross-tenant key.
  3. **Bound onto the envelope, not a wrapper.** The signature is `DelegationEnvelope.signature`
     (an `EnvelopeSignature`), so it travels WITH the envelope through `dispatcher.delegate` and the
     dispatcher can verify it as its first admission check.

CANONICALIZATION — reuses the repo convention EXACTLY (§4.1). `json.dumps(payload, sort_keys=True,
separators=(",", ":"))`, UTF-8, and crucially **no `default=`**: a non-JSON-native value raises
`TypeError` at signing time rather than being silently coerced (the `deadline` `T`-vs-space trap the
ADR calls out). `payload_hash = sha256(payload_ref)`; `payload_meta_hash = sha256(canonical(
payload_meta))` under the SAME recipe. `deadline` maps via `.astimezone(UTC).isoformat()`.

FAIL-CLOSED, everywhere (§4.4). The signer refuses a key shorter than `MIN_SIGNING_KEY_BYTES` (the
same floor `CardSigner` enforces) and refuses to sign an envelope with `deadline=None` (§4.3.1) or a
tenant it was not built for. The verifier's every gate is independent and fail-closed, MAC compared
constant-time (`hmac.compare_digest`); `verify` NEVER raises (mirrors `CardSigner.verify`) so the
dispatcher decides the rejection policy.
"""

from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

from maezo.a2a.delegation import DelegationEnvelope, EnvelopeSignature
from maezo.a2a.signing import MIN_SIGNING_KEY_BYTES

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The envelope signature scheme tag (§4.2). A DISTINCT string space from the Card's
#: `SIGNATURE_SCHEME = "v1"` (the two surfaces are independent, §4.1) and `v2` because it binds the
#: v2 digest (`payload_meta_hash` + `task_type`). Bound INTO the digest so a downgrade to any other
#: scheme string invalidates the MAC (the alg-downgrade / "alg:none"-class defense).
ENVELOPE_SIGNATURE_SCHEME = "hmac-sha256-envelope-v2"

#: The ordinary trust epoch envelopes are signed/verified under by default. `replay_epoch` is a
#: monotonically-increasing incident-response cutover integer (§4.2): an epoch BUMP hard-rejects
#: every prior-epoch signature. It starts at 0; an operator raises it during incident response. The
#: MECHANISM (bound in the digest, checked by the verifier) is what this leg builds; the value is an
#: operational input, and 0 is the fail-closed default both signer and verifier share here.
DEFAULT_REPLAY_EPOCH = 0

#: Verifier-side maximum signature age (§4.3.2), an INDEPENDENT bound reject-older-than regardless of
#: `deadline`. Derived by the owner (§Decisoes do dono, row 3, 2026-08-12): ordinary rotation cadence
#: = 7 days, rotation grace = 7 days, so a signature the verifier would still accept can be at most
#: one rotation cycle (7 days) old — capping acceptance at the cycle length keeps the age bound
#: consistent with the keyset's own retention. It also floors `a2a_idempotency` retention (§4.3.3).
MAX_SIGNATURE_AGE = timedelta(days=7)


class EnvelopeSignatureError(ValueError):
    """A delegation envelope's signing key or metadata is missing/malformed (fail-closed, §4.4).

    Raised at SIGN time and at verifier CONSTRUCTION (a degenerate config). `verify` itself never
    raises — it returns a bool — so a forged/tampered envelope is a rejection, never an exception on
    the dispatcher's caller path (the dispatcher's 'never raises to caller' contract)."""


def _canonical_bytes(value: Mapping[str, str]) -> bytes:
    """The §4.1 canonical JSON bytes of `value` — the SAME recipe as the outer digest, no `default=`.

    `payload_meta` is `Mapping[str, str]` (`delegation.py`), JSON-native by construction, so this
    never needs coercion; a non-native value would (correctly) raise `TypeError` here."""
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode("utf-8")


def envelope_canonical_digest(
    envelope: DelegationEnvelope,
    *,
    scheme: str,
    key_id: str,
    replay_epoch: int,
    signed_at: datetime,
) -> bytes:
    """The ADR-0039 §4.1 (v2) + §4.2 canonical digest bytes — the ONE preimage signer and verifier
    share, byte-for-byte.

    Pure. The v2 field set: the v1 mandated `{tenant, task_id, origin, target, payload_hash,
    deadline, budget, delegation_chain}` + §4.2 `{scheme, key_id, replay_epoch}` + owner decisions
    1-2 `{payload_meta_hash, task_type}` + the §4.3.2 `signed_at` the max-age bound measures. `no
    default=` (a non-JSON-native value must raise, never be silently coerced — the ADR is explicit).
    """
    payload: dict[str, object] = {
        "tenant": envelope.tenant,
        "task_id": envelope.task_id,
        "task_type": envelope.task_type,
        "origin": envelope.origin,
        "target": envelope.target,
        "payload_hash": sha256(envelope.payload_ref.encode("utf-8")).hexdigest(),
        "payload_meta_hash": sha256(_canonical_bytes(envelope.payload_meta)).hexdigest(),
        "deadline": envelope.deadline.astimezone(UTC).isoformat() if envelope.deadline else None,
        "budget": {
            "tokens": envelope.budget.tokens,
            "time_ms": envelope.budget.time_ms,
            "cost_per_hop": envelope.budget.cost_per_hop,
        },
        "delegation_chain": list(envelope.delegation_chain),
        "scheme": scheme,
        "key_id": key_id,
        "replay_epoch": replay_epoch,
        "signed_at": signed_at.astimezone(UTC).isoformat(),
    }
    # §4.1: sort_keys + compact separators + NO default= — a pure function of content, no whitespace
    # ambiguity a re-encoder could exploit (the malleability argument).
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def derive_key_id(key: bytes) -> str:
    """A stable, non-secret key identifier derived from the key material (a JWK-thumbprint-style
    fingerprint): `env-sha256:<first 16 hex of sha256(key)>`.

    Deriving the `key_id` from the key means rotation is automatic — provisioning a new key yields a
    new id, and a superseded key keeps its own id in the trusted keyset for its grace window — with
    NO separate id env var to keep in sync (a mismatched id would be its own fail-open footgun).
    Sixteen hex of a SHA-256 of a high-entropy HMAC secret is a one-way fingerprint: it identifies
    the key without being reversible to it.
    """
    return f"env-sha256:{sha256(key).hexdigest()[:16]}"


def build_verification_keyset(
    *, active_key: bytes, prior_key: bytes | None
) -> tuple[dict[str, bytes], str]:
    """Assemble a tenant's trusted verification keyset `{key_id: key}` + the active `key_id` (§4.3).

    The active key is always trusted; the PRIOR key (present only during the 7-day rotation grace) is
    trusted too, under its OWN derived id, so an envelope signed just before a rotation still finds
    its `key_id` for as long as the max-signature-age lets it stay in flight. A prior key EQUAL to
    the active one collapses to a single entry (idempotent). NO other key is ever trusted — no
    repo-wide, no cross-tenant (the resolver upstream enforces per-tenant custody).
    """
    active_key_id = derive_key_id(active_key)
    trusted: dict[str, bytes] = {active_key_id: active_key}
    if prior_key is not None:
        trusted[derive_key_id(prior_key)] = prior_key
    return trusted, active_key_id


def _validate_key_or_raise(signing_key: bytes) -> None:
    """Fail-closed key validation, IDENTICAL floor to `CardSigner` (empty/whitespace/too-short)."""
    stripped = signing_key.strip() if signing_key else b""
    if not stripped:
        raise EnvelopeSignatureError(
            "empty or whitespace-only envelope signing_key: inject the per-tenant key from "
            "vault/KMS, never a default (ADR-0039 §4.4)"
        )
    if len(stripped) < MIN_SIGNING_KEY_BYTES:
        raise EnvelopeSignatureError(
            f"envelope signing_key too short ({len(stripped)} bytes after strip, minimum "
            f"{MIN_SIGNING_KEY_BYTES}): a degenerate key gives no real HMAC security (ADR-0039 §4.4)"
        )


class EnvelopeSigner:
    """Signs `DelegationEnvelope`s with HMAC-SHA256 over the §4.1 v2 canonical digest (ADR-0039).

    Built once per composition root for ONE tenant from that tenant's ACTIVE key (resolved through
    `maezo.a2a.keyset`), the derived `key_id`, and the current `replay_epoch`. `sign` fails closed:
    it refuses an envelope for a DIFFERENT tenant (cross-tenant signing is a confusion vector), and
    refuses an envelope with `deadline=None` (§4.3.1 — a signed envelope MUST carry a deadline, else
    the rotation grace window and expiry defense are vacuous). The key material is validated at
    construction to the same floor `CardSigner` uses.
    """

    def __init__(
        self,
        *,
        tenant: str,
        signing_key: bytes,
        key_id: str,
        replay_epoch: int = DEFAULT_REPLAY_EPOCH,
        scheme: str = ENVELOPE_SIGNATURE_SCHEME,
    ) -> None:
        _validate_key_or_raise(signing_key)
        if not tenant:
            raise EnvelopeSignatureError("EnvelopeSigner requires a tenant (per-tenant custody)")
        self._tenant = tenant
        self._key = signing_key
        self._key_id = key_id
        self._replay_epoch = replay_epoch
        self._scheme = scheme

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, envelope: DelegationEnvelope, *, now: datetime | None = None) -> DelegationEnvelope:
        """Return a signed copy of `envelope`. Fail-closed on wrong-tenant / missing-deadline."""
        if envelope.tenant != self._tenant:
            raise EnvelopeSignatureError(
                f"envelope signer for tenant {self._tenant!r} refuses to sign an envelope for "
                f"tenant {envelope.tenant!r} (per-tenant custody, ADR-0039 §4.4)"
            )
        if envelope.deadline is None:
            raise EnvelopeSignatureError(
                "cannot sign an envelope with deadline=None: a signed envelope MUST carry a "
                "non-None deadline (ADR-0039 §4.3.1), otherwise the rotation grace window and the "
                "expiry defense are vacuous"
            )
        signed_at = now if now is not None else datetime.now(tz=UTC)
        digest = envelope_canonical_digest(
            envelope,
            scheme=self._scheme,
            key_id=self._key_id,
            replay_epoch=self._replay_epoch,
            signed_at=signed_at,
        )
        mac = hmac.new(self._key, digest, sha256).hexdigest()
        return envelope.signed_copy(
            EnvelopeSignature(
                scheme=self._scheme,
                key_id=self._key_id,
                replay_epoch=self._replay_epoch,
                signed_at=signed_at,
                mac=mac,
            )
        )


class EnvelopeVerifier:
    """Verifies a `DelegationEnvelope.signature` — the fail-closed admission gate (ADR-0039 §4.4).

    Built once per composition root for ONE tenant from that tenant's trusted KEYSET (active + any
    prior-in-grace key, `build_verification_keyset`), the current `replay_epoch`, and the
    max-signature-age. `verify` returns a bool and NEVER raises (mirrors `CardSigner.verify`) — every
    gate is independent and fail-closed:

      * unsigned envelope -> reject (no `signature`);
      * unexpected `scheme` -> reject (§4.2 alg-downgrade / "alg:none"-class);
      * unknown/retired `key_id` (not in the trusted keyset) -> reject (§4.2 stale key);
      * non-current `replay_epoch` -> reject, UNLESS it is exactly the prior epoch AND a short
        prior-epoch grace window is configured AND `now` is within it (§4.2 decision 5; the default
        is ZERO grace — a hard cutover — which is the incident semantics);
      * `deadline is None` -> reject (§4.3.1 — a signed envelope must carry a deadline);
      * signature older than the max age, or future-dated -> reject (§4.3.2, two-sided);
      * MAC mismatch over the recomputed v2 digest -> reject (constant-time compare).
    """

    def __init__(
        self,
        *,
        trusted_keys: dict[str, bytes],
        active_key_id: str,
        current_epoch: int,
        max_signature_age: timedelta,
        scheme: str = ENVELOPE_SIGNATURE_SCHEME,
        prior_epoch_grace_until: datetime | None = None,
    ) -> None:
        if active_key_id not in trusted_keys:
            raise EnvelopeSignatureError(
                "active_key_id must be present in trusted_keys (fail-closed verifier config)"
            )
        if not trusted_keys:
            raise EnvelopeSignatureError("trusted_keys must be non-empty (fail-closed verifier config)")
        self._trusted_keys = dict(trusted_keys)
        self._active_key_id = active_key_id
        self._current_epoch = current_epoch
        self._max_signature_age = max_signature_age
        self._scheme = scheme
        #: The instant up to which PRIOR-epoch signatures are tolerated after an epoch bump (§4.2
        #: decision 5). `None` = ZERO grace (a hard cutover) — the fail-closed default. A short
        #: window is an operational input the operator sets at bump time, never a fixed number here.
        self._prior_epoch_grace_until = prior_epoch_grace_until

    def _epoch_accepted(self, replay_epoch: int, now: datetime) -> bool:
        if replay_epoch == self._current_epoch:
            return True
        # Short prior-epoch grace (decision 5): only the IMMEDIATELY prior epoch, only while the
        # operator-set grace window is open. Zero grace (the default) rejects every prior epoch.
        return (
            replay_epoch == self._current_epoch - 1
            and self._prior_epoch_grace_until is not None
            and now <= self._prior_epoch_grace_until
        )

    def verify(self, envelope: DelegationEnvelope, *, now: datetime | None = None) -> bool:
        """True iff `envelope` carries a valid, current, unexpired signature under a trusted key."""
        now = now if now is not None else datetime.now(tz=UTC)
        sig = envelope.signature
        if sig is None:
            return False  # unsigned -> fail-closed
        if sig.scheme != self._scheme:
            return False  # §4.2 scheme binding (alg-downgrade defense)
        key = self._trusted_keys.get(sig.key_id)
        if key is None:
            return False  # §4.2 unknown/retired key_id
        if not self._epoch_accepted(sig.replay_epoch, now):
            return False  # §4.2 replay-epoch cutover (+ optional short prior-epoch grace)
        if envelope.deadline is None:
            return False  # §4.3.1 a signed envelope must carry a deadline
        age = now - sig.signed_at
        if age < timedelta(0) or age > self._max_signature_age:
            return False  # §4.3.2 max signature age (two-sided: future-dated is also refused)
        expected = envelope_canonical_digest(
            envelope,
            scheme=sig.scheme,
            key_id=sig.key_id,
            replay_epoch=sig.replay_epoch,
            signed_at=sig.signed_at,
        )
        expected_mac = hmac.new(key, expected, sha256).hexdigest()
        try:
            provided = sig.mac.encode("ascii")
        except (UnicodeEncodeError, AttributeError):
            return False  # a non-ASCII / non-str MAC is definitionally not a valid signature
        return hmac.compare_digest(provided, expected_mac.encode("ascii"))
