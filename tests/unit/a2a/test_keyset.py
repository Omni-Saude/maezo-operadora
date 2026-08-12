"""Unit tests for `maezo.a2a.keyset` — the PER-TENANT signing-key seam (ADR-0039 §4.4, decision 4).

This is leg E2's own proof surface. It pins the properties an adversarial gatekeeper (forgery
batteries + cross-tenant key-confusion probes) will attack:

  1. **env convention** — `per_tenant_key_env_var(tenant)` is `MAEZO_A2A_CARD_SIGNING_KEY__<T>`,
     upper-cased, double-underscore delimited, and never collides with the bare repo-wide var;
  2. **fail-closed tenant validation** — an out-of-convention tenant REFUSES (never coerced into a
     possibly-colliding variable name);
  3. **env-backed resolution** — mirrors `card_signing_key_from_env`'s posture per tenant
     (whitespace-strip, absent/blank -> None, present -> bytes);
  4. **NO cross-tenant fallback** (the key-confusion attack decision 4 exists to prevent) — a
     tenant with no provisioned key resolves to `None`, NEVER another tenant's key nor the bare
     repo-wide key;
  5. **no silent migration shim** — the seam never reads the bare `MAEZO_A2A_CARD_SIGNING_KEY`;
  6. **anti-masquerade** — the labeled test double is unreachable from production `src/maezo`.

Leg E3 EXTENDS the same seam with the ROTATION slot (ADR-0039 §4.3, owner decision 3: 7-day
rotation cadence / 7-day grace) — `per_tenant_prior_key_env_var` + `EnvTenantKeyset.prior_key_for`
— and this module pins the extension against the SAME posture, plus the verification keyset the
two slots assemble into (`build_verification_keyset`, the thing an `EnvelopeVerifier` trusts):

  7. **rotation naming** — the PRIOR var is `MAEZO_A2A_CARD_SIGNING_KEY_PRIOR__<T>`, and the PRIOR
     and ACTIVE var spaces are structurally disjoint (incl. the `*_prior`-named-tenant alias vector);
  8. **E2's posture, unchanged for the rotation slot** — absent/blank -> `None`, whitespace
     stripped, NO cross-tenant and NO repo-wide/active fallback, out-of-convention tenant REFUSED;
  9. **the assembled keyset** — a tenant's trusted keyset is EXACTLY its own active + prior key,
     indexed BY `key_id`; another tenant's prior key never enters it, a wrong/unknown `key_id` is
     refused even when the key MATERIAL is trusted, the grace ends when the operator deprovisions
     the prior var, and `MAX_SIGNATURE_AGE` bounds how long a prior-key signature stays acceptable.

Every defence in group 9 carries an in-test RED CONTROL: a locally-neutered keyset/verifier for
which the SAME probe flips to the fail-open answer, so no assertion here can pass vacuously.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from maezo.a2a import (
    CARD_SIGNING_KEY_ENV_VAR,
    Budget,
    DelegationEnvelope,
    EnvelopeSigner,
    EnvelopeVerifier,
    EnvTenantKeyset,
    TenantKeyset,
    build_verification_keyset,
    derive_key_id,
    per_tenant_key_env_var,
    per_tenant_prior_key_env_var,
)
from maezo.a2a.envelope_signing import (
    DEFAULT_REPLAY_EPOCH,
    MAX_SIGNATURE_AGE,
    EnvelopeSignatureError,
)

from .fakes import LabeledFakeTenantKeyset

if TYPE_CHECKING:
    from collections.abc import Mapping

_TENANT_A = "amh"
_TENANT_B = "outra"  # [a-z][a-z0-9_]* — a DIFFERENT tenant for the key-confusion probes
# Obvious test literals, NOT real secrets. Real keys are injected from vault/KMS (external/blocked,
# see docs/design/A2A-dispatcher-card-signing.md §6.2). >= MIN_SIGNING_KEY_BYTES so they are usable.
_KEY_A = b"tenant-a-per-tenant-signing-key-0123456789"
_KEY_B = b"tenant-b-per-tenant-signing-key-abcdefghij"
# The ROTATION (prior-in-grace) counterparts, distinct material from every active key above.
_PRIOR_KEY_A = b"tenant-a-prior-rotation-key-0123456789abc"
_PRIOR_KEY_B = b"tenant-b-prior-rotation-key-abcdefghijklm"


# --- per_tenant_key_env_var: the convention + injective, collision-free, fail-closed naming -----


def test_per_tenant_env_var_is_the_namespaced_upper_cased_convention() -> None:
    assert per_tenant_key_env_var("amh") == "MAEZO_A2A_CARD_SIGNING_KEY__AMH"
    assert per_tenant_key_env_var("outra") == "MAEZO_A2A_CARD_SIGNING_KEY__OUTRA"
    # Built from the SAME repo-wide prefix constant Card signing uses (owner decision 4: same seam).
    assert per_tenant_key_env_var("amh").startswith(CARD_SIGNING_KEY_ENV_VAR + "__")


def test_per_tenant_env_var_never_equals_the_bare_repo_wide_var() -> None:
    """The double-underscore suffix guarantees a per-tenant var can never be the bare repo-wide var
    — so the two key spaces (repo-wide precedent vs per-tenant) can never be silently confused."""
    for tenant in ("amh", "outra", "t1", "a_b"):
        assert per_tenant_key_env_var(tenant) != CARD_SIGNING_KEY_ENV_VAR


def test_per_tenant_env_var_is_injective_no_two_tenants_alias() -> None:
    """Upper-casing `[a-z0-9_]` is injective — distinct tenants can never map to the same var name
    (there is no hyphen->underscore folding that could alias `a-b` onto `a_b`)."""
    tenants = ["amh", "outra", "a_b", "ab", "t1", "t2", "tenant_x", "tenantx"]
    names = [per_tenant_key_env_var(t) for t in tenants]
    assert len(set(names)) == len(names), f"collision in {dict(zip(tenants, names, strict=True))}"


@pytest.mark.parametrize(
    "bad_tenant",
    # Hyphen (the A2A-registry docstring's example tenant style), uppercase, leading digit, special
    # chars, empty, whitespace — all outside `[a-z][a-z0-9_]*`. Underscores (incl. `__`) are NOT
    # here: they are valid per the platform convention and, since upper-casing stays injective, they
    # never alias two tenants onto one var name.
    ["outra-operadora", "Amh", "1tenant", "tenant!", "", "a b", "-", "amh-"],
)
def test_per_tenant_env_var_refuses_out_of_convention_tenant(bad_tenant: str) -> None:
    """Fail-closed: a tenant outside `[a-z][a-z0-9_]*` RAISES rather than being coerced into a
    possibly-colliding env var name — an ambiguous tenant->variable mapping is itself a
    cross-tenant-confusion vector (mirrors schema_for_tenant's anti-injection refusal)."""
    with pytest.raises(ValueError, match="not a valid tenant id"):
        per_tenant_key_env_var(bad_tenant)


# --- per_tenant_prior_key_env_var: rotation grace slot, collision-free with the active space (E3) -


def test_prior_key_env_var_is_the_rotation_namespaced_convention() -> None:
    """The rotation slot's name is the DEPLOYMENT contract (what a secret-injector must populate),
    so it is pinned as a literal: a silent rename would leave every provisioned prior key unread and
    end the grace window early, with no signal at all."""
    assert per_tenant_prior_key_env_var("amh") == "MAEZO_A2A_CARD_SIGNING_KEY_PRIOR__AMH"
    assert per_tenant_prior_key_env_var("outra") == "MAEZO_A2A_CARD_SIGNING_KEY_PRIOR__OUTRA"
    # Built from the SAME repo-wide prefix constant (decision 4: ONE key-custody namespace), with
    # `_PRIOR` on the PREFIX and the tenant still the `__`-delimited suffix...
    assert per_tenant_prior_key_env_var("amh").startswith(CARD_SIGNING_KEY_ENV_VAR + "_PRIOR__")
    # ...and it is never the same variable as that tenant's ACTIVE slot (two distinct storages).
    assert per_tenant_prior_key_env_var("amh") != per_tenant_key_env_var("amh")


def test_prior_and_active_var_spaces_are_structurally_disjoint() -> None:
    """The rotation PRIOR var (`..._PRIOR__<T>`) can NEVER collide with the ACTIVE var (`...__<T2>`)
    for ANY tenants — the char right after the shared prefix is `_` (2nd of `__`) for active vs `P`
    (of `_PRIOR`) for prior. If the two spaces could alias, one tenant's ACTIVE key would land in
    another's PRIOR (grace) slot: a cross-tenant trust leak through the rotation extension."""
    # The alias vector, pinned as LITERALS: a tenant literally NAMED `foo_prior` is the only way the
    # active space could reach into the prior space, and it does not.
    assert per_tenant_key_env_var("foo_prior") == "MAEZO_A2A_CARD_SIGNING_KEY__FOO_PRIOR"
    assert per_tenant_prior_key_env_var("foo") == "MAEZO_A2A_CARD_SIGNING_KEY_PRIOR__FOO"
    tenants = ["amh", "outra", "foo", "foo_prior", "a_b", "ab", "prior", "x__prior", "tenant_prior"]
    active = [per_tenant_key_env_var(t) for t in tenants]
    prior = [per_tenant_prior_key_env_var(t) for t in tenants]
    assert len(set(active + prior)) == len(active + prior), "active/prior collision"
    # Neither space is ever the bare repo-wide var.
    for name in active + prior:
        assert name != CARD_SIGNING_KEY_ENV_VAR


@pytest.mark.parametrize(
    # The SAME battery the active var is held to (parity is the point: the rotation slot must not be
    # the laxer of the two) — hyphen, uppercase, leading digit, special chars, empty, whitespace.
    "bad_tenant",
    ["outra-operadora", "Amh", "1tenant", "tenant!", "", "a b", "-", "amh-"],
)
def test_prior_key_env_var_refuses_out_of_convention_tenant(bad_tenant: str) -> None:
    """Fail-closed, same as the active var: an out-of-convention tenant RAISES rather than being
    coerced into a possibly-colliding variable name (the ambiguous-mapping confusion vector)."""
    with pytest.raises(ValueError, match="not a valid tenant id"):
        per_tenant_prior_key_env_var(bad_tenant)


# --- EnvTenantKeyset: per-tenant resolution mirrors card_signing_key_from_env, per tenant --------


def test_env_keyset_resolves_the_present_per_tenant_key() -> None:
    ks = EnvTenantKeyset({per_tenant_key_env_var(_TENANT_A): _KEY_A.decode()})
    assert ks.key_for(_TENANT_A) == _KEY_A


def test_env_keyset_absent_key_is_none() -> None:
    assert EnvTenantKeyset({}).key_for(_TENANT_A) is None


@pytest.mark.parametrize("blank", [" ", "   ", "\n", "\t", " \n "])
def test_env_keyset_whitespace_only_is_unset(blank: str) -> None:
    """A whitespace-only value is treated as unset -> None (dev fail-safe, same as absent), exactly
    like `card_signing_key_from_env`."""
    ks = EnvTenantKeyset({per_tenant_key_env_var(_TENANT_A): blank})
    assert ks.key_for(_TENANT_A) is None


def test_env_keyset_strips_transport_whitespace() -> None:
    """Leading/trailing whitespace (e.g. a mounted secret file's trailing newline) is a transport
    artifact and is stripped, so the same secret resolves identically however it was materialized."""
    ks = EnvTenantKeyset({per_tenant_key_env_var(_TENANT_A): f"  {_KEY_A.decode()}  \n"})
    assert ks.key_for(_TENANT_A) == _KEY_A


def test_env_keyset_reads_live_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default (no injected mapping) reads the live process env — the same seam a secret
    injector populates — and sees a key set AFTER construction (monkeypatch mutates os.environ)."""
    monkeypatch.delenv(per_tenant_key_env_var(_TENANT_A), raising=False)
    ks = EnvTenantKeyset()
    assert ks.key_for(_TENANT_A) is None
    monkeypatch.setenv(per_tenant_key_env_var(_TENANT_A), _KEY_A.decode())
    assert ks.key_for(_TENANT_A) == _KEY_A


# --- NO cross-tenant fallback — the key-confusion attack decision 4 exists to prevent ------------


def test_env_keyset_no_cross_tenant_fallback() -> None:
    """KEY-CONFUSION PROBE (env-backed): only tenant-B's key is present; resolving tenant-A returns
    None — it NEVER falls back to tenant-B's key. Falling back would let an un-provisioned tenant
    sign/verify under another tenant's key, which is exactly the attack."""
    ks = EnvTenantKeyset({per_tenant_key_env_var(_TENANT_B): _KEY_B.decode()})
    assert ks.key_for(_TENANT_B) == _KEY_B  # B resolves...
    assert ks.key_for(_TENANT_A) is None  # ...A does NOT borrow B's key


def test_env_keyset_does_not_fall_back_to_the_bare_repo_wide_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NO SILENT MIGRATION SHIM: with ONLY the bare repo-wide `MAEZO_A2A_CARD_SIGNING_KEY` set (the
    pre-per-tenant precedent) and no per-tenant var, `key_for` returns None — it never reuses the
    repo-wide key as a silent per-tenant default (§4.4 forbids the untimed silent shim)."""
    monkeypatch.setenv(CARD_SIGNING_KEY_ENV_VAR, "a-repo-wide-key-that-must-not-be-reused-123456")
    monkeypatch.delenv(per_tenant_key_env_var(_TENANT_A), raising=False)
    assert EnvTenantKeyset().key_for(_TENANT_A) is None


# --- EnvTenantKeyset.prior_key_for: the ROTATION slot, held to the IDENTICAL E2 posture (E3) -----


def test_env_keyset_resolves_the_present_prior_key() -> None:
    """The prior slot resolves from ITS OWN variable — and, in the same state, the ACTIVE variable
    is untouched (`None`), so the two slots are read independently rather than one shadowing the
    other."""
    ks = EnvTenantKeyset({per_tenant_prior_key_env_var(_TENANT_A): _PRIOR_KEY_A.decode()})
    assert ks.prior_key_for(_TENANT_A) == _PRIOR_KEY_A
    assert ks.key_for(_TENANT_A) is None  # the prior var does NOT satisfy the active slot


def test_env_keyset_absent_prior_key_is_none() -> None:
    """Steady state (no rotation in progress): no prior var -> `None`, so the verification keyset
    trusts the active key ALONE."""
    assert EnvTenantKeyset({}).prior_key_for(_TENANT_A) is None


@pytest.mark.parametrize("blank", [" ", "   ", "\n", "\t", " \n "])
def test_env_keyset_whitespace_only_prior_is_unset(blank: str) -> None:
    """A blank-but-PRESENT prior var is treated as unset -> `None` (never an empty-bytes key silently
    admitted into the trusted keyset), exactly as the active slot treats it."""
    ks = EnvTenantKeyset({per_tenant_prior_key_env_var(_TENANT_A): blank})
    assert ks.prior_key_for(_TENANT_A) is None


def test_env_keyset_strips_transport_whitespace_from_the_prior_key() -> None:
    """A mounted secret file's trailing newline is a transport artifact: the SAME prior secret must
    resolve to the SAME bytes however it was materialized — otherwise its derived `key_id` would
    differ and the rotation grace would silently not apply."""
    ks = EnvTenantKeyset({per_tenant_prior_key_env_var(_TENANT_A): f"  {_PRIOR_KEY_A.decode()}  \n"})
    assert ks.prior_key_for(_TENANT_A) == _PRIOR_KEY_A


def test_env_keyset_prior_reads_live_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default (no injected mapping) reads the live process env — the seam a secret injector
    mutates — and sees a prior key provisioned AFTER construction (a rotation starting mid-process)."""
    monkeypatch.delenv(per_tenant_prior_key_env_var(_TENANT_A), raising=False)
    ks = EnvTenantKeyset()
    assert ks.prior_key_for(_TENANT_A) is None
    monkeypatch.setenv(per_tenant_prior_key_env_var(_TENANT_A), _PRIOR_KEY_A.decode())
    assert ks.prior_key_for(_TENANT_A) == _PRIOR_KEY_A


def test_env_keyset_prior_key_does_not_fall_back_to_active_or_repo_wide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The prior slot reads ONLY the prior var — never that tenant's ACTIVE var, never the bare
    repo-wide var (no silent shim, ADR-0039 §4.4). A fallback here would permanently trust the
    active key under a second key_id, making the grace window meaningless."""
    monkeypatch.setenv(per_tenant_key_env_var(_TENANT_A), _KEY_A.decode())  # active set...
    monkeypatch.setenv(CARD_SIGNING_KEY_ENV_VAR, "repo-wide-must-not-be-reused-0123456789")
    monkeypatch.delenv(per_tenant_prior_key_env_var(_TENANT_A), raising=False)
    ks = EnvTenantKeyset()
    # POSITIVE CONTROL first: this object really is reading the live env we just populated, so the
    # `None` below is a genuine refusal and not a dead read of an empty mapping.
    assert ks.key_for(_TENANT_A) == _KEY_A
    assert ks.prior_key_for(_TENANT_A) is None  # ...prior stays None


def test_env_keyset_no_cross_tenant_prior_fallback() -> None:
    """KEY-CONFUSION PROBE, rotation slot: with ONLY tenant-B's prior key provisioned, tenant-A's
    prior resolves to `None` — an un-rotated tenant NEVER borrows another tenant's grace key."""
    ks = EnvTenantKeyset({per_tenant_prior_key_env_var(_TENANT_B): _PRIOR_KEY_B.decode()})
    assert ks.prior_key_for(_TENANT_B) == _PRIOR_KEY_B  # B's prior resolves...
    assert ks.prior_key_for(_TENANT_A) is None  # ...A does NOT borrow it
    assert ks.key_for(_TENANT_A) is None  # nor does it leak into A's ACTIVE slot


def test_env_keyset_prior_keys_stay_separated_when_both_tenants_are_rotating() -> None:
    """Both tenants mid-rotation in ONE process: each resolves its OWN prior key, distinct bytes —
    the shared-mapping variant of the confusion probe (a single `os.environ` holds all four keys)."""
    ks = EnvTenantKeyset(
        {
            per_tenant_key_env_var(_TENANT_A): _KEY_A.decode(),
            per_tenant_prior_key_env_var(_TENANT_A): _PRIOR_KEY_A.decode(),
            per_tenant_key_env_var(_TENANT_B): _KEY_B.decode(),
            per_tenant_prior_key_env_var(_TENANT_B): _PRIOR_KEY_B.decode(),
        }
    )
    assert ks.prior_key_for(_TENANT_A) == _PRIOR_KEY_A
    assert ks.prior_key_for(_TENANT_B) == _PRIOR_KEY_B
    assert ks.prior_key_for(_TENANT_A) != ks.prior_key_for(_TENANT_B)
    assert ks.key_for(_TENANT_A) == _KEY_A  # and the active slots stay separated too
    assert ks.key_for(_TENANT_B) == _KEY_B


def test_env_keyset_prior_value_is_not_length_filtered_at_the_seam() -> None:
    """SEAM PARITY: like `key_for`, `prior_key_for` reports what the injector placed there and does
    NOT apply the `MIN_SIGNING_KEY_BYTES` floor — by design, the floor is a DOWNSTREAM job so a
    garbage key fails loudly there instead of silently reading as 'unset'. For the ACTIVE key that
    downstream refusal is `EnvelopeSigner`'s (asserted here as the paired half). NOTE: no equivalent
    floor exists on the PRIOR key's path into the verification keyset — reported to the orchestrator
    as an E3 source finding; this test pins ONLY the seam's own (correct) pass-through."""
    ks = EnvTenantKeyset({per_tenant_prior_key_env_var(_TENANT_A): "x"})  # 1 byte, floor is 16
    assert ks.prior_key_for(_TENANT_A) == b"x"  # unfiltered at the seam...
    with pytest.raises(EnvelopeSignatureError, match="too short"):  # ...refused downstream (active)
        EnvelopeSigner(tenant=_TENANT_A, signing_key=b"x", key_id=_UNKNOWN_KEY_ID)


# --- The ROTATION VERIFICATION KEYSET the two slots assemble into (ADR-0039 §4.3, leg E3) --------
#
# `build_verification_keyset` turns the seam's (active, prior) pair into the `{key_id: key}` map an
# `EnvelopeVerifier` trusts. These probes assert the ASSEMBLED trust, behaviourally (verify() True /
# False), because that map is what an attacker's forgery is actually checked against.

#: Pinned key_id LITERALS. Provenance: `derive_key_id(k)` = `env-sha256:` + first 16 hex of
#: sha256(k) (`envelope_signing.py:127-137`), computed once from the key literals above and
#: re-asserted in `test_rotation_key_ids_are_the_pinned_fingerprints_of_the_key_material`.
_ACTIVE_KEY_ID_A = "env-sha256:64091c6be3e8ef59"
_PRIOR_KEY_ID_A = "env-sha256:8936e678ff0f4b50"
_PRIOR_KEY_ID_B = "env-sha256:3261884dfed60c20"
#: A key_id that is not the fingerprint of ANY key — the "never provisioned" id.
_UNKNOWN_KEY_ID = "env-sha256:0000000000000000"

#: A fixed clock so the max-signature-age arithmetic is deterministic.
_NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _rotating_env(*, tenant_a_prior: bool = True, tenant_b_prior: bool = False) -> dict[str, str]:
    """The process env a deployment presents: tenant-A always has an ACTIVE key; the prior (grace)
    keys are switched on/off per probe."""
    env = {per_tenant_key_env_var(_TENANT_A): _KEY_A.decode()}
    if tenant_a_prior:
        env[per_tenant_prior_key_env_var(_TENANT_A)] = _PRIOR_KEY_A.decode()
    if tenant_b_prior:
        env[per_tenant_key_env_var(_TENANT_B)] = _KEY_B.decode()
        env[per_tenant_prior_key_env_var(_TENANT_B)] = _PRIOR_KEY_B.decode()
    return env


def _keyset_for(tenant: str, environ: Mapping[str, str]) -> tuple[dict[str, bytes], str]:
    """Assemble `tenant`'s trusted keyset exactly the way the composition root does: BOTH keys out of
    the SAME per-tenant seam and nothing else (`a2a_composition._require_envelope_signing_or_fail_closed`)."""
    ks = EnvTenantKeyset(environ)
    active = ks.key_for(tenant)
    assert active is not None, "probe setup: the tenant's ACTIVE key must be provisioned"
    return build_verification_keyset(active_key=active, prior_key=ks.prior_key_for(tenant))


def _verifier(trusted: dict[str, bytes], active_key_id: str) -> EnvelopeVerifier:
    return EnvelopeVerifier(
        trusted_keys=trusted,
        active_key_id=active_key_id,
        current_epoch=DEFAULT_REPLAY_EPOCH,
        max_signature_age=MAX_SIGNATURE_AGE,
    )


def _signed(*, key: bytes, key_id: str | None = None, at: datetime = _NOW) -> DelegationEnvelope:
    """A tenant-A envelope signed with `key` (optionally CLAIMING a `key_id` that key did not
    produce — the retag forgery). Signing is what an attacker holding `key` can do."""
    envelope = DelegationEnvelope.root(
        task_id="task-rotation-1",
        task_type="authorization.analyze",
        origin="helena",
        target="rafael",
        tenant=_TENANT_A,
        budget=Budget(tokens=64, time_ms=60_000, cost_per_hop=1),
        payload_ref="fhir://Coverage/rotation-1",
        deadline=_NOW + timedelta(hours=1),
        payload_meta={"codigo_procedimento_tuss": "10101012"},
    )
    signer = EnvelopeSigner(
        tenant=_TENANT_A, signing_key=key, key_id=key_id if key_id is not None else derive_key_id(key)
    )
    return signer.sign(envelope, now=at)


class _NeuteredCrossTenantPriorKeyset(EnvTenantKeyset):
    """RED CONTROL ONLY — the VULNERABLE variant of the seam, defined here so the no-cross-tenant
    assertions can be shown to fail when the defence is removed. It does what the real seam refuses:
    when a tenant has no prior key of its own, it borrows ANY other tenant's prior key. Never
    importable from production (it lives in this test module and subclasses nothing production
    reads)."""

    def prior_key_for(self, tenant: str) -> bytes | None:
        own = super().prior_key_for(tenant)
        if own is not None:
            return own
        for other in (_TENANT_A, _TENANT_B):  # the forbidden cross-tenant sweep
            borrowed = super().prior_key_for(other)
            if borrowed is not None:
                return borrowed
        return None


def test_rotation_key_ids_are_the_pinned_fingerprints_of_the_key_material() -> None:
    """The keyset is INDEXED by `key_id`, so the derivation is a wire-visible contract: a change to
    it instantly un-trusts every in-flight signature. Pinned as literals (not recomputed with the
    same call under test) so 'both sides moved together' cannot hide the change."""
    assert derive_key_id(_KEY_A) == _ACTIVE_KEY_ID_A
    assert derive_key_id(_PRIOR_KEY_A) == _PRIOR_KEY_ID_A
    assert derive_key_id(_PRIOR_KEY_B) == _PRIOR_KEY_ID_B
    # Distinct material -> distinct ids (the property that lets active and prior coexist in one map).
    assert len({_ACTIVE_KEY_ID_A, _PRIOR_KEY_ID_A, _PRIOR_KEY_ID_B}) == 3


def test_rotation_keyset_is_exactly_this_tenants_own_two_keys_indexed_by_key_id() -> None:
    """With ALL FOUR keys present in one process env, tenant-A's trusted keyset is EXACTLY
    `{A-active-id: A-active, A-prior-id: A-prior}` — an exhaustive equality, so any extra key
    (another tenant's, a repo-wide one) fails the assertion rather than hiding in a superset."""
    trusted, active_key_id = _keyset_for(_TENANT_A, _rotating_env(tenant_b_prior=True))
    assert trusted == {_ACTIVE_KEY_ID_A: _KEY_A, _PRIOR_KEY_ID_A: _PRIOR_KEY_A}
    assert active_key_id == _ACTIVE_KEY_ID_A  # signing continues under the ACTIVE key only
    assert _PRIOR_KEY_ID_B not in trusted  # tenant-B's grace key is not tenant-A's business
    assert derive_key_id(_KEY_B) not in trusted


def test_rotation_keyset_never_admits_another_tenants_prior_key() -> None:
    """KEY-CONFUSION PROBE (the E2 invariant, extended to the rotation slot): tenant-A is NOT
    rotating, tenant-B IS. An envelope forged with tenant-B's prior key material is REJECTED by
    tenant-A's verifier, and A's keyset stays a single entry — nothing borrowed."""
    env = _rotating_env(tenant_a_prior=False, tenant_b_prior=True)
    trusted, active_key_id = _keyset_for(_TENANT_A, env)
    assert trusted == {_ACTIVE_KEY_ID_A: _KEY_A}  # exactly one key: A's own active
    forged = _signed(key=_PRIOR_KEY_B)  # an A-envelope signed with tenant-B's grace key
    assert _verifier(trusted, active_key_id).verify(forged, now=_NOW) is False

    # RED CONTROL: neuter the no-cross-tenant rule (borrow another tenant's prior key) and the SAME
    # forged envelope now VERIFIES — so the assertion above is the defence, not an accident.
    neutered = _NeuteredCrossTenantPriorKeyset(env)
    borrowed = neutered.prior_key_for(_TENANT_A)
    assert borrowed == _PRIOR_KEY_B  # the neuter really did reach across tenants
    n_trusted, n_active = build_verification_keyset(active_key=_KEY_A, prior_key=borrowed)
    assert _verifier(n_trusted, n_active).verify(forged, now=_NOW) is True


def test_prior_key_verifies_during_the_grace_and_stops_when_it_is_deprovisioned() -> None:
    """GRACE SEMANTICS. The seam carries no clock: the grace window is OPEN exactly while the prior
    var is provisioned. An envelope signed under the prior key verifies while it is (the rotation
    guarantee — in-flight work is not broken by a key roll) and is rejected the moment the operator
    removes the var (the same probe, one env entry apart — its own control)."""
    signed = _signed(key=_PRIOR_KEY_A)
    during_trusted, during_active = _keyset_for(_TENANT_A, _rotating_env(tenant_a_prior=True))
    assert _verifier(during_trusted, during_active).verify(signed, now=_NOW) is True
    after_trusted, after_active = _keyset_for(_TENANT_A, _rotating_env(tenant_a_prior=False))
    assert _PRIOR_KEY_ID_A not in after_trusted  # deprovisioned -> dropped from the trusted map
    assert _verifier(after_trusted, after_active).verify(signed, now=_NOW) is False


def test_prior_key_signature_older_than_the_grace_bound_is_refused_while_still_provisioned() -> None:
    """OUTSIDE THE GRACE, time-wise. Because the seam has no clock, a prior var left in place past
    its window must not extend trust forever: `MAX_SIGNATURE_AGE` is the compensating bound, set to
    the rotation grace length itself (7 days — ADR-0039 owner decision 3, cadence 7d / grace 7d), so
    a prior-key signature can never be accepted beyond one rotation cycle."""
    assert timedelta(days=7) == MAX_SIGNATURE_AGE  # provenance: ADR-0039 §Decisoes do dono, row 3
    trusted, active_key_id = _keyset_for(_TENANT_A, _rotating_env(tenant_a_prior=True))
    verifier = _verifier(trusted, active_key_id)
    signed = _signed(key=_PRIOR_KEY_A, at=_NOW)
    assert verifier.verify(signed, now=_NOW + timedelta(days=6)) is True  # inside the bound
    assert verifier.verify(signed, now=_NOW + timedelta(days=7, seconds=1)) is False  # past it


def test_a_signature_claiming_the_wrong_trusted_key_id_is_refused() -> None:
    """KEY_ID DISCRIMINATION. The keyset is looked up BY `key_id` and the id is bound INTO the
    digest, so a signature made with the PRIOR key but CLAIMING the ACTIVE id is refused — even
    though BOTH the claimed id and the used material are individually trusted."""
    trusted, active_key_id = _keyset_for(_TENANT_A, _rotating_env(tenant_a_prior=True))
    retagged = _signed(key=_PRIOR_KEY_A, key_id=_ACTIVE_KEY_ID_A)  # prior material, active id
    assert _verifier(trusted, active_key_id).verify(retagged, now=_NOW) is False
    # CONTROL: the same material under its HONEST id verifies against the same keyset — so the
    # refusal above is the id<->material binding, not a distrusted key.
    assert _verifier(trusted, active_key_id).verify(_signed(key=_PRIOR_KEY_A), now=_NOW) is True
    # RED CONTROL: desync the index (map the ACTIVE id to the PRIOR material, i.e. neuter the
    # binding) and the retagged forgery verifies.
    assert _verifier({_ACTIVE_KEY_ID_A: _PRIOR_KEY_A}, _ACTIVE_KEY_ID_A).verify(retagged, now=_NOW) is True


def test_a_signature_under_an_unprovisioned_key_id_is_refused_even_with_trusted_material() -> None:
    """A key_id that is in NO tenant's keyset (never provisioned / already retired) is refused at the
    lookup, before any MAC work — trusted key MATERIAL alone never buys admission."""
    trusted, active_key_id = _keyset_for(_TENANT_A, _rotating_env(tenant_a_prior=True))
    signed = _signed(key=_KEY_A, key_id=_UNKNOWN_KEY_ID)  # the ACTIVE material, an unknown id
    assert _verifier(trusted, active_key_id).verify(signed, now=_NOW) is False
    # RED CONTROL: admit that id into the keyset and the very same envelope verifies — so the
    # rejection came from the key_id lookup, not from a malformed signature.
    assert _verifier({**trusted, _UNKNOWN_KEY_ID: _KEY_A}, active_key_id).verify(signed, now=_NOW) is True


def test_a_prior_key_alone_is_never_promoted_into_a_tenants_active_key() -> None:
    """FAIL-CLOSED on a half-provisioned rotation: with ONLY the prior var set, the tenant has NO
    active key, so no keyset can be anchored — the prior key is never promoted to fill the gap
    (which would keep a retired key signing indefinitely). The composition root turns the `None`
    active key into its refusal; the verifier independently refuses an unanchored keyset."""
    ks = EnvTenantKeyset({per_tenant_prior_key_env_var(_TENANT_A): _PRIOR_KEY_A.decode()})
    assert ks.key_for(_TENANT_A) is None  # no active key -> composition refuses upstream
    assert ks.prior_key_for(_TENANT_A) == _PRIOR_KEY_A  # ...even though the prior key IS present
    with pytest.raises(EnvelopeSignatureError, match="active_key_id must be present"):
        _verifier({_PRIOR_KEY_ID_A: _PRIOR_KEY_A}, _ACTIVE_KEY_ID_A)


# --- LabeledFakeTenantKeyset: same no-fallback contract, for injectable probes -------------------


def test_labeled_fake_keyset_resolves_only_its_exact_tenants() -> None:
    ks = LabeledFakeTenantKeyset({_TENANT_A: _KEY_A})
    assert ks.key_for(_TENANT_A) == _KEY_A
    assert ks.key_for(_TENANT_B) is None  # no cross-tenant fallback
    assert ks.calls == [_TENANT_A, _TENANT_B]  # both resolutions recorded


def test_labeled_fake_keyset_no_cross_tenant_fallback_under_shared_construction() -> None:
    """KEY-CONFUSION PROBE (injectable): a keyset holding BOTH tenants' keys still never leaks one
    tenant's key to the other — each resolves its OWN key, distinct bytes."""
    ks = LabeledFakeTenantKeyset({_TENANT_A: _KEY_A, _TENANT_B: _KEY_B})
    assert ks.key_for(_TENANT_A) == _KEY_A
    assert ks.key_for(_TENANT_B) == _KEY_B
    assert ks.key_for(_TENANT_A) != ks.key_for(_TENANT_B)


def test_labeled_fake_keyset_prior_slot_has_the_same_no_fallback_contract() -> None:
    """The injectable double's ROTATION half must not be laxer than the real seam, or a composition
    probe wired with it would prove nothing: only the exact tenants given a prior key resolve one,
    and the prior slot never answers with the active key (nor another tenant's prior key)."""
    ks = LabeledFakeTenantKeyset({_TENANT_A: _KEY_A}, prior_keys={_TENANT_B: _PRIOR_KEY_B})
    assert ks.prior_key_for(_TENANT_B) == _PRIOR_KEY_B  # B is rotating...
    assert ks.prior_key_for(_TENANT_A) is None  # ...A borrows nothing, and its active key
    assert ks.key_for(_TENANT_A) == _KEY_A  # is NOT offered as a prior key
    assert ks.prior_calls == [_TENANT_B, _TENANT_A]  # both rotation resolutions recorded


# --- Protocol conformance (structural + runtime_checkable) ---------------------------------------


def test_both_impls_satisfy_the_tenant_keyset_protocol() -> None:
    assert isinstance(EnvTenantKeyset(), TenantKeyset)
    assert isinstance(LabeledFakeTenantKeyset({_TENANT_A: _KEY_A}), TenantKeyset)


# --- anti-masquerade: the labeled fake is unreachable from production src/maezo ------------------

_SRC_MAEZO = Path(__file__).resolve().parents[3] / "src" / "maezo"


def test_no_production_module_references_the_labeled_fake_keyset() -> None:
    """ANTI-MASQUERADE. The labeled fake must NEVER be reachable from production. A text sweep of the
    ENTIRE `src/maezo` tree asserts zero references to it or its module — so the effect-chokepoint
    fence's 'no test double in a composition root' invariant is backed by a direct reachability
    proof here, not merely by the fence scanning `src/`."""
    forbidden = ("LabeledFakeTenantKeyset", "tests.unit.a2a.fakes", "tests.unit.a2a")
    hits: list[str] = []
    for path in _SRC_MAEZO.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits.extend(f"{path}: {token}" for token in forbidden if token in text)
    assert hits == [], f"labeled fake keyset leaked into production source: {hits}"
