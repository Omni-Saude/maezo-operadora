"""GAP-FAB-NOTIF item D — two pure contract-text drifts confirmed by the R1 verifier (part-B
verification report §6, item D of the WP brief):

  1. `docs/processes/contracts/SP-OP-CANCEL-001.md:4` cited a BPMN filename
     (`SP-OP-CANCEL-001_Cancelamento_Contratual.bpmn`) that does not exist — the real file is
     `SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn`.
  2. `docs/processes/contracts/SP-OP-FRAUDE-001.md` claimed `hitPolicy COLLECT` for
     `fraude_indicadores` (`:135` before the fix) and `hitPolicy UNIQUE` for `fraude_sla`
     (`:145` before the fix); the deployed DMN files are both `hitPolicy="FIRST"` — each DMN's own
     description block documents WHY it was downgraded from the contract's original ask
     ("landmine 7": COLLECT/UNIQUE collide with `mapDecisionResult="singleResult"`+
     `resultVariable`, or with a catch-all row, in the real engine).

This module proves both fixes AND generalizes fence (1) across every contract (not just CANCEL),
since the check is mechanical and cheap.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[3]
_CONTRACTS_DIR = _REPO_ROOT / "docs" / "processes" / "contracts"
_BPMN_DIR = _REPO_ROOT / "spec" / "processes" / "bpmn"
_DMN_DIR = _REPO_ROOT / "spec" / "processes" / "dmn"

_BPMN_CITATION_RE = re.compile(r"`(spec/processes/bpmn/[A-Za-z0-9_.-]+\.bpmn)`")


def _all_contract_files() -> list[Path]:
    files = sorted(_CONTRACTS_DIR.glob("*.md"))
    assert files, f"expected contract .md files under {_CONTRACTS_DIR}"
    return files


def test_contracts_directory_has_16_contracts() -> None:
    """Sanity: the fences below are not silently scanning zero/a truncated set (BRIEF-COMMON
    ground truth: 16 contracts on main)."""
    assert len(_all_contract_files()) == 16


def test_every_contract_bpmn_citation_resolves_to_a_real_file() -> None:
    """Every backtick-quoted `spec/processes/bpmn/*.bpmn` path cited anywhere in any contract must
    exist on disk. Reproduces (and generalizes) the R1 verifier's part-C finding: `SP-OP-CANCEL-
    001.md:4` used to cite `..._Cancelamento_Contratual.bpmn`, which never existed — the real file
    is `..._Cancelamento_Contrato.bpmn`."""
    missing: dict[str, list[str]] = {}
    for contract in _all_contract_files():
        text = contract.read_text(encoding="utf-8")
        for rel_path in _BPMN_CITATION_RE.findall(text):
            if not (_REPO_ROOT / rel_path).is_file():
                missing.setdefault(contract.name, []).append(rel_path)
    assert not missing, f"contract(s) cite a BPMN file that does not exist: {missing}"


def test_cancel_contract_cites_the_real_bpmn_filename() -> None:
    """Pins the specific GAP-FAB-NOTIF item D fix (not just the generalized fence above)."""
    text = (_CONTRACTS_DIR / "SP-OP-CANCEL-001.md").read_text(encoding="utf-8")
    assert "SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn" in text
    assert "SP-OP-CANCEL-001_Cancelamento_Contratual.bpmn" not in re.sub(
        r"GAP-FAB-NOTIF.*", "", text.split("\n", 5)[3]
    ), "the wrong filename should not survive as a bare (undisclosed) citation on the header line"
    real_bpmn = _BPMN_DIR / "SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn"
    assert real_bpmn.is_file()


def _dmn_hit_policy(decision_id: str) -> str:
    dmn_path = _DMN_DIR / f"{decision_id}.dmn"
    assert dmn_path.is_file(), f"expected DMN file for decision id {decision_id!r} at {dmn_path}"
    ns = {"dmn": "https://www.omg.org/spec/DMN/20191111/MODEL/"}
    tree = ET.parse(dmn_path)
    tables = tree.getroot().findall(".//dmn:decisionTable", ns)
    assert len(tables) == 1, f"{dmn_path} expected exactly one decisionTable, found {len(tables)}"
    hit_policy = tables[0].get("hitPolicy")
    assert hit_policy, f"{dmn_path} decisionTable has no hitPolicy attribute"
    return hit_policy


def test_fraude_dmn_hit_policies_are_all_first() -> None:
    """Ground truth, independently re-derived from the XML `hitPolicy` attribute (not the
    contract's prose) for FRAUDE-001's three DMN tables."""
    assert _dmn_hit_policy("fraude_indicadores") == "FIRST"
    assert _dmn_hit_policy("fraude_routing") == "FIRST"
    assert _dmn_hit_policy("fraude_sla") == "FIRST"


def test_fraude_contract_hit_policy_claims_match_the_deployed_dmn() -> None:
    """The contract's `### \\`<decision_id>\\` (hitPolicy <CLAIM> ...)` heading for each of
    FRAUDE-001's three DMN tables must state the REAL hitPolicy — GAP-FAB-NOTIF fix corrected
    `fraude_indicadores` (was COLLECT) and `fraude_sla` (was UNIQUE); `fraude_routing` already
    correctly said FIRST and must stay that way."""
    text = (_CONTRACTS_DIR / "SP-OP-FRAUDE-001.md").read_text(encoding="utf-8")
    heading_re = re.compile(r"###\s+`(fraude_\w+)`\s+\(hitPolicy\s+([A-Z]+)\b")
    claims = dict(heading_re.findall(text))
    assert claims.keys() >= {"fraude_indicadores", "fraude_routing", "fraude_sla"}, (
        f"expected all three FRAUDE DMN headings, found: {sorted(claims)}"
    )
    mismatches = {
        decision_id: (claimed, _dmn_hit_policy(decision_id))
        for decision_id, claimed in claims.items()
        if decision_id.startswith("fraude_") and claimed != _dmn_hit_policy(decision_id)
    }
    assert not mismatches, f"contract hitPolicy claim(s) do not match the deployed DMN: {mismatches}"


# DOCUMENTED baseline (same "grandfather with a ticket" pattern as
# `test_worker_handler_purity.py`'s `_NONDETERMINISM_BASELINE`/`_FABRICATED_FACT_BASELINE`) — the
# same COLLECT/UNIQUE-vs-real-FIRST drift confirmed for FRAUDE (this fix) also exists in these
# OTHER contracts, which are OUT OF SCOPE for GAP-FAB-NOTIF item D (the WP brief names only the
# FRAUDE contract's two claims). Two of these six (`SP-OP-CRED-001.md::cred_prior_notice`/
# `cred_sla`, `SP-OP-ADEQUACAO-001.md::adequacao_sla`) were already confirmed by the R1 verifier
# (part-B verification report §6, finding D-B6, "CONFIRMADO 3/3"). The other two
# (`SP-OP-PAGTO-001.md::pagto_sla`, `SP-OP-REEMBOLSO-001.md::reembolso_calculo`/`reembolso_sla`)
# are NEW — outside Part-B's own 6-process scope — surfaced by this test and reported, not fixed,
# here (see this WP's final report "found beyond scope"). Tracked, not silently fixed: a NEW
# contract adopting the pattern (not listed here) still fails the fence below.
_HIT_POLICY_DRIFT_BASELINE: frozenset[str] = frozenset(
    {
        "SP-OP-CRED-001.md::cred_prior_notice",  # D-B6 (part-B verification report)
        "SP-OP-CRED-001.md::cred_sla",  # D-B6
        "SP-OP-ADEQUACAO-001.md::adequacao_sla",  # D-B6
        "SP-OP-PAGTO-001.md::pagto_sla",  # NEW — found by this test, out of GAP-FAB-NOTIF scope
        "SP-OP-REEMBOLSO-001.md::reembolso_calculo",  # NEW — found by this test, out of scope
        "SP-OP-REEMBOLSO-001.md::reembolso_sla",  # NEW — found by this test, out of scope
    }
)


def test_no_new_contract_claims_collect_or_unique_for_a_dmn_actually_deployed_as_first() -> None:
    """Generalizes the FRAUDE fix's shape across every contract that annotates a DMN heading with
    a hitPolicy claim: a contract must never claim `COLLECT`/`UNIQUE` for a decision id whose real
    deployed table is `FIRST` (the defect class D-B6 confirmed for FRAUDE/CRED/ADEQUACAO), UNLESS
    it is a documented pre-existing, out-of-scope instance in `_HIT_POLICY_DRIFT_BASELINE`. FRAUDE
    itself is asserted clean (not merely absent from `actual`) so this fence cannot pass by
    accident if FRAUDE's own claims regress."""
    heading_re = re.compile(r"###\s+`([a-z][a-z0-9_]*)`\s+\(hitPolicy\s+([A-Z]+)\b")
    actual: dict[str, tuple[str, str]] = {}
    for contract in _all_contract_files():
        text = contract.read_text(encoding="utf-8")
        for decision_id, claimed in heading_re.findall(text):
            dmn_path = _DMN_DIR / f"{decision_id}.dmn"
            if not dmn_path.is_file():
                continue  # not every heading names a decision id with a 1:1 file (out of scope here)
            if claimed in {"COLLECT", "UNIQUE"} and _dmn_hit_policy(decision_id) == "FIRST":
                actual[f"{contract.name}::{decision_id}"] = (claimed, "FIRST")

    new_violations = {k: v for k, v in actual.items() if k not in _HIT_POLICY_DRIFT_BASELINE}
    assert not new_violations, f"NEW contract(s) claim COLLECT/UNIQUE for a table deployed as FIRST: {new_violations}"

    resolved = _HIT_POLICY_DRIFT_BASELINE - set(actual)
    assert not resolved, (
        f"baseline entries no longer drift (fixed!): {sorted(resolved)} — remove them from "
        "_HIT_POLICY_DRIFT_BASELINE."
    )

    assert not any(k.startswith("SP-OP-FRAUDE-001.md::") for k in actual), (
        "FRAUDE contract regressed back to claiming COLLECT/UNIQUE for a FIRST table"
    )
