"""Onda 3 / Train C leg 4 — the ADVERSARIAL (attack-oriented) A2A test suite.

This package is written adversarially: each module WRITES the attack, and the gatekeeper then
attacks the attack. Two honest halves (see `docs/adr/0039-a2a-delegation-envelope-signing.md`
Status: Proposed, and the leg-4 brief):

  Half A — attacks against defenses that ARE built (real RED controls that must bind NOW):
    * cross-tenant replay      -> `test_cross_tenant_replay_attack.py`
    * crash-before-complete    -> `test_crash_before_complete_attack.py`
    * duplicate fact delivery  -> `test_duplicate_fact_delivery_attack.py`
    * idempotency fail-open     -> `test_idempotency_fail_open_attack.py`
    * live-Postgres binders     -> `test_attacks_live_pg.py` (skip-loudly precedent)

  Half B — attacks against envelope signing, which IS now implemented (leg E4 re-bind, 2026-08-12).
    ADR-0039 is **Accepted** with seven owner decisions, and leg E3 shipped the real digest v2 +
    `EnvelopeSigner`/`EnvelopeVerifier`. Half B therefore exercises PRODUCTION code:
    * `test_envelope_signing_attacks.py` (tamper battery / altered payload_hash / stale key /
      MAC-valid scheme downgrade / expiry both sides / rotation + epoch grace / cross-tenant key
      confusion / the two FLIPPED decision-1-2 acceptance proofs / the anti-masquerade guards)
    * `reference_envelope_verifier.py` — RE-SCOPED by leg E4 from "stand-in for absent production
      code" to "independently-written oracle for the PRE-decision (v1) digest". It is now both the
      RED control for the two flips (the v1 digest omits `payload_meta_hash`/`task_type`, so it
      still ACCEPTS what v2 refuses) and the differential oracle the shipped verifier is checked
      against on the v1 field set.

NON-VACUITY BAR (GK-enforced): every attack has a RED control that makes the attack SUCCEED when
the defense is neutered. An attack that passes against an undefended system is vacuous. Half A's
neuters live in `adversary.py`. Half B's are TARGETED (one defense at a time, per ADR-0039 §4.8
obligation 2) and — except for the v1 digest oracle — built from knobs the production verifier
actually ships, so each control is a real configuration rather than a test-only fiction; the
accept-everything `NeuteredReferenceEnvelopeVerifier` was retired by leg E4 as too weak to
distinguish WHICH defense refused. Consequence-level: the assertions pin the observable HARM
(double effect, cross-tenant serve, duplicate downstream, the forged envelope reaching the agent
handler), never merely that code exists.
"""
