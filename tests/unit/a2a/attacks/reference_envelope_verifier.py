"""LABELED REFERENCE envelope verifier — the PRE-DECISION (v1) ADR-0039 digest, kept as an ORACLE.

=================================================================================================
RE-SCOPED BY LEG E4 (2026-08-12). Read this before assuming the module docstring you remember.
=================================================================================================
ORIGINAL CHARTER (Onda 3, ADR-0039 **Proposed**): envelope signing was NOT implemented, because
§Perguntas abertas 1 (`payload_meta_hash`) and 3 (max signature age) were unanswered and building
against an unratified digest is building on sand. This module stood in for the absent production
code so the Half-B attack battery could be non-vacuous without CLAIMING signing was done.

THAT CHARTER IS DISCHARGED. The ADR is **Accepted**, the owner answered all seven questions, and leg
E3 shipped `maezo.a2a.envelope_signing` (digest v2 = the v1 field set PLUS `payload_meta_hash` and
`task_type`, per decisions 1-2). Leg E4 re-bound every Half-B attack onto that real verifier.

NEW CHARTER — this module is RETAINED, deliberately, as a **conformance oracle**, and it earns its
keep in two ways no self-referential test can:

  1. **RED CONTROL for the two acceptance flips.** `reference_canonical_digest` implements the
     PRE-decision field set, which OMITS `payload_meta_hash` and `task_type`. So the exact mutations
     the shipped v2 digest now catches remain INVISIBLE to it. That is ADR-0039 §4.8 obligation 2's
     "dropping the field from the digest" — performed against the historical field set rather than a
     synthetic mutant, which makes it the r1-r3 honest negative preserved as live evidence instead of
     deleted. It is a strictly better control than the accept-everything double it replaces, because
     it isolates WHICH field is load-bearing.
  2. **DIFFERENTIAL ORACLE.** It is an INDEPENDENTLY WRITTEN implementation of the ADR text. Running
     it beside the shipped code catches the failure a single implementation cannot: a field quietly
     dropped from the preimage, or a gate quietly skipped, while every self-referential assertion
     stays green. `test_envelope_signing_attacks.py` checks both digest-level agreement across the
     v1 field set and verdict-level agreement across a battery of attack scenarios.

RETIRED BY LEG E4: `NeuteredReferenceEnvelopeVerifier`, the accept-everything twin. Its `verify`
returned `True` unconditionally, which proves an attack succeeds against a TOTALLY undefended system
but never which defense actually refused it. Half B's controls are now targeted — one defense at a
time, built from knobs the production verifier ships (`trusted_keys`, `current_epoch`,
`prior_epoch_grace_until`, `scheme`, `max_signature_age`) plus the unwired-dispatcher baseline for
the MAC. Its NAME stays in the anti-masquerade forbidden-token list so it cannot return via `src/`.

QUARANTINE IS UNCHANGED, and matters MORE now, not less: an oracle that imported (or was imported
by) the code it audits would be worthless.
  * Lives under `tests/`, named `LabeledReferenceEnvelopeVerifier` per the repo's anti-masquerade
    convention for labeled doubles (`LabeledMockAnsGatewayTransport`, `LabeledFake*`, `Noop*`).
  * NEVER reachable from a production composition root. The effect-chokepoint fence
    (`scripts/ci/check_effect_chokepoint_fence.py`) scans only `src/maezo`; nothing in `src/` imports
    this, and `test_envelope_signing_attacks.py::test_no_production_module_references_the_reference_
    verifier` makes that a TESTED expectation (a text sweep of `src/maezo`), not an assumption.
  * It is NOT maintained toward the shipped implementation. It implements §4.1/§4.2 **as written in
    revisions r1-r3**, and it must stay frozen there — "fixing" it to match `envelope_signing.py`
    would collapse the two implementations into one and destroy the differential.

=================================================================================================
Digest — ADR-0039 §4.1 + §4.2 as of r1-r3, with ONE disclosed extension for §4.3.2 (then-open Q3)
=================================================================================================
Field set exactly per §4.1 v1: `{tenant, task_id, origin, target, payload_hash, deadline, budget,
delegation_chain}` plus §4.2's `{scheme, key_id, replay_epoch}`, canonicalized by the §4.1 recipe
(`json.dumps(payload, sort_keys=True, separators=(",", ":"))`, **no `default=`** — the ADR is
explicit that `default=str` is forbidden, `deadline` is mapped via `.astimezone(UTC).isoformat()`).
`payload_hash = sha256(payload_ref)`, not `payload_ref` directly (§4.1).

DISCLOSED DEVIATION — `signed_at`: §4.3.2 requires a verifier-side maximum signature age INDEPENDENT
of `deadline`. Measuring an age needs a signing timestamp, and the v1 mandated field set did not
carry one — that mechanism was exactly what Q3 left open. This module bound a `signed_at` field into
its digest as a documented reference interpretation. Q3 is now ANSWERED (decision 3: 7-day cadence)
and the shipped digest binds `signed_at` the same way — so this deviation turned out to anticipate
the ratified design correctly. It is recorded rather than removed, because the point of an oracle is
that its history is auditable.
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


# RETIRED BY LEG E4: `NeuteredReferenceEnvelopeVerifier` (an accept-everything `verify`) used to live
# here as the RED control for every Half-B attack. It was removed, not merely unused: a control that
# accepts EVERYTHING shows an attack beats a totally undefended system, but never which defense
# refused it — and ADR-0039 §4.8 obligation 2 asks precisely for the latter ("dropping the field from
# the digest, widening the keyset to accept any key_id, ignoring the epoch"). Half B's controls are
# now targeted and built from shipped verifier knobs; see this module's docstring and
# `test_envelope_signing_attacks.py`'s. The class NAME remains in the anti-masquerade forbidden-token
# sweep so it cannot reappear inside `src/`.
