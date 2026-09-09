"""End-to-end JOURNEY eval for Lucas (gap 11.5, WP-EVALS) -- extends the pattern
`test_helena_journey_evals.py` establishes to Lucas's J3 (`cancelamento`) escalation journey.

Lucas's graph (`src/maezo/agents/lucas/graph.py`) is:

    receive -> gather -> assess -> {respond_member | escalate_human} -> start_process -> complete

`escalate_human` mirrors Helena's `escalate` closely (same shared SP-OP-ESCALATION-001
contract), with one deliberate ordering difference this module exercises: LUC-05 moved the
beneficiary ACK to its OWN node, `send_escalation_ack`, reachable ONLY after `start_process`
reports a live instance (`process_started is True`) -- so the ACK is sent ONLY after the
escalation actually exists in the engine, never before (module docstring: pre-LUC-05, a start
failure could leave the beneficiary told "a human will continue" while zero engine instances
existed).

Three observable stages:

    1. intake  (`receive`)               -- the turn started cleanly, no runtime-context error.
    2. triage  (`gather` + `assess`)     -- `lucas_escalation_routing` actually ran (its key
                                             lands in `dmn_refs`) AND its routing facts
                                             (`route`, `motivo_humano`, `grupo_humano`) reached
                                             the state.
    3. hand-off (`start_process` +       -- SP-OP-ESCALATION-001 actually started (idempotently,
       `send_escalation_ack`)               queryable by business key), its engine-bound
                                             variables carry the bounded routing tokens, AND the
                                             beneficiary actually received the WhatsApp ACK --
                                             observably AFTER the engine start (never before).

Same mechanism as every other eval (`run_case` against the real `build()` contract) -- a new
ASSERTION SHAPE, not a new harness path.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.lucas.graph import PROCESS_KEY, build

from ._harness import assert_expect, assert_no_leak, load_golden, run_case, run_mutation_check
from .conftest import FakeWhatsAppSender

LUCAS_JOURNEY_CASES = [c for c in load_golden("lucas") if c["id"].startswith("EVL-LUCAS-JOURNEY-")]


def _lucas_extra_config(sender: FakeWhatsAppSender) -> dict[str, Any]:
    return {"whatsapp": sender}


def _mutate_grupo_humano(case: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy `case` with `expect.fields.grupo_humano` flipped to a DIFFERENT valid group.

    Non-vacuousness proof for the SF half of this golden."""
    mutated = copy.deepcopy(dict(case))
    fields = dict(mutated["expect"]["fields"])
    fields["grupo_humano"] = "atendimentoHumano"
    mutated["expect"] = {**mutated["expect"], "fields": fields}
    return mutated


@pytest.mark.eval
@pytest.mark.parametrize("case", LUCAS_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_lucas_journey_eval_intake_triage_handoff(case: dict[str, Any]) -> None:
    """Drive ONE escalation turn through the full compiled graph and assert an observable
    checkpoint at each of the three journey stages (module docstring)."""
    sender = FakeWhatsAppSender()
    result = await run_case(build, case, extra_config=_lucas_extra_config(sender))
    journey = case["journey"]

    assert_expect(result.state, case["expect"])
    assert_no_leak(result.state, case.get("leak_canaries") or [], sender=result.whatsapp)

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

    # --- Stage 3: hand-off (`start_process` + `send_escalation_ack`) -------------------------
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
    if handoff.get("whatsapp_delivered"):
        assert sender.sent, "hand-off stage: the beneficiary never received the escalation ACK"
        delivered_to, delivered_text = sender.sent[-1]
        assert delivered_to == case["input"]["state"]["to_hash"], (
            f"hand-off stage: ACK delivered to {delivered_to!r}, expected the case's own to_hash"
        )
        assert delivered_text, "hand-off stage: the escalation ACK text sent to the beneficiary was empty"


@pytest.mark.eval
@pytest.mark.parametrize("case", LUCAS_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_lucas_journey_grupo_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the SF half of the journey golden."""
    await run_mutation_check(
        build, case, mutation=_mutate_grupo_humano, extra_config=_lucas_extra_config(FakeWhatsAppSender())
    )


@pytest.mark.eval
async def test_evl_lucas_journey_01_mutation_check_handoff_stage_is_non_vacuous() -> None:
    """Corrupting the journey's OWN hand-off-stage expectation (`engine_variables.
    motivo_encaminhamento`) must make THIS MODULE's stage-by-stage assertion fail."""
    case = next(c for c in LUCAS_JOURNEY_CASES if c["id"] == "EVL-LUCAS-JOURNEY-01")
    mutated = copy.deepcopy(case)
    mutated["journey"]["handoff"]["engine_variables"]["motivo_encaminhamento"] = "outro"
    with pytest.raises(AssertionError, match="hand-off engine variable mismatch"):
        await test_lucas_journey_eval_intake_triage_handoff(mutated)
