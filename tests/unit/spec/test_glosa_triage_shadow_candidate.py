"""M-4 — `glosa_triage`: the permissive terminal that used to sit at position 1.

STATE SINCE DEC-14 (owner decision 2026-10-31, issue #1084, option (a) "rotear a humano"; R-062 /
F-033): the live table now IS the candidate — `r_pagar` sits at position 4 and gates
`categoria_normalizada` on the closed POSITIVE set `{documental, administrativa, valor}`. The tests
below pin that the live table routes every out-of-domain category to a human, that live and
candidate are equivalent over the whole grid, and that the PRE-DEC-14 table (reconstructed here
from the live rules with the old negative cell) still reproduces the defect — so a revert is caught.

Pre-DEC-14 the row 1 (`r_pagar`) gated `categoria_normalizada` on the
NEGATIVE set `not("tecnica","clinica")`, which admitted `"desconhecida"` — the value the upstream
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
    Rule,
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


#: The negative cell the live row carried before DEC-14 — used ONLY to rebuild the old table.
_PRE_DEC14_CELL = 'not("tecnica","clinica")'
_LIVE_ORDER = ("r_tecnica_humano", "r_documental_humano", "r_valor_humano", "r_pagar", "r_catchall")


def _pre_dec14() -> DecisionTable:
    """The table as it stood before DEC-14: `r_pagar` at row 1 with the NEGATIVE category set."""
    live = _live()
    by_id = {rule.rule_id: rule for rule in live.rules}
    col = live.input_names.index("categoria_normalizada")
    pagar = by_id["r_pagar"]
    old_inputs = tuple(_PRE_DEC14_CELL if i == col else v for i, v in enumerate(pagar.inputs))
    old_pagar = Rule(rule_id="r_pagar", inputs=old_inputs, outputs=pagar.outputs)
    return DecisionTable(
        decision_id=live.decision_id,
        source=live.source,
        input_names=live.input_names,
        output_names=live.output_names,
        rules=(
            old_pagar,
            by_id["r_tecnica_humano"],
            by_id["r_documental_humano"],
            by_id["r_valor_humano"],
            by_id["r_catchall"],
        ),
    )


def test_live_r_pagar_is_row_4_with_the_closed_positive_category_set_dec14() -> None:
    """DEC-14 applied: specific before permissive, and the permissive row names its categories."""
    live = _live()
    assert live.rule_ids() == _LIVE_ORDER
    pagar = live.rules[3]
    entry = pagar.inputs[live.input_names.index("categoria_normalizada")]
    assert "not(" not in entry, "a NEGATIVE set would re-admit `desconhecida` (M-4)"
    assert set(entry.replace('"', "").split(",")) == {"documental", "administrativa", "valor"}
    assert pagar.outputs[0] == "PAGAR"


def test_the_pre_dec14_reconstruction_still_reproduces_the_defect() -> None:
    """Non-vacuity of the rebuild: the old table really did adjudicate `desconhecida` as PAGAR."""
    verdict = evaluate(_pre_dec14(), _inputs("desconhecida", True, False, True))
    assert (verdict.saidas["roteamento"], verdict.regra) == ("PAGAR", "r_pagar")


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

#: (label, inputs, PRE-DEC-14 verdict, pre rule, LIVE == candidate verdict, live/candidate rule).
_DIVERGENCIAS: tuple[tuple[str, dict[str, Any], str, str, str, str], ...] = (
    (
        "MISSION REPRO — categoria 'desconhecida' with the three favourable facts closes the case",
        _inputs("desconhecida", True, False, True),
        "PAGAR",
        "r_pagar",
        "ANALISE_HUMANA",
        "catchall",
    ),
    (
        "same, with a non-empty tipo_item — the column is `-` in every rule, so it cannot matter",
        _inputs("desconhecida", True, False, True, tipo="material"),
        "PAGAR",
        "r_pagar",
        "ANALISE_HUMANA",
        "catchall",
    ),
    (
        "empty categoria — an upstream analysis that returns a blank instead of a category",
        _inputs("", True, False, True),
        "PAGAR",
        "r_pagar",
        "ANALISE_HUMANA",
        "catchall",
    ),
    (
        "a literal outside the declared domain entirely",
        _inputs("fraude_suspeita", True, False, True),
        "PAGAR",
        "r_pagar",
        "ANALISE_HUMANA",
        "catchall",
    ),
)


@pytest.mark.parametrize(
    ("label", "values", "pre_expected", "pre_rule", "now_expected", "now_rule"),
    _DIVERGENCIAS,
    ids=[row[0][:60] for row in _DIVERGENCIAS],
)
def test_live_now_routes_to_a_human_where_the_pre_dec14_table_closed_the_case(
    label: str,
    values: dict[str, Any],
    pre_expected: str,
    pre_rule: str,
    now_expected: str,
    now_rule: str,
) -> None:
    """M-4 closed (DEC-14): every input the old table adjudicated PAGAR now reaches the catch-all in
    the LIVE table, exactly as in the candidate."""
    pre_verdict = evaluate(_pre_dec14(), values)
    live_verdict = evaluate(_live(), values)
    cand_verdict = evaluate(_candidate(), values)

    assert (pre_verdict.saidas["roteamento"], pre_verdict.regra) == (pre_expected, pre_rule), label
    assert (live_verdict.saidas["roteamento"], live_verdict.regra) == (now_expected, f"r_{now_rule}"), label
    assert (cand_verdict.saidas["roteamento"], cand_verdict.regra) == (now_expected, f"c_{now_rule}"), label


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
    assert evaluate(_pre_dec14(), values).saidas["roteamento"] == expected, label
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


def test_live_and_candidate_agree_everywhere_and_differ_from_pre_dec14_exactly_on_m4() -> None:
    """`equivalencia_provada` in the manifest, checked over the whole input space: since DEC-14 the
    live table and the candidate emit the same `roteamento` on EVERY input, and both differ from
    the pre-DEC-14 table exactly on the M-4 subspace (PAGAR -> ANALISE_HUMANA, never the reverse)."""
    pre, live, candidate = _pre_dec14(), _live(), _candidate()
    diverged: list[dict[str, Any]] = []
    for tipo in ("", "material", "taxa"):
        for categoria in _CATEGORIES:
            for conforme in (True, False):
                for divergencia in (True, False):
                    for docs in (True, False):
                        values = _inputs(categoria, conforme, divergencia, docs, tipo=tipo)
                        pre_out = evaluate(pre, values).saidas["roteamento"]
                        live_out = evaluate(live, values).saidas["roteamento"]
                        cand_out = evaluate(candidate, values).saidas["roteamento"]
                        assert live_out == cand_out, values
                        expected_divergence = (
                            categoria not in _POSITIVE_SET
                            and categoria not in _HUMAN_ROUTED
                            and conforme
                            and not divergencia
                            and docs
                        )
                        assert (pre_out != live_out) is expected_divergence, values
                        if pre_out != live_out:
                            assert (pre_out, live_out) == ("PAGAR", "ANALISE_HUMANA"), values
                            diverged.append(values)
    # The 3 grid categories outside the declared domain-minus-{tecnica,clinica} x 3 tipo_item values.
    assert len(diverged) == 9


def test_live_never_pays_a_category_outside_the_positive_set() -> None:
    """The DEC-14 property stated directly on the live table: PAGAR only for the closed set."""
    live = _live()
    for categoria in _CATEGORIES:
        out = evaluate(live, _inputs(categoria, True, False, True)).saidas["roteamento"]
        assert (out == "PAGAR") is (categoria in _POSITIVE_SET), categoria


def test_reordering_alone_would_not_have_closed_the_hole_achado_1() -> None:
    """ACHADO-1, proved rather than asserted: with the OLD negative set, moving `r_pagar` below
    the three specific rules still emits PAGAR for `desconhecida` — the closed positive set is what
    fixed M-4, the reorder is defence in depth."""
    pre = _pre_dec14()
    by_id = {rule.rule_id: rule for rule in pre.rules}
    reordered = DecisionTable(
        decision_id=pre.decision_id,
        source=pre.source,
        input_names=pre.input_names,
        output_names=pre.output_names,
        rules=tuple(by_id[rule_id] for rule_id in _LIVE_ORDER),
    )
    values = _inputs("desconhecida", True, False, True)
    assert evaluate(reordered, values).saidas["roteamento"] == "PAGAR"
    assert evaluate(_live(), values).saidas["roteamento"] == "ANALISE_HUMANA"
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
