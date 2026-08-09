"""M-7 (CLINICAL) — `triage_redflag_gestante` / `triage_redflag_pediatric`.

BOTH CANDIDATES PROPOSE NO RULE CHANGE. This module is what makes that claim checkable rather than
trustworthy: it proves candidate == live on ALL FOUR outputs over an exhaustive grid, proves the
property that justifies proposing no reordering (the live order already emits the MOST SEVERE
matching verdict for every input), and VERIFIES EVERY ACHADO REPRO VECTOR RECORDED IN THE MANIFESTS
against the live artifact — so a vector that drifts from the table fails here, and the clinical
reviewer can trust what the manifest shows them.

No clinical threshold is asserted anywhere in this module or in either manifest.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from maezo.platform.deploy.engine_deploy import collect_artifacts, resolve_spec_processes_dir
from tests.support.dmn_first_hit import (
    DMN_DIR,
    DecisionTable,
    evaluate,
    load_manifest,
    matches,
    read_candidate_table,
    read_live_table,
)

_ADULT = DMN_DIR / "triage_redflag_adult.dmn"

#: live table -> (candidate manifest, population input name, grid of population values).
_TABLES: dict[str, tuple[str, str, tuple[int, ...]]] = {
    "triage_redflag_gestante.dmn": (
        "triage-redflag-gestante-shadow-candidate.yaml",
        "idade_gestacional_semanas",
        (6, 19, 20, 21, 25, 26, 27, 36, 37, 38, 40),
    ),
    "triage_redflag_pediatric.dmn": (
        "triage-redflag-pediatric-shadow-candidate.yaml",
        "idade_meses",
        (0, 1, 2, 3, 4, 6, 11, 12, 13, 24),
    ),
}

#: The four `intensidade` values the family's common schema declares (triage_redflag_adult.dmn:17),
#: plus one literal outside it.
_INTENSIDADES = ("leve", "moderada", "grave", "desconhecida", "")

#: Severity order, derived from the declared output domains (triage_redflag_adult.dmn:18-19).
_SEVERITY: dict[tuple[bool, str, str], int] = {
    (True, "P1", "ESCALATE_EMERGENCY"): 2,
    (True, "P2", "ESCALATE_NURSE"): 1,
    (False, "-", "CONTINUE"): 0,
}


def _live(name: str) -> DecisionTable:
    return read_live_table(DMN_DIR / name)


def _candidate(name: str) -> DecisionTable:
    manifest, _, _ = _TABLES[name]
    return read_candidate_table(DMN_DIR / manifest, _live(name))


def _symptom_codes(table: DecisionTable) -> tuple[str, ...]:
    """The `sintoma_codigo` literals the table declares, read off the artifact."""
    column = table.input_names.index("sintoma_codigo")
    return tuple(rule.inputs[column].strip('"') for rule in table.rules if rule.inputs[column] != "-")


def _grid(name: str) -> list[dict[str, Any]]:
    table = _live(name)
    _, population, ages = _TABLES[name]
    codes = (*_symptom_codes(table), "sintoma_nao_mapeado", "")
    return [
        {"sintoma_codigo": code, "intensidade": intensidade, population: age}
        for code in codes
        for intensidade in _INTENSIDADES
        for age in ages
    ]


def _rank(saidas: dict[str, Any]) -> int:
    key = (saidas["red_flag"], saidas["prioridade"], saidas["conduta"])
    assert key in _SEVERITY, f"undeclared verdict shape {key}"
    return _SEVERITY[key]


# =================================================================================================
# 1. THE COMMON SCHEMA THESE MANIFESTS REASON FROM IS REALLY DECLARED WHERE THEY SAY IT IS
# =================================================================================================


def test_the_common_schema_declares_the_intensity_domain_and_the_fail_safe() -> None:
    text = _ADULT.read_text(encoding="utf-8")
    assert "[leve|moderada|grave|desconhecida]" in text
    assert "[P1|P2|-]" in text
    assert "[ESCALATE_EMERGENCY|ESCALATE_NURSE|CONTINUE]" in text
    assert 'intensidade "grave" de QUALQUER sintoma nao mapeado escala para enfermagem' in text


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_each_table_incorporates_the_common_schema_by_reference(name: str) -> None:
    assert "Schema comum triage_redflag_* (ver triage_redflag_adult)" in (DMN_DIR / name).read_text(
        encoding="utf-8"
    )


# =================================================================================================
# 2. WHY NO REORDERING IS PROPOSED — the live order is already severity-maximal
# =================================================================================================


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_the_live_order_already_emits_the_most_severe_matching_verdict(name: str) -> None:
    """Under FIRST-hit, no permutation of these eight rules could escalate any input further."""
    table = _live(name)
    for values in _grid(name):
        ordered = tuple(values[column] for column in table.input_names)
        matching = [
            rule
            for rule in table.rules
            if all(matches(entry, value) for entry, value in zip(rule.inputs, ordered, strict=True))
        ]
        assert matching, values
        best = max(_rank(dict(zip(table.output_names, rule.outputs, strict=True))) for rule in matching)
        assert _rank(evaluate(table, values).saidas) == best, values


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_rule_severity_is_non_increasing_down_the_table(name: str) -> None:
    table = _live(name)
    ranks = [_rank(dict(zip(table.output_names, rule.outputs, strict=True))) for rule in table.rules]
    assert ranks == sorted(ranks, reverse=True), ranks


# =================================================================================================
# 3. THE CANDIDATES ARE THE LIVE TABLES — total equivalence, zero divergence vectors
# =================================================================================================


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_candidate_is_cell_for_cell_the_live_table(name: str) -> None:
    live, candidate = _live(name), _candidate(name)
    assert candidate.rule_ids() == live.rule_ids()
    for cand_rule, live_rule in zip(candidate.rules, live.rules, strict=True):
        assert cand_rule.inputs == live_rule.inputs, cand_rule.rule_id
        assert cand_rule.outputs == live_rule.outputs, cand_rule.rule_id


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_candidate_and_live_agree_on_every_output_over_the_whole_grid(name: str) -> None:
    live, candidate = _live(name), _candidate(name)
    divergences = [values for values in _grid(name) if evaluate(live, values) != evaluate(candidate, values)]
    assert divergences == []


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_the_manifest_declares_that_it_proposes_no_rule_change(name: str) -> None:
    manifest, _, _ = _TABLES[name]
    data = load_manifest(DMN_DIR / manifest)
    assert data["proposta"] == "NENHUMA_MUDANCA_DE_REGRA"
    assert data["achados"], "an achado-only candidate with no achados would be empty"


# =================================================================================================
# 4. EVERY ACHADO REPRO VECTOR IS VERIFIED AGAINST THE LIVE ARTIFACT
# =================================================================================================


def _recorded_vectors(manifest: Path) -> list[tuple[str, dict[str, Any], str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any], str, dict[str, Any]]] = []
    for achado in load_manifest(manifest)["achados"]:
        for vector in achado.get("vetores_repro", []):
            rows.append((achado["id"], vector["entrada"], vector["regra_viva"], vector["saida_viva"]))
    return rows


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_every_recorded_repro_vector_reproduces_on_the_live_table(name: str) -> None:
    manifest, _, _ = _TABLES[name]
    table = _live(name)
    vectors = _recorded_vectors(DMN_DIR / manifest)
    assert vectors, "the achados record no repro vectors — the evidence would be prose only"
    for achado_id, entrada, regra_viva, saida_viva in vectors:
        verdict = evaluate(table, {column: entrada[column] for column in table.input_names})
        assert verdict.regra == regra_viva, f"{achado_id}: {entrada}"
        for output, expected in saida_viva.items():
            assert verdict.saidas[output] == expected, f"{achado_id}: {entrada}[{output}]"


def test_the_pediatric_fever_band_gap_is_recorded_with_its_contrast_pair() -> None:
    """The mission's named finding: febre is only a red flag below 3 months, and the table declares
    no band above it. Recorded as an ACHADO — no threshold is proposed."""
    table = _live("triage_redflag_pediatric.dmn")
    under = evaluate(table, {"sintoma_codigo": "febre", "intensidade": "moderada", "idade_meses": 2})
    over = evaluate(table, {"sintoma_codigo": "febre", "intensidade": "moderada", "idade_meses": 4})
    assert (under.regra, under.saidas["red_flag"], under.saidas["prioridade"]) == ("r1", True, "P1")
    assert (over.regra, over.saidas["red_flag"], over.saidas["conduta"]) == ("r8", False, "CONTINUE")

    achado_ids = {
        achado["id"]
        for achado in load_manifest(DMN_DIR / _TABLES["triage_redflag_pediatric.dmn"][0])["achados"]
    }
    assert "ACHADO-P1-lacuna-da-faixa-de-febre-acima-de-3-meses" in achado_ids


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_an_explicitly_unknown_intensity_never_raises_a_red_flag(name: str) -> None:
    """`desconhecida` is a DECLARED domain value with no rule in either table — recorded as an
    ACHADO in both. Proved here so the finding cannot rot."""
    table = _live(name)
    _, population, _ = _TABLES[name]
    # A symptom whose rule GATES on intensidade, at an age well inside the table's own bands.
    code, age = {
        "triage_redflag_gestante.dmn": ("febre", 30),
        "triage_redflag_pediatric.dmn": ("sinais_desidratacao", 8),
    }[name]

    unknown = evaluate(table, {"sintoma_codigo": code, "intensidade": "desconhecida", population: age})
    known = evaluate(table, {"sintoma_codigo": code, "intensidade": "moderada", population: age})
    assert unknown.saidas["red_flag"] is False
    assert known.saidas["red_flag"] is True


# =================================================================================================
# 5. THE ACTIVATION PATH IS REAL
# =================================================================================================


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_candidate_manifest_is_invisible_to_artifact_deployment(name: str) -> None:
    manifest, _, _ = _TABLES[name]
    artifacts = {path.resolve() for path in collect_artifacts(resolve_spec_processes_dir())}
    assert (DMN_DIR / manifest).resolve() not in artifacts
    assert (DMN_DIR / name).resolve() in artifacts
