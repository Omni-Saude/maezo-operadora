"""Unit tests for CeilingResolver (T1.9 — financial ceilings stop being decorative, defect B3).

Hand-written fixtures (tmp_path) do NOT re-read the real L0-core — they prove the resolver
mechanics in isolation (the real ceiling=0 wiring is proven in the AUTH/REEMBOLSO worker
property tests):
  - positive ceiling: value <= ceiling (inclusive at the boundary) -> True; above -> False;
  - ceiling 0 / absent / invalid / negative / bool -> ALWAYS False (fail-closed, D-07 state);
  - unloadable matrix (missing/broken file, overlay referencing an unknown action) -> False
    (config unavailable NEVER auto-approves — the resolver's swallow-to-False posture);
  - the tenant overlay refines the ceiling (params merge via pep._apply_overlay).

Note: every synthetic core must carry ALL HARD_ACTIONS as L0 — pep._parse_core validates the
frozen list's presence (defence-in-depth) even in fixtures.

B-1 addendum (blocker, money, fail-OPEN): the VALUE side of the comparison used to be raw — no
type check, no lower bound. The `value_cents` vector block below pins the generalized guard
(`_is_valid_value_cents`) that closes it, and pins that the inclusive-at-the-teto boundary and
AUTH's existing behaviour for valid ints are UNCHANGED by it.
"""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import structlog.testing

from maezo.agents import resolve_spec_dir
from maezo.gateway.pep import load_matrix
from maezo.tools.workers.auth import _CEILING_ACTION as AUTH_ACTION
from maezo.tools.workers.auth import _CEILING_PARAM as AUTH_PARAM
from maezo.tools.workers.ceilings import CeilingResolver, _is_valid_value_cents
from maezo.tools.workers.pagto import _CEILING_ACTION as PAGTO_ACTION
from maezo.tools.workers.pagto import _CEILING_PARAM as PAGTO_PARAM
from maezo.tools.workers.reembolso import _CEILING_ACTION as REEMBOLSO_ACTION
from maezo.tools.workers.reembolso import _CEILING_PARAM as REEMBOLSO_PARAM

# The code-frozen hard items (pep.HARD_ACTIONS) — mandatory in any valid core.
_HARD_BLOCK = """\
  clinical_decision:      { level: L0, hard: true }
  authorization_denial:   { level: L0, hard: true }
  nip_manter_negativa:    { level: L0, hard: true }
  fraud_accusation:       { level: L0, hard: true }
  contract_termination:   { level: L0, hard: true }
"""


def _write_core(tmp_path: Path, actions_yaml: str) -> Path:
    core = tmp_path / "L0-core.yaml"
    core.write_text(f"version: 1\nactions:\n{_HARD_BLOCK}{actions_yaml}", encoding="utf-8")
    return core


# ---------------------------------------------------------------------------
# Positive ceiling — within / above / boundary
# ---------------------------------------------------------------------------


def test_positive_ceiling_within_and_above(tmp_path: Path) -> None:
    """Ceiling R$500: 50000 cents (boundary, inclusive) -> True; 50001 -> False; 0 -> True (0 <= teto)."""
    core = _write_core(tmp_path, "  authorization_approval: { level: L2, params: { max_value_brl: 500 } }\n")
    resolver = CeilingResolver(core_path=core)

    kw = {"tenant": "amh", "action": "authorization_approval", "param": "max_value_brl"}
    assert resolver.within_l2_ceiling(**kw, value_cents=49999) is True
    assert resolver.within_l2_ceiling(**kw, value_cents=50000) is True, "boundary inclusive (<= teto)"
    assert resolver.within_l2_ceiling(**kw, value_cents=50001) is False
    assert resolver.within_l2_ceiling(**kw, value_cents=0) is True


def test_high_value_threshold_param(tmp_path: Path) -> None:
    """The same resolver serves `high_value_payment.threshold_brl` (PAGTO): R$100k -> 10MM cents."""
    core = _write_core(tmp_path, "  high_value_payment: { level: L1, params: { threshold_brl: 100000 } }\n")
    resolver = CeilingResolver(core_path=core)

    kw = {"tenant": "amh", "action": "high_value_payment", "param": "threshold_brl"}
    assert resolver.within_l2_ceiling(**kw, value_cents=10_000_000) is True
    assert resolver.within_l2_ceiling(**kw, value_cents=10_000_001) is False


# ---------------------------------------------------------------------------
# FAIL-CLOSED — ceiling 0 / absent / invalid / negative / unavailable matrix
# ---------------------------------------------------------------------------


def test_ceiling_zero_is_fail_closed(tmp_path: Path) -> None:
    """Ceiling 0 (D-07 state): NEVER within teto, even value 0 (fail-closed, not `0 <= 0`)."""
    core = _write_core(tmp_path, "  authorization_approval: { level: L2, params: { max_value_brl: 0 } }\n")
    resolver = CeilingResolver(core_path=core)
    kw = {"tenant": "amh", "action": "authorization_approval", "param": "max_value_brl"}
    assert resolver.within_l2_ceiling(**kw, value_cents=0) is False
    assert resolver.within_l2_ceiling(**kw, value_cents=1) is False


def test_missing_action_param_or_negative_is_fail_closed(tmp_path: Path) -> None:
    """Action absent from matrix, param absent, non-numeric param, or negative -> False."""
    core = _write_core(
        tmp_path,
        "  authorization_approval: { level: L2, params: { requires: dmn_favorable } }\n"
        "  reembolso_auto_approval: { level: L2, params: { max_value_brl: nao-e-numero } }\n"
        "  high_value_payment: { level: L1, params: { threshold_brl: -5 } }\n",
    )
    resolver = CeilingResolver(core_path=core)

    # unknown action
    assert (
        resolver.within_l2_ceiling(
            tenant="amh", action="acao_inexistente", param="max_value_brl", value_cents=1
        )
        is False
    )
    # absent param
    assert (
        resolver.within_l2_ceiling(
            tenant="amh", action="authorization_approval", param="max_value_brl", value_cents=1
        )
        is False
    )
    # non-numeric param
    assert (
        resolver.within_l2_ceiling(
            tenant="amh", action="reembolso_auto_approval", param="max_value_brl", value_cents=1
        )
        is False
    )
    # negative ceiling (clamped to 0)
    assert (
        resolver.within_l2_ceiling(
            tenant="amh", action="high_value_payment", param="threshold_brl", value_cents=1
        )
        is False
    )


def test_bool_ceiling_is_fail_closed(tmp_path: Path) -> None:
    """A YAML `max_value_brl: true` is NOT a valid teto (bool-is-int trap) -> False."""
    core = _write_core(
        tmp_path,
        "  authorization_approval: { level: L2, params: { max_value_brl: true } }\n",
    )
    resolver = CeilingResolver(core_path=core)
    assert (
        resolver.within_l2_ceiling(
            tenant="amh", action="authorization_approval", param="max_value_brl", value_cents=1
        )
        is False
    )
    assert resolver.ceiling_brl(tenant="amh", action="authorization_approval", param="max_value_brl") == 0


def test_unloadable_matrix_is_fail_closed(tmp_path: Path) -> None:
    """Missing or malformed core -> matrix None -> ceiling 0 -> False (broken config NEVER auto-approves)."""
    missing = CeilingResolver(core_path=tmp_path / "nao-existe.yaml")
    assert (
        missing.within_l2_ceiling(
            tenant="amh", action="authorization_approval", param="max_value_brl", value_cents=1
        )
        is False
    )

    broken_path = tmp_path / "broken.yaml"
    broken_path.write_text("nao e um mapa de politica", encoding="utf-8")
    broken = CeilingResolver(core_path=broken_path)
    assert (
        broken.within_l2_ceiling(
            tenant="amh", action="authorization_approval", param="max_value_brl", value_cents=1
        )
        is False
    )


# ---------------------------------------------------------------------------
# B-1 — the VALUE side of the comparison is fail-closed too
#
# Every vector below is asserted against a GENEROUS, VALID teto (R$500 = 50000 centavos), so a
# False can only come from the value guard — never from the pre-existing ceiling-0 posture. That
# is what makes these tests bite: pre-fix, each of them either returned True (fail-OPEN) or
# raised.
# ---------------------------------------------------------------------------


def _generous_resolver(tmp_path: Path) -> CeilingResolver:
    """A resolver with a real R$500 teto — 50000 centavos. Nothing here fails closed by config."""
    core = _write_core(tmp_path, "  authorization_approval: { level: L2, params: { max_value_brl: 500 } }\n")
    return CeilingResolver(core_path=core)


_KW = {"tenant": "amh", "action": "authorization_approval", "param": "max_value_brl"}


def test_negative_value_cents_is_denied(tmp_path: Path) -> None:
    """A NEGATIVE amount is not "within" the teto — pre-fix `-1 <= 50000` was True (fail-OPEN).

    Nonsense money that would trivially sit inside every positive teto. Denial, not a raise: the
    primitive's whole job is to answer the within-teto question conservatively.
    """
    resolver = _generous_resolver(tmp_path)
    assert resolver.within_l2_ceiling(**_KW, value_cents=-1) is False
    assert resolver.within_l2_ceiling(**_KW, value_cents=-50_000) is False
    # Not merely "below the teto": even a magnitude far ABOVE it is denied, so the guard is not
    # accidentally passing through a sign-flipped comparison.
    assert resolver.within_l2_ceiling(**_KW, value_cents=-10_000_000) is False


def test_bool_value_cents_is_denied(tmp_path: Path) -> None:
    """`bool` is an `int` in Python: pre-fix `True` compared as 1 centavo and sat within the teto."""
    resolver = _generous_resolver(tmp_path)
    assert resolver.within_l2_ceiling(**_KW, value_cents=True) is False
    assert resolver.within_l2_ceiling(**_KW, value_cents=False) is False


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(8000.0, id="integral-float"),
        pytest.param(8000.5, id="fractional-float"),
        pytest.param(-0.0, id="negative-zero-float"),
        pytest.param(math.nan, id="nan"),
        pytest.param(math.inf, id="positive-inf"),
        pytest.param(-math.inf, id="negative-inf"),
        pytest.param(Decimal("8000"), id="decimal"),
    ],
)
def test_non_int_numeric_value_cents_is_denied(tmp_path: Path, value: Any) -> None:
    """Money is INTEGER centavos (ADR-0018 parte 2) — `float`/`Decimal` are typing defects upstream.

    Integral floats included: no rounding is ever applied to money here (same refusal
    `reembolso._require_valor_pagamento_cents` makes). NaN/+-inf fall out as a subcase — they
    exist only as floats — so no non-finite value can reach the comparison. `Decimal` is covered
    because it is numeric-looking but not an `int`, and `Decimal <= int` would have SUCCEEDED
    silently pre-fix.

    HONEST SCOPE of the bite (measured against the pre-fix primitive, not assumed): `8000.0`,
    `8000.5`, `-0.0`, `-inf` and `Decimal("8000")` were FAIL-OPEN — they returned True. `nan` and
    `+inf` already returned False, but only INCIDENTALLY, via IEEE comparison semantics
    (`nan <= x` and `inf <= x` are both False) rather than by any decision of this code. They are
    pinned here so the denial becomes deliberate and survives any future change to how the value
    reaches the comparison.
    """
    resolver = _generous_resolver(tmp_path)
    assert resolver.within_l2_ceiling(**_KW, value_cents=value) is False


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("5000", id="numeric-string"),
        pytest.param("abc", id="non-numeric-string"),
        pytest.param("", id="empty-string"),
        pytest.param(None, id="none"),
        pytest.param([5000], id="list"),
        pytest.param({"value": 5000}, id="dict"),
        pytest.param(b"5000", id="bytes"),
        pytest.param(object(), id="arbitrary-object"),
    ],
)
def test_non_numeric_value_cents_is_denied_and_never_raises(tmp_path: Path, value: Any) -> None:
    """Pre-fix these raised an UNCAUGHT `TypeError` from `<=` — the ceiling CRASHED instead of denying.

    Live vector, not hypothetical: an engine `String` process variable reaches a worker as a
    Python `str` (`harness._from_camunda_var` returns every non-`Json` wire value as-is), and a
    `Json` one as a `list`/`dict`. A crash here is worse than a denial — the failure mode became
    whatever the calling worker happened to do with an unexpected exception.
    """
    resolver = _generous_resolver(tmp_path)
    assert resolver.within_l2_ceiling(**_KW, value_cents=value) is False


def test_value_guard_rejection_logs_the_type_and_never_the_raw_amount(tmp_path: Path) -> None:
    """The rejection is diagnosable (bounded type name) without putting an untrusted object in the log."""
    resolver = _generous_resolver(tmp_path)
    with structlog.testing.capture_logs() as logs:
        assert resolver.within_l2_ceiling(**_KW, value_cents="5000") is False
    rejected = [entry for entry in logs if entry["event"] == "ceiling_value_cents_rejected"]
    assert len(rejected) == 1, logs
    assert rejected[0]["value_type"] == "str"
    assert "5000" not in str(rejected[0])


def test_inclusive_boundary_at_the_teto_is_unchanged_by_the_value_guard(tmp_path: Path) -> None:
    """The `<=` semantics at exactly the teto are UNTOUCHED (ceilings.py module docstring; the DMN
    mirrors it with `<= 10000000`). The guard only rejects values that were never money."""
    resolver = _generous_resolver(tmp_path)
    assert resolver.within_l2_ceiling(**_KW, value_cents=49_999) is True
    assert resolver.within_l2_ceiling(**_KW, value_cents=50_000) is True, "boundary EXACT — inclusive"
    assert resolver.within_l2_ceiling(**_KW, value_cents=50_001) is False, "boundary + 1 — above"
    # 0 is a VALID amount for this primitive and IS within a positive teto. "0 is not a payment"
    # is enforced at the money chokepoints (pagto/reembolso), not here — pinned so a future
    # hardening does not silently move that line and change AUTH/REEMBOLSO routing.
    assert resolver.within_l2_ceiling(**_KW, value_cents=0) is True


def test_auth_behaviour_through_the_shared_primitive_is_unchanged_for_valid_ints(tmp_path: Path) -> None:
    """AUTH REGRESSION PIN: the guard is generalized to the shared primitive, so AUTH inherits it.

    For every VALID int, AUTH's outcome must be byte-identical to pre-fix. AUTH reaches this
    primitive with `_ceiling_valor_cents(...)` results — always `int`, and always `>= 0` — so no
    legitimate AUTH path can newly be denied.

    All three AUTH call sites (`_criterio_financeiro`, `AnalyzeRequestWorker.execute`,
    `IssueAuthorizationWorker._auto_ceiling_verdict`) now share `_ceiling_valor_cents`, so a
    NEGATIVE (or bool/NaN/inf/unparseable) `valor_estimado_brl` is rejected before it ever
    reaches this primitive — `valor_cents=None` -> `teto_ok=False` directly. That still routes
    the request to HUMAN REVIEW, the same safe branch it already takes for an absent/non-numeric
    amount. No denial-of-care path is opened.
    """
    resolver = _generous_resolver(tmp_path)
    for value, expected in ((0, True), (1, True), (49_999, True), (50_000, True), (50_001, False)):
        assert resolver.within_l2_ceiling(**_KW, value_cents=value) is expected, value


def test_reembolso_behaviour_through_the_shared_primitive_is_unchanged_for_valid_ints(
    tmp_path: Path,
) -> None:
    """REEMBOLSO REGRESSION PIN (MINOR-3): the guard is generalized to the shared primitive, so
    REEMBOLSO inherits it via `calculate_value`'s `within_l2_ceiling(value_cents=valor_calculado)`
    call (reembolso.py:272-277), where `valor_calculado` comes from `_compute_table_value`
    (reembolso.py:549-579).

    For every VALID int, REEMBOLSO's outcome is byte-identical to pre-fix — a MAPPED
    `categoria_procedimento` always yields an int `base` (the `base_values` dict is all int
    literals; the urgencia/emergencia multiplier is `int(base * 1.5)`), and `max(base, 0)` of an
    int is an int.

    The path that CAN newly see the guard is an UNMAPPED `categoria_procedimento`
    (reembolso.py:571, `base_values.get(..., valor_solicitado_cents)`): `_compute_table_value` then
    returns `valor_solicitado_cents` AS-IS (nothing type-checks it — `ReembolsoInput.
    valor_solicitado_cents` is annotated `int` but a dataclass does not enforce that at
    construction) through `max(base, 0)`, which passes a `bool`/`float` straight through unchanged
    (`max(True, 0) is True`; `max(8000.5, 0) is 8000.5`) into `calculate_value`'s
    `within_l2_ceiling(value_cents=...)` call. Pre-fix that primitive was fail-OPEN for exactly
    these shapes; post-fix it now safely fails CLOSED — routing the request to `ANALISE_HUMANA`
    instead of silently admitting a non-integer-money value into a "within teto" comparison. No
    legitimate REEMBOLSO path (a MAPPED categoria, always int) can newly be denied.

    Tests the SHARED PRIMITIVE's contract only, not `_compute_table_value` internals — a sibling
    branch (F5) reworks that function; this pin is written to survive a rebase over it.
    """
    core = _write_core(tmp_path, "  reembolso_auto_approval: { level: L2, params: { max_value_brl: 500 } }\n")
    resolver = CeilingResolver(core_path=core)
    kw = {"tenant": "amh", "action": REEMBOLSO_ACTION, "param": REEMBOLSO_PARAM}

    for value, expected in ((0, True), (1, True), (49_999, True), (50_000, True), (50_001, False)):
        assert resolver.within_l2_ceiling(**kw, value_cents=value) is expected, value
    # The bool/float shapes an UNMAPPED categoria_procedimento can pass straight through
    # `_compute_table_value`'s `max(base, 0)` (reembolso.py:571,579) now safely fail closed.
    bad_shapes: tuple[Any, ...] = (True, False, 8000.5, -0.0)
    for bad_value in bad_shapes:
        assert resolver.within_l2_ceiling(**kw, value_cents=bad_value) is False, bad_value


def test_is_valid_value_cents_vector_table() -> None:
    """The guard predicate itself — the single place every caller's rejection set is defined."""
    for ok in (0, 1, 50_000, 5_000_000_000, 10**30):
        assert _is_valid_value_cents(ok) is True, ok
    for bad in (
        True,
        False,
        -1,
        8000.0,
        math.nan,
        math.inf,
        -math.inf,
        Decimal("1"),
        "1",
        None,
        [1],
        {"a": 1},
        b"1",
    ):
        assert _is_valid_value_cents(bad) is False, bad


# ---------------------------------------------------------------------------
# Tenant overlay — refines the ceiling (params merge)
# ---------------------------------------------------------------------------


def test_tenant_overlay_refines_ceiling(tmp_path: Path) -> None:
    """Overlay `tenants-<tenant>.yaml` (auto-discovered beside the core) refines max_value_brl."""
    core = _write_core(tmp_path, "  authorization_approval: { level: L2, params: { max_value_brl: 0 } }\n")
    (tmp_path / "tenants-amh.yaml").write_text(
        "version: 1\ntenant: amh\noverrides:\n"
        "  authorization_approval:\n"
        "    params: { max_value_brl: 500 }\n",
        encoding="utf-8",
    )
    resolver = CeilingResolver(core_path=core)

    kw = {"action": "authorization_approval", "param": "max_value_brl"}
    # tenant amh: overlay discovered -> ceiling 500 BRL
    assert resolver.within_l2_ceiling(tenant="amh", **kw, value_cents=50000) is True
    assert resolver.within_l2_ceiling(tenant="amh", **kw, value_cents=50001) is False
    # tenant with no overlay: falls back to the core (ceiling 0 -> fail-closed)
    assert resolver.within_l2_ceiling(tenant="outro", **kw, value_cents=1) is False


def test_ceiling_brl_resolves_raw_value(tmp_path: Path) -> None:
    """`ceiling_brl` exposes the resolved raw teto (0 fail-closed when absent)."""
    core = _write_core(tmp_path, "  authorization_approval: { level: L2, params: { max_value_brl: 750 } }\n")
    resolver = CeilingResolver(core_path=core)
    assert resolver.ceiling_brl(tenant="amh", action="authorization_approval", param="max_value_brl") == 750
    assert resolver.ceiling_brl(tenant="amh", action="nao_existe", param="max_value_brl") == 0


# ---------------------------------------------------------------------------
# §5.8 — overlay referencing an unknown action fails CLOSED in the resolver
# ---------------------------------------------------------------------------


def test_overlay_unknown_action_fails_closed_in_resolver(tmp_path: Path) -> None:
    """An overlay whose `overrides` names an action absent from the core -> load_matrix raises
    PolicyError -> `_load_matrix_cached` swallows to None -> `within_l2_ceiling` is False for
    EVERY action of that tenant.

    Dual semantics by layer (design §5.8): the PEP factory REFUSES TO START on this same input
    (ADR-0025 §4 test 12); the resolver fail-closes to deny-auto-approval. Both are fail-closed;
    neither warns-and-passes.
    """
    core = _write_core(tmp_path, "  authorization_approval: { level: L2, params: { max_value_brl: 500 } }\n")
    (tmp_path / "tenants-amh.yaml").write_text(
        "version: 1\ntenant: amh\noverrides:\n  acao_fantasma:\n    params: { max_value_brl: 999999 }\n",
        encoding="utf-8",
    )
    resolver = CeilingResolver(core_path=core)

    # authorization_approval WOULD be within the 500 teto absent the broken overlay — but the
    # unknown-action overlay makes the whole matrix unloadable -> every action fail-closes.
    assert (
        resolver.within_l2_ceiling(
            tenant="amh", action="authorization_approval", param="max_value_brl", value_cents=1
        )
        is False
    )


# ---------------------------------------------------------------------------
# §5.7 — worker ceiling constants exist in the REAL matrix (catches rename drift)
# ---------------------------------------------------------------------------


def test_ceiling_action_names_exist_in_matrix() -> None:
    """Each worker's `_CEILING_ACTION`/`_CEILING_PARAM` resolves in the real matrix with its param key."""
    core = resolve_spec_dir() / "policies" / "autonomy" / "L0-core.yaml"
    matrix = load_matrix(core, tenant="core")

    for action, param in (
        (AUTH_ACTION, AUTH_PARAM),
        (REEMBOLSO_ACTION, REEMBOLSO_PARAM),
        (PAGTO_ACTION, PAGTO_PARAM),
    ):
        policy = matrix.policy_for(action)
        assert policy is not None, f"worker ceiling action {action!r} missing from L0-core matrix"
        assert param in policy.params, (
            f"worker ceiling param {param!r} missing from action {action!r} params "
            f"({sorted(policy.params)}) — worker/policy drift"
        )
