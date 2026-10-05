"""Canonical read-action publication tests; real loader/PEP, no fabricated policy objects."""

from pathlib import Path

import pytest
import yaml

from maezo.gateway.pep import HARD_ACTIONS, PEP, Decision, Level, PolicyError, build_pep, load_matrix

ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "spec/policies/autonomy/L0-core.yaml"
ACTION = "provider_contract_read"


def test_actual_canonical_file_explicitly_classifies_read_only_action() -> None:
    raw = yaml.safe_load(CORE.read_text())
    assert raw["version"] == 2
    assert raw["actions"][ACTION] == {"level": "L3"}
    matrix = load_matrix(CORE, tenant="read-policy-unit")
    policy = matrix.policy_for(ACTION)
    assert policy is not None and policy.level is Level.L3 and policy.hard is False
    assert policy.params == {}
    assert PEP(matrix).evaluate(ACTION) is Decision.ALLOW


def test_action_absent_from_real_file_load_remains_denied_and_has_no_alias(tmp_path: Path) -> None:
    raw = yaml.safe_load(CORE.read_text())
    raw["actions"].pop(ACTION, None)
    previous = tmp_path / "before-publication.yaml"
    previous.write_text(yaml.safe_dump(raw))
    pep = PEP(load_matrix(previous, tenant="read-policy-unit"))
    assert pep.evaluate(ACTION, {"level": "L3", "enforcing": True}) is Decision.DENY
    for alias in [
        "contract.authority.resolve",
        "provider_contract_write",
        "provider_contract_read_any",
        "ler_contrato",
    ]:
        assert pep.evaluate(alias) is Decision.DENY


@pytest.mark.parametrize(
    "level,decision", [("L0", Decision.REQUIRE_HUMAN), ("L1", Decision.REQUIRE_HUMAN), ("L2", Decision.ALLOW)]
)
def test_real_tenant_overlay_can_restrict_canonical_read_level(
    tmp_path: Path, level: str, decision: Decision
) -> None:
    overlay = tmp_path / "tenant-overlay.yaml"
    overlay.write_text(
        yaml.safe_dump({"tenant": "read-policy-unit", "overrides": {ACTION: {"level": level}}})
    )
    matrix = load_matrix(CORE, tenant="read-policy-unit", overlay_path=overlay)
    assert matrix.policy_for(ACTION).level is Level(level)
    assert PEP(matrix).evaluate(ACTION) is decision


def test_tenant_overlay_cannot_add_unknown_action_or_modify_hard_action(tmp_path: Path) -> None:
    overlay = tmp_path / "tenant-overlay.yaml"
    overlay.write_text(yaml.safe_dump({"overrides": {"provider_contract_read_other": {"level": "L3"}}}))
    with pytest.raises(PolicyError):
        load_matrix(CORE, tenant="read-policy-unit", overlay_path=overlay)
    for hard in HARD_ACTIONS:
        overlay.write_text(yaml.safe_dump({"overrides": {hard: {"level": "L3"}}}))
        with pytest.raises(PolicyError):
            load_matrix(CORE, tenant="read-policy-unit", overlay_path=overlay)


def test_coarse_allow_does_not_prove_request_scope_or_transfer_protected_authority() -> None:
    pep = PEP(load_matrix(CORE, tenant="read-policy-unit"))
    # Actual evaluate() treats context as audit metadata, not policy predicates.
    # Source admission must independently deny mismatched context; matrix does not.
    assert pep.evaluate(ACTION, {"tenant": "wrong-tenant", "purpose": "wrong-purpose"}) is Decision.ALLOW
    for hard in HARD_ACTIONS:
        assert pep.evaluate(hard) is Decision.DENY
    assert pep.evaluate("high_value_payment") is Decision.REQUIRE_HUMAN
    assert pep.evaluate("provider_decredentialing") is Decision.REQUIRE_HUMAN


def test_loader_and_build_factory_refuse_missing_policy_instead_of_default_l3(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError):
        load_matrix(missing, tenant="read-policy-unit")
    with pytest.raises(PolicyError):
        build_pep(core_path=missing, tenant="read-policy-unit")


def test_restricted_canonical_base_cannot_be_loosened_by_tenant_overlay(tmp_path: Path) -> None:
    raw = yaml.safe_load(CORE.read_text())
    raw["actions"][ACTION] = {"level": "L1"}
    restricted = tmp_path / "restricted-canonical.yaml"
    restricted.write_text(yaml.safe_dump(raw))
    overlay = tmp_path / "tenant-overlay.yaml"
    overlay.write_text(yaml.safe_dump({"overrides": {ACTION: {"level": "L3"}}}))
    with pytest.raises(PolicyError):
        load_matrix(restricted, tenant="read-policy-unit", overlay_path=overlay)


def test_all_hard_actions_remain_l0_in_the_real_loaded_matrix() -> None:
    matrix = load_matrix(CORE, tenant="read-policy-unit")
    for action in HARD_ACTIONS:
        policy = matrix.policy_for(action)
        assert policy is not None and policy.hard and policy.level is Level.L0
        assert PEP(matrix).evaluate(action) is Decision.DENY
