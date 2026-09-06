"""End-to-end JOURNEY eval for Rafael (gap 11.5, WP-EVALS) -- extends the pattern
`test_helena_journey_evals.py` establishes to Rafael's `human_auditor` prior-authorization
journey.

Rafael's graph (`src/maezo/agents/rafael/graph.py`) is:

    receive -> gather -> assess -> {auto_approve | human_auditor} -> start_process -> complete

BOTH branches feed `start_process` unconditionally (graph topology) -- Rafael always anchors
SP-OP-AUTH-001, whichever branch decided the routing fact. This golden drives `human_auditor`
(NOT `auto_approve`): RAF-01/RAF-06 (`README.md`'s `dmn_fixture` notes, `test_harness_rule_
fixtures.py`) already proved `auto_approve`'s real DMN criteria (the five `auth_auto_approval`
booleans) are unreachable from any input this agent's typed seam actually supplies -- reusing a
STATIC `AUTO_APROVAR` fixture here would reintroduce exactly that vacuity for a routing claim.
`human_auditor` is the honestly-reachable, always-available fail-safe path (module docstring:
"Rafael NEVER denies coverage and NEVER makes the coverage decision").

Three observable stages:

    1. intake   (`receive`)            -- the turn started cleanly, no runtime-context error.
    2. triage   (`gather` + `assess`)  -- the DMN chain `auth_admissibility` -> `auth_sla` ->
                                          `auth_auto_approval` actually ran (all three keys land
                                          in `dmn_refs`) AND the routing fact
                                          (`recomendacao_auto=ANALISE_HUMANA`,
                                          `route=human_auditor`) reached the state.
    3. hand-off (`start_process` +      -- SP-OP-AUTH-001 actually started (idempotently,
       `complete`)                        queryable by business key) with the contract's
                                          engine-bound variables carrying the bounded routing
                                          tokens (`rafael_route`, `motivo_encaminhamento`) --
                                          never the coverage decision itself
                                          (`decisao_cobertura` never appears in engine-bound
                                          variables -- it lives, always `None`, only inside the
                                          instructive dossier).

Same mechanism as every other eval (`run_case` against the real `build()` contract) -- a new
ASSERTION SHAPE, not a new harness path.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.rafael.graph import PROCESS_KEY, build

from ._harness import assert_expect, assert_no_leak, load_golden, run_case, run_mutation_check

RAFAEL_JOURNEY_CASES = [c for c in load_golden("rafael") if c["id"].startswith("EVL-RAFAEL-JOURNEY-")]


def _mutate_recomendacao_auto(case: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy `case` with `expect.fields.recomendacao_auto` flipped to a DIFFERENT valid
    value. Non-vacuousness proof for the SF half of this golden."""
    mutated = copy.deepcopy(dict(case))
    fields = dict(mutated["expect"]["fields"])
    fields["recomendacao_auto"] = "AUTO_APROVAR"
    mutated["expect"] = {**mutated["expect"], "fields": fields}
    return mutated


@pytest.mark.eval
@pytest.mark.parametrize("case", RAFAEL_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_rafael_journey_eval_intake_triage_handoff(case: dict[str, Any]) -> None:
    """Drive ONE prior-authorization turn through the full compiled graph and assert an
    observable checkpoint at each of the three journey stages (module docstring)."""
    result = await run_case(build, case)
    journey = case["journey"]

    assert_expect(result.state, case["expect"])
    assert_no_leak(result.state, case.get("leak_canaries") or [])

    # --- Stage 1: intake (`receive`) ----------------------------------------------------------
    intake = journey["intake"]
    if intake.get("no_error"):
        assert not result.state.get("error"), (
            f"intake stage recorded an error before assess ran: {result.state.get('error')!r}"
        )

    # --- Stage 2: triage (`gather` + `assess`) ------------------------------------------------
    triage = journey["triage"]
    for field_name, expected in triage.get("fields", {}).items():
        assert result.state.get(field_name) == expected, (
            f"triage stage mismatch: {field_name}={result.state.get(field_name)!r}, expected {expected!r}"
        )
    dmn_refs = result.state.get("dmn_refs") or {}
    for table in triage.get("dmn_tables_consulted", []):
        assert table in dmn_refs, (
            f"triage stage: DMN table {table!r} never reached `dmn_refs` -- got dmn_refs={dmn_refs!r}"
        )

    # --- Stage 3: hand-off (`start_process` + `complete`) -------------------------------------
    handoff = journey["handoff"]
    for field_name, expected in handoff.get("fields", {}).items():
        assert result.state.get(field_name) == expected, (
            f"hand-off stage mismatch: {field_name}={result.state.get(field_name)!r}, expected {expected!r}"
        )
    business_key = result.state.get("business_key")
    assert business_key == handoff["business_key"], (
        f"hand-off business_key mismatch: {business_key!r}, expected {handoff['business_key']!r}"
    )
    assert handoff["process_key"] == PROCESS_KEY
    status = await result.cibseven.get_process_status(business_key)
    for var_name, expected in handoff.get("engine_variables", {}).items():
        assert status.variables.get(var_name) == expected, (
            f"hand-off engine variable mismatch: {var_name}={status.variables.get(var_name)!r}, "
            f"expected {expected!r}"
        )
    # L0 hard invariant (module docstring): the coverage decision NEVER travels as an
    # engine-bound variable in its own right -- only inside the instructive dossier, always None.
    assert "decisao_cobertura" not in status.variables, (
        "hand-off stage: `decisao_cobertura` leaked into engine-bound variables directly -- "
        f"got variables={status.variables!r}"
    )


@pytest.mark.eval
@pytest.mark.parametrize("case", RAFAEL_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_rafael_journey_recomendacao_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the SF half of the journey golden."""
    await run_mutation_check(build, case, mutation=_mutate_recomendacao_auto)


@pytest.mark.eval
async def test_evl_rafael_journey_01_mutation_check_handoff_stage_is_non_vacuous() -> None:
    """Corrupting the journey's OWN hand-off-stage expectation (`engine_variables.rafael_route`)
    must make THIS MODULE's stage-by-stage assertion fail."""
    case = next(c for c in RAFAEL_JOURNEY_CASES if c["id"] == "EVL-RAFAEL-JOURNEY-01")
    mutated = copy.deepcopy(case)
    mutated["journey"]["handoff"]["engine_variables"]["rafael_route"] = "auto_approve"
    with pytest.raises(AssertionError, match="hand-off engine variable mismatch"):
        await test_rafael_journey_eval_intake_triage_handoff(mutated)
