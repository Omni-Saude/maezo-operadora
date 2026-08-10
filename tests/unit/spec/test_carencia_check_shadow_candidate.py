"""M-5 — `carencia_check`: the CPT block masks the urgency ladder (divergence evidence).

`spec/processes/dmn/carencia_check.dmn` opens with `r01`/`r02` (:89-108), which gate ONLY on
`dias_desde_adesao` and `cpt_declarada = true` and carry the WILDCARD `-` on `tipo_procedimento`
(:91, :102). Every other rule requires `cpt_declarada = false`. Under `hitPolicy=FIRST` that makes
the 24h/1-day urgency period the table declares for itself (:19-20, rules `r03`/`r04`) UNREACHABLE
whenever a CPT is declared — and makes the L0-hard catch-all `r99` unreachable for that whole
half-space.

Regulatory basis the SME ratifies against (cited in the manifest, NOT applied here): Lei 9.656/1998
art. 12, V + art. 35-C, against art. 11. No day-count is invented anywhere: every number the
candidate uses is copied from a live rule, and that containment is itself a test below.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from maezo.platform.deploy.engine_deploy import collect_artifacts, resolve_spec_processes_dir
from tests.support.dmn_first_hit import (
    DMN_DIR,
    DecisionTable,
    evaluate,
    read_candidate_table,
    read_live_table,
)

_LIVE = DMN_DIR / "carencia_check.dmn"
_MANIFEST = DMN_DIR / "carencia-check-shadow-candidate.yaml"

_TIPOS_DECLARADOS = ("urgencia_emergencia", "parto", "eletivo", "alta_complexidade", "outros")
_TIPOS_GRID = (*_TIPOS_DECLARADOS, "", "internacao")
#: Every boundary the live table declares, plus their neighbours.
_DIAS_GRID = (0, 1, 2, 179, 180, 181, 299, 300, 301, 729, 730, 731)

#: candidate rule id -> the live rule it is derived from, in candidate evaluation order.
#: `fonte` is re-worded with a `CANDIDATO —` prefix in EVERY candidate rule (the same convention
#: W3 uses for `motivo`), so it is provenance text and never a divergence signal; what IS a signal
#: is WHICH live rule the candidate lands on.
_ORIGEM: dict[str, str] = {
    "c_urgencia_nao_cumprida": "r03",
    "c_urgencia_cumprida": "r04",
    "c_cpt_nao_cumprida": "r01",
    "c_cpt_cumprida": "r02",
    "c_parto_nao_cumprida": "r05",
    "c_parto_cumprida": "r06",
    "c_eletivo_nao_cumprida": "r07",
    "c_eletivo_cumprida": "r08",
    "c_alta_complexidade_nao_cumprida": "r09",
    "c_alta_complexidade_cumprida": "r10",
    "c_outros": "r11",
    "c_catchall": "r99",
}


def _live() -> DecisionTable:
    return read_live_table(_LIVE)


def _candidate() -> DecisionTable:
    return read_candidate_table(_MANIFEST, _live())


def _inputs(tipo: str, dias: int, cpt: bool) -> dict[str, Any]:
    return {"tipo_procedimento": tipo, "dias_desde_adesao": dias, "cpt_declarada": cpt}


def _decision(saidas: dict[str, Any]) -> tuple[Any, Any]:
    """The two decision-bearing outputs. `fonte` is provenance text, compared separately."""
    return saidas["carencia_cumprida"], saidas["prazo_restante_dias"]


# =================================================================================================
# 1. STRUCTURAL FACTS about the live table
# =================================================================================================


def test_live_cpt_block_is_first_and_wildcards_the_procedure_type() -> None:
    live = _live()
    tipo_col = live.input_names.index("tipo_procedimento")
    cpt_col = live.input_names.index("cpt_declarada")
    r01, r02 = live.rules[0], live.rules[1]
    assert (r01.rule_id, r02.rule_id) == ("r01", "r02")
    assert r01.inputs[tipo_col] == "-" and r02.inputs[tipo_col] == "-"
    assert r01.inputs[cpt_col] == "true" and r02.inputs[cpt_col] == "true"


def test_live_urgency_ladder_requires_cpt_false() -> None:
    live = _live()
    cpt_col = live.input_names.index("cpt_declarada")
    by_id = {rule.rule_id: rule for rule in live.rules}
    assert by_id["r03"].inputs[cpt_col] == "false"
    assert by_id["r04"].inputs[cpt_col] == "false"


def test_live_everything_below_the_cpt_block_is_unreachable_under_cpt_fact_a() -> None:
    """FACT (A): with `cpt_declarada = true`, rows 3..12 can never be reached — whatever the type."""
    live = _live()
    reached = {evaluate(live, _inputs(tipo, dias, True)).regra for tipo in _TIPOS_GRID for dias in _DIAS_GRID}
    assert reached == {"r01", "r02"}


def test_live_l0_catchall_is_unreachable_under_cpt_fact_b() -> None:
    """FACT (B): the catch-all the table declares 'OBRIGATORIO L0 HARD' never fires under CPT."""
    live = _live()
    assert live.rules[-1].rule_id == "r99"
    reached = {evaluate(live, _inputs(tipo, dias, True)).regra for tipo in _TIPOS_GRID for dias in _DIAS_GRID}
    assert "r99" not in reached
    # Non-vacuity: it IS reachable without a CPT, for a type outside the declared domain.
    assert evaluate(live, _inputs("", 100, False)).regra == "r99"


def test_candidate_introduces_no_new_day_count() -> None:
    """Every numeric literal in the candidate's input entries already exists in the live table."""

    def numbers(table: DecisionTable) -> set[float]:
        found: set[float] = set()
        for rule in table.rules:
            for entry in rule.inputs:
                found.update(float(token) for token in re.findall(r"-?\d+(?:\.\d+)?", entry))
        return found

    assert numbers(_candidate()) <= numbers(_live())
    assert numbers(_candidate()) == {1.0, 180.0, 300.0, 730.0}


def test_candidate_only_widens_the_cpt_column_of_the_two_urgency_rules() -> None:
    """The whole diff: two rows move to the front, and ONE cell in each changes `false` -> `-`."""
    live, candidate = _live(), _candidate()
    cpt_col = live.input_names.index("cpt_declarada")
    live_by_id = {rule.rule_id: rule for rule in live.rules}
    assert candidate.rule_ids() == tuple(_ORIGEM)
    for cand_id, live_id in _ORIGEM.items():
        cand_rule = next(rule for rule in candidate.rules if rule.rule_id == cand_id)
        live_rule = live_by_id[live_id]
        expected = list(live_rule.inputs)
        if cand_id in ("c_urgencia_nao_cumprida", "c_urgencia_cumprida"):
            expected[cpt_col] = "-"
        assert list(cand_rule.inputs) == expected, cand_id
        # The two decision-bearing outputs are preserved verbatim; only `fonte` is re-worded.
        assert cand_rule.outputs[:2] == live_rule.outputs[:2], cand_id


# =================================================================================================
# 2. THE DIVERGENCE CORPUS — the ratification evidence
# =================================================================================================

#: (label, inputs, live (cumprida, prazo), live rule, candidate (cumprida, prazo), candidate rule).
_DIVERGENCIAS: tuple[tuple[str, dict[str, Any], tuple[Any, Any], str, tuple[Any, Any], str], ...] = (
    (
        "MISSION REPRO — urgencia + 30 dias + CPT reads the 24-MONTH period",
        _inputs("urgencia_emergencia", 30, True),
        (False, 700),
        "r01",
        (True, 0),
        "c_urgencia_cumprida",
    ),
    (
        "one day after enrolment — the exact day the table's own 24h period is met",
        _inputs("urgencia_emergencia", 1, True),
        (False, 729),
        "r01",
        (True, 0),
        "c_urgencia_cumprida",
    ),
    (
        "one day short of the CPT period — still an urgency, still masked",
        _inputs("urgencia_emergencia", 729, True),
        (False, 1),
        "r01",
        (True, 0),
        "c_urgencia_cumprida",
    ),
    (
        "same-day enrolment — both say not met, but the deadline reported differs by 729 days",
        _inputs("urgencia_emergencia", 0, True),
        (False, 730),
        "r01",
        (False, 1),
        "c_urgencia_nao_cumprida",
    ),
)


@pytest.mark.parametrize(
    ("label", "values", "live_expected", "live_rule", "cand_expected", "cand_rule"),
    _DIVERGENCIAS,
    ids=[row[0][:60] for row in _DIVERGENCIAS],
)
def test_cpt_block_masks_the_urgency_period_the_table_declares(
    label: str,
    values: dict[str, Any],
    live_expected: tuple[Any, Any],
    live_rule: str,
    cand_expected: tuple[Any, Any],
    cand_rule: str,
) -> None:
    live_verdict = evaluate(_live(), values)
    cand_verdict = evaluate(_candidate(), values)
    assert (_decision(live_verdict.saidas), live_verdict.regra) == (live_expected, live_rule), label
    assert (_decision(cand_verdict.saidas), cand_verdict.regra) == (cand_expected, cand_rule), label


def test_the_third_divergence_class_is_provenance_only() -> None:
    """At or beyond the CPT period the two agree on the numbers but attribute a DIFFERENT source —
    the record the auditor reads would cite the CPT rule rather than the urgency rule."""
    values = _inputs("urgencia_emergencia", 730, True)
    live_verdict = evaluate(_live(), values)
    cand_verdict = evaluate(_candidate(), values)
    assert _decision(live_verdict.saidas) == _decision(cand_verdict.saidas) == (True, 0)
    assert (live_verdict.regra, cand_verdict.regra) == ("r02", "c_urgencia_cumprida")
    assert live_verdict.saidas["fonte"] != cand_verdict.saidas["fonte"]


_INALTERADOS: tuple[tuple[str, dict[str, Any], tuple[Any, Any]], ...] = (
    ("urgencia without CPT, day 0", _inputs("urgencia_emergencia", 0, False), (False, 1)),
    ("urgencia without CPT, day 30", _inputs("urgencia_emergencia", 30, False), (True, 0)),
    ("parto under CPT stays on the CPT ladder (ACHADO-1)", _inputs("parto", 250, True), (False, 480)),
    ("eletivo under CPT stays on the CPT ladder", _inputs("eletivo", 100, True), (False, 630)),
    ("eletivo without CPT, day 179", _inputs("eletivo", 179, False), (False, 1)),
    ("eletivo without CPT, day 180", _inputs("eletivo", 180, False), (True, 0)),
    ("unmapped type without CPT falls to the L0 catch-all", _inputs("", 100, False), (False, -1)),
)


@pytest.mark.parametrize(
    ("label", "values", "expected"), _INALTERADOS, ids=[row[0][:60] for row in _INALTERADOS]
)
def test_the_divergence_corpus_is_not_all_of_the_table(
    label: str, values: dict[str, Any], expected: tuple[Any, Any]
) -> None:
    assert _decision(evaluate(_live(), values).saidas) == expected, label
    assert _decision(evaluate(_candidate(), values).saidas) == expected, label


# =================================================================================================
# 3. THE EQUIVALENCE STATEMENT, PROVED BY EXHAUSTION
# =================================================================================================


def test_candidate_and_live_diverge_exactly_on_urgencia_under_cpt() -> None:
    """Two measures, because they draw different lines:

    - DECISION divergence (`carencia_cumprida` / `prazo_restante_dias`) — urgencia under CPT, below
      the CPT period;
    - ORIGIN divergence (which live rule the candidate lands on) — urgencia under CPT, all of it,
      including the region where the numbers happen to coincide.

    `fonte` is never compared: like W3's `motivo`, EVERY candidate rule re-words it with a
    `CANDIDATO —` prefix, so it is provenance text, not a signal.
    """
    live, candidate = _live(), _candidate()
    numeric_divergences = 0
    origin_divergences = 0
    for tipo in _TIPOS_GRID:
        for dias in _DIAS_GRID:
            for cpt in (True, False):
                values = _inputs(tipo, dias, cpt)
                live_verdict = evaluate(live, values)
                cand_verdict = evaluate(candidate, values)

                is_urgencia_under_cpt = tipo == "urgencia_emergencia" and cpt

                origin_differs = _ORIGEM[cand_verdict.regra] != live_verdict.regra
                assert origin_differs is is_urgencia_under_cpt, values
                origin_divergences += int(origin_differs)

                numbers_differ = _decision(live_verdict.saidas) != _decision(cand_verdict.saidas)
                assert numbers_differ is (is_urgencia_under_cpt and dias < 730), values
                numeric_divergences += int(numbers_differ)

                if not origin_differs:
                    assert _decision(live_verdict.saidas) == _decision(cand_verdict.saidas), values

    # 12 grid days, urgencia only, CPT only.
    assert origin_divergences == 12
    # 10 of those 12 days are below the CPT period.
    assert numeric_divergences == 10


# =================================================================================================
# 4. THE L0 HARD INVARIANT IS UNTOUCHED, AND THE ACTIVATION PATH IS REAL
# =================================================================================================


def test_candidate_never_turns_a_met_waiting_period_into_an_unmet_one() -> None:
    """The candidate is monotone in the beneficiary's favour: it never flips `true` -> `false`, and
    never lengthens a reported deadline."""
    live, candidate = _live(), _candidate()
    for tipo in _TIPOS_GRID:
        for dias in _DIAS_GRID:
            for cpt in (True, False):
                values = _inputs(tipo, dias, cpt)
                live_ok, live_prazo = _decision(evaluate(live, values).saidas)
                cand_ok, cand_prazo = _decision(evaluate(candidate, values).saidas)
                assert not (live_ok and not cand_ok), values
                if live_prazo >= 0 and cand_prazo >= 0:
                    assert cand_prazo <= live_prazo, values


def test_candidate_manifest_is_invisible_to_artifact_deployment() -> None:
    artifacts = {path.resolve() for path in collect_artifacts(resolve_spec_processes_dir())}
    assert _MANIFEST.resolve() not in artifacts
    assert _LIVE.resolve() in artifacts
