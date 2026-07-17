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
"""

from __future__ import annotations

from pathlib import Path

from maezo.agents import resolve_spec_dir
from maezo.gateway.pep import load_matrix
from maezo.tools.workers.auth import _CEILING_ACTION as AUTH_ACTION
from maezo.tools.workers.auth import _CEILING_PARAM as AUTH_PARAM
from maezo.tools.workers.ceilings import CeilingResolver
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
