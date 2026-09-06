"""Structural fence for the SP-OP-PAGTO-001 payment-amount guard (B-1 — money, fail-OPEN).

These are SPEC-level assertions, not worker-behaviour pins: they read the REAL `spec/**`
artifacts and derive, from the model itself, the two decisions the fix rests on. Sibling of
`test_auto_approval_criteria_fence.py` and `test_dentro_teto_source.py` — same posture: prove the
property against the real artifacts, never a fixture copy of them.

The defect: `route_aprovacao` read the amount as `variables.get("valor_pagamento_cents", 0)`, so
an ABSENT amount became 0, the ceiling admitted it, and `pagto_alcada`'s `r_dentro_teto_l2` row
routed the order to the auto-release band — `ST_ReleaseLowValue` paid for an order that never
carried an amount. Negative amounts took the same path; a `str` crashed the ceiling comparison.

The two model-derived decisions this file fences:

  1. **Fail NEUTRALLY, never raise.** `ST_CalculateFacts` declares NO error boundary event, so per
     ADR-0030 a `bpmnError` from this task would silently end the scope and a plain raise would
     become an incident that STALLS the order instead of routing it to a human.
  2. **The conservative route is the DMN's OWN catch-all, not an invented one.** Forcing
     `dentro_teto_l2=False` plus the conservative amount token lands `pagto_alcada` on
     `r_catchall` -> `ANALISE_HUMANA` / `comite-financeiro` / `tier_minimo=4`. That is DERIVED
     here by replaying the table's FIRST hit policy over its real rules, not asserted by hand.
"""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from maezo.tools.workers import pagto
from maezo.tools.workers.pagto import _VALOR_CENTS_ROTA_CONSERVADORA

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SPEC = _REPO_ROOT / "spec" / "processes"
_PAGTO_BPMN = _SPEC / "bpmn" / "SP-OP-PAGTO-001_Pagamentos_Alcada.bpmn"
_ALCADA_DMN = _SPEC / "dmn" / "pagto_alcada.dmn"

_BPMN_NS = {"bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
_FACTS_TASK = "ST_CalculateFacts"
_FACTS_TOPIC = "operadora.pagto.calculate_facts"
_AUTO_BAND = "DENTRO_TETO_L2"


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


@pytest.fixture(scope="module")
def bpmn_process() -> ET.Element:
    process = ET.parse(_PAGTO_BPMN).getroot().find("bpmn:process", _BPMN_NS)
    assert process is not None
    return process


@pytest.fixture(scope="module")
def alcada_table() -> ET.Element:
    root = ET.parse(_ALCADA_DMN).getroot()
    return next(el for el in root.iter() if _local(el.tag) == "decisionTable")


def _rules(table: ET.Element) -> list[ET.Element]:
    return [el for el in table if _local(el.tag) == "rule"]


def _entry_texts(rule: ET.Element, kind: str) -> list[str]:
    out: list[str] = []
    for el in rule:
        if _local(el.tag) != kind:
            continue
        text = next((c.text for c in el if _local(c.tag) == "text"), None)
        out.append((text or "").strip())
    return out


# ---------------------------------------------------------------------------------------------
# 1. The route_aprovacao task has NO error boundary -> the worker must fail NEUTRALLY (ADR-0030)
# ---------------------------------------------------------------------------------------------


def test_calculate_facts_is_the_external_task_route_aprovacao_serves(bpmn_process: ET.Element) -> None:
    """Anchors the rest of this file to the right task: `ST_CalculateFacts` on the worker's topic."""
    task = next(el for el in bpmn_process if _local(el.tag) == "serviceTask" and el.get("id") == _FACTS_TASK)
    camunda_topic = f"{{{'http://camunda.org/schema/1.0/bpmn'}}}topic"
    assert task.get(camunda_topic) == _FACTS_TOPIC


def test_calculate_facts_declares_no_error_boundary_event(bpmn_process: ET.Element) -> None:
    """THE reason an invalid amount is not raised on.

    A `WorkerBpmnError` whose code has no modeled catch ends the enclosing scope SILENTLY on CIB
    Seven 2.1.0 (ADR-0030), and a plain raise becomes `failure(retries=0)` -> an incident that
    STALLS the payment order rather than routing it to a human. So the guard returns. This
    asserts the premise; `test_route_aprovacao_never_raises_on_an_invalid_amount` asserts the
    conclusion.

    The file's only boundary events are `BE_PagtoOrdemInvalida` (on `ST_ValidatePaymentData`) and
    the two SLA boundaries on `UT_AprovacaoAlcada` — none of them attached here.
    """
    attached = [
        el.get("id")
        for el in bpmn_process
        if _local(el.tag) == "boundaryEvent" and el.get("attachedToRef") == _FACTS_TASK
    ]
    assert attached == [], (
        f"{_FACTS_TASK} gained a boundary event — re-decide whether the amount guard should now "
        f"raise a modeled bpmnError instead of routing conservatively: {attached}"
    )


def test_route_aprovacao_never_raises_on_an_invalid_amount() -> None:
    """The conclusion: no `raise` reachable from the amount guard, proven on the source AST.

    A behavioural pass is not enough — this asserts the guard branch contains no raise statement
    at all, so a future edit cannot reintroduce one against the boundary-less model.
    """
    source = Path(pagto.__file__).read_text(encoding="utf-8")
    func = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "route_aprovacao"
    )
    raises = [node for node in ast.walk(func) if isinstance(node, ast.Raise)]
    assert raises == [], f"route_aprovacao raises at line(s) {[n.lineno for n in raises]}"

    helper = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_valor_pagamento_cents_or_none"
    )
    helper_raises = [node for node in ast.walk(helper) if isinstance(node, ast.Raise)]
    assert helper_raises == [], (
        "the amount guard raises — `ST_CalculateFacts` has no error boundary (ADR-0030); it must "
        f"return a rejection instead. Line(s): {[n.lineno for n in helper_raises]}"
    )


# ---------------------------------------------------------------------------------------------
# 2. The conservative route is DERIVED from pagto_alcada's own rules
# ---------------------------------------------------------------------------------------------


def test_alcada_table_uses_first_hit_policy(alcada_table: ET.Element) -> None:
    """The derivation below replays FIRST-hit semantics — assert that is really the table's policy."""
    assert alcada_table.get("hitPolicy") == "FIRST"


def test_money_input_is_long_typed(alcada_table: ET.Element) -> None:
    """`valor_pagamento_cents` is `long` (ADR-0018 parte 2) — which is why the guard accepts only
    `int`, and why a rejected `str`/`float` cannot simply be forwarded to the engine."""
    expr = next(
        el
        for el in alcada_table.iter()
        if _local(el.tag) == "inputExpression"
        and (el.findtext("{*}text") or "").strip() == "valor_pagamento_cents"
    )
    assert expr.get("typeRef") == "long"


def test_the_auto_release_band_structurally_requires_dentro_teto_l2_true(alcada_table: ET.Element) -> None:
    """`dentro_teto_l2=False` alone closes the auto-release band — the load-bearing guarantee.

    Whatever amount the worker forwards, no rule can produce DENTRO_TETO_L2 unless the boolean is
    `true`. That is what makes the guard's `dentro_teto = False` sufficient rather than merely
    convenient.
    """
    auto_rules = [r for r in _rules(alcada_table) if _AUTO_BAND in _entry_texts(r, "outputEntry")[0]]
    assert len(auto_rules) == 1, "more than one rule produces the auto-release band"
    assert _entry_texts(auto_rules[0], "inputEntry")[1] == "true"


def _matches(entry: str, value: int) -> bool:
    """Minimal FEEL matcher for exactly the unary-test forms `pagto_alcada` uses.

    Deliberately narrow: an unrecognised form RAISES, so a finance edit that introduces a new
    operator FAILS this fence instead of silently passing under a guessed interpretation.
    """
    entry = entry.strip()
    if entry == "-":
        return True
    if entry.startswith("<="):
        return value <= int(entry[2:].strip())
    if entry.startswith("(") and entry.endswith("]") and ".." in entry:
        low, _, high = entry[1:-1].partition("..")
        return int(low) < value <= int(high)
    raise AssertionError(f"unsupported unary test {entry!r} — extend this matcher deliberately")


def _matches_boolean(entry: str, value: bool) -> bool:
    """Minimal FEEL matcher for the unary-test forms `pagto_alcada`'s boolean column uses.

    MINOR-1: mirrors `_matches` above — deliberately narrow, an unrecognised form RAISES instead of
    silently `continue`-ing past it. Pre-fix, the caller's `teto_entry != "-" and teto_entry !=
    "false"` check treated ANY other literal (e.g. a future finance edit adding `not(true)`) as
    "does not match" via a bare `continue` — masking the fact that the matcher never actually
    understood the new form. A future rule using an unrecognised boolean unary test now FAILS this
    fence loudly instead of being silently skipped.
    """
    entry = entry.strip()
    if entry == "-":
        return True
    if entry in ("true", "false"):
        return (entry == "true") == value
    raise AssertionError(f"unsupported boolean unary test {entry!r} — extend this matcher deliberately")


def _first_hit(table: ET.Element, *, valor: int, dentro_teto: bool) -> ET.Element:
    """Replay FIRST-hit policy over the REAL rules for one (valor, dentro_teto_l2) input pair.

    Shared by the sweep in `test_no_rule_can_yield_dentro_teto_l2_when_the_gate_is_pinned_false`
    below — the same replay `test_conservative_amount_token_lands_on_the_tables_own_catch_all` does
    for the ONE token this worker actually forwards, generalised to an arbitrary valor.
    """
    for rule in _rules(table):
        valor_entry, teto_entry, _tipo_entry = _entry_texts(rule, "inputEntry")
        if not _matches(valor_entry, valor):
            continue
        if not _matches_boolean(teto_entry, dentro_teto):
            continue
        return rule
    raise AssertionError(
        f"no rule matched valor={valor} dentro_teto_l2={dentro_teto} — table lost its catch-all"
    )


def test_conservative_amount_token_lands_on_the_tables_own_catch_all(alcada_table: ET.Element) -> None:
    """DERIVES the conservative route by replaying FIRST-hit over the real rules.

    Inputs are exactly what the worker sends for a rejected amount:
    `valor_pagamento_cents=_VALOR_CENTS_ROTA_CONSERVADORA`, `dentro_teto_l2=False`. The row that
    wins must be the table's conservative catch-all — `ANALISE_HUMANA` / `comite-financeiro` /
    tier 4 — the highest human band. Single-sourced from the worker constant, so drift in either
    direction fails here.
    """
    winner = None
    for rule in _rules(alcada_table):
        valor_entry, teto_entry, tipo_entry = _entry_texts(rule, "inputEntry")
        if not _matches(valor_entry, _VALOR_CENTS_ROTA_CONSERVADORA):
            continue
        if not _matches_boolean(teto_entry, False):
            continue  # the boolean gate: MINOR-1 — RAISES on an unrecognised form, never skips it
        assert tipo_entry == "-", "tipo_pagamento became discriminating — re-derive this route"
        winner = rule
        break

    assert winner is not None, "no rule matches the conservative token — the table lost its catch-all"
    assert winner.get("id") == "r_catchall"
    faixa, grupo, tier, _motivo = _entry_texts(winner, "outputEntry")
    assert faixa == '"ANALISE_HUMANA"'
    assert grupo == '"comite-financeiro"'
    assert tier == "4", "the conservative route must demand the HIGHEST approver tier"


def test_catch_all_is_the_last_rule_and_matches_everything(alcada_table: ET.Element) -> None:
    """FIRST-hit only makes the catch-all a catch-all if it is LAST and unconditional."""
    rules = _rules(alcada_table)
    assert rules[-1].get("id") == "r_catchall"
    assert _entry_texts(rules[-1], "inputEntry") == ["-", "-", "-"]


def test_no_rule_can_release_a_payment(alcada_table: ET.Element) -> None:
    """L1 invariant (ADR-0008/0018) restated as a fence: `faixa_valor` never authorises payment.

    The conservative route is only safe because NO row of this table releases — the auto band is
    a routing label consumed by `ST_ReleaseLowValue`, and every other band routes to a human.
    """
    forbidden = ("LIBERAR", "AUTORIZAR", "PAGAR")
    for rule in _rules(alcada_table):
        faixa = _entry_texts(rule, "outputEntry")[0].upper()
        assert not any(token in faixa for token in forbidden), (rule.get("id"), faixa)


# ---------------------------------------------------------------------------------------------
# 3. GK-recommended fence (MAJOR-1): the END-TO-END property the whole process depends on — not
#    just the ONE token this worker forwards, but that `dentro_teto_l2=False` closes DENTRO_TETO_L2
#    for ANY valor, because `BRT_AlcadaRouting` (bpmn:180-205) re-evaluates this SAME table
#    natively from the raw, unechoed `valor_pagamento_cents` (see pagto.py `route_aprovacao`'s
#    "THE ENGINE-SIDE GUARANTEE" docstring paragraph).
# ---------------------------------------------------------------------------------------------


def test_no_rule_can_yield_dentro_teto_l2_when_the_boolean_gate_is_pinned_false(
    alcada_table: ET.Element,
) -> None:
    """Structural half: a rule producing DENTRO_TETO_L2 can NEVER match with `dentro_teto_l2=False`.

    This is valor-INDEPENDENT: a rule's boolean-column unary test alone decides whether it can ever
    fire once `dentro_teto_l2` is pinned False, regardless of what `valor_pagamento_cents` is. Since
    `test_the_auto_release_band_structurally_requires_dentro_teto_l2_true` already proves exactly
    ONE rule produces DENTRO_TETO_L2, and `_matches_boolean` (MINOR-1) RAISES rather than silently
    skips an unrecognised form, this replays EVERY rule's boolean gate for real instead of trusting
    that single rule count to stay true forever.
    """
    for rule in _rules(alcada_table):
        faixa = _entry_texts(rule, "outputEntry")[0]
        if faixa != f'"{_AUTO_BAND}"':
            continue
        _valor_entry, teto_entry, _tipo_entry = _entry_texts(rule, "inputEntry")
        assert not _matches_boolean(teto_entry, False), (
            rule.get("id"),
            "a DENTRO_TETO_L2 rule can still fire with dentro_teto_l2=False",
        )


def test_no_rule_can_yield_dentro_teto_l2_across_the_full_value_domain_when_pinned_false(
    alcada_table: ET.Element,
) -> None:
    """End-to-end replay (GK-recommended fence, MAJOR-1): sweeps the value domain, not just the ONE
    conservative token this worker forwards, and asserts the FIRST-hit winner is never DENTRO_TETO_L2.

    `test_conservative_amount_token_lands_on_the_tables_own_catch_all` only proves this for
    `_VALOR_CENTS_ROTA_CONSERVADORA` (0) — the single value `route_aprovacao` actually sends for a
    REJECTED amount. But `BRT_AlcadaRouting` re-evaluates from the engine's RAW, unechoed amount,
    which can be ANY int (a legitimately ACCEPTED valor with `dentro_teto_l2` independently False —
    e.g. a config problem on the resolver side — reaches this same table too). This sweep covers:
    negative, zero, every ALCADA boundary and boundary+1, and a value far beyond the highest
    configured tier — proving the property the whole process depends on, not one input vector.
    """
    sweep = [
        -10_000_000,
        -1,
        0,
        1,
        10_000_000,  # DENTRO_TETO_L2's own upper boundary — WOULD win it if dentro_teto_l2 were true
        10_000_001,
        50_000_000,
        50_000_001,
        200_000_000,
        200_000_001,
        1_000_000_000,
        1_000_000_001,
        5_000_000_000,
        2**63 - 1,
    ]
    for valor in sweep:
        winner = _first_hit(alcada_table, valor=valor, dentro_teto=False)
        faixa = _entry_texts(winner, "outputEntry")[0]
        assert faixa != f'"{_AUTO_BAND}"', (valor, winner.get("id"), faixa)
