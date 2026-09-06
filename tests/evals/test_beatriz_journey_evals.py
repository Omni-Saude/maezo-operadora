"""End-to-end JOURNEY eval for Beatriz (gap 11.5, WP-EVALS) -- extends the pattern
`test_helena_journey_evals.py` establishes to an agent whose hand-off is a DELEGATION ENVELOPE,
never an engine start.

Beatriz's graph (`src/maezo/agents/beatriz/graph.py`) is linear, single-entry, no conditional
edge at all:

    receive -> gather -> instruct_investigation -> finalize

She NEVER starts SP-OP-FRAUDE-001 (the engine drives it and convokes her via A2A
`fraude.investigate`; her tools allowlist deliberately excludes `mcp-cibseven.start_process`).
Her hand-off is therefore the assembled investigation DOSSIER returned as part of the graph's
final state -- the delegation envelope the process's own `seal_custody_bundle` worker consumes
and a human decides over in `UT_DecisaoInvestigador` -- which is exactly the alternative shape
BRIEF-EVALS-JOURNEY anticipates ("a process start OR a delegation").

Three observable stages:

    1. intake  (`receive`)               -- the turn started cleanly (business key derivable,
                                             no runtime-context error).
    2. gather  (`gather`)                -- the raw evidence pointers were actually NORMALIZED
                                             to the custody-safe `{ref,hash,tipo,origem}`
                                             projection (no-PHI-in-custody) -- not merely echoed.
    3. hand-off (`instruct_investigation`
       + `finalize`)                     -- the DELEGATION ENVELOPE (the dossier) carries the
                                             pre-resolved indicators/score UNCHANGED (Beatriz
                                             never recomputes a score) AND the L0 structural
                                             guardrail fields (`decisao_fraude`/`bundle_root`/
                                             `destino_referral`) are ALWAYS `None` -- the
                                             observable proof that no accusation/seal/referral
                                             was fabricated by this hand-off.

Same mechanism as every other eval (`run_case` against the real `build()` contract) -- a new
ASSERTION SHAPE, not a new harness path.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.beatriz.graph import build

from ._harness import assert_expect, assert_no_leak, load_golden, run_case, run_mutation_check

BEATRIZ_JOURNEY_CASES = [c for c in load_golden("beatriz") if c["id"].startswith("EVL-BEATRIZ-JOURNEY-")]


def _mutate_desfecho(case: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy `case` with `expect.fields.desfecho` flipped to a DIFFERENT valid value.

    Non-vacuousness proof for the SF half of this golden (Beatriz's `Desfecho` type admits only
    `{"dossie_instruido", "instrucao_incompleta"}` -- module docstring)."""
    mutated = copy.deepcopy(dict(case))
    fields = dict(mutated["expect"]["fields"])
    other = "instrucao_incompleta" if fields.get("desfecho") != "instrucao_incompleta" else "outro"
    fields["desfecho"] = other
    mutated["expect"] = {**mutated["expect"], "fields": fields}
    return mutated


@pytest.mark.eval
@pytest.mark.parametrize("case", BEATRIZ_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_beatriz_journey_eval_intake_gather_handoff(case: dict[str, Any]) -> None:
    """Drive ONE investigation turn through the full compiled graph and assert an observable
    checkpoint at each of the three journey stages (module docstring)."""
    result = await run_case(build, case)
    journey = case["journey"]

    assert_expect(result.state, case["expect"])
    assert_no_leak(result.state, case.get("leak_canaries") or [])

    # --- Stage 1: intake (`receive`) ----------------------------------------------------------
    intake = journey["intake"]
    if intake.get("no_error"):
        assert not result.state.get("error"), (
            f"intake stage recorded an error before gather ran: {result.state.get('error')!r}"
        )

    # --- Stage 2: gather (custody normalization) ----------------------------------------------
    gather = journey["gather"]
    for field_name, expected in gather.get("fields", {}).items():
        assert result.state.get(field_name) == expected, (
            f"gather stage mismatch: {field_name}={result.state.get(field_name)!r}, expected {expected!r}"
        )
    if "evidencia_normalizada" in gather:
        actual_evidence = result.state.get("evidencia_normalizada")
        assert actual_evidence == gather["evidencia_normalizada"], (
            "gather stage: evidence custody projection mismatch -- expected "
            f"{gather['evidencia_normalizada']!r}, got {actual_evidence!r}"
        )

    # --- Stage 3: hand-off (`instruct_investigation` + `finalize`) ---------------------------
    handoff = journey["handoff"]
    for field_name, expected in handoff.get("fields", {}).items():
        assert result.state.get(field_name) == expected, (
            f"hand-off stage mismatch: {field_name}={result.state.get(field_name)!r}, expected {expected!r}"
        )
    dossier = result.state.get("dossier") or {}
    assert dossier.get("business_key") == handoff["business_key"], (
        f"hand-off delegation envelope business_key mismatch: {dossier.get('business_key')!r}, "
        f"expected {handoff['business_key']!r}"
    )
    for field_name, expected in handoff.get("dossier_fields", {}).items():
        assert dossier.get(field_name) == expected, (
            f"hand-off delegation envelope field mismatch: {field_name}={dossier.get(field_name)!r}, "
            f"expected {expected!r}"
        )


@pytest.mark.eval
@pytest.mark.parametrize("case", BEATRIZ_JOURNEY_CASES, ids=lambda c: c["id"])
async def test_beatriz_journey_desfecho_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the SF half of the journey golden: flipping
    `expect.fields.desfecho` must make the harness fail."""
    await run_mutation_check(build, case, mutation=_mutate_desfecho)


@pytest.mark.eval
async def test_evl_beatriz_journey_01_mutation_check_handoff_stage_is_non_vacuous() -> None:
    """Corrupting the journey's OWN hand-off-stage expectation (`dossier_fields.
    decisao_fraude`) must make THIS MODULE's stage-by-stage assertion fail -- proving the L0
    zero_auto_accusation checkpoint this eval adds is actually exercised, not vacuously green."""
    case = next(c for c in BEATRIZ_JOURNEY_CASES if c["id"] == "EVL-BEATRIZ-JOURNEY-01")
    mutated = copy.deepcopy(case)
    mutated["journey"]["handoff"]["dossier_fields"]["decisao_fraude"] = "ACUSAR-SENTINEL"
    with pytest.raises(AssertionError, match="hand-off delegation envelope field mismatch"):
        await test_beatriz_journey_eval_intake_gather_handoff(mutated)
