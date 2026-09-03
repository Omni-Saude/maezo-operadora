"""M-4 — `glosa_triage`: the permissive terminal at position 1 (divergence evidence).

`spec/processes/dmn/glosa_triage.dmn` row 1 (`r_pagar`) gates `categoria_normalizada` on the
NEGATIVE set `not("tecnica","clinica")`, which admits `"desconhecida"` — the value the upstream
table declares must route to a human downstream IN THIS VERY TABLE
(`glosa_reason_normalization.dmn`) and which this table's own catch-all names. Under
`hitPolicy=FIRST` the fail-safe is unreachable for the whole {conforme, no divergence, docs}
subspace, and `PAGAR` is a terminal outcome with no user task on the payer's side.

ADR-0040 (Proposed) rebuilt the live table in the PAYER's vocabulary — `roteamento` is now the
closed set `{PAGAR, ANALISE_HUMANA}` and there is no output value that gloses. M-4 IS UNCHANGED by
that: the same defective input still reaches the same permissive row, and the candidate still
routes it to a human. What changed is the NAME of the value and the BLAST RADIUS: the input now
emits a demonstrativo and creates an order in SP-OP-PAGTO-001 which STOPS in the human
`UT_AnaliseAdmissibilidade` (invariant I-PAGTO-1 — the handoff never seeds `lastro_confirmado`).
More visibility, no automatic money — and the residue on the payer's own side is ADR-0040 OQ-10.

Every "the live table says X" below is DERIVED from the artifact by `tests/support/dmn_first_hit`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import pytest

from maezo.platform.deploy.engine_deploy import collect_artifacts, resolve_spec_processes_dir
from tests.support.dmn_first_hit import (
    DMN_DIR,
    REPO_ROOT,
    DecisionTable,
    evaluate,
    read_candidate_table,
    read_live_table,
)

_LIVE = DMN_DIR / "glosa_triage.dmn"
_NORMALIZATION = DMN_DIR / "glosa_reason_normalization.dmn"
_MANIFEST = DMN_DIR / "glosa-triage-shadow-candidate.yaml"
_BPMN = REPO_ROOT / "spec" / "processes" / "bpmn" / "SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn"

#: The categories the candidate keeps eligible for `PAGAR` — derived, never typed by hand:
#: the closed domain `glosa_reason_normalization` emits, MINUS the two the live table already
#: excludes, MINUS the fail-safe value that table declares must go to a human.
_ALREADY_EXCLUDED = ("tecnica", "clinica")
_FAILSAFE_CATEGORY = "desconhecida"


def _live() -> DecisionTable:
    return read_live_table(_LIVE)


def _candidate() -> DecisionTable:
    return read_candidate_table(_MANIFEST, _live())


def _inputs(categoria: str, conforme: bool, divergencia: bool, docs: bool, tipo: str = "") -> dict[str, Any]:
    return {
        "tipo_item": tipo,
        "categoria_normalizada": categoria,
        "item_conforme_tabela": conforme,
        "divergencia_valor": divergencia,
        "documentacao_anexa": docs,
    }


# =================================================================================================
# 1. STRUCTURAL FACTS about the live table — these make the corpus a property, not a sample
# =================================================================================================


def test_live_row_1_is_the_permissive_rule_with_a_negative_category_set() -> None:
    live = _live()
    first = live.rules[0]
    assert first.rule_id == "r_pagar"
    assert first.inputs[live.input_names.index("categoria_normalizada")] == 'not("tecnica","clinica")'
    assert first.outputs[0] == "PAGAR"


def test_live_catchall_is_last_and_declares_the_human_fail_safe() -> None:
    live = _live()
    last = live.rules[-1]
    assert last.rule_id == "r_catchall"
    assert set(last.inputs) == {"-"}
    assert last.outputs[0] == "ANALISE_HUMANA"


def test_the_declared_category_domain_comes_from_the_upstream_table() -> None:
    """The six categories are read off `glosa_reason_normalization.dmn`, not asserted in prose."""
    domain = {rule.outputs[0] for rule in read_live_table(_NORMALIZATION).rules}
    assert domain == {"administrativa", "tecnica", "valor", "documental", "clinica", "desconhecida"}


def test_the_candidate_positive_set_is_the_declared_domain_minus_the_three_human_routed() -> None:
    """The candidate INVENTS NO CATEGORY: its positive set is a derived subtraction."""
    domain = {rule.outputs[0] for rule in read_live_table(_NORMALIZATION).rules}
    expected = domain - set(_ALREADY_EXCLUDED) - {_FAILSAFE_CATEGORY}

    candidate = _candidate()
    pagar = next(rule for rule in candidate.rules if rule.rule_id == "c_pagar")
    entry = pagar.inputs[candidate.input_names.index("categoria_normalizada")]
    assert set(entry.replace('"', "").split(",")) == expected
    assert expected == {"documental", "administrativa", "valor"}


def test_upstream_table_declares_desconhecida_must_be_routed_to_a_human_here() -> None:
    """The cross-table contract the live table breaks — quoted from the upstream artifact itself."""
    text = _NORMALIZATION.read_text(encoding="utf-8")
    assert "rota conservadora a humano a jusante na" in text
    assert "glosa_triage" in text


def test_pagar_reaches_its_terminal_without_any_user_task() -> None:
    """Derived from the BPMN: the `PAGAR` flow reaches an endEvent through a chain of SERVICE tasks
    only — no `userTask` anywhere on it — while every other routing value is the default flow into
    the human dossier task.

    SUBSTITUI `test_sem_glosa_is_a_terminal_end_state_with_no_user_task`. Under ADR-0040 the
    permissive leg is no longer a bare publish->end pair: it emits a demonstrativo and hands the
    payment off. The PROPERTY the test exists to prove is unchanged and is what M-4 is about —
    no analista de contas ever sees the conta on this path."""
    root = ET.parse(_BPMN).getroot()

    def local(tag: str) -> str:
        return tag.rpartition("}")[2]

    flows = [el for el in root.iter() if local(el.tag) == "sequenceFlow"]
    pagar_flow = next(
        flow
        for flow in flows
        if any("'PAGAR'" in (child.text or "") for child in flow if local(child.tag) == "conditionExpression")
    )
    end_events = {el.get("id") for el in root.iter() if local(el.tag) == "endEvent"}
    user_tasks = {el.get("id") for el in root.iter() if local(el.tag) == "userTask"}

    # Walk the chain from the PAGAR target to its terminal; nothing on it may be a User Task.
    node = pagar_flow.get("targetRef")
    visited: list[str | None] = []
    while node not in end_events:
        assert node not in user_tasks, node
        visited.append(node)
        outgoing = [flow.get("targetRef") for flow in flows if flow.get("sourceRef") == node]
        assert len(outgoing) == 1, (node, outgoing)
        node = outgoing[0]
    assert visited == [
        "ST_EmitirDemonstrativoIntegral",
        "ST_HandoffPagamentoAuto",
        "ST_PublishPagamentoIntegral",
    ]
    assert node == "End_ContaAprovadaIntegral"


# =================================================================================================
# 2. THE DIVERGENCE CORPUS — the ratification evidence
# =================================================================================================

#: (label, inputs, live verdict, live rule, candidate verdict, candidate rule).
_DIVERGENCIAS: tuple[tuple[str, dict[str, Any], str, str, str, str], ...] = (
    (
        "MISSION REPRO — categoria 'desconhecida' with the three favourable facts closes the case",
        _inputs("desconhecida", True, False, True),
        "PAGAR",
        "r_pagar",
        "ANALISE_HUMANA",
        "c_catchall",
    ),
    (
        "same, with a non-empty tipo_item — the column is `-` in every rule, so it cannot matter",
        _inputs("desconhecida", True, False, True, tipo="material"),
        "PAGAR",
        "r_pagar",
        "ANALISE_HUMANA",
        "c_catchall",
    ),
    (
        "empty categoria — an upstream analysis that returns a blank instead of a category",
        _inputs("", True, False, True),
        "PAGAR",
        "r_pagar",
        "ANALISE_HUMANA",
        "c_catchall",
    ),
    (
        "a literal outside the declared domain entirely",
        _inputs("fraude_suspeita", True, False, True),
        "PAGAR",
        "r_pagar",
        "ANALISE_HUMANA",
        "c_catchall",
    ),
)


@pytest.mark.parametrize(
    ("label", "values", "live_expected", "live_rule", "cand_expected", "cand_rule"),
    _DIVERGENCIAS,
    ids=[row[0][:60] for row in _DIVERGENCIAS],
)
def test_live_closes_the_case_where_the_candidate_routes_to_a_human(
    label: str,
    values: dict[str, Any],
    live_expected: str,
    live_rule: str,
    cand_expected: str,
    cand_rule: str,
) -> None:
    live_verdict = evaluate(_live(), values)
    cand_verdict = evaluate(_candidate(), values)

    assert (live_verdict.saidas["roteamento"], live_verdict.regra) == (live_expected, live_rule), label
    assert (cand_verdict.saidas["roteamento"], cand_verdict.regra) == (cand_expected, cand_rule), label


#: Inputs the corpus must NOT cover — the candidate changes nothing here.
_INALTERADOS: tuple[tuple[str, dict[str, Any], str], ...] = (
    ("tecnica -> human in both", _inputs("tecnica", True, False, True), "ANALISE_HUMANA"),
    ("clinica -> human in both", _inputs("clinica", True, False, True), "ANALISE_HUMANA"),
    ("valor + divergence -> human in both", _inputs("valor", True, True, True), "ANALISE_HUMANA"),
    ("valor, no divergence -> PAGAR in both", _inputs("valor", True, False, True), "PAGAR"),
    (
        "documental + anexos -> PAGAR in both (ACHADO-4)",
        _inputs("documental", True, False, True),
        "PAGAR",
    ),
    (
        "administrativa + anexos -> PAGAR in both",
        _inputs("administrativa", True, False, True),
        "PAGAR",
    ),
    ("documental, no anexos -> human in both", _inputs("documental", True, False, False), "ANALISE_HUMANA"),
    (
        "desconhecida but item NOT conforme -> human in both",
        _inputs("desconhecida", False, False, True),
        "ANALISE_HUMANA",
    ),
)


@pytest.mark.parametrize(
    ("label", "values", "expected"), _INALTERADOS, ids=[row[0][:60] for row in _INALTERADOS]
)
def test_the_divergence_corpus_is_not_all_of_the_table(
    label: str, values: dict[str, Any], expected: str
) -> None:
    assert evaluate(_live(), values).saidas["roteamento"] == expected, label
    assert evaluate(_candidate(), values).saidas["roteamento"] == expected, label


# =================================================================================================
# 3. THE EQUIVALENCE STATEMENT, PROVED BY EXHAUSTION
# =================================================================================================

_CATEGORIES = (
    "tecnica",
    "clinica",
    "documental",
    "administrativa",
    "valor",
    "desconhecida",
    "",
    "fraude_suspeita",
)
_POSITIVE_SET = frozenset({"documental", "administrativa", "valor"})
_HUMAN_ROUTED = frozenset({"tecnica", "clinica"})


def test_candidate_and_live_diverge_exactly_on_the_declared_subspace() -> None:
    """`equivalencia_provada` in the manifest, checked over the whole input space."""
    live, candidate = _live(), _candidate()
    diverged: list[dict[str, Any]] = []
    for tipo in ("", "material", "taxa"):
        for categoria in _CATEGORIES:
            for conforme in (True, False):
                for divergencia in (True, False):
                    for docs in (True, False):
                        values = _inputs(categoria, conforme, divergencia, docs, tipo=tipo)
                        live_out = evaluate(live, values).saidas["roteamento"]
                        cand_out = evaluate(candidate, values).saidas["roteamento"]
                        expected_divergence = (
                            categoria not in _POSITIVE_SET
                            and categoria not in _HUMAN_ROUTED
                            and conforme
                            and not divergencia
                            and docs
                        )
                        assert (live_out != cand_out) is expected_divergence, values
                        if live_out != cand_out:
                            assert (live_out, cand_out) == ("PAGAR", "ANALISE_HUMANA"), values
                            diverged.append(values)
    # The 3 grid categories outside the declared domain-minus-{tecnica,clinica} x 3 tipo_item values.
    assert len(diverged) == 9


def test_reordering_alone_would_not_have_closed_the_hole_achado_1() -> None:
    """ACHADO-1, proved rather than asserted: with the LIVE negative set, moving `r_pagar` below
    the three specific rules still emits PAGAR for `desconhecida`."""
    live = _live()
    by_id = {rule.rule_id: rule for rule in live.rules}
    reordered = DecisionTable(
        decision_id=live.decision_id,
        source=live.source,
        input_names=live.input_names,
        output_names=live.output_names,
        rules=(
            by_id["r_tecnica_humano"],
            by_id["r_documental_humano"],
            by_id["r_valor_humano"],
            by_id["r_pagar"],
            by_id["r_catchall"],
        ),
    )
    values = _inputs("desconhecida", True, False, True)
    assert evaluate(reordered, values).saidas["roteamento"] == "PAGAR"
    assert evaluate(_candidate(), values).saidas["roteamento"] == "ANALISE_HUMANA"


# =================================================================================================
# 4. THE L0 HARD INVARIANT IS UNTOUCHED
# =================================================================================================


def test_candidate_emits_only_the_two_declared_routing_values_and_never_gloses() -> None:
    """ADR-0018 part 2, now trivially checkable: the domain contains NO adverse value at all.

    Under the payer perspective the adverse act IS `GLOSAR`, so it cannot be a DMN output. The
    live domain shrank from three values to two, and the candidate emits a subset of those."""
    live, candidate = _live(), _candidate()
    live_domain = {rule.outputs[0] for rule in live.rules}
    cand_domain = {rule.outputs[0] for rule in candidate.rules}
    assert live_domain == {"PAGAR", "ANALISE_HUMANA"}
    assert cand_domain <= live_domain
    text = _MANIFEST.read_text(encoding="utf-8").upper()
    for forbidden in ("GLOSAR", "PAGAR_PARCIAL", "ACEITAR", "CONFIRMAR"):
        assert f'"{forbidden}"' not in text
        assert f"ROTEAMENTO: {forbidden}" not in text


# =================================================================================================
# 5. THE ACTIVATION PATH IS REAL
# =================================================================================================


def test_candidate_manifest_is_invisible_to_artifact_deployment() -> None:
    artifacts = {path.resolve() for path in collect_artifacts(resolve_spec_processes_dir())}
    assert _MANIFEST.resolve() not in artifacts
    assert _LIVE.resolve() in artifacts
