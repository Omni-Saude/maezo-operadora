"""ATTACK: idempotency fail-open — smuggle a NON-DURABLE production build past the gate (leg 3).

Threat: `_require_idempotency_store_or_fail_closed` must REFUSE to compose a production A2A edge with
no durable idempotency store (ADR-0039 §4.6; the harm is `RE-EXECUTES ... double process starts,
duplicated dossiers`). An attacker/operator tries to reach the pre-leg-3 silent-`None` posture by
fooling the gate's two inputs — the runtime mode and the DSN.

Every attack here hardcodes its expected outcome with provenance, and the modes are written as
LITERALS so a change to `_LOCAL_RUNTIME_MODE` cannot silently rewrite the expectation (the
"no matrix derived from the constant under test" rule). This is the ATTACK framing of leg-3's
decision table, plus one adversarial FINDING leg 3's rows did not cover: a whitespace-only DSN.

FINDING (disclosed, no code change — this leg writes attacks, it does not re-implement leg 3): the
gate's `if database_url:` is a truthiness check, so a whitespace-only `DATABASE_URL` ("   ") is
treated as PRESENT and the gate constructs a `PostgresIdempotencyStore` pointed at garbage instead
of returning the legible refusal. This is NOT a silent non-durable build (the store fails CLOSED at
first connect — asyncpg cannot dial "   "), so the security posture holds; but the operator gets an
opaque connect error at first delegation rather than the 3am-legible "provide DATABASE_URL" refusal.
The behaviour is PINNED below so a future one-line hardening (`database_url.strip()`) flips this test
loudly — the honest signal.
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


def test_finding_a_whitespace_only_dsn_slips_past_the_truthiness_gate() -> None:
    """FINDING (disclosed). A whitespace-only DSN is TRUTHY, so the gate constructs a store instead
    of refusing — the legibility gap named in the module docstring. Pinned so a future
    `database_url.strip()` hardening flips this loudly. It is NOT a security fail-open: the store's
    normalized DSN is the whitespace garbage, so it can only fail CLOSED at first connect, never
    silently serve as a durable store."""
    store = _require_idempotency_store_or_fail_closed(
        runtime_mode="production", tenant=_TENANT, edge="attack", database_url="   "
    )
    assert isinstance(store, PostgresIdempotencyStore)  # truthiness gate passed it (the gap)
    assert store._dsn == "   "  # type: ignore[attr-defined]  # garbage DSN -> fails closed at connect


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
