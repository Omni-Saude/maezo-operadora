"""ATTACK: idempotency fail-open — smuggle a NON-DURABLE production build past the gate (leg 3).

Threat: `_require_idempotency_store_or_fail_closed` must REFUSE to compose a production A2A edge with
no durable idempotency store (ADR-0039 §4.6; the harm is `RE-EXECUTES ... double process starts,
duplicated dossiers`). An attacker/operator tries to reach the pre-leg-3 silent-`None` posture by
fooling the gate's two inputs — the runtime mode and the DSN.

Every attack here hardcodes its expected outcome with provenance, and the modes are written as
LITERALS so a change to `_LOCAL_RUNTIME_MODE` cannot silently rewrite the expectation (the
"no matrix derived from the constant under test" rule). This is the ATTACK framing of leg-3's
decision table, plus one adversarial FINDING leg 3's rows did not cover: a whitespace-only DSN.

FINDING — RAISED by leg 4, now CLOSED (the pinned test below has been FLIPPED, as designed). The
gate's presence check used to be a bare `if database_url:` truthiness test, so a whitespace-only
`DATABASE_URL` ("   ") counted as PRESENT and the gate constructed a `PostgresIdempotencyStore`
pointed at garbage instead of returning the legible refusal. That was never a silent non-durable
build (the store fails CLOSED at first connect — asyncpg cannot dial "   "), so the security posture
always held; the defect was LEGIBILITY — the operator got an opaque connect error at first
delegation rather than the 3am-legible "provide DATABASE_URL" refusal at composition time.

The hardening landed as the shared `_dsn_is_present` helper (`a2a_composition.py`): blank-after-strip
is now treated exactly like absent. Leg 4 pinned the OLD behaviour so this hardening would flip the
test loudly rather than silently — it did, and
`test_a_whitespace_only_dsn_is_now_refused_at_composition_time` below is that flip, asserting the
REFUSAL. The hardening deliberately landed on BOTH durability gates through ONE helper: hardening
only the idempotency gate would have made `("production", "   ")` a build on the fact gate and a
refusal here — a half-refusing root, which is precisely what the gate-agreement proof in
`tests/unit/runtime/agent_runtime/test_a2a_composition_idempotency.py` exists to prevent.
"""

from __future__ import annotations

import pytest

from maezo.a2a import PostgresIdempotencyStore
from maezo.runtime.agent_runtime.a2a_composition import _require_idempotency_store_or_fail_closed

_TENANT = "amh"

#: (mode, dsn, why-the-attack-fails) — literal modes, hardcoded verdicts. Provenance:
#: `_require_idempotency_store_or_fail_closed`'s docstring — refuse iff no DSN AND mode != the exact
#: literal "local". These are BYPASS ATTEMPTS, not neutral rows.
_REFUSED_BYPASS_ATTEMPTS: tuple[tuple[str, str | None, str], ...] = (
    ("", None, "blanking AGENT_RUNTIME_MODE does not disable the gate — '' is production"),
    ("", "", "both empty: an empty-string DSN is falsy, so it is 'absent' -> refuse"),
    ("production", "   ", "a whitespace-only DSN is blank-after-strip, so it is 'absent' -> refuse"),
    ("", "   ", "both blank: neither blanking the mode nor blanking the DSN buys a build"),
    (" local ", None, "whitespace-padded 'local' is not the exact literal -> production -> refuse"),
    ("Local", None, "case-variant 'Local' is not the exact literal -> production -> refuse"),
    ("LOCAL", None, "case-variant 'LOCAL' is not the exact literal -> production -> refuse"),
    ("dev", None, "any unrecognized mode fails CLOSED to production -> refuse"),
    ("staging", None, "staging is production -> refuse"),
    ("kubernetes", None, "Helm's injected 'kubernetes' is production -> refuse"),
)


@pytest.mark.parametrize(("mode", "dsn", "reason"), _REFUSED_BYPASS_ATTEMPTS)
def test_bypass_attempt_is_refused(mode: str, dsn: str | None, reason: str) -> None:
    """Each mode/DSN smuggling attempt is REFUSED with the legible RuntimeError. The attacker cannot
    reach a silent non-durable production build by manipulating the mode string or emptying the DSN."""
    with pytest.raises(RuntimeError, match="no DATABASE_URL is present"):
        _require_idempotency_store_or_fail_closed(
            runtime_mode=mode, tenant=_TENANT, edge="attack", database_url=dsn
        )
    _ = reason


def test_injecting_a_producer_does_not_buy_out_of_the_idempotency_gate() -> None:
    """The idempotency gate is INDEPENDENT of leg-2's fact gate: it consults only mode+DSN, never a
    producer. So the "inject a Kafka producer to satisfy the fact gate" bypass does nothing here —
    the helper refuses on the DSN alone. (Leg-3 proves this end-to-end at the dossier ROOT with a
    real injected `RecordingProducer`; this pins it at the gate helper, the single decision point.)"""
    with pytest.raises(RuntimeError, match="durable, cross-replica idempotency"):
        _require_idempotency_store_or_fail_closed(
            runtime_mode="production", tenant=_TENANT, edge="attack", database_url=None
        )


@pytest.mark.parametrize("blank_dsn", ["   ", "\t", "\n", " \t\n "])
def test_a_whitespace_only_dsn_is_now_refused_at_composition_time(blank_dsn: str) -> None:
    """THE FLIP of leg-4's pinned FINDING (see the module docstring). A whitespace-only DSN is
    truthy in Python, and the gate used to build a `PostgresIdempotencyStore` pointed at that
    garbage; it now takes the REFUSAL branch at composition time, which is the whole point of the
    hardening — the operator learns what to provide at bring-up instead of getting an opaque asyncpg
    connect error at the first delegation. Blank-after-strip is treated exactly like absent, so this
    must produce the SAME legible refusal the absent-DSN rows above get, not a different error."""
    with pytest.raises(RuntimeError, match="no DATABASE_URL is present"):
        _require_idempotency_store_or_fail_closed(
            runtime_mode="production", tenant=_TENANT, edge="attack", database_url=blank_dsn
        )


def test_the_whitespace_dsn_refusal_is_the_same_legible_message_as_an_absent_dsn() -> None:
    """Non-vacuity on the flip: it is not enough that SOMETHING raised. The blank-DSN refusal must
    carry the same operator-actionable content as the absent-DSN one (missing act, durability
    protected, effect prevented, migration, explicit way out) — otherwise the hardening would have
    traded an opaque connect error for an opaque refusal."""
    with pytest.raises(RuntimeError) as exc:
        _require_idempotency_store_or_fail_closed(
            runtime_mode="production", tenant=_TENANT, edge="attack", database_url="   "
        )
    message = str(exc.value)
    for required in ("DATABASE_URL", "Guard 4", "RE-EXECUTES", "0003_a2a_idempotency", "local"):
        assert required in message


def test_a_whitespace_padded_but_real_dsn_is_still_built_and_passed_through_unchanged() -> None:
    """Non-vacuity the other way — the hardening must not have become a blanket DSN normalizer. A
    DSN that is merely PADDED is still present, so it still builds, and the operator's string
    reaches the store BYTE-UNCHANGED (`_dsn_is_present` decides presence; it never rewrites)."""
    padded = "  postgresql://x/y  "
    store = _require_idempotency_store_or_fail_closed(
        runtime_mode="production", tenant=_TENANT, edge="attack", database_url=padded
    )
    assert isinstance(store, PostgresIdempotencyStore)
    assert store._dsn == padded  # type: ignore[attr-defined]  # presence check, NOT normalization


def test_the_one_true_non_durable_path_is_explicit_local_and_is_genuinely_none() -> None:
    """The ONLY sanctioned non-durable build: the EXACT literal 'local' + no DSN -> in-memory
    `_inflight` (returned as `None`, never a fabricated store pointed at nothing). This is the dev
    ergonomics leg 3 preserves — and the single hole an attacker cannot widen, because every
    non-'local' mode above fails closed."""
    store = _require_idempotency_store_or_fail_closed(
        runtime_mode="local", tenant=_TENANT, edge="attack", database_url=None
    )
    assert store is None


def test_a_dsn_in_local_dev_still_gets_the_durable_store() -> None:
    """Non-vacuity on the build side: 'local' is not a blanket opt-out of durability — a DSN present
    in local dev STILL wires the durable store (construction is lazy, so no server is needed here)."""
    store = _require_idempotency_store_or_fail_closed(
        runtime_mode="local", tenant=_TENANT, edge="attack", database_url="postgresql://x/y"
    )
    assert isinstance(store, PostgresIdempotencyStore)


if __name__ == "__main__":  # pragma: no cover - convenience for the R1 verifier
    raise SystemExit(pytest.main([__file__, "-v"]))
