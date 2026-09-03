"""End-to-end JOURNEY eval for Helena (gap 11.5, WP-EVALS) — the first eval beyond
classification/dossier.

Finding this closes: `docs/audits/maezo-deep-audit/reports/domain-11-product-fit.md:32`
("Os 44 golden evals cobrem os 10 agentes, mas sao finos ... nenhum eval de jornada ponta a
ponta") + `gateways/gvr-d11.md` (confirmed 44/44 distribution, all classification/dossier-shaped).

Every existing golden in `tests/evals/golden/*/` (via `test_classifier_evals.py`/
`test_dossier_adverse_evals.py`/`test_dossier_admin_evals.py`) asserts only the TURN'S FINAL
route/fields — `assert_expect(result.state, case["expect"])`. This module asserts an OBSERVABLE
OUTPUT at EACH of the three stages `docs/processes/journeys/AGJ-HELENA-TRIAGE.md` describes for
one beneficiary turn:

    1. intake            (`saudacao_identificacao`) — the turn started cleanly, no runtime-
                          context/transport failure recorded before triage ran.
    2. triage             (`coleta_sintomas` + `avaliacao_red_flag`) — the classify LLM's
                          extraction AND the red-flag DMN's decision both reached the state
                          correctly (ADR-0012: the LLM normalizes, the DMN decides).
    3. routing/hand-off outcome (`escalado`) — SP-OP-ESCALATION-001 actually started
                          (idempotently), the engine-bound variables carry the bounded routing
                          tokens, AND the beneficiary actually received the human-handoff
                          confirmation over WhatsApp — the observable END of the journey, not
                          just an in-memory route label.

This is driven through the exact SAME golden mechanism as every other eval (`run_case` against
the real `build()` contract, a golden JSON under `golden/helena/`) — it is a new ASSERTION
SHAPE, not a new harness path. It is possible in ONE `run_case`/`ainvoke` call (not three
separate turns) because `HelenaGraph`'s compiled `StateGraph` merges every node's return dict
into one cumulative state with a plain dict-update reducer: `escalate()`'s return never clears
the keys `classify()` set (`intent`/`population`/`sintoma_codigo`/`dmn_table`/`dmn_decision`/
...), so the FINAL `RunResult.state` already carries an observable checkpoint from every stage
the turn passed through — this module's job is to assert against all of them, not just the
last one.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.helena.graph import PROCESS_KEY, build

from ._harness import (
    assert_expect,
    assert_no_leak,
    load_golden,
    mutate_expected_route,
    run_case,
    run_mutation_check,
)
from .conftest import FakeWhatsAppSender

HELENA_JOURNEY_CASES = [c for c in load_golden("helena") if c["id"].startswith("EVL-HELENA-JOURNEY-")]


def _helena_extra_config(sender: FakeWhatsAppSender) -> dict[str, Any]:
    return {"whatsapp": sender}


@pytest.mark.eval
@pytest.mark.parametrize("case", HELENA_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_helena_journey_eval_intake_triage_handoff(case: dict[str, Any]) -> None:
    """Drive ONE beneficiary turn through the full compiled graph and assert an observable
    checkpoint at each of the three journey stages (module docstring)."""
    sender = FakeWhatsAppSender()
    result = await run_case(build, case, extra_config=_helena_extra_config(sender))
    journey = case["journey"]

    # The turn-level RT/SF/ABS criterion still holds — this IS also an ordinary Tier-A eval.
    assert_expect(result.state, case["expect"])
    assert_no_leak(result.state, case.get("leak_canaries") or [])

    # --- Stage 1: intake (`saudacao_identificacao`) ------------------------------------------
    intake = journey["intake"]
    if intake.get("no_error"):
        assert result.state.get("error") is None, (
            f"intake stage recorded an error before triage ran: {result.state.get('error')!r}"
        )

    # --- Stage 2: triage (`coleta_sintomas` + `avaliacao_red_flag`) --------------------------
    # The LLM normalizes free text into a symptom code + population (ADR-0012: it NEVER decides
    # red_flag itself); the DMN table is what actually decided the escalation this turn took.
    triage = journey["triage"]
    for field_name, expected in triage.get("fields", {}).items():
        assert result.state.get(field_name) == expected, (
            f"triage stage mismatch: {field_name}={result.state.get(field_name)!r}, expected {expected!r}"
        )
    dmn_decision = result.state.get("dmn_decision") or {}
    for field_name, expected in triage.get("dmn_decision_fields", {}).items():
        assert dmn_decision.get(field_name) == expected, (
            f"triage DMN decision mismatch: {field_name}={dmn_decision.get(field_name)!r}, "
            f"expected {expected!r}"
        )

    # --- Stage 3: routing/hand-off outcome (`escalado`) --------------------------------------
    # SP-OP-ESCALATION-001 actually started (idempotently, queryable by business key), the
    # engine-bound variables carry the bounded routing tokens the human attendant will see, and
    # the beneficiary actually received the WhatsApp handoff confirmation — not merely an
    # in-memory `next_kind` label.
    handoff = journey["handoff"]
    for field_name, expected in handoff.get("fields", {}).items():
        assert result.state.get(field_name) == expected, (
            f"hand-off stage mismatch: {field_name}={result.state.get(field_name)!r}, expected {expected!r}"
        )
    business_key = result.state.get("escalation_business_key")
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
        assert sender.sent, "hand-off stage: the beneficiary never received a WhatsApp confirmation"
        delivered_to, delivered_text = sender.sent[-1]
        assert delivered_to, "hand-off stage: WhatsApp send target (phone hash) was empty"
        assert delivered_text == result.state.get("response_text"), (
            "hand-off stage: the text actually sent over WhatsApp does not match response_text"
        )


@pytest.mark.eval
@pytest.mark.parametrize("case", HELENA_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_helena_journey_route_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the RT half of the journey golden: flipping
    `expect.next_kind` must make the harness fail."""
    await run_mutation_check(
        build,
        case,
        mutation=mutate_expected_route,
        extra_config=_helena_extra_config(FakeWhatsAppSender()),
    )


@pytest.mark.eval
async def test_evl_helena_journey_01_mutation_check_handoff_stage_is_non_vacuous() -> None:
    """Corrupting the journey's OWN hand-off-stage expectation (`engine_variables.
    motivo_categoria`) must make THIS MODULE's stage-by-stage assertion fail — proving the
    per-stage checks this eval adds (beyond the shared RT/SF/ABS criterion, already proven
    non-vacuous above) are actually exercised, not vacuously green."""
    case = next(c for c in HELENA_JOURNEY_CASES if c["id"] == "EVL-HELENA-JOURNEY-01")
    mutated = copy.deepcopy(case)
    mutated["journey"]["handoff"]["engine_variables"]["motivo_categoria"] = "outro"
    with pytest.raises(AssertionError, match="hand-off engine variable mismatch"):
        await test_helena_journey_eval_intake_triage_handoff(mutated)
