"""End-to-end JOURNEY eval for Valentina (gap 11.5, WP-EVALS) -- extends the pattern
`test_helena_journey_evals.py` establishes to Valentina's `enroll` care-program journey.

Valentina's graph (`src/maezo/agents/valentina/graph.py`) INVERTS the reference shape (module
docstring): the entry CHOKEPOINT is consent (LGPD art. 7/11), not a classification step --

    receive -> consent_gate --(consentimento ativo)--> gather -> assess(DMN)
                                                           -> {auto_route | human_review}
                                                           -> start_process -> finalize

so this module's three observable stages map onto Valentina's OWN structural gates rather than
Helena's generic intake/triage/hand-off names:

    1. consent  (`consent_gate`)        -- the LGPD chokepoint COMPUTED the `ativo` verdict from
                                            the raw consent facts (never trusted a planted
                                            verdict) -- invariant A, no PHI processing without it.
    2. triage   (`gather` + `assess`)   -- `programa_routing` actually ran (its key lands in
                                            `dmn_refs`) AND its routing fact
                                            (`elegivel_programa=ELEGIVEL`, `route=auto_route`)
                                            reached the state.
    3. hand-off (`start_process` +      -- SP-OP-PROGRAMA-001 actually started (idempotently,
       `finalize`)                         queryable by business key) with the contract's
                                            engine-bound variables carrying the consent verdict
                                            AND the ALWAYS-`None` `decisao_programa` L0 clinical
                                            guardrail (invariant C: the disenrollment/discharge
                                            decision belongs SOLELY to `UT_DecisaoClinica`).

Same mechanism as every other eval (`run_case` against the real `build()` contract) -- a new
ASSERTION SHAPE, not a new harness path.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.valentina.graph import PROCESS_KEY_PROGRAMA, build

from ._harness import assert_expect, assert_no_leak, load_golden, run_case, run_mutation_check

_VALENTINA_CASES = load_golden("valentina")
VALENTINA_JOURNEY_CASES = [c for c in _VALENTINA_CASES if c["id"].startswith("EVL-VALENTINA-JOURNEY-")]


def _mutate_elegivel_programa(case: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy `case` with `expect.fields.elegivel_programa` flipped to a DIFFERENT valid
    value. Non-vacuousness proof for the SF half of this golden."""
    mutated = copy.deepcopy(dict(case))
    fields = dict(mutated["expect"]["fields"])
    fields["elegivel_programa"] = "ANALISE_HUMANA"
    mutated["expect"] = {**mutated["expect"], "fields": fields}
    return mutated


@pytest.mark.eval
@pytest.mark.parametrize("case", VALENTINA_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_valentina_journey_eval_consent_triage_handoff(case: dict[str, Any]) -> None:
    """Drive ONE enrollment turn through the full compiled graph and assert an observable
    checkpoint at each of the three journey stages (module docstring)."""
    result = await run_case(build, case)
    journey = case["journey"]

    assert_expect(result.state, case["expect"])
    assert_no_leak(result.state, case.get("leak_canaries") or [])

    # --- Stage 1: consent (`consent_gate`) ----------------------------------------------------
    consent = journey["consent"]
    for field_name, expected in consent.get("fields", {}).items():
        assert result.state.get(field_name) == expected, (
            f"consent stage mismatch: {field_name}={result.state.get(field_name)!r}, expected {expected!r}"
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
    assert handoff["process_key"] == PROCESS_KEY_PROGRAMA
    status = await result.cibseven.get_process_status(business_key)
    for var_name, expected in handoff.get("engine_variables", {}).items():
        assert status.variables.get(var_name) == expected, (
            f"hand-off engine variable mismatch: {var_name}={status.variables.get(var_name)!r}, "
            f"expected {expected!r}"
        )


@pytest.mark.eval
@pytest.mark.parametrize("case", VALENTINA_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_valentina_journey_elegibilidade_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the SF half of the journey golden."""
    await run_mutation_check(build, case, mutation=_mutate_elegivel_programa)


@pytest.mark.eval
async def test_evl_valentina_journey_01_mutation_check_handoff_stage_is_non_vacuous() -> None:
    """Corrupting the journey's OWN hand-off-stage expectation (`engine_variables.
    decisao_programa`, the L0 clinical guardrail) must make THIS MODULE's stage-by-stage
    assertion fail."""
    case = next(c for c in VALENTINA_JOURNEY_CASES if c["id"] == "EVL-VALENTINA-JOURNEY-01")
    mutated = copy.deepcopy(case)
    mutated["journey"]["handoff"]["engine_variables"]["decisao_programa"] = "DESLIGAR_CLINICO-SENTINEL"
    with pytest.raises(AssertionError, match="hand-off engine variable mismatch"):
        await test_valentina_journey_eval_consent_triage_handoff(mutated)
