"""DL-0193: every loader of `spec/policies/autonomy/*.yaml` REFUSES a repeated mapping key.

YAML's default is last-one-wins: a key appended at the end of `L0-core.yaml` or `tenants-amh.yaml`
with an EXISTING action name silently replaces the reviewed entry, so an apparent "addition" can
loosen a rule. Each test copies the REAL folder into `tmp_path`, plants a duplicate, shows with
plain `yaml.safe_load` that the duplicate WOULD have won, and proves each loader now refuses.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from maezo.agents import resolve_spec_dir
from maezo.gateway import pep as pep_module
from maezo.gateway.action_execution import load_action_approvals
from maezo.gateway.pep import PolicyError, build_pep, load_matrix
from maezo.platform.validation import policy
from maezo.platform.validation.result import Report

REAL_DIR = resolve_spec_dir() / "policies" / "autonomy"

_APPENDED_L0 = "  ans_official_submission:      { level: L3 }\n"
_APPENDED_TENANT = "  authorization_approval:\n    params: { max_value_brl: 999999 }\n"
_APPENDED_APPROVALS = "status: RATIFICADO\n"


@pytest.fixture(autouse=True)
def _clear_matrix_cache() -> Iterator[None]:
    pep_module._load_matrix_cached.cache_clear()
    yield
    pep_module._load_matrix_cached.cache_clear()


def _copy_real_folder(tmp_path: Path) -> Path:
    target = tmp_path / "autonomy"
    shutil.copytree(REAL_DIR, target)
    return target


def _append(path: Path, text: str) -> None:
    body = path.read_text(encoding="utf-8")
    path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def _replace_once(path: Path, old: str, new: str) -> None:
    body = path.read_text(encoding="utf-8")
    assert body.count(old) == 1, f"fixture anchor {old!r} not unique in {path.name}"
    path.write_text(body.replace(old, new), encoding="utf-8")


def _policy_report(folder: Path) -> Report:
    report = Report()
    policy.validate_dir(folder, report)
    return report


def _rendered(report: Report) -> str:
    return "\n".join(finding.render() for finding in report.findings)


# --- the real files on main carry no duplicate --------------------------------------------------


def test_real_autonomy_files_load_under_every_strict_loader() -> None:
    assert sorted(p.name for p in REAL_DIR.glob("*.yaml")) == [
        "L0-core.yaml",
        "_hard_frozen.yaml",
        "action-approvals.yaml",
        "tenants-amh.yaml",
    ]
    load_matrix(REAL_DIR / "L0-core.yaml", tenant="amh", overlay_path=REAL_DIR / "tenants-amh.yaml")
    report = _policy_report(REAL_DIR)
    assert report.ok, _rendered(report)
    assert load_action_approvals(REAL_DIR / "action-approvals.yaml").degraded is False


# --- L0-core.yaml ---------------------------------------------------------------------------------


def test_l0_core_appended_duplicate_would_have_won_and_is_now_refused(tmp_path: Path) -> None:
    folder = _copy_real_folder(tmp_path)
    core = folder / "L0-core.yaml"
    honest = yaml.safe_load(core.read_text(encoding="utf-8"))["actions"]["ans_official_submission"]
    assert honest["level"] == "L1"
    _append(core, _APPENDED_L0)

    # Before DL-0193: plain safe_load keeps the LAST entry — the appended line loosens L1 -> L3.
    shadowed = yaml.safe_load(core.read_text(encoding="utf-8"))["actions"]["ans_official_submission"]
    assert shadowed == {"level": "L3"}

    with pytest.raises(PolicyError, match=r"duplicate YAML mapping key 'ans_official_submission'"):
        load_matrix(core, tenant="amh")
    with pytest.raises(PolicyError, match=r"ans_official_submission"):
        build_pep(tenant="amh", core_path=core, overlay_path=folder / "tenants-amh.yaml")

    report = _policy_report(folder)
    assert not report.ok
    assert "duplicate YAML mapping key 'ans_official_submission'" in _rendered(report)
    assert "L0-core.yaml" in _rendered(report)


def test_l0_core_nested_duplicate_is_refused(tmp_path: Path) -> None:
    folder = _copy_real_folder(tmp_path)
    core = folder / "L0-core.yaml"
    _replace_once(
        core, "params: { threshold_brl: 100000 }", "params: { threshold_brl: 100000, threshold_brl: 1 }"
    )
    shadowed = yaml.safe_load(core.read_text(encoding="utf-8"))["actions"]["high_value_payment"]
    assert shadowed["params"]["threshold_brl"] == 1

    with pytest.raises(PolicyError, match=r"duplicate YAML mapping key 'threshold_brl'"):
        load_matrix(core, tenant="amh")
    assert "duplicate YAML mapping key 'threshold_brl'" in _rendered(_policy_report(folder))


# --- tenants-amh.yaml -----------------------------------------------------------------------------


def test_tenant_overlay_appended_duplicate_would_have_won_and_is_now_refused(tmp_path: Path) -> None:
    folder = _copy_real_folder(tmp_path)
    overlay = folder / "tenants-amh.yaml"
    honest = yaml.safe_load(overlay.read_text(encoding="utf-8"))["overrides"]["authorization_approval"]
    assert honest["params"]["max_value_brl"] == 500
    _append(overlay, _APPENDED_TENANT)

    shadowed = yaml.safe_load(overlay.read_text(encoding="utf-8"))["overrides"]["authorization_approval"]
    assert shadowed["params"]["max_value_brl"] == 999999

    with pytest.raises(PolicyError, match=r"duplicate YAML mapping key 'authorization_approval'"):
        load_matrix(folder / "L0-core.yaml", tenant="amh", overlay_path=overlay)
    with pytest.raises(PolicyError, match=r"authorization_approval"):
        build_pep(tenant="amh", core_path=folder / "L0-core.yaml", overlay_path=overlay)

    report = _policy_report(folder)
    assert "duplicate YAML mapping key 'authorization_approval'" in _rendered(report)
    assert "tenants-amh.yaml" in _rendered(report)


def test_tenant_overlay_nested_duplicate_is_refused(tmp_path: Path) -> None:
    folder = _copy_real_folder(tmp_path)
    overlay = folder / "tenants-amh.yaml"
    _replace_once(
        overlay, "params: { max_value_brl: 500 }", "params: { max_value_brl: 500, max_value_brl: 999999 }"
    )
    assert (
        yaml.safe_load(overlay.read_text(encoding="utf-8"))["overrides"]["authorization_approval"]["params"][
            "max_value_brl"
        ]
        == 999999
    )
    with pytest.raises(PolicyError, match=r"duplicate YAML mapping key 'max_value_brl'"):
        load_matrix(folder / "L0-core.yaml", tenant="amh", overlay_path=overlay)
    assert "duplicate YAML mapping key 'max_value_brl'" in _rendered(_policy_report(folder))


# --- _hard_frozen.yaml (read by pep and by the policy validator) ------------------------------------


def test_hard_frozen_duplicate_top_level_key_is_refused(tmp_path: Path) -> None:
    folder = _copy_real_folder(tmp_path)
    _append(folder / "_hard_frozen.yaml", "hard_items: []\n")
    assert yaml.safe_load((folder / "_hard_frozen.yaml").read_text(encoding="utf-8"))["hard_items"] == []

    with pytest.raises(PolicyError, match=r"duplicate YAML mapping key 'hard_items'"):
        load_matrix(folder / "L0-core.yaml", tenant="amh")
    assert "duplicate YAML mapping key 'hard_items'" in _rendered(_policy_report(folder))


# --- action-approvals.yaml ------------------------------------------------------------------------


def test_action_approvals_appended_duplicate_would_have_won_and_is_now_refused(tmp_path: Path) -> None:
    folder = _copy_real_folder(tmp_path)
    manifest = folder / "action-approvals.yaml"
    assert yaml.safe_load(manifest.read_text(encoding="utf-8"))["status"] == "DRAFT"
    _append(manifest, _APPENDED_APPROVALS)
    assert yaml.safe_load(manifest.read_text(encoding="utf-8"))["status"] == "RATIFICADO"

    approvals = load_action_approvals(manifest)
    assert approvals.degraded is True
    assert approvals.approved == frozenset()


def test_action_approvals_nested_duplicate_is_refused(tmp_path: Path) -> None:
    folder = _copy_real_folder(tmp_path)
    manifest = folder / "action-approvals.yaml"
    _replace_once(manifest, '  expires: "2027-02-09"\n', '  expires: "2027-02-09"\n  expires: "2099-12-31"\n')
    assert yaml.safe_load(manifest.read_text(encoding="utf-8"))["deviation"]["expires"] == "2099-12-31"
    assert load_action_approvals(manifest).degraded is True
