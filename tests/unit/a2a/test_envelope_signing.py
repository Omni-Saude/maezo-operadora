"""Unit tests for the REAL envelope signing (ADR-0039 leg E3) — `maezo.a2a.envelope_signing`.

This is leg E3's own proof surface (the load-bearing leg). It pins, against the SHIPPED
`EnvelopeSigner`/`EnvelopeVerifier` (not the reference double — that stays for E4):

  1. **Digest v2 non-vacuity** — the canonical preimage contains every §4.1 v2 field, and mutating
     `payload_meta` OR `task_type` alone CHANGES it (the flip closing owner decisions 1-2).
  2. **Positive control + signer fail-closed** — a pristine signature verifies; the signer refuses a
     wrong tenant, a `deadline=None` envelope, and a degenerate key.
  3. **Verifier fail-closed battery** — tamper (every signed field, incl. payload_meta/task_type),
     retired/unknown key_id, prior epoch (zero grace, and the short grace acceptance), scheme
     downgrade, max signature age (two-sided), deadline-None, malformed MAC, cross-tenant retag.
  4. **Rotation keyset** — a prior-in-grace key verifies; key_id derivation is stable + distinct.
  5. **The four builders, end to end** — each builder signed by a real signer VERIFIES (deadline
     non-None end to end), and a tampered copy is REJECTED.
  6. **Dispatcher placement** — verification is the FIRST check in `delegate`: unsigned -> a
     `SIGNATURE_INVALID` structured rejection (never a raise, handler never runs); a forged envelope
     never claims an attacker-chosen idempotency key.
  7. **The composition fail-closed gate** — `_require_envelope_signing_or_fail_closed`, incl. the
     no-cross-tenant-fallback invariant and the `MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES` opt-out.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from maezo.a2a import (
    Budget,
    DelegationEnvelope,
    EnvelopeSignature,
    EnvelopeSigner,
    EnvelopeVerifier,
    RejectionReason,
    build_verification_keyset,
    derive_key_id,
    envelope_canonical_digest,
    per_tenant_key_env_var,
    per_tenant_prior_key_env_var,
)
from maezo.a2a.envelope_signing import (
    DEFAULT_REPLAY_EPOCH,
    ENVELOPE_SIGNATURE_SCHEME,
    MAX_SIGNATURE_AGE,
    EnvelopeSignatureError,
)

from .fakes import FakeAgentHandler, LabeledFakeTenantKeyset, build_test_dispatcher, make_card

_TENANT = "amh"
_OTHER = "outra"
# Obvious test literals, NOT secrets. >= MIN_SIGNING_KEY_BYTES so they are usable.
_KEY = b"envelope-active-signing-key-0123456789abcdef"
_PRIOR_KEY = b"envelope-prior-signing-key-0123456789abcdef"
_OTHER_KEY = b"other-tenant-signing-key-0123456789abcdef"


def _signer(*, tenant: str = _TENANT, key: bytes = _KEY, epoch: int = DEFAULT_REPLAY_EPOCH) -> EnvelopeSigner:
    return EnvelopeSigner(tenant=tenant, signing_key=key, key_id=derive_key_id(key), replay_epoch=epoch)


def _verifier(
    *,
    active_key: bytes = _KEY,
    prior_key: bytes | None = None,
    epoch: int = DEFAULT_REPLAY_EPOCH,
    max_age: timedelta = MAX_SIGNATURE_AGE,
    prior_epoch_grace_until: datetime | None = None,
) -> EnvelopeVerifier:
    trusted, active_id = build_verification_keyset(active_key=active_key, prior_key=prior_key)
    return EnvelopeVerifier(
        trusted_keys=trusted,
        active_key_id=active_id,
        current_epoch=epoch,
        max_signature_age=max_age,
        prior_epoch_grace_until=prior_epoch_grace_until,
    )


def _envelope(*, tenant: str = _TENANT, deadline: datetime | None = None, **kw: object) -> DelegationEnvelope:
    base: dict[str, object] = {
        "task_id": "task-sign-1",
        "task_type": "authorization.analyze",
        "origin": "helena",
        "target": "rafael",
        "tenant": tenant,
        "budget": Budget(tokens=64, time_ms=60_000, cost_per_hop=1),
        "payload_ref": "fhir://Coverage/signing-1",
        "deadline": deadline if deadline is not None else datetime.now(tz=UTC) + timedelta(hours=1),
        "payload_meta": {"codigo_procedimento_tuss": "10101012", "valor_estimado_brl": "500.0"},
    }
    base.update(kw)
    return DelegationEnvelope.root(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. Digest v2 — non-vacuity + the payload_meta/task_type flip
# ---------------------------------------------------------------------------


def _digest(env: DelegationEnvelope, *, signed_at: datetime | None = None) -> bytes:
    return envelope_canonical_digest(
        env,
        scheme=ENVELOPE_SIGNATURE_SCHEME,
        key_id="kid",
        replay_epoch=0,
        signed_at=signed_at or datetime(2026, 8, 12, tzinfo=UTC),
    )


def test_digest_v2_contains_every_mandated_field() -> None:
    """§4.8 obligation 3: the canonical payload actually carries every §4.1 v2 field — so a field
    silently dropped from the preimage is caught."""
    env = _envelope()
    payload = json.loads(_digest(env).decode("utf-8"))
    assert set(payload) == {
        "tenant", "task_id", "task_type", "origin", "target",
        "payload_hash", "payload_meta_hash", "deadline", "budget",
        "delegation_chain", "scheme", "key_id", "replay_epoch", "signed_at",
    }
    # payload_hash / payload_meta_hash are the sha256 of their sources (not the raw values).
    from hashlib import sha256

    assert payload["payload_hash"] == sha256(env.payload_ref.encode()).hexdigest()
    assert payload["payload_meta_hash"] == sha256(
        json.dumps(dict(env.payload_meta), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_digest_v2_binds_payload_meta_the_owner_decision_1_flip() -> None:
    """Owner decision 1 (2026-08-12): mutating `payload_meta` alone MUST change the v2 digest — the
    residual the r1-r3 honest-negative disclosed is CLOSED. (E4 flips the reference-based negative.)"""
    env = _envelope()
    tampered = replace(env, payload_meta={**dict(env.payload_meta), "prestador_id": "attacker"})
    assert _digest(tampered) != _digest(env)


def test_digest_v2_binds_task_type_the_owner_decision_2_flip() -> None:
    """Owner decision 2: `task_type` is bound directly — swapping it changes the digest."""
    env = _envelope()
    assert _digest(replace(env, task_type="clinical.decision")) != _digest(env)


def test_digest_deadline_uses_isoformat_not_default_str() -> None:
    """§4.1: `deadline` maps via `.astimezone(UTC).isoformat()` (a `T` separator), NOT `default=str`
    (a space). The two produce different bytes for the same instant — pin the `T`."""
    env = _envelope(deadline=datetime(2026, 8, 11, 12, 0, tzinfo=UTC))
    payload = json.loads(_digest(env).decode("utf-8"))
    assert payload["deadline"] == "2026-08-11T12:00:00+00:00"


def test_digest_is_deterministic_and_order_independent() -> None:
    fixed = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)
    env_a = _envelope(deadline=fixed, payload_meta={"a": "1", "b": "2"})
    env_b = _envelope(deadline=fixed, payload_meta={"b": "2", "a": "1"})
    assert _digest(env_a) == _digest(env_b)


# ---------------------------------------------------------------------------
# 2. Positive control + signer fail-closed
# ---------------------------------------------------------------------------


def test_pristine_signature_verifies() -> None:
    """Rules out a vacuous verifier: a correctly-signed, current, unexpired envelope VERIFIES."""
    signed = _signer().sign(_envelope())
    assert signed.signature is not None
    assert _verifier().verify(signed) is True


def test_signer_refuses_wrong_tenant() -> None:
    """Per-tenant custody: a signer built for tenant-A refuses to sign a tenant-B envelope."""
    with pytest.raises(EnvelopeSignatureError, match="per-tenant custody"):
        _signer(tenant=_TENANT).sign(_envelope(tenant=_OTHER))


def test_signer_refuses_deadline_none() -> None:
    """§4.3.1: a signed envelope MUST carry a non-None deadline — the signer refuses to sign one."""
    with pytest.raises(EnvelopeSignatureError, match="deadline=None"):
        _signer().sign(DelegationEnvelope.root(
            task_id="t", task_type="authorization.analyze", origin="helena", target="rafael",
            tenant=_TENANT, budget=Budget(tokens=8, time_ms=8), payload_ref="fhir://Coverage/x",
        ))


@pytest.mark.parametrize("bad", [b"", b"   ", b"short"])
def test_signer_refuses_degenerate_key(bad: bytes) -> None:
    """Fail-closed key validation, same floor as CardSigner (empty/whitespace/too-short)."""
    with pytest.raises(EnvelopeSignatureError):
        EnvelopeSigner(tenant=_TENANT, signing_key=bad, key_id="kid")


# ---------------------------------------------------------------------------
# 3. Verifier fail-closed battery
# ---------------------------------------------------------------------------

_SIGNED_FIELD_TAMPERS = (
    ("tenant retag", lambda e: replace(e, tenant="attacker")),
    ("origin spoof", lambda e: replace(e, origin="mallory")),
    ("target redirect", lambda e: replace(e, target="marina")),
    ("budget inflate", lambda e: replace(e, budget=Budget(tokens=999999, time_ms=999999, cost_per_hop=1))),
    ("chain rewrite", lambda e: replace(e, delegation_chain=("helena", "marina"))),
    ("payload_ref swap", lambda e: replace(e, payload_ref="fhir://Coverage/SWAPPED")),
    ("payload_meta tamper", lambda e: replace(e, payload_meta={"prestador_id": "attacker"})),
    ("task_type tamper", lambda e: replace(e, task_type="clinical.decision")),
    ("deadline extend", lambda e: replace(e, deadline=datetime(2099, 1, 1, tzinfo=UTC))),
)


@pytest.mark.parametrize(("label", "tamper"), _SIGNED_FIELD_TAMPERS)
def test_tampering_any_signed_field_is_rejected(label: str, tamper: object) -> None:
    """Every signed field (incl. payload_meta and task_type, digest v2) — altered after signing —
    invalidates the MAC and is REJECTED. The signature rides on the envelope, so `replace` keeps it."""
    verifier = _verifier()
    signed = _signer().sign(_envelope())
    tampered = tamper(signed)  # type: ignore[operator]
    assert tampered.signature is signed.signature  # same MAC, changed content
    assert verifier.verify(tampered) is False, f"{label} must be rejected"


def test_unsigned_envelope_is_rejected() -> None:
    assert _verifier().verify(_envelope()) is False  # signature is None


def test_signature_under_a_retired_key_is_rejected() -> None:
    """A signature under a key_id the verifier does not trust (retired / never provisioned) fails."""
    retired = _signer(key=_PRIOR_KEY)  # signs under a key_id the verifier below does NOT trust
    signed = retired.sign(_envelope())
    assert _verifier(active_key=_KEY).verify(signed) is False  # keyset holds only the active key


def test_prior_epoch_is_hard_rejected_by_default_zero_grace() -> None:
    """§4.2 replay-epoch cutover, zero grace (default): a prior-epoch signature is rejected."""
    signed = _signer(epoch=DEFAULT_REPLAY_EPOCH).sign(_envelope())
    assert _verifier(epoch=DEFAULT_REPLAY_EPOCH + 1).verify(signed) is False


def test_prior_epoch_accepted_within_a_short_grace_window() -> None:
    """§4.2 decision 5: a SHORT prior-epoch grace window accepts the immediately-prior epoch until it
    closes, then rejects — the operator-set incident tolerance."""
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    signed = _signer(epoch=6).sign(_envelope(), now=now)
    graced = _verifier(epoch=7, prior_epoch_grace_until=now + timedelta(minutes=10))
    assert graced.verify(signed, now=now + timedelta(minutes=1)) is True  # inside the window
    assert graced.verify(signed, now=now + timedelta(minutes=30)) is False  # window closed
    # two epochs back is never graced:
    assert graced.verify(_signer(epoch=5).sign(_envelope(), now=now), now=now) is False


def test_scheme_downgrade_is_rejected() -> None:
    """§4.2 scheme binding (the 'alg:none'-class defense): a different scheme is refused."""
    signed = _signer().sign(_envelope())
    assert signed.signature is not None
    downgraded = replace(signed, signature=replace(signed.signature, scheme="none"))
    assert _verifier().verify(downgraded) is False


def test_signature_older_than_max_age_is_rejected() -> None:
    """§4.3.2: a captured envelope replayed after the max-signature-age window is rejected."""
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    signed = _signer().sign(_envelope(), now=now)
    verifier = _verifier(max_age=timedelta(minutes=5))
    assert verifier.verify(signed, now=now + timedelta(minutes=1)) is True
    assert verifier.verify(signed, now=now + timedelta(minutes=30)) is False


def test_future_dated_signature_is_rejected() -> None:
    """§4.3.2 two-sided: a clock-skew / future-dated signature is refused."""
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    signed = _signer().sign(_envelope(), now=now + timedelta(hours=1))
    assert _verifier().verify(signed, now=now) is False


def test_signed_envelope_with_deadline_none_is_rejected_by_verifier() -> None:
    """§4.3.1: even if a MAC somehow covered a deadline-None envelope, the verifier refuses it."""
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    env = _envelope(deadline=datetime(2026, 8, 12, 18, 0, tzinfo=UTC))
    signed = _signer().sign(env, now=now)
    # Strip the deadline AFTER signing (would also fail the MAC, but the deadline-None gate fires
    # first and independently — the belt to the max-age suspenders).
    assert _verifier().verify(replace(signed, deadline=None), now=now) is False


def test_malformed_non_ascii_mac_is_rejected_not_raised() -> None:
    """A non-ASCII MAC (only reachable by direct construction) returns False, never a TypeError —
    preserving 'verify never raises'."""
    signed = _signer().sign(_envelope())
    assert signed.signature is not None
    bad = replace(signed, signature=replace(signed.signature, mac="mác-not-ascii"))
    assert _verifier().verify(bad) is False


def test_cross_tenant_retag_after_signing_is_rejected() -> None:
    """Cross-tenant replay (signing half): a tenant-A signature retagged to tenant-B fails — `tenant`
    is bound in the digest, so the MAC no longer matches."""
    signed = _signer(tenant=_TENANT).sign(_envelope(tenant=_TENANT))
    assert _verifier().verify(replace(signed, tenant=_OTHER)) is False


# ---------------------------------------------------------------------------
# 4. Rotation keyset
# ---------------------------------------------------------------------------


def test_prior_in_grace_key_verifies_after_rotation() -> None:
    """§4.3: an envelope signed under the PRIOR key still verifies while that key is in the trusted
    keyset (the rotation grace) — its own key_id is trusted alongside the active one."""
    prior_signer = _signer(key=_PRIOR_KEY)
    signed = prior_signer.sign(_envelope())
    verifier = _verifier(active_key=_KEY, prior_key=_PRIOR_KEY)  # both in the keyset
    assert verifier.verify(signed) is True
    # ...but once the prior key is purged (grace over) it no longer verifies:
    assert _verifier(active_key=_KEY, prior_key=None).verify(signed) is False


def test_derive_key_id_is_stable_and_distinct() -> None:
    assert derive_key_id(_KEY) == derive_key_id(_KEY)
    assert derive_key_id(_KEY) != derive_key_id(_PRIOR_KEY)
    assert derive_key_id(_KEY).startswith("env-sha256:")


def test_build_verification_keyset_collapses_equal_active_prior() -> None:
    trusted, active_id = build_verification_keyset(active_key=_KEY, prior_key=_KEY)
    assert trusted == {active_id: _KEY}


def test_verifier_refuses_an_empty_trusted_keyset_with_its_own_message() -> None:
    """DEGENERATE CONFIG, LEGIBLY (E3 repair). The empty-keyset refusal used to be DEAD CODE: the
    `active_key_id not in trusted_keys` check ran first and fires for an empty dict too, so a
    verifier configured to trust NOTHING was reported as "missing active key id" — which points the
    operator at the wrong knob. The checks are now ordered so each degenerate config gets its own
    message, and this test pins both (an empty keyset is what an operator sees when key resolution
    returned nothing at all)."""
    with pytest.raises(EnvelopeSignatureError, match="trusted_keys must be non-empty"):
        EnvelopeVerifier(
            trusted_keys={},
            active_key_id=derive_key_id(_KEY),
            current_epoch=DEFAULT_REPLAY_EPOCH,
            max_signature_age=MAX_SIGNATURE_AGE,
        )
    # ...and the NON-empty-but-unanchored case keeps its own, different message.
    with pytest.raises(EnvelopeSignatureError, match="active_key_id must be present"):
        EnvelopeVerifier(
            trusted_keys={derive_key_id(_PRIOR_KEY): _PRIOR_KEY},
            active_key_id=derive_key_id(_KEY),
            current_epoch=DEFAULT_REPLAY_EPOCH,
            max_signature_age=MAX_SIGNATURE_AGE,
        )


# ---------------------------------------------------------------------------
# 5. The four builders, end to end (deadline non-None -> verifies; tamper -> rejected)
# ---------------------------------------------------------------------------


def _build_each_signed(signer: EnvelopeSigner) -> dict[str, DelegationEnvelope]:
    from maezo.agents.andre.delegation import (
        build_adequacao_dossier_envelope,
        build_pagto_dossier_envelope,
    )
    from maezo.agents.carolina.delegation import build_cred_dossier_envelope
    from maezo.agents.helena.delegation import build_auth_analysis_envelope

    return {
        "helena": build_auth_analysis_envelope(
            tenant=_TENANT, numero_guia_tiss="G-1", coverage_ref="fhir://Coverage/1",
            case_meta={"codigo_procedimento_tuss": "10101012", "valor_estimado_brl": 500.0},
            signer=signer,
        ),
        "carolina": build_cred_dossier_envelope(
            tenant=_TENANT, prestador_id="P-1",
            case_meta={"direcao": "descredenciamento"}, signer=signer,
        ),
        "andre_adequacao": build_adequacao_dossier_envelope(
            tenant=_TENANT, regiao_saude="SP-01", especialidade="cardiologia",
            case_meta={"gap_adequacao": "GAP_CRITICO"}, signer=signer,
        ),
        "andre_pagto": build_pagto_dossier_envelope(
            tenant=_TENANT, case_meta={"valor_pagamento_cents": 123499},
            ordem_pagamento_id="OP-1", signer=signer,
        ),
    }


@pytest.mark.parametrize("edge", ["helena", "carolina", "andre_adequacao", "andre_pagto"])
def test_each_builder_produces_a_verifying_signed_envelope(edge: str) -> None:
    """Evidence (§4.8): a validly-signed real envelope from EACH of the four builders VERIFIES, with
    a non-None deadline end to end."""
    signer, verifier = _signer(), _verifier()
    env = _build_each_signed(signer)[edge]
    assert env.signature is not None
    assert env.deadline is not None  # §4.3.1 end to end
    assert verifier.verify(env) is True


@pytest.mark.parametrize("edge", ["helena", "carolina", "andre_adequacao", "andre_pagto"])
def test_each_builder_envelope_rejected_when_tampered(edge: str) -> None:
    """...and a tampered copy from each builder is REJECTED (the MAC no longer matches)."""
    signer, verifier = _signer(), _verifier()
    env = _build_each_signed(signer)[edge]
    tampered = replace(env, payload_meta={**dict(env.payload_meta), "prestador_id": "attacker"})
    assert verifier.verify(tampered) is False


def test_builder_without_signer_is_unsigned_dev_path() -> None:
    """No signer -> an UNSIGNED envelope with deadline=None (the pre-signing dev behaviour)."""
    from maezo.agents.helena.delegation import build_auth_analysis_envelope

    env = build_auth_analysis_envelope(
        tenant=_TENANT, numero_guia_tiss="G-1", coverage_ref="fhir://Coverage/1", case_meta={},
    )
    assert env.signature is None
    assert env.deadline is None


# ---------------------------------------------------------------------------
# 6. Dispatcher placement — verification is the FIRST check in delegate
# ---------------------------------------------------------------------------


async def test_dispatcher_rejects_unsigned_envelope_with_signature_invalid() -> None:
    """A verifier-wired dispatcher rejects an unsigned envelope as SIGNATURE_INVALID and NEVER runs
    the handler (a structured rejection, not a raise)."""
    handler = FakeAgentHandler()
    dispatcher, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}, envelope_verifier=_verifier()
    )
    result = await dispatcher.delegate(_envelope())  # unsigned
    assert result.success is False
    assert result.rejection_reason is RejectionReason.SIGNATURE_INVALID
    assert handler.call_count == 0


async def test_dispatcher_accepts_a_validly_signed_envelope() -> None:
    handler = FakeAgentHandler(output_ref="fhir://Task/done")
    dispatcher, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}, envelope_verifier=_verifier()
    )
    result = await dispatcher.delegate(_signer().sign(_envelope()))
    assert result.success is True
    assert handler.call_count == 1


async def test_verification_precedes_idempotency_claim() -> None:
    """§4.4 placement: a forged envelope is rejected BEFORE any idempotency claim, so it cannot poison
    an attacker-chosen task_id. Proof: a forged delegate on task X (rejected, handler not run) does
    NOT stop a later VALID delegate on the same task X from executing."""
    handler = FakeAgentHandler(output_ref="fhir://Task/done")
    dispatcher, _, _ = build_test_dispatcher(
        cards=[make_card("rafael")], handlers={"rafael": handler}, envelope_verifier=_verifier()
    )
    forged = _envelope(task_id="poison-1")  # unsigned = forged
    assert (await dispatcher.delegate(forged)).rejection_reason is RejectionReason.SIGNATURE_INVALID
    assert handler.call_count == 0  # forged never reached _execute / claimed the task_id

    legit = _signer().sign(_envelope(task_id="poison-1"))
    result = await dispatcher.delegate(legit)
    assert result.success is True
    assert handler.call_count == 1  # the valid same-task_id delegation still executes (not poisoned)


async def test_dispatcher_without_verifier_accepts_unsigned_dev_behaviour() -> None:
    """No verifier wired (dev / opt-out) -> unsigned envelopes are accepted (W2 behaviour intact)."""
    handler = FakeAgentHandler()
    dispatcher, _, _ = build_test_dispatcher(cards=[make_card("rafael")], handlers={"rafael": handler})
    assert (await dispatcher.delegate(_envelope())).success is True


# ---------------------------------------------------------------------------
# 7. The composition fail-closed gate
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        per_tenant_key_env_var(_TENANT),
        per_tenant_key_env_var(_OTHER),
        per_tenant_prior_key_env_var(_TENANT),
        "MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES",
    ):
        monkeypatch.delenv(var, raising=False)


def _gate(
    *, mode: str, keyset: object | None = None
) -> tuple[EnvelopeSigner | None, EnvelopeVerifier | None]:
    from maezo.runtime.agent_runtime.a2a_composition import _require_envelope_signing_or_fail_closed

    return _require_envelope_signing_or_fail_closed(
        runtime_mode=mode, tenant=_TENANT, edge="test-edge", keyset=keyset  # type: ignore[arg-type]
    )


def test_gate_prod_absent_key_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="no envelope-signing key"):
        _gate(mode="kubernetes")


def test_gate_prod_absent_key_ignores_optout(monkeypatch: pytest.MonkeyPatch) -> None:
    """UN-BYPASSABLE: the opt-out is IGNORED in production."""
    monkeypatch.setenv("MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES", "1")
    with pytest.raises(RuntimeError, match="no envelope-signing key"):
        _gate(mode="kubernetes")


def test_gate_local_absent_key_no_optout_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES"):
        _gate(mode="local")


def test_gate_local_absent_key_with_optout_returns_none_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAEZO_A2A_ALLOW_UNVERIFIED_ENVELOPES", "true")
    signer, verifier = _gate(mode="local")
    assert signer is None and verifier is None


def test_gate_present_key_returns_working_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(per_tenant_key_env_var(_TENANT), _KEY.decode())
    signer, verifier = _gate(mode="kubernetes")
    assert isinstance(signer, EnvelopeSigner)
    assert isinstance(verifier, EnvelopeVerifier)
    # end to end through the gate-built pair:
    assert verifier.verify(signer.sign(_envelope())) is True


def test_gate_uses_prior_key_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rotation: with both active and prior per-tenant vars set, an envelope signed under the PRIOR
    key still verifies through the gate-built verifier (the grace window)."""
    monkeypatch.setenv(per_tenant_key_env_var(_TENANT), _KEY.decode())
    monkeypatch.setenv(per_tenant_prior_key_env_var(_TENANT), _PRIOR_KEY.decode())
    _, verifier = _gate(mode="kubernetes")
    assert isinstance(verifier, EnvelopeVerifier)
    prior_signed = _signer(key=_PRIOR_KEY).sign(_envelope())
    assert verifier.verify(prior_signed) is True


def test_gate_refuses_a_degenerate_prior_key_instead_of_trusting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE FAIL-OPEN, CLOSED AT THE COMPOSITION ROOT (E3 repair). A healthy ACTIVE key plus a
    misprovisioned 1-byte PRIOR var used to compose a verifier that TRUSTED the 1-byte key — i.e. a
    key anyone can brute-force was a valid signing key for the whole grace window. The gate now
    refuses to compose, the same way a degenerate ACTIVE key already did (via `EnvelopeSigner`)."""
    monkeypatch.setenv(per_tenant_key_env_var(_TENANT), _KEY.decode())
    monkeypatch.setenv(per_tenant_prior_key_env_var(_TENANT), "x")  # 1 byte, the floor is 16
    with pytest.raises(EnvelopeSignatureError, match="prior .* verification key too short"):
        _gate(mode="kubernetes")
    # CONTROL: the identical composition with a HEALTHY prior key succeeds — so the refusal is the
    # floor, not the mere presence of a rotation var (cf. test_gate_uses_prior_key_when_present).
    monkeypatch.setenv(per_tenant_prior_key_env_var(_TENANT), _PRIOR_KEY.decode())
    _, verifier = _gate(mode="kubernetes")
    assert isinstance(verifier, EnvelopeVerifier)


def test_gate_no_cross_tenant_fallback_injected_keyset() -> None:
    """KEY-CONFUSION PROBE: a keyset holding ONLY tenant-B's key, resolving tenant-A in production,
    REFUSES — the gate never borrows tenant-B's key for tenant-A (E2's invariant, extended)."""
    keyset = LabeledFakeTenantKeyset({_OTHER: _OTHER_KEY})
    with pytest.raises(RuntimeError, match="no envelope-signing key"):
        _gate(mode="kubernetes", keyset=keyset)
    assert keyset.calls == [_TENANT]  # asked for tenant-A, got None, refused


def test_gate_env_no_cross_tenant_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env-backed twin: ONLY tenant-B's per-tenant key present; composing tenant-A refuses."""
    monkeypatch.setenv(per_tenant_key_env_var(_OTHER), _OTHER_KEY.decode())
    with pytest.raises(RuntimeError, match="no envelope-signing key"):
        _gate(mode="kubernetes")
