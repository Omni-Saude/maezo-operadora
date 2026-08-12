"""LABELED REFERENCE envelope verifier — the PROPOSED ADR-0039 digest, unmistakably NOT ratified.

=================================================================================================
READ THIS FIRST — envelope signing is NOT implemented, and this file does not implement it either.
=================================================================================================
`docs/adr/0039-a2a-delegation-envelope-signing.md` ships **Status: Proposed**. Its own close-out
records that §Perguntas abertas 1 (`payload_meta_hash`) and 3 (max signature age / retention floor)
are questions a HUMAN must answer before the digest is settled. Building envelope-signature
verification against an unratified digest would be building on sand. So this module is a **reference
verifier**, deliberately labeled and quarantined:

  * It lives under `tests/`, is named `LabeledReferenceEnvelopeVerifier`, and mirrors the repo's
    anti-masquerade convention for labeled test doubles (`LabeledMockAnsGatewayTransport`,
    `Fake*`, `Noop*`) — a reader can never mistake it for the ratified implementation.
  * It is NEVER reachable from a production composition root. The effect-chokepoint fence
    (`scripts/ci/check_effect_chokepoint_fence.py`) scans only `src/maezo`; nothing in `src/`
    imports this, and `test_envelope_signing_attacks.py::test_no_production_module_references...`
    makes that a TESTED expectation (an AST sweep of `src/maezo` asserting zero references), not an
    assumption.
  * It implements ADR-0039 §4.1's canonical digest and §4.2's signature metadata **as PROPOSED**.
    When a human ratifies the digest and the real verifier lands, these attacks re-bind to it: the
    attack bodies stay, only the verifier under test changes.

Why a reference verifier at all (Option ii, not a strict-xfail): a strict-xfail against a verifier
that does not exist "passes" (xfails) even against a totally undefended system — that IS the vacuity
anti-pattern the leg-4 non-vacuity bar forbids. A reference verifier gives every signing-dependent
attack a REAL red control (`NeuteredReferenceEnvelopeVerifier` accepts the malicious envelope), so
the attack is non-vacuous and the DESIGN is shown to defend, without ANY claim that signing is done.

=================================================================================================
Digest — ADR-0039 §4.1 + §4.2, faithful, with ONE disclosed reference extension for §4.3.2 (Q3)
=================================================================================================
Field set exactly per §4.1: `{tenant, task_id, origin, target, payload_hash, deadline, budget,
delegation_chain}` plus §4.2's `{scheme, key_id, replay_epoch}`, canonicalized by the §4.1 recipe
(`json.dumps(payload, sort_keys=True, separators=(",", ":"))`, **no `default=`** — the ADR is
explicit that `default=str` is forbidden, `deadline` is mapped via `.astimezone(UTC).isoformat()`).
`payload_hash = sha256(payload_ref)`, not `payload_ref` directly (§4.1).

DISCLOSED DEVIATION — `signed_at`: §4.3.2 requires a verifier-side maximum signature age
INDEPENDENT of `deadline` (because every real envelope carries `deadline=None`, so `expired()` is
vacuous). Measuring an age needs a signing timestamp, and the §4.1 mandated field set does not carry
one — **that mechanism is exactly what Q3 leaves open** (the ADR pins the age VALUE as a human
policy number; it does not pin the carrier field). This reference verifier binds a `signed_at`
field into its digest as a documented reference interpretation, so the expiry/max-age attack can be
exercised non-vacuously TODAY. It is flagged, not hidden: when Q3 is answered the field re-binds to
whatever the ratified mechanism is. Every OTHER field is faithful to §4.1/§4.2 as written.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from maezo.a2a import DelegationEnvelope

#: The reference scheme tag (§4.2's example `"hmac-sha256-envelope-v1"`, a DISTINCT string space
#: from the Card's `SIGNATURE_SCHEME = "v1"`). "reference" is in the name so the tag itself
#: announces this is not the ratified scheme.
REFERENCE_SCHEME = "hmac-sha256-envelope-reference-v1"


def reference_canonical_digest(
    envelope: DelegationEnvelope,
    *,
    scheme: str,
    key_id: str,
    replay_epoch: int,
    signed_at: datetime,
) -> bytes:
    """The ADR-0039 §4.1 + §4.2 canonical digest bytes, faithful to the recipe (see module doc).

    Pure — the malleability/tamper attacks assert on these exact bytes. `signed_at` is the disclosed
    §4.3.2/Q3 reference extension."""
    payload: dict[str, object] = {
        "tenant": envelope.tenant,
        "task_id": envelope.task_id,
        "origin": envelope.origin,
        "target": envelope.target,
        "payload_hash": sha256(envelope.payload_ref.encode("utf-8")).hexdigest(),
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
        "signed_at": signed_at.astimezone(UTC).isoformat(),  # DISCLOSED §4.3.2/Q3 reference extension
    }
    # §4.1: sort_keys=True + compact separators + NO default= (a non-JSON-native value must raise,
    # never be silently coerced). All values here are JSON-native by construction.
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class ReferenceSignedEnvelope:
    """An envelope + the signature metadata bound into its digest + the MAC. The `Reference` prefix
    marks it as the PROPOSED shape, not a ratified wire format."""

    envelope: DelegationEnvelope
    scheme: str
    key_id: str
    replay_epoch: int
    signed_at: datetime
    mac: str


class LabeledReferenceEnvelopeVerifier:
    """The PROPOSED ADR-0039 verifier — reference only, NEVER a production composition-root
    dependency (module docstring). HMAC-SHA256 over the §4.1/§4.2 canonical digest, a trusted
    KEYSET (not a single scalar — §4.2's rotation requirement), a current `replay_epoch` hard
    cutover (§4.2), and a max-signature-age bound (§4.3.2, the Q3 reference extension).

    `verify` NEVER raises — it returns a bool, mirroring `CardSigner.verify`'s own contract — so a
    caller decides the rejection policy. Fail-closed on every check."""

    def __init__(
        self,
        *,
        trusted_keys: dict[str, bytes],
        active_key_id: str,
        current_epoch: int,
        max_signature_age: timedelta,
        scheme: str = REFERENCE_SCHEME,
    ) -> None:
        if active_key_id not in trusted_keys:
            raise ValueError("active_key_id must be present in trusted_keys (fail-closed config)")
        self._trusted_keys = dict(trusted_keys)
        self._active_key_id = active_key_id
        self._current_epoch = current_epoch
        self._max_signature_age = max_signature_age
        self._scheme = scheme

    def sign(
        self,
        envelope: DelegationEnvelope,
        *,
        key_id: str | None = None,
        replay_epoch: int | None = None,
        signed_at: datetime | None = None,
        signing_key: bytes | None = None,
    ) -> ReferenceSignedEnvelope:
        """Produce a reference-signed envelope. Defaults sign honestly under the active key / current
        epoch / now; the overrides let an attack forge a stale key, a prior epoch, or a stale
        timestamp, and let a test sign under a key the verifier does NOT trust."""
        kid = key_id if key_id is not None else self._active_key_id
        epoch = replay_epoch if replay_epoch is not None else self._current_epoch
        ts = signed_at if signed_at is not None else datetime.now(tz=UTC)
        key = signing_key if signing_key is not None else self._trusted_keys[kid]
        digest = reference_canonical_digest(
            envelope, scheme=self._scheme, key_id=kid, replay_epoch=epoch, signed_at=ts
        )
        mac = hmac.new(key, digest, sha256).hexdigest()
        return ReferenceSignedEnvelope(
            envelope=envelope, scheme=self._scheme, key_id=kid, replay_epoch=epoch, signed_at=ts, mac=mac
        )

    def verify(self, signed: ReferenceSignedEnvelope, *, now: datetime | None = None) -> bool:
        """True iff `signed` is a valid, current, unexpired signature under a trusted key. Every
        gate is fail-closed and independent (§4.4 "fail-closed gate")."""
        now = now if now is not None else datetime.now(tz=UTC)
        # §4.2 scheme binding (JWT "alg:none"-class downgrade defense): an unexpected scheme is refused.
        if signed.scheme != self._scheme:
            return False
        # §4.2 stale key: an unknown / retired key_id (not in the trusted keyset) is refused.
        key = self._trusted_keys.get(signed.key_id)
        if key is None:
            return False
        # §4.2 replay epoch hard cutover: a prior (or otherwise non-current) epoch is refused.
        if signed.replay_epoch != self._current_epoch:
            return False
        # §4.3.2 max signature age (Q3 reference extension): a signature older than the policy
        # bound (or future-dated) is refused. `deadline` cannot answer this — it is None on every
        # real envelope, which is precisely why the independent bound exists.
        age = now - signed.signed_at
        if age < timedelta(0) or age > self._max_signature_age:
            return False
        # §4.1 content MAC: recompute the digest over the CLAIMED metadata (all of which is bound
        # into the digest) and compare constant-time. Any tampered signed field -> mismatch.
        expected = reference_canonical_digest(
            signed.envelope,
            scheme=signed.scheme,
            key_id=signed.key_id,
            replay_epoch=signed.replay_epoch,
            signed_at=signed.signed_at,
        )
        expected_mac = hmac.new(key, expected, sha256).hexdigest()
        return hmac.compare_digest(signed.mac.encode("ascii"), expected_mac.encode("ascii"))


class NeuteredReferenceEnvelopeVerifier:
    """RED control — the reference verifier with EVERY defense removed. `verify` returns True
    unconditionally: the undefended baseline. Each signing-dependent attack asserts the real
    verifier REJECTS the malicious envelope while this one ACCEPTS it — proving the attack is
    non-vacuous (it succeeds against an undefended system) and the real digest is what stops it."""

    def verify(self, signed: ReferenceSignedEnvelope, *, now: datetime | None = None) -> bool:
        _ = (signed, now)
        return True
