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
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maezo.a2a import (
    CARD_SIGNING_KEY_ENV_VAR,
    EnvTenantKeyset,
    TenantKeyset,
    per_tenant_key_env_var,
)

from .fakes import LabeledFakeTenantKeyset

_TENANT_A = "amh"
_TENANT_B = "outra"  # [a-z][a-z0-9_]* — a DIFFERENT tenant for the key-confusion probes
# Obvious test literals, NOT real secrets. Real keys are injected from vault/KMS (external/blocked,
# see docs/design/A2A-dispatcher-card-signing.md §6.2). >= MIN_SIGNING_KEY_BYTES so they are usable.
_KEY_A = b"tenant-a-per-tenant-signing-key-0123456789"
_KEY_B = b"tenant-b-per-tenant-signing-key-abcdefghij"


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
