"""Unit tests for maezo.gateway.pep — Policy Enforcement Point (ADR-0005, ADR-0008, ADR-0025, DL-0005).

Vocabulary migrated to the English canonical action names of
``spec/policies/autonomy/L0-core.yaml`` (ADR-0025 D1). The former Portuguese
names are retired and now fail-close to DENY (proven in
``test_pep_policy_unification.py``). The matrix is loaded from YAML via
``build_pep`` rather than a hard-coded dict.
"""

from pathlib import Path
from typing import Any

import pytest
import structlog
import yaml

import maezo.gateway.pep as pep_module
from maezo.agents import resolve_spec_dir
from maezo.gateway.pep import (
    HARD_ACTIONS,
    PEP,
    ActionPolicy,
    AutonomyMatrix,
    Decision,
    Level,
    PolicyError,
    _assert_hard_frozen_matches,
    _default_autonomy_dir,
    _parse_core,
    build_pep,
    load_matrix,
)


def test_pep_deny_l0_hard() -> None:
    """The frozen-5 L0-hard actions must return DENY regardless of agent context."""
    pep = build_pep()

    for action in HARD_ACTIONS:
        result = pep.evaluate(action, agent_context={"agent_id": "helena", "level": "L2"})
        assert result == Decision.DENY, f"L0-hard action '{action}' must be DENY, got {result}"


def test_pep_require_human_l1() -> None:
    """L1 actions must return REQUIRE_HUMAN."""
    pep = build_pep()

    result = pep.evaluate("high_value_payment", agent_context={"agent_id": "marina", "level": "L2"})
    assert result == Decision.REQUIRE_HUMAN


def test_pep_allow_l2() -> None:
    """L2 actions must return ALLOW."""
    pep = build_pep()

    result = pep.evaluate("authorization_approval", agent_context={"agent_id": "rafael", "level": "L2"})
    assert result == Decision.ALLOW


def test_pep_allow_l3() -> None:
    """L3 actions must return ALLOW."""
    pep = build_pep()

    result = pep.evaluate("triage_and_routing", agent_context={"agent_id": "helena", "level": "L2"})
    assert result == Decision.ALLOW


def test_pep_contract_termination_hardened_to_deny() -> None:
    """contract_termination (formerly the L0-non-hard `cancelamento_contrato`) is now L0 HARD → DENY.

    ADR-0025 §3 migration table, Δ HARDEN row: the closest PT predecessor was coded
    L0 non-hard (REQUIRE_HUMAN); the frozen canonical action is L0 hard, so it must DENY.
    """
    pep = build_pep()

    result = pep.evaluate("contract_termination", agent_context={"agent_id": "beatriz", "level": "L2"})
    assert result == Decision.DENY


def test_pep_unknown_action_deny() -> None:
    """Unknown actions not in the autonomy matrix default to DENY (fail-closed)."""
    pep = build_pep()

    result = pep.evaluate("acao_inexistente", agent_context={"agent_id": "helena", "level": "L2"})
    assert result == Decision.DENY


def test_pep_hard_actions_is_frozenset() -> None:
    """HARD_ACTIONS must be an immutable frozenset of the English canonical 5 (ADR-0025 D3)."""
    assert isinstance(HARD_ACTIONS, frozenset)
    assert (
        frozenset(
            {
                "clinical_decision",
                "authorization_denial",
                "nip_manter_negativa",
                "fraud_accusation",
                "contract_termination",
            }
        )
        == HARD_ACTIONS
    )


def test_pep_agent_context_preserved_in_result() -> None:
    """Passing agent_context must not change the decision (context is audit-only)."""
    pep = build_pep()

    ctx = {"agent_id": "rafael", "level": "L2", "tenant": "amh"}
    result = pep.evaluate("triage_and_routing", agent_context=ctx)
    assert result == Decision.ALLOW


# --- R-117 (forensic audit F-187): mutation guards ------------------------------------------
# Each test below pins behaviour stated by ADR-0025 (D3 hard set, D4 overlay, D5 fail-closed +
# audit record) or by the module's design-of-record docstring. Synthetic action names only; no
# business value (level/ceiling) is asserted that the spec does not give.

_CTX = {"agent_id": "agente-sintetico", "level": "L2"}


def _core_actions(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    actions: dict[str, Any] = {name: {"level": "L0", "hard": True} for name in sorted(HARD_ACTIONS)}
    actions.update(
        {
            "acao_l0_comum": {"level": "L0"},
            "acao_l1": {"level": "L1"},
            "acao_l2": {"level": "L2", "params": {"chave": 1}},
            "acao_l3": {"level": "L3"},
        }
    )
    actions.update(extra or {})
    return actions


def _write(path: Path, data: Any) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _core_file(tmp_path: Path) -> Path:
    return _write(tmp_path / "L0-core.yaml", {"actions": _core_actions()})


def _load_with_overlay(tmp_path: Path, overrides: Any, *, tenant: str = "t1") -> AutonomyMatrix:
    overlay = _write(tmp_path / f"tenants-{tenant}.yaml", {"tenant": tenant, "overrides": overrides})
    return load_matrix(_core_file(tmp_path), tenant=tenant, overlay_path=overlay)


def _policy_error(fn: Any, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(PolicyError) as exc:
        fn(*args, **kwargs)
    return str(exc.value)


# Audit record of every decision (ADR-0025 D5: refusal carries an audit record; agent_context
# is audit-only).


@pytest.mark.parametrize(
    ("action", "event", "level_name", "decision"),
    [
        ("acao_l1", "pep_require_human", "info", Decision.REQUIRE_HUMAN),
        ("acao_l0_comum", "pep_require_human", "info", Decision.REQUIRE_HUMAN),
        ("acao_l2", "pep_allow", "info", Decision.ALLOW),
        ("acao_l3", "pep_allow", "info", Decision.ALLOW),
    ],
)
def test_decision_audit_record_carries_action_level_and_context(
    tmp_path: Path, action: str, event: str, level_name: str, decision: Decision
) -> None:
    pep = PEP(load_matrix(_core_file(tmp_path), tenant="t1"))
    with structlog.testing.capture_logs() as logs:
        assert pep.evaluate(action, agent_context=_CTX) is decision
    expected_level = pep.matrix.actions[action].level.value
    assert logs == [
        {
            "event": event,
            "log_level": level_name,
            "action": action,
            "level": expected_level,
            "agent_context": _CTX,
        }
    ]


def test_frozen_hard_denial_audit_record() -> None:
    pep = build_pep()
    for action in sorted(HARD_ACTIONS):
        with structlog.testing.capture_logs() as logs:
            assert pep.evaluate(action, agent_context=_CTX) is Decision.DENY
        assert logs == [
            {"event": "pep_deny_l0_hard", "log_level": "warning", "action": action, "agent_context": _CTX}
        ]


def test_unknown_action_denial_audit_record() -> None:
    with structlog.testing.capture_logs() as logs:
        assert build_pep().evaluate("acao_inexistente", agent_context=_CTX) is Decision.DENY
    assert logs == [
        {
            "event": "pep_deny_unknown_action",
            "log_level": "warning",
            "action": "acao_inexistente",
            "agent_context": _CTX,
        }
    ]


def test_yaml_hard_l0_action_outside_frozen_set_is_denied_with_audit_record() -> None:
    """Matrix-level hardness (L0 + hard) denies even for a name outside the code-frozen 5."""
    matrix = AutonomyMatrix(
        tenant="t1",
        actions={
            "acao_hard_yaml": ActionPolicy(action="acao_hard_yaml", level=Level.L0, hard=True),
            "acao_l0_comum": ActionPolicy(action="acao_l0_comum", level=Level.L0, hard=False),
        },
    )
    with structlog.testing.capture_logs() as logs:
        assert PEP(matrix).evaluate("acao_hard_yaml", agent_context=_CTX) is Decision.DENY
    assert logs == [
        {
            "event": "pep_deny_l0_hard",
            "log_level": "warning",
            "action": "acao_hard_yaml",
            "agent_context": _CTX,
        }
    ]
    # L0 non-hard is REQUIRE_HUMAN, not DENY (fail-closed order, ADR-0025 D5).
    assert PEP(matrix).evaluate("acao_l0_comum") is Decision.REQUIRE_HUMAN


# _load_yaml / _parse_core / _load_frozen_actions (ADR-0025 D3/D5)


def test_non_mapping_root_error_names_the_file(tmp_path: Path) -> None:
    core = tmp_path / "L0-core.yaml"
    core.write_text("- lista\n", encoding="utf-8")
    assert str(core) in _policy_error(load_matrix, core, tenant="t1")


def test_core_without_actions_mapping_is_refused() -> None:
    assert _policy_error(_parse_core, {"actions": ["x"]}) == "L0-core has no `actions` mapping"


@pytest.mark.parametrize("spec", [{"hard": True}, ["level"], "level"])
def test_core_entry_without_level_is_refused_naming_it(spec: Any) -> None:
    assert "acao_ruim" in _policy_error(_parse_core, {"actions": _core_actions({"acao_ruim": spec})})


def test_core_entry_with_invalid_level_is_refused_naming_value() -> None:
    message = _policy_error(_parse_core, {"actions": _core_actions({"acao_ruim": {"level": "L9"}})})
    assert "acao_ruim" in message
    assert "L9" in message


def test_effective_hard_is_yaml_flag_union_code_frozen_set() -> None:
    frozen = sorted(HARD_ACTIONS)[0]
    actions = _core_actions({"acao_hard_yaml": {"level": "L0", "hard": True}})
    actions[frozen] = {"level": "L0"}  # YAML "forgets" the flag: the code re-imposes it
    parsed = _parse_core({"actions": actions})
    assert parsed[frozen].hard is True
    assert parsed["acao_hard_yaml"].hard is True
    assert parsed["acao_l2"].hard is False
    assert parsed["acao_l2"] == ActionPolicy(
        action="acao_l2", level=Level.L2, hard=False, params={"chave": 1}
    )


def test_missing_or_non_l0_hard_item_error_names_the_item() -> None:
    name = sorted(HARD_ACTIONS)[0]
    actions = _core_actions()
    del actions[name]
    assert name in _policy_error(_parse_core, {"actions": actions})
    actions[name] = {"level": "L1", "hard": True}
    assert name in _policy_error(_parse_core, {"actions": actions})


def _frozen_beside(tmp_path: Path, body: str) -> Path:
    (tmp_path / "_hard_frozen.yaml").write_text(body, encoding="utf-8")
    return tmp_path / "L0-core.yaml"


def test_frozen_file_without_hard_items_list_is_refused_naming_file(tmp_path: Path) -> None:
    core = _frozen_beside(tmp_path, "hard_items: nao-lista\n")
    assert "_hard_frozen.yaml" in _policy_error(_assert_hard_frozen_matches, core)


@pytest.mark.parametrize("entry", ["{nome: x}", "texto-solto"])
def test_frozen_entry_without_action_is_refused_naming_entry(tmp_path: Path, entry: str) -> None:
    core = _frozen_beside(tmp_path, f"hard_items:\n  - {entry}\n")
    assert "invalid hard_items entry" in _policy_error(_assert_hard_frozen_matches, core)


def test_frozen_divergence_error_lists_both_sides(tmp_path: Path) -> None:
    dropped = sorted(HARD_ACTIONS)[0]
    kept = [n for n in sorted(HARD_ACTIONS) if n != dropped]
    items = "".join(f"  - action: {n}\n" for n in [*kept, "acao_so_no_yaml"])
    core = _frozen_beside(tmp_path, "hard_items:\n" + items)
    assert _policy_error(_assert_hard_frozen_matches, core) == (
        "code-frozen HARD_ACTIONS does not match _hard_frozen.yaml: "
        f"only-in-yaml=['acao_so_no_yaml'] only-in-code=['{dropped}']"
    )


# _apply_overlay / load_matrix (ADR-0025 D4)


def test_overlay_overrides_must_be_a_mapping(tmp_path: Path) -> None:
    assert _policy_error(_load_with_overlay, tmp_path, ["acao_l2"]) == "overlay `overrides` is malformed"


def test_overlay_unknown_action_and_malformed_override_name_the_action(tmp_path: Path) -> None:
    assert "acao_typo" in _policy_error(_load_with_overlay, tmp_path, {"acao_typo": {"level": "L1"}})
    assert "acao_l2" in _policy_error(_load_with_overlay, tmp_path, {"acao_l2": "L1"})


@pytest.mark.parametrize("override", [{"level": "L0"}, {"hard": True}, {"params": {"chave": 2}}])
def test_overlay_may_not_touch_any_key_of_a_hard_item(tmp_path: Path, override: dict[str, Any]) -> None:
    name = sorted(HARD_ACTIONS)[0]
    assert name in _policy_error(_load_with_overlay, tmp_path, {name: override})


def test_overlay_entry_for_hard_item_without_keys_is_a_no_op_and_later_entries_apply(tmp_path: Path) -> None:
    name = sorted(HARD_ACTIONS)[0]
    matrix = _load_with_overlay(tmp_path, {name: {}, "acao_l2": {"level": "L1"}})
    assert matrix.actions[name] == ActionPolicy(action=name, level=Level.L0, hard=True, params={})
    assert matrix.actions["acao_l2"].level is Level.L1


def test_overlay_may_raise_restriction_of_a_non_hard_action(tmp_path: Path) -> None:
    matrix = _load_with_overlay(tmp_path, {"acao_l2": {"level": "L1"}})
    assert matrix.actions["acao_l2"] == ActionPolicy(
        action="acao_l2", level=Level.L1, hard=False, params={"chave": 1}
    )


def test_overlay_restating_the_same_level_is_not_loosening(tmp_path: Path) -> None:
    matrix = _load_with_overlay(tmp_path, {"acao_l2": {"level": "L2"}})
    assert matrix.actions["acao_l2"].level is Level.L2


def test_overlay_loosening_error_names_the_action(tmp_path: Path) -> None:
    assert "acao_l2" in _policy_error(_load_with_overlay, tmp_path, {"acao_l2": {"level": "L3"}})


def test_overlay_invalid_level_error_names_value(tmp_path: Path) -> None:
    message = _policy_error(_load_with_overlay, tmp_path, {"acao_l2": {"level": "L9"}})
    assert "acao_l2" in message
    assert "L9" in message


def test_overlay_params_refinement_keeps_level_and_hardness(tmp_path: Path) -> None:
    matrix = _load_with_overlay(tmp_path, {"acao_l2": {"params": {"outra": 2}}})
    assert matrix.actions["acao_l2"] == ActionPolicy(
        action="acao_l2", level=Level.L2, hard=False, params={"chave": 1, "outra": 2}
    )


def test_overlay_malformed_params_error_names_the_action(tmp_path: Path) -> None:
    assert "acao_l2" in _policy_error(_load_with_overlay, tmp_path, {"acao_l2": {"params": "x"}})


def test_overlay_may_add_but_never_remove_hard_on_non_hard_action(tmp_path: Path) -> None:
    assert _load_with_overlay(tmp_path, {"acao_l2": {"hard": True}}).actions["acao_l2"].hard is True
    assert "acao_l2" in _policy_error(_load_with_overlay, tmp_path, {"acao_l2": {"hard": False}})


def test_overlay_for_another_tenant_is_refused_naming_both(tmp_path: Path) -> None:
    overlay = _write(tmp_path / "tenants-x.yaml", {"tenant": "outro", "overrides": {}})
    message = _policy_error(load_matrix, _core_file(tmp_path), tenant="t1", overlay_path=overlay)
    assert "outro" in message
    assert "t1" in message


def test_overlay_without_tenant_field_applies_and_matrix_records_tenant(tmp_path: Path) -> None:
    overlay = _write(tmp_path / "tenants-t1.yaml", {"overrides": {"acao_l2": {"level": "L1"}}})
    matrix = load_matrix(_core_file(tmp_path), tenant="t1", overlay_path=overlay)
    assert matrix.tenant == "t1"
    assert matrix.actions["acao_l2"].level is Level.L1


# build_pep (ADR-0025 D2/D4/D5)


def test_build_pep_resolves_core_under_spec_policies_autonomy(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _default_autonomy_dir() == resolve_spec_dir() / "policies" / "autonomy"
    seen: list[tuple[str, str, str | None]] = []
    real = pep_module._load_matrix_cached

    def _spy(core: str, tenant: str, overlay: str | None) -> AutonomyMatrix:
        seen.append((core, tenant, overlay))
        return real(core, tenant, overlay)

    monkeypatch.setattr(pep_module, "_load_matrix_cached", _spy)
    pep = build_pep()
    assert pep.matrix.tenant == "core"
    assert seen == [(str(resolve_spec_dir() / "policies" / "autonomy" / "L0-core.yaml"), "core", None)]


def test_build_pep_auto_discovers_the_tenant_overlay() -> None:
    autonomy = resolve_spec_dir() / "policies" / "autonomy"
    pep = build_pep(tenant="amh")
    assert pep.matrix.tenant == "amh"
    assert pep.matrix == load_matrix(
        autonomy / "L0-core.yaml", tenant="amh", overlay_path=autonomy / "tenants-amh.yaml"
    )
    assert pep.matrix.actions != load_matrix(autonomy / "L0-core.yaml", tenant="amh").actions


def test_build_pep_honours_explicit_core_and_overlay_paths(tmp_path: Path) -> None:
    core = _core_file(tmp_path)
    overlay = _write(tmp_path / "pinned.yaml", {"tenant": "t1", "overrides": {"acao_l2": {"level": "L1"}}})
    pep = build_pep(tenant="t1", core_path=core, overlay_path=overlay)
    assert pep.matrix.actions["acao_l2"].level is Level.L1
    assert pep.evaluate("acao_l2") is Decision.REQUIRE_HUMAN


def test_build_pep_refusal_message_names_the_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "ausente.yaml"
    message = _policy_error(build_pep, core_path=missing)
    assert "refused to start" in message
    assert "ausente.yaml" in message
