"""HALF B — attacks against the REAL envelope-signature verifier (ADR-0039 Accepted; leg E4).

RE-BOUND (leg E4). Onda 3 built this battery against `LabeledReferenceEnvelopeVerifier`, a labeled
stand-in, because ADR-0039 was **Proposed** and building against an unratified digest is building on
sand. The ADR is now **Accepted** with seven owner decisions (2026-08-12), leg E3 shipped the real
`maezo.a2a.envelope_signing` (digest v2, `EnvelopeSigner`/`EnvelopeVerifier`, per-tenant rotation
keyset), and this suite now exercises **that production code**. The attack bodies survived the
re-bind essentially unchanged — which was the point of building them against a faithful reference.

WHAT CHANGED IN THE RE-BIND, mechanically: the reference carried the signature in a side-car
`ReferenceSignedEnvelope(envelope, scheme, key_id, replay_epoch, signed_at, mac)`; the real design
binds it ONTO the envelope as `DelegationEnvelope.signature: EnvelopeSignature`. So an attack that
used to be `replace(signed, envelope=tampered)` is now simply `replace(signed_envelope, field=...)`
— the signature rides along untouched, which is exactly the wire shape an attacker sees.

RED CONTROLS — SHIPPED KNOBS, NOT A SYNTHETIC `return True` (§4.8 obligation 2). The Onda-3 suite's
control was `NeuteredReferenceEnvelopeVerifier`, which accepted everything unconditionally. That
proves an attack succeeds against a *totally* undefended system but NOT which specific defense is
load-bearing — and §4.8 obligation 2 asks for the latter ("dropping the field from the digest,
widening the keyset to accept any `key_id`, ignoring the epoch"). Every control here is therefore
**targeted**, and — with one disclosed exception — built from a knob the production verifier
actually ships, so the control is a real configuration rather than a test-only fiction:

  * key gate      -> `trusted_keys` still holding the retired / cross-tenant key (the un-purged
                     keyset, and for the cross-tenant case the repo-wide keyset owner-decision 4
                     rules out) -> ACCEPT.
  * epoch gate    -> `current_epoch` / `prior_epoch_grace_until` (decision 5's own grace) -> ACCEPT.
  * scheme gate   -> `scheme=` on the verifier, matched to the attacker's forged tag -> ACCEPT.
  * age gate      -> `max_signature_age` widened past the capture delay -> ACCEPT.
  * digest fields -> the PRE-DECISION v1 digest (`reference_envelope_verifier`, re-scoped by this
                     leg into a conformance oracle) omits `payload_meta_hash`/`task_type`, so it
                     ACCEPTS the two mutations the v2 digest now refuses. This is literally "drop
                     the field from the digest", and it is the r1-r3 honest negative preserved as
                     evidence rather than deleted.
  * MAC itself    -> no knob exists (correctly). Its control is the DISPATCHER end to end: the same
                     tampered envelope through a dispatcher with NO verifier wired — the §4.4
                     dev-local opt-out, and the literal pre-E3 production behaviour — reaches the
                     handler. `_TAMPER_BATTERY`'s third column records that outcome per attack.

The transient SOURCE-neuter campaign (each gate deleted from `src/` in turn, suite re-run, gate
restored) is recorded in this leg's commit body; it is the evidence that these controls bite against
the shipped code, not merely against each other.

`verify` TOTALITY (§9). This leg found and disclosed a real defect: `EnvelopeVerifier.verify` raised
`TypeError` on a naive `signed_at` and on a non-JSON-native `payload_meta`, breaking its own
documented never-raises contract, and the `TypeError` escaped `dispatcher.delegate` to the caller.
Those honest negatives have since been FLIPPED by the E4-repair leg: §9 now asserts the positive
form — malformed wire-shaped input is a REFUSAL (`is False` / `SIGNATURE_INVALID`), never an
exception — each with the pre-fix behaviour retained as an explicit RED CONTROL.

Each attack names the ADR-0039 §4.5 threat row it discharges (§4.8 obligation 1):
  * Tamper (any signed field)   — §4.5 "Tamper", digest v2 scope (decisions 1-2).
  * Altered `payload_hash`      — §4.5 "Altered payload_hash".
  * Cross-tenant replay         — §4.5 "Cross-tenant replay" (SIGNING half; the durable-store half
                                  is `test_cross_tenant_replay_attack.py`).
  * Stale key / prior epoch / scheme downgrade — §4.5 "Stale key" + §4.2 metadata binding.
  * Expiry bypass / max age     — §4.3.1 + §4.3.2 (both sides of the bound).
  * Cross-tenant KEY confusion  — §4.4 + decision 4 (the attack per-tenant custody exists to stop).
"""

from __future__ import annotations

import hmac
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from maezo.a2a import (
    AgentCard,
    Budget,
    DelegationEnvelope,
    EnvelopeSignature,
    EnvelopeSigner,
    EnvelopeVerifier,
    RejectionReason,
    build_verification_keyset,
    derive_key_id,
    envelope_canonical_digest,
)
from maezo.a2a.envelope_signing import (
    ENVELOPE_SIGNATURE_SCHEME,
    MAX_SIGNATURE_AGE,
    EnvelopeSignatureError,
)
from tests.unit.a2a.attacks.reference_envelope_verifier import (
    LabeledReferenceEnvelopeVerifier,
    ReferenceSignedEnvelope,
    reference_canonical_digest,
)
from tests.unit.a2a.fakes import FakeAgentHandler, build_test_dispatcher, make_card

# --- Test fixtures. Obvious literals, NOT secrets; each >= MIN_SIGNING_KEY_BYTES so it is usable. --
_TENANT_A = "amh"
_TENANT_B = "outra"
_KEY_ACTIVE = b"attack-suite-active-envelope-key-0123456789abcdef"
_KEY_PRIOR = b"attack-suite-prior-envelope-key-0123456789abcdef"
_KEY_RETIRED = b"attack-suite-retired-envelope-key-0123456789abcdef"
_KEY_TENANT_B = b"attack-suite-tenant-b-envelope-key-0123456789abcdef"

#: A non-zero current epoch, so "the prior epoch" is a real integer an attacker can claim (an epoch
#: of 0 would make `current_epoch - 1` negative and the grace arithmetic untestable).
_EPOCH = 7
#: A SHORT test-only age bound, so the capture-and-replay attacks need no multi-day clock arithmetic.
#: The PRODUCTION value is `MAX_SIGNATURE_AGE = 7 days` and is pinned — with its ADR provenance and
#: its 6d-accept / 7d+1s-reject boundary — by `tests/unit/a2a/test_keyset.py:489-494`. This suite
#: deliberately does NOT re-pin it: one owner per constant.
_MAX_AGE = timedelta(minutes=5)
_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
#: Far-future so `DelegationEnvelope.expired()` (checked against the REAL wall clock by the
#: dispatcher's structural guards) never fires and confounds a signature-attack result.
_DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)


def _verifier(
    *,
    active_key: bytes = _KEY_ACTIVE,
    prior_key: bytes | None = None,
    epoch: int = _EPOCH,
    max_age: timedelta = _MAX_AGE,
    prior_epoch_grace_until: datetime | None = None,
    scheme: str = ENVELOPE_SIGNATURE_SCHEME,
    also_trust: tuple[bytes, ...] = (),
) -> EnvelopeVerifier:
    """The REAL verifier under attack.

    `also_trust` is the RED-CONTROL knob and nothing else: it widens the trusted keyset with extra
    key material (a retired key not yet purged, or — for the decision-4 probes — another tenant's
    key, i.e. the repo-wide keyset the owner ruled out). Production never assembles a keyset this
    way; `build_verification_keyset` admits exactly the active key and one prior-in-grace key.
    """
    trusted, active_id = build_verification_keyset(active_key=active_key, prior_key=prior_key)
    for extra in also_trust:
        trusted[derive_key_id(extra)] = extra
    return EnvelopeVerifier(
        trusted_keys=trusted,
        active_key_id=active_id,
        current_epoch=epoch,
        max_signature_age=max_age,
        scheme=scheme,
        prior_epoch_grace_until=prior_epoch_grace_until,
    )


def _signer(
    *,
    tenant: str = _TENANT_A,
    key: bytes = _KEY_ACTIVE,
    epoch: int = _EPOCH,
    scheme: str = ENVELOPE_SIGNATURE_SCHEME,
) -> EnvelopeSigner:
    """An attacker's or an honest edge's signer. The ATTACKER cases pass key material the verifier
    does not trust (`_KEY_RETIRED`, `_KEY_TENANT_B`) or a forged `scheme`/`epoch` — modelling an
    adversary who holds key bytes, not one who holds our composition root's signer object."""
    return EnvelopeSigner(
        tenant=tenant, signing_key=key, key_id=derive_key_id(key), replay_epoch=epoch, scheme=scheme
    )


def _envelope(*, tenant: str = _TENANT_A, **kw: object) -> DelegationEnvelope:
    base: dict[str, object] = {
        "task_id": "task-signing-1",
        "task_type": "authorization.analyze",
        "origin": "helena",
        "target": "rafael",
        "tenant": tenant,
        "budget": Budget(tokens=64, time_ms=60_000, cost_per_hop=1),
        "payload_ref": "fhir://Coverage/signing-1",
        "deadline": _DEADLINE,
        "payload_meta": {"codigo_procedimento_tuss": "10101012", "valor_estimado_brl": "500.0"},
    }
    base.update(kw)
    return DelegationEnvelope.root(**base)  # type: ignore[arg-type]


def _signed(*, now: datetime = _NOW, **signer_kw: object) -> DelegationEnvelope:
    return _signer(**signer_kw).sign(_envelope(), now=now)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 0. Known-answer vector — the canonical preimage and the MAC, pinned byte-for-byte
# ---------------------------------------------------------------------------


def test_known_answer_vector_pins_the_v2_canonical_preimage_and_mac() -> None:
    """KAT. The canonical digest is the ONE thing signer and verifier must agree on byte-for-byte;
    a silent canonicalization change (key order, separators, a `default=` coercion, a renamed or
    dropped field) would keep every relational test green while changing what is actually signed.
    So the exact preimage and the exact MAC are hardcoded here, not recomputed from the code under
    test.

    PROVENANCE. Emitted 2026-08-12 by running the SHIPPED
    `envelope_canonical_digest(...)` / `hmac.new(...).hexdigest()` on this module's `_envelope()`
    with `key_id="env-golden-key-id"`, `replay_epoch=0`, `signed_at=_NOW`, key `_KEY_ACTIVE`.
    Note the `budget` values are 63/59999, not 64/60000: `DelegationEnvelope.root()` decrements one
    hop's worth of budget at construction, so the preimage covers the POST-decrement figures — a
    detail a hand-written expectation gets wrong and this vector therefore also pins.
    """
    expected_preimage = (
        b'{"budget":{"cost_per_hop":1,"time_ms":59999,"tokens":63},'
        b'"deadline":"2030-01-01T00:00:00+00:00",'
        b'"delegation_chain":["helena","rafael"],'
        b'"key_id":"env-golden-key-id",'
        b'"origin":"helena",'
        b'"payload_hash":"1fc06fe25a884922413e4d50507e8869b58d466c44f6287b951a306b8b2f540c",'
        b'"payload_meta_hash":"c5822af8d2b280efdfd63c9cfb9fc377f98f59ccddb64e50f964c0acd2427eae",'
        b'"replay_epoch":0,'
        b'"scheme":"hmac-sha256-envelope-v2",'
        b'"signed_at":"2026-08-12T12:00:00+00:00",'
        b'"target":"rafael",'
        b'"task_id":"task-signing-1",'
        b'"task_type":"authorization.analyze",'
        b'"tenant":"amh"}'
    )
    digest = envelope_canonical_digest(
        _envelope(),
        scheme=ENVELOPE_SIGNATURE_SCHEME,
        key_id="env-golden-key-id",
        replay_epoch=0,
        signed_at=_NOW,
    )
    assert digest == expected_preimage
    assert hmac.new(_KEY_ACTIVE, digest, sha256).hexdigest() == (
        "d5380b21969186d6bcebbe9bba10e810ceac2edce0eab7c8a4f7fce7d1cdacaf"
    )


def test_derive_key_id_vector_is_pinned() -> None:
    """The `key_id` is what the verifier resolves against its keyset, so a change in its derivation
    silently invalidates every in-flight signature. Pinned literal.

    PROVENANCE: `derive_key_id(_KEY_ACTIVE)`, shipped code, 2026-08-12."""
    assert derive_key_id(_KEY_ACTIVE) == "env-sha256:be0a9edad201c9ad"


# ---------------------------------------------------------------------------
# 1. Positive control — the real verifier is not "reject everything"
# ---------------------------------------------------------------------------


def test_positive_control_a_pristine_signature_verifies() -> None:
    """Rules out a vacuous verifier: a correctly-signed, current, unexpired envelope under the active
    key VERIFIES. Without this, every "rejects the tampered envelope" below would prove nothing."""
    assert _verifier().verify(_signed(), now=_NOW + timedelta(minutes=1)) is True


# ---------------------------------------------------------------------------
# 2. Tamper — any SIGNED field altered in transit (§4.5 "Tamper", digest v2 scope)
# ---------------------------------------------------------------------------

#: (label, tamper, outcome through a dispatcher with NO verifier wired).
#:
#: The third column is the load-bearing one. It records what each attack achieves against the system
#: as it stood BEFORE this train — and as it still stands under the §4.4 `MAEZO_A2A_ALLOW_UNVERIFIED_
#: ENVELOPES` dev opt-out. `None` = the forged envelope is EXECUTED and the handler receives the
#: attacker's values; a `RejectionReason` = some OTHER, later guard happens to catch it (disclosed
#: defense in depth — the signature is not the only thing refusing that particular shape).
#:
#: PROVENANCE: measured 2026-08-12 by delegating each tampered envelope through
#: `build_test_dispatcher(..., envelope_verifier=None)` against a single `make_card("rafael")`
#: registry. Hardcoded, not recomputed: this table is the non-vacuity evidence itself, so deriving it
#: from the code under test would make it prove nothing.
_TAMPER_BATTERY: tuple[tuple[str, object, RejectionReason | None], ...] = (
    ("origin spoofed", lambda e: replace(e, origin="mallory"), None),
    (
        "budget inflated",
        lambda e: replace(e, budget=Budget(tokens=999_999, time_ms=999_999, cost_per_hop=1)),
        None,
    ),
    ("delegation_chain rewritten", lambda e: replace(e, delegation_chain=("helena", "marina")), None),
    ("payload_ref swapped", lambda e: replace(e, payload_ref="fhir://Coverage/SWAPPED-BY-ATTACKER"), None),
    ("payload_meta rewritten", lambda e: replace(e, payload_meta={"prestador_id": "attacker"}), None),
    ("deadline extended", lambda e: replace(e, deadline=datetime(2099, 1, 1, tzinfo=UTC)), None),
    ("tenant retagged", lambda e: replace(e, tenant="attacker-tenant"), RejectionReason.UNKNOWN_TARGET),
    ("target redirected", lambda e: replace(e, target="marina"), RejectionReason.UNKNOWN_TARGET),
    (
        "task_type swapped",
        lambda e: replace(e, task_type="clinical.decision"),
        RejectionReason.TASK_TYPE_NOT_ACCEPTED,
    ),
)


@pytest.mark.parametrize(("label", "tamper", "_unwired"), _TAMPER_BATTERY)
def test_tampering_a_signed_field_is_rejected(label: str, tamper: object, _unwired: object) -> None:
    """§4.5 "Tamper". A signed field altered after signing moves the canonical preimage, so the MAC
    no longer matches and the real verifier REJECTS.

    Two assertions, because "the digest changed" and "the verifier refused" are different claims and
    only both together rule out a rejection that happens for some unrelated reason: (1) the tampered
    envelope's preimage genuinely differs from the pristine one (non-vacuity of the digest), and
    (2) the verifier returns False for the tampered envelope while returning True for the pristine
    one under the IDENTICAL call — so the MAC gate is demonstrably what refused.
    """
    verifier = _verifier()
    signed = _signed()
    tampered = tamper(signed)  # type: ignore[operator]
    assert tampered.signature is signed.signature, "the signature must ride along untouched"

    def preimage(env: DelegationEnvelope) -> bytes:
        assert env.signature is not None
        return envelope_canonical_digest(
            env,
            scheme=env.signature.scheme,
            key_id=env.signature.key_id,
            replay_epoch=env.signature.replay_epoch,
            signed_at=env.signature.signed_at,
        )

    assert preimage(tampered) != preimage(signed), f"{label}: the digest did not move"
    assert verifier.verify(tampered, now=_NOW + timedelta(minutes=1)) is False, f"{label} must be rejected"
    # The same verifier, same clock, pristine envelope -> True. The ONLY difference is the tamper.
    assert verifier.verify(signed, now=_NOW + timedelta(minutes=1)) is True


@pytest.mark.parametrize(("label", "tamper", "unwired_outcome"), _TAMPER_BATTERY)
async def test_tampered_envelope_is_refused_at_the_dispatcher_and_reaches_the_agent_without_it(
    label: str, tamper: object, unwired_outcome: RejectionReason | None
) -> None:
    """THE RED CONTROL FOR THE MAC — end to end, at the real chokepoint (§4.8 obligation 2).

    There is deliberately no knob that disables the MAC comparison, so the honest control is the
    system WITHOUT the verifier wired: the §4.4 dev-local opt-out, and the literal pre-E3 production
    configuration. Wired, every tamper is `SIGNATURE_INVALID` and the handler never runs. Unwired,
    `_TAMPER_BATTERY`'s third column says what happens instead — and for six of the nine shapes the
    forged envelope EXECUTES and the agent handler receives the attacker's own object.

    It also pins the §4.4 ORDERING claim: the wired rejection is `SIGNATURE_INVALID`, never the
    `UNKNOWN_TARGET` / `TASK_TYPE_NOT_ACCEPTED` that the structural guards would have produced for
    the last three rows. Verification really is the first check in `delegate` — if it ran after
    routing, those three would surface a different reason here.
    """
    tampered = tamper(_signer().sign(_envelope()))  # type: ignore[operator]

    wired_handler = FakeAgentHandler()
    wired, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": wired_handler}, envelope_verifier=_verifier()
    )
    result = await wired.delegate(tampered)
    assert result.success is False
    assert result.rejection_reason is RejectionReason.SIGNATURE_INVALID, f"{label}: wrong check refused"
    assert wired_handler.call_count == 0

    red_handler = FakeAgentHandler()
    unwired, _, _ = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": red_handler})
    red = await unwired.delegate(tampered)
    assert red.rejection_reason is unwired_outcome, f"{label}: unwired outcome drifted from the table"
    if unwired_outcome is None:
        assert red.success is True
        assert red_handler.calls == [tampered], f"{label}: the attacker's envelope reached the agent"
    else:
        assert red_handler.call_count == 0  # a DIFFERENT guard caught it — disclosed defense in depth


def test_altered_payload_hash_is_rejected() -> None:
    """§4.5 "Altered payload_hash". Swapping `payload_ref` after signing changes
    `payload_hash = sha256(payload_ref)` inside the preimage, so the MAC no longer matches.

    RED CONTROL: `_TAMPER_BATTERY`'s "payload_ref swapped" row records that this attack EXECUTES
    against an unwired dispatcher — no structural guard inspects `payload_ref`'s value, so the
    signature is the only thing binding the authorized FHIR reference to this envelope."""
    verifier, signed = _verifier(), _signed()
    swapped = replace(signed, payload_ref="fhir://Coverage/SWAPPED-BY-ATTACKER")
    assert verifier.verify(swapped, now=_NOW + timedelta(minutes=1)) is False
    assert verifier.verify(signed, now=_NOW + timedelta(minutes=1)) is True


def test_an_unsigned_envelope_is_rejected() -> None:
    """The fail-closed floor: no signature at all is a rejection, not a bypass."""
    assert _verifier().verify(_envelope(), now=_NOW) is False


# ---------------------------------------------------------------------------
# 3. Stale key / prior epoch / scheme downgrade (§4.2 metadata binding, §4.5 "Stale key")
# ---------------------------------------------------------------------------


def test_a_signature_under_a_retired_key_is_rejected() -> None:
    """§4.5 "Stale key": an envelope signed under a RETIRED key — one purged from the trusted keyset
    — is rejected, because the verifier resolves `key_id` against a KEYSET (not a single scalar) and
    an unknown id fails outright.

    RED CONTROL (`also_trust`, the "widened keyset" §4.8 obligation 2 names): the IDENTICAL envelope
    verifies against a verifier whose keyset still holds the retired key. So what refuses is keyset
    MEMBERSHIP — the operator having completed the purge. That is the operational point: retiring a
    key is only a defense once the key is actually gone.

    HONEST SCOPE, measured (leg E4's source-neuter campaign, neuter N8): the refusal here is
    OVER-DETERMINED, and the `key_id` LOOKUP is not the half that carries it. Widening the lookup so
    any claimed id resolves to the active key changes nothing — the retired key's MAC still fails,
    and no test in this suite moves. Only removing the MAC comparison AS WELL (combined neuter C3)
    breaks this test. So the honest claim is: the trusted keyset is the defense at the level of KEY
    MATERIAL, and the `key_id` lookup is a routing fast path over a MAC that would refuse anyway.
    `test_another_tenants_key_spoofing_this_tenants_key_id_still_fails_the_mac` pins that directly."""
    stale = _signed(key=_KEY_RETIRED)
    at = _NOW + timedelta(minutes=1)
    assert _verifier().verify(stale, now=at) is False
    assert _verifier(also_trust=(_KEY_RETIRED,)).verify(stale, now=at) is True  # RED: un-purged keyset


def test_a_prior_replay_epoch_is_hard_rejected() -> None:
    """§4.2 replay-epoch cutover: an envelope claiming a PRIOR epoch is rejected regardless of key
    validity — the blunt incident-response instrument for "trust nothing signed before now". The
    signature here is otherwise perfect: active key, fresh timestamp, untampered content.

    RED CONTROL (`epoch`, "ignoring the epoch"): the same envelope verifies against a verifier whose
    `current_epoch` is the attacker's. Only the epoch gate refuses it — the MAC cannot, because
    `replay_epoch` is bound INTO the digest and the verifier recomputes with the CLAIMED value."""
    old = _signed(epoch=_EPOCH - 1)
    at = _NOW + timedelta(minutes=1)
    assert _verifier(epoch=_EPOCH).verify(old, now=at) is False
    assert _verifier(epoch=_EPOCH - 1).verify(old, now=at) is True  # RED: epoch gate not looking


def test_a_scheme_downgrade_stripped_after_signing_is_rejected() -> None:
    """§4.2 scheme binding. A signature whose `scheme` tag is rewritten in transit is rejected.

    HONEST NOTE ON WHICH CHECK REFUSES: two independent gates fire here, and only one of them is the
    scheme gate. `scheme` is bound INTO the digest, so rewriting it post-signing ALSO breaks the MAC.
    The next test isolates the scheme gate properly, with an attack the MAC cannot catch."""
    signed = _signed()
    assert signed.signature is not None
    assert signed.signature.scheme == ENVELOPE_SIGNATURE_SCHEME
    downgraded = replace(signed, signature=replace(signed.signature, scheme="none"))
    assert _verifier().verify(downgraded, now=_NOW + timedelta(minutes=1)) is False


def test_a_mac_valid_scheme_downgrade_is_rejected_by_the_scheme_gate_alone() -> None:
    """THE REAL "alg:none" ANALOGUE — an attack the previous test does NOT cover, added by leg E4.

    The interesting adversary is not one who edits a scheme tag in transit (the MAC catches that);
    it is one who HOLDS A TRUSTED KEY and signs honestly under a DIFFERENT scheme string — a
    superseded `...-v1` tag, or `"none"`. Every byte then agrees: the MAC is computed over a preimage
    whose `"scheme"` really is `"none"`, and the verifier recomputes with that same claimed value, so
    the MAC VERIFIES. Nothing but the explicit `sig.scheme != self._scheme` gate stands between that
    envelope and the handler. This is the exact shape that made JWT `alg:none` exploitable: the
    algorithm identifier was authenticated by the very algorithm it named.

    RED CONTROL (`scheme=`, the shipped knob): a verifier configured for `"none"` ACCEPTS the same
    envelope — proving the MAC really is satisfied and the scheme gate really is the sole refusal.
    That is also why the scheme tag must be a CONSTANT in the verifier and never read from the
    signature: `scheme=sig.scheme` would be the fail-open this test exists to forbid."""
    forged = _signed(scheme="none")
    at = _NOW + timedelta(minutes=1)
    assert forged.signature is not None
    assert forged.signature.scheme == "none"
    assert _verifier().verify(forged, now=at) is False
    assert _verifier(scheme="none").verify(forged, now=at) is True  # RED: the MAC is genuinely valid


# ---------------------------------------------------------------------------
# 4. Expiry / max signature age (§4.3.1 + §4.3.2) — the captured-envelope-replayed-later attack
# ---------------------------------------------------------------------------


def test_a_signature_older_than_the_max_age_is_rejected() -> None:
    """§4.3.2: a validly-signed envelope CAPTURED and replayed after the max-signature-age window is
    rejected by the verifier-side age bound — the defense `deadline` cannot provide, since `deadline`
    is sender-chosen and (until §4.3.1) was `None` on every real envelope.

    RED CONTROL (`max_signature_age`, the shipped knob): widen the bound past the capture delay and
    the identical replay is ACCEPTED. The bound's VALUE is what refuses — nothing incidental."""
    signed = _signed()
    assert _verifier().verify(signed, now=_NOW + timedelta(minutes=1)) is True  # inside the window
    assert _verifier().verify(signed, now=_NOW + timedelta(minutes=30)) is False  # captured, replayed
    assert _verifier(max_age=timedelta(hours=1)).verify(signed, now=_NOW + timedelta(minutes=30)) is True


def test_a_future_dated_signature_is_rejected() -> None:
    """§4.3.2, the OTHER side of the bound: a signature timestamped in the future (a clock-skew
    forgery, or an attacker buying an arbitrarily long acceptance window) is refused too.

    RED CONTROL — DISCLOSED EXCEPTION: `age < timedelta(0)` has no configuration knob, so this is the
    one gate with no shipped-configuration control. Its non-vacuity is shown differentially instead:
    the SAME envelope, the SAME verifier, evaluated at a `now` AFTER `signed_at` verifies. Only the
    sign of `now - signed_at` differs, so the future-dating is demonstrably what refuses. (The
    transient source-neuter of this branch is recorded in the leg's commit body.)"""
    future = _signed(now=_NOW + timedelta(hours=1))
    assert _verifier().verify(future, now=_NOW) is False  # signed "in the future" relative to now
    assert _verifier().verify(future, now=_NOW + timedelta(hours=1, minutes=1)) is True


def test_a_signed_envelope_whose_deadline_is_stripped_is_rejected() -> None:
    """§4.3.1: a signed envelope MUST carry a non-None `deadline` — the constraint that stops §4.5's
    "Expiry bypass" row from being vacuous. Stripping it post-signing is refused.

    Disclosed, as above: `deadline` is also inside the digest, so the MAC refuses this too. The
    independent `deadline is None` gate is belt to the MAC's suspenders, and E3's
    `test_signed_envelope_with_deadline_none_is_rejected_by_verifier` owns pinning it directly."""
    assert _verifier().verify(replace(_signed(), deadline=None), now=_NOW + timedelta(minutes=1)) is False


# ---------------------------------------------------------------------------
# 5. THE FLIP — the two r1-r3 honest negatives, now the acceptance proof (§4.8 obligation 3)
# ---------------------------------------------------------------------------


def test_tampering_payload_meta_changes_the_digest_and_is_rejected() -> None:
    """THE ACCEPTANCE PROOF FOR OWNER DECISION 1 (ADR-0039 §4.8 obligation 3 / §Decisoes do dono,
    "Consequencia imediata").

    THIS TEST WAS THE HONEST NEGATIVE. Until 2026-08-12 it was named
    `test_disclosed_gap_tampering_unsigned_payload_meta_is_not_defended_today` and asserted the
    OPPOSITE: that mutating `payload_meta` left the digest BYTE-IDENTICAL, so even a working verifier
    accepted the tampered envelope. That was true and disclosed — `payload_meta` was outside the
    §4.1 mandated field set, which meant the TUSS code, `valor_estimado_brl`, the `prestador_id`
    Carolina turns into the CIB business key, and Andre's `valor_pagamento_cents` all travelled
    UNSIGNED. The owner's decision 1 adopted `payload_meta_hash`; leg E3 implemented it. The ADR
    pre-committed the acceptance criterion in the owner's own words: these two negatives must FLIP to
    positive assertions, and "se passarem sem virar, a mudanca de digest nao aconteceu, qualquer que
    seja o resto do verde". They are flipped in place rather than deleted, because the flip IS the
    evidence.

    RED CONTROL — the pre-decision digest, which is the strongest possible one here: the SAME
    mutation against the v1 (pre-decision) canonical digest still leaves it byte-identical. That is
    §4.8 obligation 2's "dropping the field from the digest", performed against the historical field
    set rather than a synthetic mutant, and it is the old honest negative preserved as a live
    control. See `reference_envelope_verifier.py` for that oracle's re-scoped charter.
    """
    env = _envelope()
    # An attacker rewrites the money and the business key inside payload_meta:
    tampered_meta = {
        "codigo_procedimento_tuss": "99999999",
        "valor_estimado_brl": "999999.0",
        "prestador_id": "attacker",
    }
    tampered = replace(env, payload_meta=tampered_meta)
    kw = {"scheme": ENVELOPE_SIGNATURE_SCHEME, "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}

    # FLIPPED (was `==`): the v2 digest MOVES.
    assert envelope_canonical_digest(tampered, **kw) != envelope_canonical_digest(env, **kw)
    # FLIPPED (was `is True`): the real verifier REJECTS.
    signed = _signer().sign(env, now=_NOW)
    at = _NOW + timedelta(minutes=1)
    assert _verifier().verify(replace(signed, payload_meta=tampered_meta), now=at) is False
    assert _verifier().verify(signed, now=at) is True  # ...and the untampered one still verifies

    # RED CONTROL: under the PRE-decision v1 digest the identical mutation is invisible.
    v1_kw = {"scheme": "v1", "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}
    assert reference_canonical_digest(tampered, **v1_kw) == reference_canonical_digest(env, **v1_kw)


def test_tampering_task_type_changes_the_digest_and_is_rejected() -> None:
    """THE ACCEPTANCE PROOF FOR OWNER DECISION 2 — the sibling flip.

    THIS TEST WAS THE HONEST NEGATIVE `test_disclosed_gap_tampering_unsigned_task_type_is_not_
    defended_today`, which asserted that swapping `task_type` left the digest byte-identical. It
    ranked below `payload_meta` only because admission separately cross-checks `task_type` against
    the target's Card — but the SIGNATURE did not bind it, so an attacker who could also satisfy the
    Card check could redirect the semantics of an authorized envelope. Decision 2 bound it directly.

    RED CONTROL, same shape as decision 1's: invisible to the pre-decision v1 digest. And note the
    complementary evidence in `_TAMPER_BATTERY`'s "task_type swapped" row — unwired, that shape is
    caught by `TASK_TYPE_NOT_ACCEPTED`, i.e. the Card cross-check; wired, it is `SIGNATURE_INVALID`,
    i.e. the signature now refuses it FIRST and independently. Both facts are the point.
    """
    env = _envelope()
    tampered = replace(env, task_type="clinical.decision")
    kw = {"scheme": ENVELOPE_SIGNATURE_SCHEME, "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}

    assert envelope_canonical_digest(tampered, **kw) != envelope_canonical_digest(env, **kw)  # FLIPPED
    signed = _signer().sign(env, now=_NOW)
    at = _NOW + timedelta(minutes=1)
    assert _verifier().verify(replace(signed, task_type="clinical.decision"), now=at) is False  # FLIPPED
    assert _verifier().verify(signed, now=at) is True

    v1_kw = {"scheme": "v1", "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}
    assert reference_canonical_digest(tampered, **v1_kw) == reference_canonical_digest(env, **v1_kw)


# ---------------------------------------------------------------------------
# 6. Rotation grace (§4.3 + decisions 3/5) — what the grace window buys, and what it costs
# ---------------------------------------------------------------------------


def test_a_prior_key_signature_is_accepted_inside_the_rotation_grace_and_rejected_after() -> None:
    """§4.3 rotation grace, stated as the attack it enables rather than as a feature.

    An envelope signed under the SUPERSEDED key verifies while that key sits in the trusted keyset,
    and stops verifying the moment it is deprovisioned. The security reading: **the grace window is
    also a forgery window.** Anyone holding the superseded key material can mint accepted envelopes
    for exactly as long as the operator leaves the prior variable provisioned — which is why §4.3's
    revocation procedure is "purge the key AND bump the epoch", not "wait for the grace to lapse".

    HOW LONG IS THE WINDOW? The keyset has NO clock — `build_verification_keyset` trusts the prior
    key iff `prior_key is not None`, so the window is open exactly while the operator keeps
    `MAEZO_A2A_CARD_SIGNING_KEY_PRIOR__<TENANT>` provisioned. The INDEPENDENT time bound is
    `MAX_SIGNATURE_AGE` (7 days), pinned with its boundary at `tests/unit/a2a/test_keyset.py:489-494`
    and NOT re-pinned here. The operator's 7-day deprovision cadence and that 7-day age bound are the
    two halves of the same number (§4.3 item 2); this test owns only the provisioning half."""
    prior_signed = _signed(key=_KEY_PRIOR)
    at = _NOW + timedelta(minutes=1)
    assert _verifier(prior_key=_KEY_PRIOR).verify(prior_signed, now=at) is True  # inside the grace
    assert _verifier(prior_key=None).verify(prior_signed, now=at) is False  # deprovisioned
    # ...and the ACTIVE key is unaffected by the rotation state (rules out a keyset that broke).
    assert _verifier(prior_key=_KEY_PRIOR).verify(_signed(), now=at) is True


def test_an_epoch_bump_revokes_a_prior_key_signature_even_inside_its_key_grace() -> None:
    """§4.3 REVOCATION — the two instruments compose, and the epoch bump dominates.

    Suspected compromise of the superseded key: the operator purges the `key_id` AND bumps
    `replay_epoch` "in the same action", because purging alone is insufficient if the attacker could
    forge under a still-trusted id from the same epoch. This test proves the converse ordering is
    also safe — that an epoch bump ALONE hard-rejects a prior-key signature even if the operator has
    not yet managed to deprovision the prior variable. So the incident responder's first action has
    effect immediately, without waiting on a config rollout.

    RED CONTROL: without the bump (`epoch=_EPOCH`) the same envelope is accepted — the key grace on
    its own would have let it through."""
    prior_signed = _signed(key=_KEY_PRIOR, epoch=_EPOCH)
    at = _NOW + timedelta(minutes=1)
    bumped = _verifier(prior_key=_KEY_PRIOR, epoch=_EPOCH + 1)  # key STILL provisioned, epoch bumped
    assert bumped.verify(prior_signed, now=at) is False
    assert _verifier(prior_key=_KEY_PRIOR, epoch=_EPOCH).verify(prior_signed, now=at) is True  # RED


def test_a_prior_epoch_replay_is_accepted_inside_the_grace_window_and_rejected_beyond_it() -> None:
    """§4.2 + owner decision 5. The owner granted a SHORT prior-epoch grace so an epoch bump does not
    mass-reject envelopes already in flight. Stated as the attack: during that window a captured
    pre-bump envelope IS still accepted — the grace is a disclosed, bounded replay window, and the
    zero-grace default is what an incident responder must use when that is unacceptable.

    Three assertions pin all three regimes: inside the window -> accepted; past it -> rejected; two
    epochs back -> NEVER graced (the grace is for the immediately-prior epoch only, so it cannot be
    walked backwards to resurrect arbitrarily old signatures)."""
    graced = _verifier(epoch=_EPOCH + 1, prior_epoch_grace_until=_NOW + timedelta(minutes=10))
    pre_bump = _signed(epoch=_EPOCH)
    assert graced.verify(pre_bump, now=_NOW + timedelta(minutes=1)) is True
    assert graced.verify(pre_bump, now=_NOW + timedelta(minutes=30)) is False
    assert graced.verify(_signed(epoch=_EPOCH - 1), now=_NOW + timedelta(minutes=1)) is False


def test_the_default_epoch_grace_is_zero_which_is_the_incident_semantics() -> None:
    """Decision 5's other half: "a semantica de graca-zero para incidente segue tendo de ser
    enunciavel". The DEFAULT is zero grace — a verifier built without an explicit
    `prior_epoch_grace_until` hard-rejects the immediately-prior epoch, so an operator who bumps the
    epoch and configures nothing else gets the strict cutover, not a silent tolerance window.

    RED CONTROL: the identical envelope against the identical verifier WITH a grace window opens ->
    accepted. The default value is what refuses."""
    pre_bump = _signed(epoch=_EPOCH)
    at = _NOW + timedelta(minutes=1)
    assert _verifier(epoch=_EPOCH + 1).verify(pre_bump, now=at) is False  # zero grace by default
    assert _verifier(epoch=_EPOCH + 1, prior_epoch_grace_until=at).verify(pre_bump, now=at) is True


# ---------------------------------------------------------------------------
# 7. Cross-tenant KEY confusion (§4.4 + owner decision 4) — the attack per-tenant custody prevents
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slot", ["active", "prior"])
def test_another_tenants_key_cannot_forge_this_tenants_envelope(slot: str) -> None:
    """OWNER DECISION 4, MADE INTO AN ATTACK. Keys are per tenant, never repo-wide. The attack that
    rules out is key confusion: an insider or a compromise that yields TENANT B's key material
    minting envelopes accepted on TENANT A's edge.

    The adversary model is deliberately generous — they hold B's key BYTES, not our composition
    root's signer object, so `EnvelopeSigner`'s own wrong-tenant refusal (which only guards our own
    code path) does not protect us here. They construct a signer that CLAIMS tenant A and holds B's
    key, and sign a perfectly well-formed tenant-A envelope. Tenant A's verifier refuses it: B's
    `key_id` is not in A's keyset, and B's key could not produce a MAC A's key validates.

    Both rotation slots are covered (`slot`): decision 4 would be half-implemented if the per-tenant
    boundary held for active keys but the prior-in-grace slot admitted a foreign one.

    RED CONTROL — and this one is the whole argument for decision 4: `also_trust` builds the
    REPO-WIDE keyset the owner rejected (one keyset trusting several tenants' keys), and against it
    the forgery is ACCEPTED. That is the blast radius in a single assertion: with a shared key, a
    compromise anywhere forges everywhere."""
    forger_key = _KEY_TENANT_B
    forged = EnvelopeSigner(
        tenant=_TENANT_A,  # the attacker CLAIMS tenant A...
        signing_key=forger_key,  # ...while holding tenant B's key material
        key_id=derive_key_id(forger_key),
        replay_epoch=_EPOCH,
    ).sign(_envelope(tenant=_TENANT_A), now=_NOW)
    at = _NOW + timedelta(minutes=1)

    tenant_a = _verifier(prior_key=_KEY_PRIOR if slot == "prior" else None)
    assert tenant_a.verify(forged, now=at) is False
    # RED: the repo-wide keyset decision 4 rules out accepts tenant B's forgery on tenant A's edge.
    assert _verifier(also_trust=(forger_key,)).verify(forged, now=at) is True


def test_another_tenants_key_spoofing_this_tenants_key_id_still_fails_the_mac() -> None:
    """The sharper cross-tenant variant: the attacker knows tenant A's `key_id` (it is a NON-SECRET
    fingerprint by design — `derive_key_id` publishes 16 hex of `sha256(key)`) and stamps it onto a
    signature produced with tenant B's key. The keyset lookup now SUCCEEDS — the claimed id is
    trusted — so the `key_id` gate cannot refuse this one, and the MAC has to.

    That is the honest statement about layering: the `key_id` gate is a routing/fast-path check, not
    the security boundary. Publishing `key_id` is safe precisely because a claimed id buys nothing
    without the key bytes behind it — which is what this test pins.

    RED CONTROL: the same envelope signed with the key that id actually names verifies. Only the key
    MATERIAL differs between the accepted and the rejected case."""
    a_key_id = derive_key_id(_KEY_ACTIVE)
    at = _NOW + timedelta(minutes=1)
    spoofed = EnvelopeSigner(
        tenant=_TENANT_A, signing_key=_KEY_TENANT_B, key_id=a_key_id, replay_epoch=_EPOCH
    ).sign(_envelope(), now=_NOW)
    assert spoofed.signature is not None
    assert spoofed.signature.key_id == a_key_id  # the keyset lookup will succeed...
    assert _verifier().verify(spoofed, now=at) is False  # ...and the MAC still refuses
    # RED: identical construction with the RIGHT key material verifies.
    honest = EnvelopeSigner(
        tenant=_TENANT_A, signing_key=_KEY_ACTIVE, key_id=a_key_id, replay_epoch=_EPOCH
    ).sign(_envelope(), now=_NOW)
    assert _verifier().verify(honest, now=at) is True


# ---------------------------------------------------------------------------
# 8. Attack surface the digest-v2 / rotation design itself invites (leg E4's own additions)
# ---------------------------------------------------------------------------


def test_no_digest_or_hash_field_travels_on_the_wire_for_an_attacker_to_supply() -> None:
    """PREIMAGE-SUBSTITUTION SURFACE, CLOSED BY CONSTRUCTION — pinned so it stays closed.

    `payload_meta_hash` binds `payload_meta` only because the verifier RECOMPUTES it from the
    envelope's own content. If the hash instead travelled in `EnvelopeSignature` (the way a JWS
    header carries its own claims), an attacker could rewrite `payload_meta` AND supply the matching
    hash, and the MAC — computed over the supplied hash — would still verify. The mutation would
    become invisible again and decision 1 would be undone without a line of the verifier changing.

    So the signature metadata must carry NO content-derived value: exactly the four §4.2/§4.3.2
    metadata fields plus the MAC. Hardcoded (PROVENANCE: `dataclasses.fields(EnvelopeSignature)`,
    `src/maezo/a2a/delegation.py:112-116`, 2026-08-12) rather than asserted as a subset, so ADDING a
    field fails this test and forces the author to re-reason about this attack."""
    assert tuple(f.name for f in fields(EnvelopeSignature)) == (
        "scheme",
        "key_id",
        "replay_epoch",
        "signed_at",
        "mac",
    )


def test_payload_meta_canonicalization_resists_delimiter_injection() -> None:
    """CANONICALIZATION AMBIGUITY. `payload_meta_hash` hashes a JSON serialization, so the question
    every hash-of-a-serialization must answer is whether two DIFFERENT mappings can serialize to the
    same bytes. If they could, an attacker could swap in a colliding `payload_meta` and keep the MAC.

    The classic break is delimiter injection: a VALUE containing the separators (`","`, `":"`) that
    reassembles into a different key/value structure. `json.dumps` escapes them, so it cannot. Both
    directions are checked — a value that mimics the separators, and a KEY that does."""
    kw = {"scheme": ENVELOPE_SIGNATURE_SCHEME, "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}
    smuggled_value = _envelope(payload_meta={"a": '1","b":"2'})
    two_honest_keys = _envelope(payload_meta={"a": "1", "b": "2"})
    assert envelope_canonical_digest(smuggled_value, **kw) != envelope_canonical_digest(two_honest_keys, **kw)
    smuggled_key = _envelope(payload_meta={'a":"1","b': "2"})
    assert envelope_canonical_digest(smuggled_key, **kw) != envelope_canonical_digest(two_honest_keys, **kw)
    # ...and key ORDER is genuinely irrelevant (sort_keys), so the resistance is not accidental
    # strictness that would break honest re-serialization.
    assert envelope_canonical_digest(_envelope(payload_meta={"b": "2", "a": "1"}), **kw) == (
        envelope_canonical_digest(two_honest_keys, **kw)
    )


def test_task_type_type_confusion_does_not_collide_in_the_digest() -> None:
    """TYPE CONFUSION. `task_type` is bound DIRECTLY into the digest (decision 2), not hashed, so it
    is the field where a JSON type change could matter: the integer `123` and the string `"123"` are
    the same value to a lax reader. They must not produce the same preimage — otherwise a
    deserializer that types the field differently from the signer would create a forgery gap.
    `json.dumps` emits `123` vs `"123"`, so they differ. (`task_type: str` is the declared type; this
    pins the behaviour for a wire deserializer that does not enforce it.)"""
    kw = {"scheme": ENVELOPE_SIGNATURE_SCHEME, "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}
    as_int = envelope_canonical_digest(_envelope(task_type=123), **kw)
    as_str = envelope_canonical_digest(_envelope(task_type="123"), **kw)
    assert as_int != as_str


def test_payload_meta_and_payload_hash_cannot_be_swapped_for_one_another() -> None:
    """FIELD-SWAP / CROSS-BINDING. Both hashes are 64 hex characters under distinct keys. An attacker
    who could get the verifier to read one where the other belongs would break the binding of
    whichever field lost its slot. The canonical JSON names them explicitly, so a swap moves the
    preimage — pinned by constructing an envelope whose two hash inputs are deliberately swapped."""
    kw = {"scheme": ENVELOPE_SIGNATURE_SCHEME, "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}
    normal = _envelope(payload_ref="fhir://Coverage/A", payload_meta={"x": "B"})
    swapped = _envelope(payload_ref="fhir://Coverage/B", payload_meta={"x": "A"})
    assert envelope_canonical_digest(normal, **kw) != envelope_canonical_digest(swapped, **kw)


# ---------------------------------------------------------------------------
# 9. THE TOTALITY FLIP — `verify` refuses malformed wire input instead of raising
# ---------------------------------------------------------------------------
#
# These three were this leg's HONEST NEGATIVES: they pinned a real, disclosed defect
# (`EnvelopeVerifier.verify` raised `TypeError` on a naive `signed_at` and on a non-JSON-native
# `payload_meta`, breaking its own documented never-raises contract, and the `TypeError` escaped
# `dispatcher.delegate` to the caller). The E4-repair leg fixed the source, so each now asserts the
# POSITIVE form the honest negatives were written to flip into: a REFUSAL (`is False` / a
# `SIGNATURE_INVALID` result), never an exception. Each keeps the pre-fix behaviour as an explicit
# RED CONTROL, so the flip cannot pass vacuously.


def test_a_naive_signed_at_is_refused_not_raised() -> None:
    """FLIPPED (was `test_disclosed_defect_a_naive_signed_at_raises_out_of_verify`).

    `EnvelopeSignature.signed_at` is typed `datetime` with no tz-awareness validation
    (`delegation.py`), so a NAIVE timestamp — perfectly type-valid, requiring no type violation, and
    exactly what a wire deserializer produces from a timestamp without an offset — reaches `verify`.
    It used to make `age = now - sig.signed_at` raise `TypeError`. It is now a REFUSAL, matching the
    sibling malformed-input case that was always guarded (`sig.mac` non-ASCII returns False, pinned
    by E3's `test_malformed_non_ascii_mac_is_rejected_not_raised`).

    The refusal is REJECTION, not assume-UTC: coercing a naive timestamp to UTC would let the
    signing party shift its signature's apparent age just by dropping the offset, negotiating its
    way around the §4.3.2 max-age bound it is supposed to be constrained by."""
    signed = _signed()
    assert signed.signature is not None
    naive = replace(signed, signature=replace(signed.signature, signed_at=_NOW.replace(tzinfo=None)))
    assert _verifier().verify(naive, now=_NOW + timedelta(minutes=1)) is False

    # RED CONTROL: the pre-fix behaviour, i.e. the raw operation the guard now short-circuits. Naive
    # MINUS aware still raises — so the `False` above is the new guard doing work, not the arithmetic
    # having quietly become total.
    with pytest.raises(TypeError, match="offset-naive and offset-aware"):
        _ = (_NOW + timedelta(minutes=1)) - naive.signature.signed_at
    # ...and the CONTROL in the other direction: the same envelope with an AWARE signed_at verifies,
    # so the refusal is the tz guard, not a broken signature.
    assert _verifier().verify(signed, now=_NOW + timedelta(minutes=1)) is True


async def test_a_naive_signed_at_is_a_signature_invalid_rejection_not_a_dispatcher_crash() -> None:
    """FLIPPED (was `test_disclosed_defect_the_naive_signed_at_typeerror_escapes_the_dispatcher`).

    The blast radius that actually matters: the defect did not stay inside `verify` — a wired
    dispatcher raised `TypeError` to its caller instead of returning `SIGNATURE_INVALID`, so a forged
    envelope was a crash rather than a rejection. Pinned end to end because the contract that was
    broken (`dispatcher.py`, "never raised to the caller") is the dispatcher's, not the verifier's.
    The handler must never run either."""
    signed = _signer().sign(_envelope())
    assert signed.signature is not None
    naive = replace(signed, signature=replace(signed.signature, signed_at=datetime(2026, 8, 12, 12, 0)))
    handler = FakeAgentHandler()
    dispatcher, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}, envelope_verifier=_verifier()
    )
    result = await dispatcher.delegate(naive)
    assert result.success is False
    assert result.rejection_reason is RejectionReason.SIGNATURE_INVALID
    assert handler.call_count == 0  # a malformed envelope never reaches the handler


def test_a_non_json_native_payload_meta_is_refused_not_raised() -> None:
    """FLIPPED (was `test_disclosed_defect_a_non_json_native_payload_meta_raises_out_of_verify`).

    The SECOND instance of the same family. `payload_meta` is declared `Mapping[str, str]` and §4.1
    deliberately forbids `default=` so a non-JSON-native value RAISES rather than being silently
    coerced. That is still exactly right at SIGN time — the signer controls the content, so it must
    fail closed and loudly, and the first assertion pins that it STILL does. At VERIFY time the same
    input is now a refusal.

    Note what did NOT change: nothing is coerced. §4.1's "no `default=`" is intact; the envelope is
    simply rejected as unverifiable."""
    non_native = {"when": datetime(2026, 1, 1, tzinfo=UTC)}
    with pytest.raises(TypeError):  # SIGN-time refusal: correct, fail-closed (§4.1 "no default=")
        _signer().sign(_envelope(payload_meta=non_native), now=_NOW)
    tampered = replace(_signed(), payload_meta=non_native)
    assert _verifier().verify(tampered, now=_NOW + timedelta(minutes=1)) is False

    # RED CONTROL: the canonicalizer itself still raises on this input — the `False` above is the
    # verification boundary catching it, not §4.1 having been loosened into coercion.
    with pytest.raises(TypeError, match="not JSON serializable"):
        envelope_canonical_digest(
            tampered,
            scheme=ENVELOPE_SIGNATURE_SCHEME,
            key_id="kid",
            replay_epoch=0,
            signed_at=_NOW,
        )


def test_the_signer_refuses_to_stamp_a_naive_signed_at() -> None:
    """THE SIGNING-SIDE HALF of the same repair. The verifier now (correctly) refuses a naive
    `signed_at`, so a signer able to EMIT one would produce envelopes nothing can ever accept — and
    worse, the digest's `signed_at.astimezone(UTC)` would silently re-read a naive value as LOCAL
    time, making the preimage host-dependent. The signer therefore refuses at sign time, which is
    fail-closed in the honest direction: the error lands on the side that can fix it."""
    with pytest.raises(EnvelopeSignatureError, match="naive"):
        _signer().sign(_envelope(), now=datetime(2026, 8, 12, 12, 0))
    # CONTROL: the aware form of the SAME instant signs fine, and the default (no `now=`) is aware.
    assert _signer().sign(_envelope(), now=_NOW).signature is not None
    stamped = _signer().sign(_envelope()).signature
    assert stamped is not None and stamped.signed_at.tzinfo is not None


#: What a JSON deserializer drops into the `signature` slot when it rebuilds an envelope off the
#: wire. `DelegationEnvelope.signature` is typed `EnvelopeSignature | None` and validated nowhere
#: (`delegation.py.__post_init__` checks task_id/tenant/max_hops/deadline/payload_ref, not this), so
#: every one of these is reachable without a single type violation in production code.
_NON_SIGNATURE_CONTAINERS: tuple[tuple[str, object], ...] = (
    (
        "dict",
        {
            "scheme": ENVELOPE_SIGNATURE_SCHEME,
            "key_id": "kid",
            "replay_epoch": 0,
            "signed_at": "2026-08-12T12:00:00+00:00",
            "mac": "deadbeef",
        },
    ),
    ("str", f"{ENVELOPE_SIGNATURE_SCHEME}:deadbeef"),
    ("int", 12345),
    ("list", [ENVELOPE_SIGNATURE_SCHEME, "deadbeef"]),
    ("bool", True),
)


@pytest.mark.parametrize(("label", "container"), _NON_SIGNATURE_CONTAINERS)
def test_a_non_envelope_signature_container_is_refused_not_raised(label: str, container: object) -> None:
    """TOTALITY over the signature SLOT'S OWN TYPE (post-merge audit finding).

    The guards above all harden an INNER field (`key_id`, `signed_at`, `mac`, the digest family) —
    but every one of them dereferences `sig` first. The CONTAINER itself went unguarded, so a
    `signature` holding a dict/str/int/list/bool made `sig.scheme` raise `AttributeError` straight
    out of `verify`, breaking the same never-raises contract the rest of §9 restored. A signature
    that is not an `EnvelopeSignature` is not a signature: it REFUSES."""
    forged = replace(_signed(), signature=container)  # type: ignore[arg-type]
    assert _verifier().verify(forged, now=_NOW + timedelta(minutes=1)) is False, label


@pytest.mark.parametrize(("label", "container"), _NON_SIGNATURE_CONTAINERS[:3])
async def test_a_non_envelope_signature_container_is_signature_invalid_not_a_dispatcher_crash(
    label: str, container: object
) -> None:
    """The blast radius that matters, same as the naive-`signed_at` half: the `AttributeError` did
    not stay inside `verify` — it escaped `dispatcher.delegate` to the caller, so a wire-mangled
    envelope was a crash rather than a rejection. It is now a structured `SIGNATURE_INVALID`, and
    the handler never runs."""
    forged = replace(_signed(), signature=container)  # type: ignore[arg-type]
    handler = FakeAgentHandler()
    dispatcher, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}, envelope_verifier=_verifier()
    )
    result = await dispatcher.delegate(forged)
    assert result.success is False, label
    assert result.rejection_reason is RejectionReason.SIGNATURE_INVALID, label
    assert handler.call_count == 0, label


def test_the_signature_container_guard_is_what_refuses_not_a_downstream_gate() -> None:
    """NON-VACUITY. Each refusal above must come from the CONTAINER guard, not from some later gate
    that happens to reject anyway. Pinned two ways: (a) the raw dereference the guard short-circuits
    still raises, so the `False` is the guard doing work; (b) a REAL `EnvelopeSignature` carrying the
    very same field values verifies True through the same verifier — so the container type is the
    only difference between acceptance and refusal."""
    signed = _signed()
    assert signed.signature is not None
    as_dict = {
        "scheme": signed.signature.scheme,
        "key_id": signed.signature.key_id,
        "replay_epoch": signed.signature.replay_epoch,
        "signed_at": signed.signature.signed_at,
        "mac": signed.signature.mac,
    }
    # (a) the pre-fix behaviour: dereferencing the dict as if it were a signature still raises.
    with pytest.raises(AttributeError, match="'dict' object has no attribute 'scheme'"):
        _ = as_dict.scheme  # type: ignore[attr-defined]
    assert _verifier().verify(replace(signed, signature=as_dict), now=_NOW) is False  # type: ignore[arg-type]
    # (b) the identical content in the RIGHT container verifies — the refusal is the type, not the
    # values, and the guard has not broken the happy path.
    assert _verifier().verify(replace(signed, signature=EnvelopeSignature(**as_dict)), now=_NOW) is True


# ---------------------------------------------------------------------------
# 10. The re-scoped reference oracle — differential conformance against the real digest
# ---------------------------------------------------------------------------

#: The §4.1 v1 mandated field set — the fields the PRE-decision digest bound, and therefore the
#: fields on which the v1 oracle and the shipped v2 digest MUST still agree. Hardcoded from the ADR
#: text (§4.1 as written in r1-r3, quoted in `reference_envelope_verifier.py`'s module docstring),
#: not derived from either implementation — a table derived from the code it audits audits nothing.
_V1_MUTATIONS: tuple[tuple[str, object], ...] = (
    ("tenant", lambda e: replace(e, tenant="attacker-tenant")),
    ("task_id", lambda e: replace(e, task_id="task-other")),
    ("origin", lambda e: replace(e, origin="mallory")),
    ("target", lambda e: replace(e, target="marina")),
    ("payload_hash", lambda e: replace(e, payload_ref="fhir://Coverage/OTHER")),
    ("deadline", lambda e: replace(e, deadline=datetime(2099, 1, 1, tzinfo=UTC))),
    ("budget", lambda e: replace(e, budget=Budget(tokens=999, time_ms=999, cost_per_hop=1))),
    ("delegation_chain", lambda e: replace(e, delegation_chain=("helena", "marina"))),
)


@pytest.mark.parametrize(("field_name", "mutate"), _V1_MUTATIONS)
def test_the_v1_oracle_and_the_shipped_v2_digest_agree_on_every_v1_field(
    field_name: str, mutate: object
) -> None:
    """DIFFERENTIAL CONFORMANCE. The shipped digest must be a strict EXTENSION of the ADR's v1 field
    set, not a rewrite of it: every field v1 bound, v2 still binds. Mutating each in turn must move
    BOTH digests.

    This is what the reference verifier is FOR now. It is no longer a stand-in for absent production
    code (production code exists); it is an independently-written implementation of the ADR text as
    ratified in r1-r3, and running it alongside the shipped digest catches the failure mode a
    single-implementation test cannot: a field quietly dropped from the preimage while every
    self-referential assertion stays green.
    """
    env = _envelope()
    mutated = mutate(env)  # type: ignore[operator]
    v2 = {"scheme": ENVELOPE_SIGNATURE_SCHEME, "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}
    v1 = {"scheme": "v1", "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}
    assert envelope_canonical_digest(mutated, **v2) != envelope_canonical_digest(env, **v2), field_name
    assert reference_canonical_digest(mutated, **v1) != reference_canonical_digest(env, **v1), field_name


def test_the_v2_digest_binds_exactly_two_fields_more_than_the_v1_oracle() -> None:
    """...and the extension is EXACTLY the owner's two decisions — no more, no less.

    The previous test proves v2 ⊇ v1. This one bounds the difference: `payload_meta` and `task_type`
    move the v2 digest and NOT the v1 one (decisions 1-2), and the v2 field set is precisely the v1
    field set plus `payload_meta_hash` and `task_type`. A third silently-added field would be an
    unratified change to what gets signed, and would fail here."""
    import json

    v2 = {"scheme": ENVELOPE_SIGNATURE_SCHEME, "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}
    v1 = {"scheme": "v1", "key_id": "kid", "replay_epoch": 0, "signed_at": _NOW}
    v2_keys = set(json.loads(envelope_canonical_digest(_envelope(), **v2)))
    v1_keys = set(json.loads(reference_canonical_digest(_envelope(), **v1)))
    assert v2_keys - v1_keys == {"payload_meta_hash", "task_type"}
    assert v1_keys - v2_keys == set()


def _reference_verifier() -> LabeledReferenceEnvelopeVerifier:
    """The v1 oracle, configured with the SAME trust parameters as `_verifier()` so the only thing
    that can differ between the two is their verification LOGIC, not their configuration."""
    return LabeledReferenceEnvelopeVerifier(
        trusted_keys={derive_key_id(_KEY_ACTIVE): _KEY_ACTIVE},
        active_key_id=derive_key_id(_KEY_ACTIVE),
        current_epoch=_EPOCH,
        max_signature_age=_MAX_AGE,
    )


def _ref_tamper(signed: ReferenceSignedEnvelope, **mutation: object) -> ReferenceSignedEnvelope:
    return replace(signed, envelope=replace(signed.envelope, **mutation))


#: (label, real-side envelope builder, oracle-side signed builder, evaluation clock, expected verdict).
#: Every scenario stays inside the fields and gates BOTH implementations share (the v1 field set +
#: scheme/key/epoch/age/MAC) — the two v2-only fields are excluded by construction, because there the
#: implementations are SUPPOSED to disagree and that disagreement is pinned separately, above.
_DIFFERENTIAL_SCENARIOS: tuple[tuple[str, object, object, datetime, bool], ...] = (
    (
        "pristine",
        lambda: _signed(),
        lambda r: r.sign(_envelope(), signed_at=_NOW),
        _NOW + timedelta(minutes=1),
        True,
    ),
    (
        "tampered v1 field (origin)",
        lambda: replace(_signed(), origin="mallory"),
        lambda r: _ref_tamper(r.sign(_envelope(), signed_at=_NOW), origin="mallory"),
        _NOW + timedelta(minutes=1),
        False,
    ),
    (
        "retired key",
        lambda: _signed(key=_KEY_RETIRED),
        lambda r: r.sign(
            _envelope(), key_id=derive_key_id(_KEY_RETIRED), signing_key=_KEY_RETIRED, signed_at=_NOW
        ),
        _NOW + timedelta(minutes=1),
        False,
    ),
    (
        "prior replay epoch",
        lambda: _signed(epoch=_EPOCH - 1),
        lambda r: r.sign(_envelope(), replay_epoch=_EPOCH - 1, signed_at=_NOW),
        _NOW + timedelta(minutes=1),
        False,
    ),
    (
        "captured and replayed past the max age",
        lambda: _signed(),
        lambda r: r.sign(_envelope(), signed_at=_NOW),
        _NOW + timedelta(minutes=30),
        False,
    ),
    (
        "future-dated signature",
        lambda: _signed(now=_NOW + timedelta(hours=1)),
        lambda r: r.sign(_envelope(), signed_at=_NOW + timedelta(hours=1)),
        _NOW,
        False,
    ),
)


@pytest.mark.parametrize(("label", "build_real", "build_ref", "at", "expected"), _DIFFERENTIAL_SCENARIOS)
def test_the_shipped_verifier_and_the_v1_oracle_reach_the_same_verdict(
    label: str, build_real: object, build_ref: object, at: datetime, expected: bool
) -> None:
    """VERDICT-LEVEL DIFFERENTIAL — two independently-written verifiers, one battery, same answers.

    The digest-level differential above proves the two agree on what is SIGNED. This proves they
    agree on what is ACCEPTED, which is a different claim: a verifier can bind every field correctly
    and still skip a gate. Each row is evaluated against both implementations and against a
    HARDCODED expected verdict, so the test does not merely assert the two agree — two implementations
    can agree by both being wrong — but that they agree ON THE RIGHT ANSWER.

    Scope is deliberately the shared surface (v1 fields + scheme/key/epoch/age/MAC). Gates the
    shipped verifier added and the oracle never had — `deadline is None` (§4.3.1) and the
    prior-epoch grace (decision 5) — are excluded here and owned by their own tests above; asserting
    agreement on those would be asserting the oracle knows about decisions made after it was frozen.
    """
    real_verdict = _verifier().verify(build_real(), now=at)  # type: ignore[operator]
    ref_verdict = _reference_verifier().verify(build_ref(_reference_verifier()), now=at)  # type: ignore[operator]
    assert real_verdict is expected, f"{label}: shipped verifier disagreed with the pinned verdict"
    assert ref_verdict is expected, f"{label}: v1 oracle disagreed with the pinned verdict"


# ---------------------------------------------------------------------------
# 11. Anti-masquerade guards — the oracle stays quarantined; signing really is implemented
# ---------------------------------------------------------------------------

_SRC_MAEZO = Path(__file__).resolve().parents[4] / "src" / "maezo"


def test_no_production_module_references_the_reference_verifier() -> None:
    """ANTI-MASQUERADE, RETAINED. The v1 oracle must NEVER be reachable from production — its whole
    value is that it is written independently of the shipped code, which a production import would
    destroy. A text sweep of the ENTIRE `src/maezo` tree asserts zero references, so the
    effect-chokepoint fence's "no test double in a composition root" invariant is backed by a direct
    reachability proof, not merely by the fence scanning `src/` (which it also does).

    `NeuteredReferenceEnvelopeVerifier` is listed although leg E4 RETIRED the class (its
    accept-everything role was replaced by targeted, shipped-knob controls — see this module's
    docstring). Keeping the token fenced costs nothing and stops the name returning to production."""
    forbidden = (
        "LabeledReferenceEnvelopeVerifier",
        "NeuteredReferenceEnvelopeVerifier",
        "ReferenceSignedEnvelope",
        "reference_envelope_verifier",
        "tests.unit.a2a.attacks",
    )
    hits: list[str] = []
    for path in _SRC_MAEZO.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits.extend(f"{path}: {token}" for token in forbidden if token in text)
    assert hits == [], f"reference verifier leaked into production source: {hits}"


def test_envelope_signing_is_now_implemented_in_production() -> None:
    """FLIPPED from the r1-r3 anti-masquerade negative (leg E3, ADR-0039 Accepted).

    RATIONALE (disclosed): envelope signing is NO LONGER unimplemented. This test used to be the
    honest negative proof that it was NOT done (`DelegationEnvelope` carried no `signature` field,
    and the §4.4 opt-out was wired nowhere). Both facts are now, by design, FALSE: the change that
    falsifies them IS the implementation, so the assertions are flipped to the POSITIVE form rather
    than deleted — the same accept-criterion pattern the two decision-1/2 flips above follow."""
    env_fields = {f.name for f in fields(DelegationEnvelope)}
    card_fields = {f.name for f in fields(AgentCard)}
    assert "signature" in env_fields  # the ENVELOPE is signed now (leg E3)...
    assert "signature" in card_fields  # ...as is the CARD (the older, separate surface)

    # The §4.4 opt-out (`MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES`) is wired exactly once, at the
    # composition root's fail-closed envelope-verification gate (never in a production-permissive
    # default). Its presence there is what the flipped assertion pins.
    wired_in = [
        path.name
        for path in _SRC_MAEZO.rglob("*.py")
        if "MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES" in path.read_text(encoding="utf-8")
    ]
    assert wired_in == ["a2a_composition.py"], (
        "the §4.4 envelope-verification opt-out must be wired at exactly the composition gate "
        f"(a2a_composition.py), found in: {wired_in}"
    )


def test_the_production_max_signature_age_is_not_re_pinned_here() -> None:
    """SINGLE OWNER PER CONSTANT — a guard against this suite drifting into a second, contradictory
    source of truth for the 7-day bound. `tests/unit/a2a/test_keyset.py:489-494` owns pinning
    `MAX_SIGNATURE_AGE` and its accept/reject boundary. This suite uses a SHORT test-only bound
    (`_MAX_AGE`) so its capture-replay attacks need no multi-day arithmetic; the assertion below only
    records that the two are deliberately different, so a future reader does not "fix" `_MAX_AGE` to
    match production and silently turn every age attack into a 7-day clock exercise."""
    assert _MAX_AGE < MAX_SIGNATURE_AGE
    assert timedelta(minutes=5) == _MAX_AGE  # this suite's own bound, provenance: leg E4


if __name__ == "__main__":  # pragma: no cover - convenience for the R1 verifier
    raise SystemExit(pytest.main([__file__, "-v"]))
