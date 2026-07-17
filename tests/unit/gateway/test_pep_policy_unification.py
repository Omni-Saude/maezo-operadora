"""Property tests for the PEP ↔ policy unification (ADR-0025, T1.8).

These pin the T1.8 acceptance criteria and ADR-0025 §4 test plan:
* every action in the YAML is resolvable by the PEP;
* the code-frozen hard set == the YAML frozen set == the core `hard: true` set;
* unknown action → DENY (fail-closed), retired PT names → DENY;
* missing / malformed / structurally-invalid policy → refuse to start;
* overlays may tighten a non-hard action but may never touch a hard item, loosen
  a non-hard one, or reference an unknown action;
* the 11 agent.yaml `autonomy_policy` paths resolve to the real policy dir.

Numbering follows ADR-0025 §4 (1, 2, 3, 4, 5, 6a, 6b, 7, 8, 9, 10, 11, 12, 13),
with a few additional guardrail tests marked "bonus".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from maezo.agents import resolve_spec_agents_dir, resolve_spec_dir
from maezo.gateway.pep import (
    HARD_ACTIONS,
    PEP,
    ActionPolicy,
    AutonomyMatrix,
    Decision,
    Level,
    PolicyError,
    build_pep,
    load_matrix,
)

# --- Real spec artifacts (single source of truth) ------------------------------------

AUTONOMY_DIR = resolve_spec_dir() / "policies" / "autonomy"
REAL_CORE = AUTONOMY_DIR / "L0-core.yaml"
REAL_AMH = AUTONOMY_DIR / "tenants-amh.yaml"
FROZEN_FILE = AUTONOMY_DIR / "_hard_frozen.yaml"

EXPECTED_HARD = frozenset(
    {
        "clinical_decision",
        "authorization_denial",
        "nip_manter_negativa",
        "fraud_accusation",
        "contract_termination",
    }
)

# Retired Portuguese names (ADR-0025 §3 migration table) — must now DENY as unknown.
RETIRED_PT_NAMES = [
    "negativa_cobertura",
    "decisao_clinica",
    "acusacao_fraude",
    "cancelamento_contrato",
    "descredenciamento",
    "pagamento_alcada",
    "resposta_nip",
    "envio_ans",
    "descredenciamento_formal",
    "aprovacao_auth_dmn_favoravel",
    "glosa_padrao",
    "reembolso_calculo",
    "analise_recurso",
    "triagem_whatsapp",
    "agendamento",
    "respostas_informativas",
    "lembretes",
    "autorizacao_automatica",
]

# A minimal, structurally-valid core: the frozen 5 as L0 hard. Synthetic cores built
# on top of this satisfy `_parse_core`'s defense-in-depth (all hard items present, L0).
_HARD5: dict[str, dict[str, Any]] = {
    "clinical_decision": {"level": "L0", "hard": True},
    "authorization_denial": {"level": "L0", "hard": True},
    "nip_manter_negativa": {"level": "L0", "hard": True},
    "fraud_accusation": {"level": "L0", "hard": True},
    "contract_termination": {"level": "L0", "hard": True},
}


def _write_core(
    path: Path,
    *,
    extra: dict[str, dict[str, Any]] | None = None,
    omit: list[str] | None = None,
) -> Path:
    actions = dict(_HARD5)
    for name in omit or []:
        actions.pop(name, None)
    actions.update(extra or {})
    path.write_text(yaml.safe_dump({"version": 1, "actions": actions}), encoding="utf-8")
    return path


def _write_yaml(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# --- 1: every YAML action resolvable by the PEP --------------------------------------


def test_every_yaml_action_resolvable_by_pep() -> None:
    pep = build_pep()
    assert pep.matrix.actions, "matrix must not be empty"
    for action in pep.matrix.actions:
        result = pep.evaluate(action)
        assert isinstance(result, Decision)
        assert result in {Decision.ALLOW, Decision.DENY, Decision.REQUIRE_HUMAN}


# --- 2: hard-frozen set == YAML frozen set == code ------------------------------------


def test_hard_frozen_set_equals_yaml_frozen_set_equals_code() -> None:
    # (a) code
    code_set = set(HARD_ACTIONS)
    # (b) _hard_frozen.yaml
    frozen_raw = yaml.safe_load(FROZEN_FILE.read_text(encoding="utf-8"))
    yaml_frozen_set = {str(item["action"]) for item in frozen_raw["hard_items"]}
    # (c) L0-core.yaml actions with hard: true
    core_raw = yaml.safe_load(REAL_CORE.read_text(encoding="utf-8"))
    core_hard_set = {name for name, spec in core_raw["actions"].items() if spec.get("hard") is True}

    assert code_set == EXPECTED_HARD
    assert yaml_frozen_set == EXPECTED_HARD
    assert core_hard_set == EXPECTED_HARD
    assert code_set == yaml_frozen_set == core_hard_set


# --- 3: unknown action → DENY --------------------------------------------------------


def test_unknown_action_denies_fail_closed() -> None:
    pep = build_pep()
    assert pep.evaluate("acao_inexistente") is Decision.DENY


# --- 4: retired PT names → DENY (vocabulary migrated, not aliased) --------------------


@pytest.mark.parametrize("pt_name", RETIRED_PT_NAMES)
def test_retired_pt_names_now_deny(pt_name: str) -> None:
    pep = build_pep()
    assert pep.evaluate(pt_name) is Decision.DENY, f"retired PT name '{pt_name}' must DENY, not resolve"


# --- 5: every hard action denies regardless of agent_context -------------------------


@pytest.mark.parametrize("action", sorted(EXPECTED_HARD))
@pytest.mark.parametrize(
    "ctx",
    [None, {"agent_id": "helena", "level": "L2"}, {"agent_id": "rafael", "level": "L3", "tenant": "amh"}],
)
def test_every_hard_action_denies(action: str, ctx: dict[str, Any] | None) -> None:
    pep = build_pep()
    assert pep.evaluate(action, agent_context=ctx) is Decision.DENY


# --- 6a: missing core file → refuse to start -----------------------------------------


def test_missing_core_file_refuses_to_start(tmp_path: Path) -> None:
    missing = tmp_path / "nao-existe.yaml"
    # raw load_matrix surfaces the distinct FileNotFoundError class (ADR-0025 D5 (i))
    with pytest.raises(FileNotFoundError):
        load_matrix(missing, tenant="core")
    # the factory normalises it into a single refuse-to-start PolicyError
    with pytest.raises(PolicyError):
        build_pep(core_path=missing)


# --- 6b: malformed core → refuse to start (two sub-cases, asserted separately) --------


def test_malformed_core_nondict_root_refuses(tmp_path: Path) -> None:
    non_dict = tmp_path / "core.yaml"
    non_dict.write_text("just a bare string\n", encoding="utf-8")
    # a YAML that parses to a non-dict → PolicyError (raw), and via the factory too
    with pytest.raises(PolicyError):
        load_matrix(non_dict, tenant="core")
    with pytest.raises(PolicyError):
        build_pep(core_path=non_dict)


def test_malformed_core_syntax_error_refuses(tmp_path: Path) -> None:
    bad_syntax = tmp_path / "core.yaml"
    bad_syntax.write_text("actions: {clinical_decision: [unbalanced\n", encoding="utf-8")
    # a YAML *syntax* error surfaces raw as yaml.YAMLError (NOT PolicyError)...
    with pytest.raises(yaml.YAMLError):
        load_matrix(bad_syntax, tenant="core")
    # ...which the factory must also normalise into refuse-to-start.
    with pytest.raises(PolicyError):
        build_pep(core_path=bad_syntax)


def test_empty_core_refuses(tmp_path: Path) -> None:
    """Bonus: an empty YAML file parses to None → PolicyError (charter item 2)."""
    empty = tmp_path / "core.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(PolicyError):
        load_matrix(empty, tenant="core")
    with pytest.raises(PolicyError):
        build_pep(core_path=empty)


# --- 7: core missing a hard item → refuse ---------------------------------------------


def test_core_missing_hard_item_refuses(tmp_path: Path) -> None:
    core = _write_core(tmp_path / "core.yaml", omit=["contract_termination"])
    with pytest.raises(PolicyError, match="contract_termination"):
        load_matrix(core, tenant="core")


def test_core_hard_item_not_l0_refuses(tmp_path: Path) -> None:
    """Bonus: a hard item demoted from L0 → PolicyError (defense-in-depth)."""
    core = _write_core(
        tmp_path / "core.yaml",
        extra={"contract_termination": {"level": "L2", "hard": True}},
        omit=["contract_termination"],
    )
    with pytest.raises(PolicyError, match="contract_termination"):
        load_matrix(core, tenant="core")


def test_load_raises_on_frozen_mismatch(tmp_path: Path) -> None:
    """Bonus (charter item 4): a `_hard_frozen.yaml` sibling that disagrees with the
    code-frozen HARD_ACTIONS → PolicyError at load."""
    core = _write_core(tmp_path / "L0-core.yaml")
    # frozen list drops contract_termination and adds a bogus item → mismatch
    _write_yaml(
        tmp_path / "_hard_frozen.yaml",
        {
            "version": 1,
            "hard_items": [
                {"action": "clinical_decision", "level": "L0"},
                {"action": "authorization_denial", "level": "L0"},
                {"action": "nip_manter_negativa", "level": "L0"},
                {"action": "fraud_accusation", "level": "L0"},
                {"action": "some_other_thing", "level": "L0"},
            ],
        },
    )
    with pytest.raises(PolicyError, match="HARD_ACTIONS"):
        load_matrix(core, tenant="core")


# --- 8: overlay cannot touch a hard item (all 5) -------------------------------------


@pytest.mark.parametrize("hard_action", sorted(EXPECTED_HARD))
@pytest.mark.parametrize("field", ["level", "hard", "params"])
def test_overlay_cannot_touch_hard_item(tmp_path: Path, hard_action: str, field: str) -> None:
    override_spec: dict[str, Any] = {
        "level": {"level": "L2"},
        "hard": {"hard": False},
        "params": {"params": {"max_value_brl": 1}},
    }[field]
    overlay = _write_yaml(
        tmp_path / "overlay.yaml",
        {"version": 1, "overrides": {hard_action: override_spec}},
    )
    with pytest.raises(PolicyError):
        load_matrix(REAL_CORE, tenant="t", overlay_path=overlay)


# --- 9: overlay may refine non-hard params; loosening a level → error ----------------


def test_overlay_may_refine_nonhard_params_only() -> None:
    # tenants-amh.yaml refines authorization_approval / reembolso_auto_approval params.
    matrix = load_matrix(REAL_CORE, tenant="amh", overlay_path=REAL_AMH)
    pol = matrix.policy_for("authorization_approval")
    assert pol is not None
    assert "max_value_brl" in pol.params  # param refinement carried through
    assert pol.level is Level.L2  # level unchanged by a params-only refinement


def test_overlay_loosening_nonhard_level_raises(tmp_path: Path) -> None:
    # standard_glosa_processing is L2 (non-hard) in the real core; L2 → L3 is loosening.
    overlay = _write_yaml(
        tmp_path / "overlay.yaml",
        {"version": 1, "overrides": {"standard_glosa_processing": {"level": "L3"}}},
    )
    with pytest.raises(PolicyError, match="LOOSEN"):
        load_matrix(REAL_CORE, tenant="t", overlay_path=overlay)


def test_overlay_may_tighten_nonhard_level(tmp_path: Path) -> None:
    """Bonus: tightening a non-hard action (L2 → L1) is allowed."""
    overlay = _write_yaml(
        tmp_path / "overlay.yaml",
        {"version": 1, "overrides": {"standard_glosa_processing": {"level": "L1"}}},
    )
    matrix = load_matrix(REAL_CORE, tenant="t", overlay_path=overlay)
    pol = matrix.policy_for("standard_glosa_processing")
    assert pol is not None
    assert pol.level is Level.L1


# --- 10: real policies reconcile + nip_manter_negativa frozen L0 ---------------------


def test_real_policies_reconcile_and_freeze_nip_manter_negativa() -> None:
    matrix = load_matrix(REAL_CORE, tenant="amh", overlay_path=REAL_AMH)
    for name in ("nip_manter_negativa", "authorization_denial"):
        pol = matrix.policy_for(name)
        assert pol is not None
        assert pol.level is Level.L0
        assert pol.hard is True
    # the real dir reconciles (frozen ↔ core-hard) — load_matrix would have raised otherwise.
    frozen_raw = yaml.safe_load(FROZEN_FILE.read_text(encoding="utf-8"))
    frozen_set = {str(item["action"]) for item in frozen_raw["hard_items"]}
    core_hard = {n for n, p in matrix.actions.items() if p.hard}
    assert frozen_set == core_hard == EXPECTED_HARD


# --- 11: every agent.yaml autonomy_actions entry is in the matrix --------------------


def test_agent_allowlist_actions_all_in_matrix() -> None:
    matrix = build_pep().matrix
    agents_dir = resolve_spec_agents_dir()
    checked = 0
    for agent_yaml in sorted(agents_dir.glob("*/agent.yaml")):
        data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
        for action in data.get("autonomy_actions") or []:
            if not isinstance(action, str):
                continue
            checked += 1
            assert matrix.policy_for(action) is not None, (
                f"{agent_yaml.parent.name}: autonomy_action '{action}' is not in the resolved matrix"
            )
    assert checked > 0, "expected at least one agent autonomy_action to check"


# --- 12: overlay referencing an unknown action → PolicyError -------------------------


def test_overlay_unknown_action_raises_policy_error(tmp_path: Path) -> None:
    overlay = _write_yaml(
        tmp_path / "overlay.yaml",
        {"version": 1, "overrides": {"acao_fantasma": {"params": {"max_value_brl": 1}}}},
    )
    with pytest.raises(PolicyError, match="unknown action"):
        load_matrix(REAL_CORE, tenant="t", overlay_path=overlay)


# --- 13: the 11 agent.yaml autonomy_policy paths resolve -----------------------------


def test_agent_yaml_autonomy_policy_paths_resolve() -> None:
    agents_dir = resolve_spec_agents_dir()
    agent_yamls = sorted(agents_dir.glob("*/agent.yaml"))
    assert len(agent_yamls) == 11, f"expected 11 agent.yaml files, found {len(agent_yamls)}"
    for agent_yaml in agent_yamls:
        data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))
        declared = data.get("autonomy_policy")
        assert declared, f"{agent_yaml.parent.name}: missing autonomy_policy"
        # Resolve the declared path relative to the agent.yaml's own directory
        # (spec/agents/<id>/), through the T0.3 resolve_spec_dir mechanism.
        resolved = (agent_yaml.parent / declared).resolve()
        assert resolved.is_dir(), (
            f"{agent_yaml.parent.name}: autonomy_policy '{declared}' → {resolved} (missing)"
        )
        assert (resolved / "L0-core.yaml").exists(), (
            f"{agent_yaml.parent.name}: resolved autonomy_policy dir has no L0-core.yaml"
        )
        # and it is exactly the real policy dir (no per-agent divergence).
        assert resolved == AUTONOMY_DIR.resolve()


# --- Bonus (T1.8 REVISE-1): installed-wheel layout boots without MAEZO_SPEC_DIR -------


def test_build_pep_boots_from_installed_wheel_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the installed-wheel layout: the hatch force-include lays the policy
    YAMLs at `<site-packages>/maezo/spec/policies/autonomy`. With MAEZO_SPEC_DIR
    unset and no repo checkout in sight, `resolve_spec_dir()`'s package-adjacent
    fallback must find them and `build_pep()` must boot and hard-deny."""
    import shutil

    import maezo.agents as agents_pkg

    site = tmp_path / "site-packages"
    fake_init = site / "maezo" / "agents" / "__init__.py"
    fake_init.parent.mkdir(parents=True)
    fake_init.write_text("# simulated installed module file\n")
    shutil.copytree(AUTONOMY_DIR, site / "maezo" / "spec" / "policies" / "autonomy")

    monkeypatch.delenv("MAEZO_SPEC_DIR", raising=False)
    monkeypatch.setattr(agents_pkg, "__file__", str(fake_init))

    pep = build_pep()
    assert pep.evaluate("authorization_denial") is Decision.DENY
    assert pep.evaluate("acao_inexistente") is Decision.DENY
    assert pep.evaluate("triage_and_routing") is Decision.ALLOW


# --- Bonus: the L0-non-hard → REQUIRE_HUMAN mapping branch ----------------------------


def test_l0_non_hard_requires_human() -> None:
    """No real matrix action is L0-non-hard (every L0 item is hard), so exercise the
    branch with a synthetic matrix to keep the mapping covered."""
    matrix = AutonomyMatrix(
        tenant="t",
        actions={"some_dossier_action": ActionPolicy("some_dossier_action", Level.L0, hard=False)},
    )
    pep = PEP(matrix)
    assert pep.evaluate("some_dossier_action") is Decision.REQUIRE_HUMAN
