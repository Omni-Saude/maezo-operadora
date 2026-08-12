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

  Half B — attacks that DEPEND on envelope signing, which is NOT implemented (ADR-0039 ships
    Proposed; the digest is unratified — two human-gated open questions block it). Built against an
    explicitly-labeled REFERENCE verifier that implements the PROPOSED digest, so the attacks are
    non-vacuous and the design demonstrably defends WITHOUT claiming signing is done:
    * `reference_envelope_verifier.py` (the labeled reference verifier + its neutered twin)
    * `test_envelope_signing_attacks.py` (tamper / altered payload_hash / stale key / expiry /
      the disclosed unsigned-`payload_meta` gap / the anti-masquerade guard)

NON-VACUITY BAR (GK-enforced): every attack has a RED control that makes the attack SUCCEED when
the defense is neutered. An attack that passes against an undefended system is vacuous. The neuters
live in `adversary.py` (Half A modeled defenses) and `reference_envelope_verifier.py` (Half B).
Consequence-level: the assertions pin the observable HARM (double effect, cross-tenant serve,
duplicate downstream), never merely that code exists.
"""
