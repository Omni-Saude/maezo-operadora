"""End-to-end JOURNEY eval for Andre (gap 11.5, WP-EVALS) -- extends the pattern
`test_helena_journey_evals.py` establishes to a non-chat, A2A-convoked dossier agent.

Finding this closes: the residual half of `docs/audits/maezo-deep-audit/reports/
domain-11-product-fit.md:32` -- only Helena had an end-to-end journey eval; the other nine
agents still had only classification/dossier-shaped evals (register gap 11.5, WAVE0-RECON
round-5 PARTIAL note).

Andre's `pagto_dossier` flow (`docs/processes/contracts/SP-OP-PAGTO-001.md`,
`src/maezo/agents/andre/graph.py`) is:

    receive -> gather -> assess(DMN) -> {auto_route | human_review} -> start_process -> finalize

This module asserts an OBSERVABLE OUTPUT at each of three stages for the `auto_route` (clean,
low-value, worker-pre-resolved `dentro_teto_l2=true`) path -- the only fully-automatic outcome
this agent has (NEVER a payment release; only a "clerical dossier ready" routing fact):

    1. intake    (`receive`)              -- the turn started cleanly, no runtime-context error.
    2. triage    (`gather` + `assess`)    -- the DMN chain `pagto_admissibility` ->
                                              `pagto_alcada` actually ran (both keys land in
                                              `dmn_refs`, ADR-0007/0028 traceability) AND their
                                              routing fact (`faixa_valor=DENTRO_TETO_L2`,
                                              `route=auto_route`) reached the state.
    3. hand-off  (`start_process` +        -- SP-OP-PAGTO-001 actually started (idempotently,
       `finalize`)                           queryable by business key) with the contract's
                                              engine-bound variables carrying the bounded routing
                                              tokens (`andre_route`, `faixa_valor`,
                                              `dentro_teto_l2`) -- the observable END of the
                                              journey, never just an in-memory `route` label.

Same mechanism as every other eval (`run_case` against the real `build()` contract, one golden
JSON under `golden/andre/`) -- a new ASSERTION SHAPE, not a new harness path. One `run_case`/
`ainvoke` call covers all three stages because `AndreGraph`'s compiled `StateGraph` merges every
node's return dict into one cumulative state (plain dict-update reducer): `start_process`'s
return never clears the keys `assess`/`auto_route` set, so the FINAL `RunResult.state` already
carries a checkpoint from every stage the turn passed through.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.andre.graph import PROCESS_KEY_PAGTO, build

from ._harness import assert_expect, assert_no_leak, load_golden, run_case, run_mutation_check

ANDRE_JOURNEY_CASES = [c for c in load_golden("andre") if c["id"].startswith("EVL-ANDRE-JOURNEY-")]


def _mutate_faixa_valor(case: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy `case` with `expect.fields.faixa_valor` flipped to a DIFFERENT valid faixa.

    Non-vacuousness proof for the SF half of this golden -- proves `assert_expect` reads the
    graph's REAL DMN-derived output rather than trivially agreeing with the golden."""
    mutated = copy.deepcopy(dict(case))
    fields = dict(mutated["expect"]["fields"])
    fields["faixa_valor"] = "ALCADA_L1" if fields.get("faixa_valor") != "ALCADA_L1" else "ALCADA_L2"
    mutated["expect"] = {**mutated["expect"], "fields": fields}
    return mutated


@pytest.mark.eval
@pytest.mark.parametrize("case", ANDRE_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_andre_journey_eval_intake_triage_handoff(case: dict[str, Any]) -> None:
    """Drive ONE dossier turn through the full compiled graph and assert an observable
    checkpoint at each of the three journey stages (module docstring)."""
    result = await run_case(build, case)
    journey = case["journey"]

    # The turn-level SF/ABS criterion still holds -- this IS also an ordinary Tier-A eval.
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
            f"triage stage: DMN table {table!r} never reached `dmn_refs` (traceability gap) -- "
            f"got dmn_refs={dmn_refs!r}"
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
    assert handoff["process_key"] == PROCESS_KEY_PAGTO
    status = await result.cibseven.get_process_status(business_key)
    for var_name, expected in handoff.get("engine_variables", {}).items():
        assert status.variables.get(var_name) == expected, (
            f"hand-off engine variable mismatch: {var_name}={status.variables.get(var_name)!r}, "
            f"expected {expected!r}"
        )


@pytest.mark.eval
@pytest.mark.parametrize("case", ANDRE_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_andre_journey_faixa_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the SF half of the journey golden: flipping
    `expect.fields.faixa_valor` must make the harness fail."""
    await run_mutation_check(build, case, mutation=_mutate_faixa_valor)


@pytest.mark.eval
async def test_evl_andre_journey_01_mutation_check_handoff_stage_is_non_vacuous() -> None:
    """Corrupting the journey's OWN hand-off-stage expectation (`engine_variables.andre_route`)
    must make THIS MODULE's stage-by-stage assertion fail -- proving the per-stage checks this
    eval adds (beyond the shared SF/ABS criterion, already proven non-vacuous above) are
    actually exercised, not vacuously green."""
    case = next(c for c in ANDRE_JOURNEY_CASES if c["id"] == "EVL-ANDRE-JOURNEY-01")
    mutated = copy.deepcopy(case)
    mutated["journey"]["handoff"]["engine_variables"]["andre_route"] = "human_review"
    with pytest.raises(AssertionError, match="hand-off engine variable mismatch"):
        await test_andre_journey_eval_intake_triage_handoff(mutated)
