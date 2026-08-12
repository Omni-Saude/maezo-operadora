"""HALF B — attacks that DEPEND on envelope signing (NOT implemented; ADR-0039 is Proposed).

Envelope-signature verification does not exist in production and this suite does not pretend it does
(see the anti-masquerade guards at the bottom, which make that a TESTED fact). These attacks are
built against `LabeledReferenceEnvelopeVerifier` — a reference implementation of ADR-0039 §4.1/§4.2's
PROPOSED digest, unmistakably labeled and unreachable from any production composition root
(`reference_envelope_verifier.py`). Option (ii) of the leg-4 brief, chosen over a strict-xfail
because a strict-xfail against a verifier that does not exist "passes" (xfails) even against a
totally undefended system — the vacuity anti-pattern. Here every attack has a REAL red control:
`NeuteredReferenceEnvelopeVerifier` accepts the malicious envelope, so the attack is demonstrably
non-vacuous and the DESIGN demonstrably defends. When a human ratifies the digest and the real
verifier lands, these attacks re-bind to it unchanged.

Each attack names the ADR-0039 §4.5 threat row / open question it exercises:
  * Tamper (signed field)      — §4.5 "Tamper"; MAC over the §4.1 digest.
  * Altered payload_hash        — §4.5 "Altered payload_hash".
  * Cross-tenant replay (signed) — §4.5 "Cross-tenant replay" (the SIGNING half; the durable-store
    half is `test_cross_tenant_replay_attack.py`).
  * Stale key / prior epoch / scheme downgrade — §4.5 "Stale key" + §4.2 metadata binding.
  * Expiry / max signature age  — §4.3.2 + §Perguntas abertas 3 (Q3): the `signed_at` bound is a
    DISCLOSED reference extension (the §4.1 field set carries no signing timestamp; Q3 is where a
    human pins the mechanism AND the value).
  * The UNSIGNED-`payload_meta` gap — §4.5 "Tamper" scope + §Perguntas abertas 1 (Q1): the honest
    negative. Mutating `payload_meta` (or `task_type`, Q2) does NOT change the §4.1 digest today, so
    even a working verifier would NOT defend it. This test FLIPS to a positive assertion when
    `payload_meta_hash` (Q1) is ratified (ADR-0039 §4.8 acceptance item 3).
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from maezo.a2a import AgentCard, Budget, DelegationEnvelope
from tests.unit.a2a.attacks.reference_envelope_verifier import (
    REFERENCE_SCHEME,
    LabeledReferenceEnvelopeVerifier,
    NeuteredReferenceEnvelopeVerifier,
    ReferenceSignedEnvelope,
    reference_canonical_digest,
)

_KEY_ACTIVE = b"reference-active-envelope-key-0123456789abcdef"
_KEY_RETIRED = b"reference-retired-envelope-key-0123456789abcdef"
_ACTIVE_KEY_ID = "env-key-active-v2"
_RETIRED_KEY_ID = "env-key-retired-v1"
_CURRENT_EPOCH = 7
_MAX_AGE = timedelta(minutes=5)


def _verifier() -> LabeledReferenceEnvelopeVerifier:
    return LabeledReferenceEnvelopeVerifier(
        trusted_keys={_ACTIVE_KEY_ID: _KEY_ACTIVE},  # the RETIRED key is deliberately NOT trusted
        active_key_id=_ACTIVE_KEY_ID,
        current_epoch=_CURRENT_EPOCH,
        max_signature_age=_MAX_AGE,
    )


def _envelope() -> DelegationEnvelope:
    return DelegationEnvelope.root(
        task_id="task-signing-1",
        task_type="authorization.analyze",
        origin="helena",
        target="rafael",
        tenant="amh",
        budget=Budget(tokens=64, time_ms=60_000, cost_per_hop=1),
        payload_ref="fhir://Coverage/signing-1",
        payload_meta={"codigo_procedimento_tuss": "10101012", "valor_estimado_brl": "500.0"},
    )


def _digest_of(envelope: DelegationEnvelope, signed: ReferenceSignedEnvelope) -> bytes:
    return reference_canonical_digest(
        envelope,
        scheme=signed.scheme,
        key_id=signed.key_id,
        replay_epoch=signed.replay_epoch,
        signed_at=signed.signed_at,
    )


# ---------------------------------------------------------------------------
# Positive control — the real verifier is not "reject everything"
# ---------------------------------------------------------------------------


def test_positive_control_a_pristine_signature_verifies() -> None:
    """Rules out a vacuous verifier: a correctly-signed, current, unexpired envelope under the active
    key VERIFIES. Without this, "rejects the tampered envelope" would prove nothing."""
    verifier = _verifier()
    signed = verifier.sign(_envelope())
    assert verifier.verify(signed) is True


# ---------------------------------------------------------------------------
# Tamper — any SIGNED field altered in transit (§4.5 "Tamper")
# ---------------------------------------------------------------------------

_SIGNED_FIELD_TAMPERS: tuple[tuple[str, object], ...] = (
    ("tenant retag (cross-tenant, §4.5)", lambda e: replace(e, tenant="attacker-tenant")),
    ("origin spoofed", lambda e: replace(e, origin="mallory")),
    ("target redirected", lambda e: replace(e, target="marina")),
    ("budget inflated", lambda e: replace(e, budget=Budget(tokens=999_999, time_ms=999_999, cost_per_hop=1))),
    ("delegation_chain rewritten", lambda e: replace(e, delegation_chain=("helena", "marina"))),
    ("deadline injected", lambda e: replace(e, deadline=datetime(2099, 1, 1, tzinfo=UTC))),
)


@pytest.mark.parametrize(("label", "tamper"), _SIGNED_FIELD_TAMPERS)
def test_tampering_a_signed_field_is_rejected(label: str, tamper: object) -> None:
    """A signed field altered after signing invalidates the MAC (the digest bytes genuinely change),
    so the real verifier REJECTS — and the neutered verifier ACCEPTS (the red control). Consequence:
    the tampered delegation cannot pass verification unless the defense is removed."""
    verifier = _verifier()
    env = _envelope()
    signed = verifier.sign(env)
    tampered_env = tamper(env)  # type: ignore[operator]
    tampered_signed = replace(signed, envelope=tampered_env)

    # Non-vacuity of the digest: the tampered field actually changed the signed bytes.
    assert _digest_of(tampered_env, signed) != _digest_of(env, signed), f"{label}: digest unchanged"
    assert verifier.verify(tampered_signed) is False  # real verifier rejects
    assert NeuteredReferenceEnvelopeVerifier().verify(tampered_signed) is True  # RED: neuter accepts


def test_altered_payload_hash_is_rejected() -> None:
    """§4.5 "Altered payload_hash": swapping `payload_ref` after signing changes
    `payload_hash = sha256(payload_ref)` inside the digest, so the MAC no longer matches — rejected.
    RED: the neutered verifier accepts the swapped reference."""
    verifier = _verifier()
    env = _envelope()
    signed = verifier.sign(env)
    swapped = replace(env, payload_ref="fhir://Coverage/SWAPPED-BY-ATTACKER")
    swapped_signed = replace(signed, envelope=swapped)

    assert _digest_of(swapped, signed) != _digest_of(env, signed)  # payload_hash moved
    assert verifier.verify(swapped_signed) is False
    assert NeuteredReferenceEnvelopeVerifier().verify(swapped_signed) is True


# ---------------------------------------------------------------------------
# Stale key / prior epoch / scheme downgrade (§4.2 metadata binding, §4.5 "Stale key")
# ---------------------------------------------------------------------------


def test_a_signature_under_a_retired_key_is_rejected() -> None:
    """§4.5 "Stale key": an envelope signed under a RETIRED `key_id` the verifier no longer trusts
    is rejected — the verifier resolves `key_id` against its trusted keyset (not a single scalar),
    and an unknown/retired id fails outright. RED: the neutered verifier accepts it."""
    verifier = _verifier()
    stale = verifier.sign(_envelope(), key_id=_RETIRED_KEY_ID, signing_key=_KEY_RETIRED)
    assert verifier.verify(stale) is False  # k_retired is not in the trusted keyset
    assert NeuteredReferenceEnvelopeVerifier().verify(stale) is True


def test_a_prior_replay_epoch_is_hard_rejected() -> None:
    """§4.2 replay-epoch hard cutover: an envelope claiming a PRIOR epoch is rejected regardless of
    key validity — the blunt incident-response instrument for "trust nothing signed before now"."""
    verifier = _verifier()
    old_epoch = verifier.sign(_envelope(), replay_epoch=_CURRENT_EPOCH - 1)
    assert verifier.verify(old_epoch) is False
    assert NeuteredReferenceEnvelopeVerifier().verify(old_epoch) is True


def test_a_scheme_downgrade_is_rejected() -> None:
    """§4.2 scheme binding (the JSON-signing analogue of JWT "alg:none"): a signature claiming a
    different/forged scheme than the verifier's is rejected before the MAC is even consulted."""
    verifier = _verifier()
    signed = verifier.sign(_envelope())
    downgraded = replace(signed, scheme="none")
    assert signed.scheme == REFERENCE_SCHEME
    assert verifier.verify(downgraded) is False
    assert NeuteredReferenceEnvelopeVerifier().verify(downgraded) is True


# ---------------------------------------------------------------------------
# Expiry / max signature age (§4.3.2 + Q3) — the captured-envelope-replayed-later attack
# ---------------------------------------------------------------------------


def test_a_signature_older_than_the_max_age_is_rejected() -> None:
    """§4.3.2 + Q3: a validly-signed envelope CAPTURED and replayed after the max-signature-age
    window is rejected by the verifier-side age bound — the defense `deadline` cannot provide,
    because every real envelope carries `deadline=None`. The `signed_at` binding is the DISCLOSED
    Q3 reference extension (module doc). RED: the neutered verifier accepts the stale capture."""
    verifier = _verifier()
    t0 = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    signed = verifier.sign(_envelope(), signed_at=t0)

    # Fresh: within the window -> accepted.
    assert verifier.verify(signed, now=t0 + timedelta(minutes=1)) is True
    # Replayed 30 minutes later (> 5-minute bound) -> rejected.
    assert verifier.verify(signed, now=t0 + timedelta(minutes=30)) is False
    assert NeuteredReferenceEnvelopeVerifier().verify(signed, now=t0 + timedelta(minutes=30)) is True


def test_a_future_dated_signature_is_rejected() -> None:
    """A signature timestamped in the future (a clock-skew forgery) is also refused — the age bound
    is two-sided. The bound is bound INTO the digest, so an attacker cannot move `signed_at` without
    the key."""
    verifier = _verifier()
    t0 = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    signed = verifier.sign(_envelope(), signed_at=t0 + timedelta(hours=1))
    assert verifier.verify(signed, now=t0) is False


# ---------------------------------------------------------------------------
# The DISCLOSED unsigned-payload_meta gap (§Perguntas abertas 1/2) — the honest negative
# ---------------------------------------------------------------------------


def test_disclosed_gap_tampering_unsigned_payload_meta_is_NOT_defended_today() -> None:
    """HONEST NEGATIVE (ADR-0039 §4.5 Tamper scope + §Perguntas abertas 1). `payload_meta` — which
    carries the TUSS code, `valor_estimado_brl`, the `prestador_id` Carolina turns into the CIB
    business key, and Andre's `valor_pagamento_cents` — is NOT in the §4.1 mandated digest. So
    mutating it alone does NOT change the digest, and even a working verifier ACCEPTS the tampered
    envelope. This proves the digest does not OVER-claim: signing, as mandated, does not bind the
    instruction content. FLIPS to a positive assertion (reject) when `payload_meta_hash` (Q1) is
    ratified — ADR-0039 §4.8 acceptance item 3."""
    verifier = _verifier()
    env = _envelope()
    signed = verifier.sign(env)
    # An attacker rewrites the money and the business key inside payload_meta:
    meta_tampered = replace(
        env, payload_meta={"codigo_procedimento_tuss": "99999999", "valor_estimado_brl": "999999.0", "prestador_id": "attacker"}
    )
    # The digest is IDENTICAL (payload_meta never enters it today)...
    assert _digest_of(meta_tampered, signed) == _digest_of(env, signed)
    # ...so the real verifier STILL ACCEPTS the tampered envelope — the disclosed Q1 gap.
    assert verifier.verify(replace(signed, envelope=meta_tampered)) is True


def test_disclosed_gap_tampering_unsigned_task_type_is_NOT_defended_today() -> None:
    """HONEST NEGATIVE (§Perguntas abertas 2): `task_type` is also outside the §4.1 digest, so
    swapping it does not change the digest and the verifier accepts it. It ranks below `payload_meta`
    only because admission still cross-checks it against the target's Card; the SIGNATURE does not."""
    verifier = _verifier()
    env = _envelope()
    signed = verifier.sign(env)
    type_tampered = replace(env, task_type="clinical.decision")
    assert _digest_of(type_tampered, signed) == _digest_of(env, signed)
    assert verifier.verify(replace(signed, envelope=type_tampered)) is True


# ---------------------------------------------------------------------------
# Anti-masquerade guards — signing is genuinely NOT implemented; the reference stays quarantined
# ---------------------------------------------------------------------------

_SRC_MAEZO = Path(__file__).resolve().parents[4] / "src" / "maezo"


def test_no_production_module_references_the_reference_verifier() -> None:
    """ANTI-MASQUERADE. The reference verifier must NEVER be reachable from production. A text sweep
    of the ENTIRE `src/maezo` tree asserts zero references to it or its module/package — so the
    effect-chokepoint fence's "no test double in a composition root" invariant is backed by a direct
    reachability proof here, not merely by the fence scanning `src/` (which it also does)."""
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


def test_envelope_signing_is_genuinely_unimplemented_in_production() -> None:
    """ANTI-MASQUERADE. Positive proof that envelope signing is NOT done: the `DelegationEnvelope`
    type carries NO `signature` field (whereas `AgentCard` — the surface that IS signed today — does),
    and ADR-0039 §4.4's PROPOSED envelope-verification opt-out env var is wired NOWHERE in `src`.
    This is what makes Half B honest: the attacks exercise a reference, not a shipped verifier."""
    env_fields = {f.name for f in fields(DelegationEnvelope)}
    card_fields = {f.name for f in fields(AgentCard)}
    assert "signature" not in env_fields  # the ENVELOPE is not signed...
    assert "signature" in card_fields  # ...only the CARD is (a different, built surface)

    # The §4.4 proposed opt-out (`MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES`) exists in no source file —
    # there is no envelope-verification gate to opt out of.
    leaked = [
        str(path)
        for path in _SRC_MAEZO.rglob("*.py")
        if "MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES" in path.read_text(encoding="utf-8")
    ]
    assert leaked == [], f"§4.4 envelope-verification opt-out appears wired in: {leaked}"


if __name__ == "__main__":  # pragma: no cover - convenience for the R1 verifier
    raise SystemExit(pytest.main([__file__, "-v"]))
