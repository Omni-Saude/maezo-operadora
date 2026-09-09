"""End-to-end JOURNEY eval for Fernando (gap 11.5, WP-EVALS) -- extends the pattern
`test_helena_journey_evals.py` establishes to Fernando's J3 (`analise_inadimplencia`) escalation
journey.

Fernando's graph (`src/maezo/agents/fernando/graph.py`) is:

    receive -> assess -> {notify | escalate -> start_process} -> __end__

The `notify` branch is the ONLY WhatsApp-sending path (J1/J2, a purely informational notice);
`escalate` (J3, this golden) NEVER sends WhatsApp -- its only outbound channel is the engine
hand-off, mirroring the module docstring's L0 hard invariant that Fernando never communicates an
adverse outcome to the beneficiary himself. This module therefore checks `sender.sent` stayed
EMPTY on the hand-off stage, the mirror image of Helena's/Lucas's `whatsapp_delivered=True`
check -- an equally observable (and equally important) fact about this hand-off.

Three observable stages:

    1. intake   (`receive`)   -- the turn started cleanly, no runtime-context error.
    2. triage   (`assess`)    -- `inadimplencia_status` actually ran (its key lands in
                                 `dmn_refs`) AND the explicit human-analysis request forces
                                 `route=escalate` regardless of what the DMN said (module
                                 docstring's J3 override).
    3. hand-off (`escalate` + -- SP-OP-INADIMPLENCIA-001 actually started (idempotently,
       `start_process`)          queryable by business key) with the contract's engine-bound
                                 variables carrying the bounded routing tokens AND the
                                 ALWAYS-`None` `decisao_inadimplencia` L0 guardrail -- AND no
                                 WhatsApp message was ever sent on this branch.

Same mechanism as every other eval (`run_case` against the real `build()` contract) -- a new
ASSERTION SHAPE, not a new harness path.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.fernando.graph import PROCESS_KEY, build

from ._harness import assert_expect, assert_no_leak, load_golden, run_case, run_mutation_check
from .conftest import FakeWhatsAppSender

FERNANDO_JOURNEY_CASES = [c for c in load_golden("fernando") if c["id"].startswith("EVL-FERNANDO-JOURNEY-")]


def _fernando_extra_config(sender: FakeWhatsAppSender) -> dict[str, Any]:
    return {"whatsapp": sender}


def _mutate_motivo_humano(case: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy `case` with `expect.fields.motivo_humano` flipped to a DIFFERENT valid value.

    Non-vacuousness proof for the SF half of this golden."""
    mutated = copy.deepcopy(dict(case))
    fields = dict(mutated["expect"]["fields"])
    fields["motivo_humano"] = "ambiguidade"
    mutated["expect"] = {**mutated["expect"], "fields": fields}
    return mutated


@pytest.mark.eval
@pytest.mark.parametrize("case", FERNANDO_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_fernando_journey_eval_intake_triage_handoff(case: dict[str, Any]) -> None:
    """Drive ONE escalation turn through the full compiled graph and assert an observable
    checkpoint at each of the three journey stages (module docstring)."""
    sender = FakeWhatsAppSender()
    result = await run_case(build, case, extra_config=_fernando_extra_config(sender))
    journey = case["journey"]

    assert_expect(result.state, case["expect"])
    assert_no_leak(result.state, case.get("leak_canaries") or [])

    # --- Stage 1: intake (`receive`) ----------------------------------------------------------
    intake = journey["intake"]
    if intake.get("no_error"):
        assert not result.state.get("error"), (
            f"intake stage recorded an error before assess ran: {result.state.get('error')!r}"
        )

    # --- Stage 2: triage (`assess`) -----------------------------------------------------------
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

    # --- Stage 3: hand-off (`escalate` + `start_process`) -------------------------------------
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
    if handoff.get("whatsapp_sent") is False:
        assert not sender.sent, (
            "hand-off stage: the escalate branch must NEVER send WhatsApp (module docstring) "
            f"-- got sender.sent={sender.sent!r}"
        )


@pytest.mark.eval
@pytest.mark.parametrize("case", FERNANDO_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_fernando_journey_motivo_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the SF half of the journey golden."""
    await run_mutation_check(
        build, case, mutation=_mutate_motivo_humano, extra_config=_fernando_extra_config(FakeWhatsAppSender())
    )


@pytest.mark.eval
async def test_evl_fernando_journey_01_mutation_check_handoff_stage_is_non_vacuous() -> None:
    """Corrupting the journey's OWN hand-off-stage expectation (`engine_variables.
    decisao_inadimplencia`, the L0 guardrail) must make THIS MODULE's stage-by-stage assertion
    fail -- proving the guardrail checkpoint is actually exercised, not vacuously green."""
    case = next(c for c in FERNANDO_JOURNEY_CASES if c["id"] == "EVL-FERNANDO-JOURNEY-01")
    mutated = copy.deepcopy(case)
    mutated["journey"]["handoff"]["engine_variables"]["decisao_inadimplencia"] = "SUSPENDER-SENTINEL"
    with pytest.raises(AssertionError, match="hand-off engine variable mismatch"):
        await test_fernando_journey_eval_intake_triage_handoff(mutated)
