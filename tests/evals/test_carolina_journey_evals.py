"""End-to-end JOURNEY eval for Carolina (gap 11.5, WP-EVALS) -- extends the pattern
`test_helena_journey_evals.py` establishes to Carolina's `credenciamento`/`descredenciamento`
agent.

Carolina's graph (`src/maezo/agents/carolina/graph.py`) is:

    receive -> gather -> assess(DMN) -> {auto_route | human_review} -> start_process -> finalize

This module asserts an observable checkpoint at each of three stages for the `auto_route` path
(a new-provider credentialing request with complete documentation -- the ONLY fully-automatic
outcome this agent has; NEVER a de-credentialing or a credentialing denial):

    1. intake   (`receive`)            -- the turn started cleanly, no runtime-context error.
    2. triage   (`gather` + `assess`)  -- `cred_admissibility` actually ran (its key lands in
                                          `dmn_refs`) AND its routing fact
                                          (`admissibilidade=CLERICAL_CREDENCIAR`,
                                          `route=auto_route`) reached the state.
    3. hand-off (`start_process` +      -- SP-OP-CRED-001 actually started (idempotently,
       `finalize`)                        queryable by business key) with the contract's
                                          engine-bound variables carrying the bounded routing
                                          tokens (`carolina_route`, `direcao`,
                                          `documentacao_completa`).

Same mechanism as every other eval (`run_case` against the real `build()` contract) -- a new
ASSERTION SHAPE, not a new harness path.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.carolina.graph import PROCESS_KEY, build

from ._harness import assert_expect, assert_no_leak, load_golden, run_case, run_mutation_check

CAROLINA_JOURNEY_CASES = [c for c in load_golden("carolina") if c["id"].startswith("EVL-CAROLINA-JOURNEY-")]


def _mutate_admissibilidade(case: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy `case` with `expect.fields.admissibilidade` flipped to a DIFFERENT valid value.

    Non-vacuousness proof for the SF half of this golden."""
    mutated = copy.deepcopy(dict(case))
    fields = dict(mutated["expect"]["fields"])
    fields["admissibilidade"] = "PENDENTE_DOCUMENTACAO"
    mutated["expect"] = {**mutated["expect"], "fields": fields}
    return mutated


@pytest.mark.eval
@pytest.mark.parametrize("case", CAROLINA_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_carolina_journey_eval_intake_triage_handoff(case: dict[str, Any]) -> None:
    """Drive ONE credentialing turn through the full compiled graph and assert an observable
    checkpoint at each of the three journey stages (module docstring)."""
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

    # --- Stage 3: hand-off (`start_process` + `finalize`) -------------------------------------
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


@pytest.mark.eval
@pytest.mark.parametrize("case", CAROLINA_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_carolina_journey_admissibilidade_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the SF half of the journey golden."""
    await run_mutation_check(build, case, mutation=_mutate_admissibilidade)


@pytest.mark.eval
async def test_evl_carolina_journey_01_mutation_check_handoff_stage_is_non_vacuous() -> None:
    """Corrupting the journey's OWN hand-off-stage expectation (`engine_variables.
    carolina_route`) must make THIS MODULE's stage-by-stage assertion fail."""
    case = next(c for c in CAROLINA_JOURNEY_CASES if c["id"] == "EVL-CAROLINA-JOURNEY-01")
    mutated = copy.deepcopy(case)
    mutated["journey"]["handoff"]["engine_variables"]["carolina_route"] = "human_review"
    with pytest.raises(AssertionError, match="hand-off engine variable mismatch"):
        await test_carolina_journey_eval_intake_triage_handoff(mutated)
