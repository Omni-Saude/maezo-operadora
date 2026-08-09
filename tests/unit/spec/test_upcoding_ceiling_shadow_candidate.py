"""M-6 — `upcoding_complexity_ceiling`: the catch-all breaks its own family's convention.

Six of the seven `fraude_scoring/*` tables the worker `operadora.fraude.score_indicators` aggregates
(src/maezo/tools/workers/fraude.py:150-158) share one structure: indicator rules, then an EXPLICIT
"indicador ausente" rule emitting `0`/`"none"`, then a terminal catch-all emitting `10` /
`"<decision_id>_indeterminado"`. `upcoding_complexity_ceiling` is the ONLY one with no explicit
"ausente" rule: its catch-all serves BOTH populations (its own description says so, :70) and
resolves them permissively as `0`/`"none"` (:73-74).

The family convention is READ OFF THE SIX SIBLING ARTIFACTS below, so "the family says 10 /
_indeterminado" is derived, not asserted. Nothing in the candidate is invented: the tier numbers are
the target table's own complements, and the catch-all values are the siblings' own.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from maezo.platform.deploy.engine_deploy import collect_artifacts, resolve_spec_processes_dir
from maezo.tools.workers.fraude import _NO_INDICATOR_LABEL, _SCORING_DECISIONS
from tests.support.dmn_first_hit import (
    DMN_DIR,
    REPO_ROOT,
    DecisionTable,
    evaluate,
    read_candidate_table,
    read_live_table,
)

_TARGET = "upcoding_complexity_ceiling"
_LIVE = DMN_DIR / f"{_TARGET}.dmn"
_MANIFEST = DMN_DIR / "upcoding-complexity-ceiling-shadow-candidate.yaml"

_SIBLINGS = tuple(decision for decision in _SCORING_DECISIONS if decision != _TARGET)
_CLASSES_DECLARADAS = ("ambulatorio", "pronto_socorro", "internacao")
_CLASSES_GRID = (*_CLASSES_DECLARADAS, "", "domiciliar", "AMBULATORIO")
_TIER_GRID = tuple(range(-1, 9))


def _live() -> DecisionTable:
    return read_live_table(_LIVE)


def _candidate() -> DecisionTable:
    return read_candidate_table(_MANIFEST, _live())


def _inputs(encounter_class: str, code_tier: int) -> dict[str, Any]:
    return {"encounter_class": encounter_class, "code_tier": code_tier}


def _indicator(saidas: dict[str, Any]) -> tuple[Any, Any]:
    return saidas["indicador_score"], saidas["indicador_label"]


# =================================================================================================
# 1. THE FAMILY CONVENTION, READ OFF THE SIBLING ARTIFACTS
# =================================================================================================


def test_the_scoring_family_is_the_seven_tables_the_worker_aggregates() -> None:
    assert _TARGET in _SCORING_DECISIONS
    assert len(_SCORING_DECISIONS) == 7
    assert len(_SIBLINGS) == 6


@pytest.mark.parametrize("decision", _SIBLINGS)
def test_every_sibling_catchall_scores_10_and_labels_indeterminado(decision: str) -> None:
    table = read_live_table(DMN_DIR / f"{decision}.dmn")
    catchall = table.rules[-1]
    assert catchall.rule_id == "r_catchall"
    assert set(catchall.inputs) == {"-"}
    assert _indicator(dict(zip(table.output_names, catchall.outputs, strict=True))) == (
        10,
        f"{decision}_indeterminado",
    )


@pytest.mark.parametrize("decision", _SIBLINGS)
def test_every_sibling_has_an_explicit_indicator_absent_rule_that_is_not_the_catchall(decision: str) -> None:
    """The (ii) piece of the convention — a POSITIVE condition for 'within the normal band'."""
    table = read_live_table(DMN_DIR / f"{decision}.dmn")
    score_col = table.output_names.index("indicador_score")
    label_col = table.output_names.index("indicador_label")
    absent = [
        rule
        for rule in table.rules[:-1]
        if rule.outputs[score_col] == 0 and rule.outputs[label_col] == _NO_INDICATOR_LABEL
    ]
    assert absent, f"{decision} has no explicit 'indicador ausente' rule"
    for rule in absent:
        assert set(rule.inputs) != {"-"}, f"{decision}:{rule.rule_id} is a wildcard-only rule"


def test_the_target_is_the_only_family_member_that_breaks_the_convention() -> None:
    live = _live()
    score_col = live.output_names.index("indicador_score")
    label_col = live.output_names.index("indicador_label")
    catchall = live.rules[-1]

    assert catchall.rule_id == "r_catchall"
    assert (catchall.outputs[score_col], catchall.outputs[label_col]) == (0, _NO_INDICATOR_LABEL)
    assert not [
        rule
        for rule in live.rules[:-1]
        if rule.outputs[score_col] == 0 and rule.outputs[label_col] == _NO_INDICATOR_LABEL
    ], "the target unexpectedly has an explicit 'indicador ausente' rule"


def test_the_permissive_catchall_label_is_the_one_the_worker_drops() -> None:
    """`"none"` is filtered out of `indicadores_presentes` (fraude.py:344-346, `_NO_INDICATOR_LABEL`),
    so today an unmapped encounter class leaves NO trace at all in the dossier — whereas the
    family's `..._indeterminado` label is carried through."""
    assert _NO_INDICATOR_LABEL == "none"
    live = _live()
    saidas = dict(zip(live.output_names, live.rules[-1].outputs, strict=True))
    assert saidas["indicador_label"] == _NO_INDICATOR_LABEL


def test_candidate_catchall_adopts_the_family_values_verbatim() -> None:
    candidate = _candidate()
    catchall = candidate.rules[-1]
    assert catchall.rule_id == "c_catchall"
    saidas = dict(zip(candidate.output_names, catchall.outputs, strict=True))
    assert _indicator(saidas) == (10, f"{_TARGET}_indeterminado")

    family_scores = set()
    for decision in _SIBLINGS:
        table = read_live_table(DMN_DIR / f"{decision}.dmn")
        family_scores.add(table.rules[-1].outputs[table.output_names.index("indicador_score")])
    assert family_scores == {10}, "the family no longer agrees on 10 — re-derive before trusting this"


def test_candidate_introduces_no_new_tier_number() -> None:
    """`< 2`, `<= 3`, `<= 4` are the exact complements of the live ceilings `> 2`/`2`/`> 3`/`> 4`."""

    def numbers(table: DecisionTable) -> set[float]:
        found: set[float] = set()
        for rule in table.rules:
            for entry in rule.inputs:
                found.update(float(token) for token in re.findall(r"-?\d+(?:\.\d+)?", entry))
        return found

    assert numbers(_candidate()) <= numbers(_live())
    assert numbers(_candidate()) == {2.0, 3.0, 4.0}


def test_candidate_preserves_the_four_indicator_rules_verbatim() -> None:
    live, candidate = _live(), _candidate()
    live_by_id = {rule.rule_id: rule for rule in live.rules}
    for cand_id, live_id in (
        ("c_ambulatorio_tier3", "r_ambulatorio_tier3"),
        ("c_ps_tier4", "r_ps_tier4"),
        ("c_ambulatorio_tier2", "r_ambulatorio_tier2"),
        ("c_internacao_hightier", "r_internacao_hightier"),
    ):
        cand_rule = next(rule for rule in candidate.rules if rule.rule_id == cand_id)
        assert cand_rule.inputs == live_by_id[live_id].inputs, cand_id
        assert cand_rule.outputs[:2] == live_by_id[live_id].outputs[:2], cand_id


# =================================================================================================
# 2. THE DIVERGENCE CORPUS — the ratification evidence
# =================================================================================================

_DIVERGENCIAS: tuple[tuple[str, dict[str, Any], tuple[Any, Any], tuple[Any, Any]], ...] = (
    (
        "empty encounter_class — indistinguishable from a checked, conforming encounter",
        _inputs("", 3),
        (0, "none"),
        (10, f"{_TARGET}_indeterminado"),
    ),
    (
        "an encounter class outside the three the table declares",
        _inputs("domiciliar", 5),
        (0, "none"),
        (10, f"{_TARGET}_indeterminado"),
    ),
    (
        "a case-variant literal — string equality, so it does not match",
        _inputs("AMBULATORIO", 4),
        (0, "none"),
        (10, f"{_TARGET}_indeterminado"),
    ),
    (
        "an unmapped class at a tier that would be flagged for ambulatorio",
        _inputs("hospital_dia", 3),
        (0, "none"),
        (10, f"{_TARGET}_indeterminado"),
    ),
)


@pytest.mark.parametrize(
    ("label", "values", "live_expected", "cand_expected"),
    _DIVERGENCIAS,
    ids=[row[0][:60] for row in _DIVERGENCIAS],
)
def test_unmapped_encounter_class_reads_as_conforming_today(
    label: str, values: dict[str, Any], live_expected: tuple[Any, Any], cand_expected: tuple[Any, Any]
) -> None:
    assert _indicator(evaluate(_live(), values).saidas) == live_expected, label
    assert _indicator(evaluate(_candidate(), values).saidas) == cand_expected, label


_INALTERADOS: tuple[tuple[str, dict[str, Any], tuple[Any, Any]], ...] = (
    ("ambulatorio tier 1 — within its ceiling", _inputs("ambulatorio", 1), (0, "none")),
    ("ambulatorio tier 2 — borderline", _inputs("ambulatorio", 2), (15, f"{_TARGET}_borderline")),
    ("ambulatorio tier 3 — over its ceiling", _inputs("ambulatorio", 3), (30, _TARGET)),
    ("pronto_socorro tier 3 — within its ceiling", _inputs("pronto_socorro", 3), (0, "none")),
    ("pronto_socorro tier 4 — over its ceiling", _inputs("pronto_socorro", 4), (30, _TARGET)),
    ("internacao tier 4 — within its ceiling", _inputs("internacao", 4), (0, "none")),
    ("internacao tier 5 — borderline", _inputs("internacao", 5), (15, f"{_TARGET}_borderline")),
)


@pytest.mark.parametrize(
    ("label", "values", "expected"), _INALTERADOS, ids=[row[0][:60] for row in _INALTERADOS]
)
def test_the_divergence_corpus_is_not_all_of_the_table(
    label: str, values: dict[str, Any], expected: tuple[Any, Any]
) -> None:
    assert _indicator(evaluate(_live(), values).saidas) == expected, label
    assert _indicator(evaluate(_candidate(), values).saidas) == expected, label


# =================================================================================================
# 3. THE EQUIVALENCE STATEMENT, PROVED BY EXHAUSTION
# =================================================================================================


def test_candidate_and_live_diverge_exactly_outside_the_three_declared_classes() -> None:
    live, candidate = _live(), _candidate()
    divergences = 0
    for encounter_class in _CLASSES_GRID:
        for code_tier in _TIER_GRID:
            values = _inputs(encounter_class, code_tier)
            live_ind = _indicator(evaluate(live, values).saidas)
            cand_ind = _indicator(evaluate(candidate, values).saidas)
            expected_divergence = encounter_class not in _CLASSES_DECLARADAS
            assert (live_ind != cand_ind) is expected_divergence, values
            if expected_divergence:
                assert live_ind == (0, "none")
                assert cand_ind == (10, f"{_TARGET}_indeterminado")
                divergences += 1
    # 3 undeclared classes x 10 tiers.
    assert divergences == 30


def test_candidate_never_lowers_a_score() -> None:
    """The change is monotone: it only ever ADDS evidence to the dossier, never removes it."""
    live, candidate = _live(), _candidate()
    for encounter_class in _CLASSES_GRID:
        for code_tier in _TIER_GRID:
            values = _inputs(encounter_class, code_tier)
            assert (
                evaluate(candidate, values).saidas["indicador_score"]
                >= evaluate(live, values).saidas["indicador_score"]
            ), values


def test_the_catchall_becomes_unreachable_for_the_three_declared_classes() -> None:
    candidate = _candidate()
    reached = {
        evaluate(candidate, _inputs(cls, tier)).regra for cls in _CLASSES_DECLARADAS for tier in _TIER_GRID
    }
    assert "c_catchall" not in reached


# =================================================================================================
# 4. THE ACTIVATION PATH IS REAL
# =================================================================================================


def test_candidate_manifest_is_invisible_to_artifact_deployment() -> None:
    artifacts = {path.resolve() for path in collect_artifacts(resolve_spec_processes_dir())}
    assert _MANIFEST.resolve() not in artifacts
    assert _LIVE.resolve() in artifacts


# =================================================================================================
# 5. WHAT BREAKS WHEN THE OWNER APPLIES IT — stated up front, derived from the artifacts
# =================================================================================================


def test_applying_the_candidate_shifts_the_live_engine_scenario_a_expectation() -> None:
    """The defect is ALREADY live-engine-verified, and closing it moves one committed expectation.

    `tests/integration/dmn/test_fraude_scoring_chain.py::test_scenario_a_no_signal_falls_to_catchalls`
    (:110-132) evaluates the seven tables against the REAL engine with NO evidence attached and
    pins `score_indicadores == 75` with SIX labels — `upcoding_complexity_ceiling` being the ONE
    of the seven that is missing, because it alone contributes `0`/`"none"`.

    Applying the candidate raises that total by EXACTLY the catch-all delta (0 -> 10) and adds the
    seventh label. This test derives the delta from the two artifacts so the follow-up is a number
    someone can act on rather than a surprise at integration time.
    """
    live_catchall = _live().rules[-1]
    cand_catchall = _candidate().rules[-1]
    score_col = _live().output_names.index("indicador_score")
    label_col = _live().output_names.index("indicador_label")

    delta = cand_catchall.outputs[score_col] - live_catchall.outputs[score_col]
    assert delta == 10

    integration = (REPO_ROOT / "tests" / "integration" / "dmn" / "test_fraude_scoring_chain.py").read_text(
        encoding="utf-8"
    )
    # The expectation this candidate moves, and the two scenarios it does NOT move.
    assert 'result["score_indicadores"] == 75' in integration
    assert f'"{_TARGET}_indeterminado"' not in integration
    assert live_catchall.outputs[label_col] == _NO_INDICATOR_LABEL
    assert integration.count('"encounter_class": "ambulatorio"') == 2
