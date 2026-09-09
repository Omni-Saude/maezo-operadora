"""End-to-end JOURNEY eval for Gustavo (gap 11.5, WP-EVALS) -- extends the pattern
`test_helena_journey_evals.py` establishes to Gustavo's J2 (NIP instruction) flow.

Gustavo's graph (`src/maezo/agents/gustavo/graph.py`) is:

    receive -> gather -> assess -> {review_submission | instruct_nip} -> start_process -> finalize

A distinguishing feature this module exploits: `receive` itself computes the flow's
`process_key`/`business_key` (module docstring: "the ONLY route ... ALWAYS a human") BEFORE any
DMN runs -- so the intake stage already carries an observable checkpoint the other eight agents
only produce at hand-off time.

Three observable stages:

    1. intake  (`receive`)             -- the turn started cleanly AND the flow's
                                          `process_key`/`business_key` were derived up front.
    2. triage  (`gather` + `assess`)   -- the DMN chain `nip_classification` -> `nip_sla` ->
                                          `nip_routing` actually ran (all three keys land in
                                          `dmn_refs`) AND their routing facts reached the state.
    3. hand-off (`instruct_nip` +      -- SP-OP-NIP-001 actually started (idempotently,
       `start_process`)                  queryable by business key) with the contract's
                                          engine-bound variables carrying the bounded routing
                                          tokens AND the ALWAYS-`None` `decisao_nip` L0 guardrail
                                          (MANTER_NEGATIVA is exclusively `UT_RevisaoJuridicaNip`'s).

Same mechanism as every other eval (`run_case` against the real `build()` contract) -- a new
ASSERTION SHAPE, not a new harness path.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.gustavo.graph import build

from ._harness import assert_expect, assert_no_leak, load_golden, run_case, run_mutation_check

GUSTAVO_JOURNEY_CASES = [c for c in load_golden("gustavo") if c["id"].startswith("EVL-GUSTAVO-JOURNEY-")]


def _mutate_roteamento_nip(case: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy `case` with `expect.fields.roteamento_nip` flipped to a DIFFERENT valid value.

    Non-vacuousness proof for the SF half of this golden."""
    mutated = copy.deepcopy(dict(case))
    fields = dict(mutated["expect"]["fields"])
    fields["roteamento_nip"] = "ELABORAR_RESPOSTA"
    mutated["expect"] = {**mutated["expect"], "fields": fields}
    return mutated


@pytest.mark.eval
@pytest.mark.parametrize("case", GUSTAVO_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_gustavo_journey_eval_intake_triage_handoff(case: dict[str, Any]) -> None:
    """Drive ONE NIP-instruction turn through the full compiled graph and assert an observable
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
    for field_name, expected in intake.get("fields", {}).items():
        assert result.state.get(field_name) == expected, (
            f"intake stage mismatch: {field_name}={result.state.get(field_name)!r}, expected {expected!r}"
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

    # --- Stage 3: hand-off (`instruct_nip` + `start_process`) ---------------------------------
    handoff = journey["handoff"]
    for field_name, expected in handoff.get("fields", {}).items():
        assert result.state.get(field_name) == expected, (
            f"hand-off stage mismatch: {field_name}={result.state.get(field_name)!r}, expected {expected!r}"
        )
    business_key = result.state.get("business_key")
    assert business_key == handoff["business_key"], (
        f"hand-off business_key mismatch: {business_key!r}, expected {handoff['business_key']!r}"
    )
    assert result.state.get("process_key") == handoff["process_key"]
    status = await result.cibseven.get_process_status(business_key)
    for var_name, expected in handoff.get("engine_variables", {}).items():
        assert status.variables.get(var_name) == expected, (
            f"hand-off engine variable mismatch: {var_name}={status.variables.get(var_name)!r}, "
            f"expected {expected!r}"
        )


@pytest.mark.eval
@pytest.mark.parametrize("case", GUSTAVO_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_gustavo_journey_roteamento_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the SF half of the journey golden."""
    await run_mutation_check(build, case, mutation=_mutate_roteamento_nip)


@pytest.mark.eval
async def test_evl_gustavo_journey_01_mutation_check_handoff_stage_is_non_vacuous() -> None:
    """Corrupting the journey's OWN hand-off-stage expectation (`engine_variables.decisao_nip`,
    the L0 guardrail) must make THIS MODULE's stage-by-stage assertion fail."""
    case = next(c for c in GUSTAVO_JOURNEY_CASES if c["id"] == "EVL-GUSTAVO-JOURNEY-01")
    mutated = copy.deepcopy(case)
    mutated["journey"]["handoff"]["engine_variables"]["decisao_nip"] = "MANTER_NEGATIVA-SENTINEL"
    with pytest.raises(AssertionError, match="hand-off engine variable mismatch"):
        await test_gustavo_journey_eval_intake_triage_handoff(mutated)
