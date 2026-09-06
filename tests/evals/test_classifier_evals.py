"""Classifier-family evals — helena/fernando/lucas (T3.2 design §6/§7).

Wave B0 lands ONLY the reference case end to end: `EVL-HELENA-01` (proves collection ->
`make evals` green -> the `evals`/`evals-nightly` CI lanes unblock). Wave B1 EXTENDS this file
with Fernando's and Lucas's own `test_<agent>_eval_tier_a` / `test_<agent>_eval_tier_b_live`
functions (mirror the Helena ones below exactly — same shape, different `agent`/graph import/
`extra_config`) plus their own `golden/fernando/`, `golden/lucas/` cases. Do not edit the Helena
functions from B1 — add new functions instead, so the two waves never touch the same lines.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

import pytest

from maezo.agents.fernando.graph import build as build_fernando
from maezo.agents.helena.graph import HelenaGraph, build
from maezo.agents.lucas.graph import build as build_lucas
from maezo.runtime.inference import InferenceProvider, InferenceSettings
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

from ._harness import (
    assert_expect,
    assert_live_score,
    assert_no_leak,
    load_golden,
    mutate_expected_route,
    mutate_plant_canary,
    register_dmn_fixture,
    run_case,
    run_mutation_check,
    score_live,
)
from .conftest import FakeWhatsAppSender, live_key_skip

HELENA_CASES = load_golden("helena")
_HELENA_TIER_B_CASES = [c for c in HELENA_CASES if "B" in c.get("tier", [])]


def _helena_extra_config() -> dict[str, Any]:
    """`HelenaGraph.build(config)`'s one seam beyond the four every agent shares."""
    return {"whatsapp": FakeWhatsAppSender()}


# ---------------------------------------------------------------------------
# Tier A — deterministic replay, no network, no key. PR merge-blocking.
# ---------------------------------------------------------------------------


@pytest.mark.eval
@pytest.mark.parametrize("case", HELENA_CASES, ids=lambda c: c["id"])
async def test_helena_eval_tier_a(case: dict[str, Any]) -> None:
    """Drive the compiled Helena graph with the ReplayInferenceProvider; assert RT+SF+ABS."""
    result = await run_case(build, case, extra_config=_helena_extra_config())
    assert_expect(result.state, case["expect"])
    # EVAL-HARNESS-SENDER (NEW-07): `sender=` folds every WhatsApp text this turn actually sent
    # into the SAME ABS scan -- `state` alone never sees `send_escalation_ack`-shaped leaks.
    assert_no_leak(result.state, case.get("leak_canaries") or [], sender=result.whatsapp)


# ---------------------------------------------------------------------------
# Non-vacuousness proof (T3.2 design §7.1) — cheap, deterministic, no network; strengthens the
# same `-m eval` merge gate rather than sitting outside it.
# ---------------------------------------------------------------------------


@pytest.mark.eval
async def test_evl_helena_01_mutation_check_route_is_non_vacuous() -> None:
    """Flipping EVL-HELENA-01's expected route must make the harness call fail — otherwise this
    eval would rubber-stamp any routing decision `classify()` makes and never catch a real
    prompt/graph/model regression (the exact failure mode ADR-0009's promotion gate exists to
    prevent)."""
    case = next(c for c in HELENA_CASES if c["id"] == "EVL-HELENA-01")
    await run_mutation_check(
        build,
        case,
        mutation=mutate_expected_route,
        extra_config=_helena_extra_config(),
    )


# ---------------------------------------------------------------------------
# Tier B — live Anthropic call, threshold-scored, nightly-only, non-blocking. Loudly skips
# without MAEZO_ANTHROPIC_API_KEY/ANTHROPIC_API_KEY (mirrors test_inference_live.py) — never
# runs on a keyless PR or fork.
# ---------------------------------------------------------------------------


@live_key_skip
@pytest.mark.eval
@pytest.mark.llm_live
@pytest.mark.parametrize("case", _HELENA_TIER_B_CASES, ids=lambda c: c["id"])
async def test_helena_eval_tier_b_live(case: dict[str, Any]) -> None:
    """Re-run `classify()` against the REAL Anthropic provider; score against the Tier-A
    baseline (`recorded_llm[0]`, the same JSON the replay provider would have returned) rather
    than a second hand-maintained dataset."""
    live = case["live"]
    baseline = json.loads(case["recorded_llm"][0])

    dmn = FakeDmnTransport()
    register_dmn_fixture(dmn, case.get("dmn_fixture"))
    graph = HelenaGraph(
        inference=InferenceProvider(settings=InferenceSettings(provider="anthropic")),
        dmn=dmn,
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=FakeWhatsAppSender(),
    )

    result = await graph.classify(case["input"]["state"])

    score = score_live(result, baseline, live["match"])
    assert_live_score(score, live["threshold"])


# =============================================================================
# T3.2 wave B1 — Helena's remaining goldens (02-08) + Fernando + Lucas (17 new evals).
#
# `HELENA_CASES`/`_HELENA_TIER_B_CASES` above are recomputed from disk every collection, so
# simply adding `golden/helena/EVL-HELENA-0{2..8}.json` makes B0's own `test_helena_eval_tier_a`
# and `test_helena_eval_tier_b_live` pick them up automatically — no edit to those functions or
# to `EVL-HELENA-01`/its own mutation-check test. This wave only ADDS: (a) a mutation-check test
# for Helena's 7 new goldens, (b) a dedicated engine-variable leak test for the PL-class
# EVL-HELENA-07 (see its rationale below), and (c) the full Fernando/Lucas test surface.
#
# GROUND-TRUTH CORRECTIONS vs the T3.2 design doc (read the graphs, not just the design table):
#   1. Fernando/Lucas have NO free-text LLM *classification* step — `intencao` arrives as an
#      already-structured caller/state field (both graphs' own module docstrings say so
#      explicitly); the DMN + `intencao` decide routing, the LLM only drafts prose afterward
#      (`_build_message`/`_build_dossier`, exactly ONE `generate()` call per Fernando turn on
#      every path; Lucas's `respond_member` path is also exactly one call, `escalate_human` is
#      TWO when `to_hash`+whatsapp are present — the dossier narrativa, then the ack draft).
#      There is therefore no analogous "classify()" node returning structured JSON to re-run
#      live and score against fields (`score_live`'s whole contract), so — unlike Helena — this
#      wave does NOT add `test_fernando_eval_tier_b_live`/`test_lucas_eval_tier_b_live`: Tier A
#      (deterministic replay) is the entire regression baseline for these two agents' ROUTING
#      correctness (CE/EU/FC/GC classes), which is 100% DMN/state-field-driven, not LLM-driven.
#      This deviates from the design doc's §5.1 tier reconciliation (which lists FERNANDO 01/02
#      and LUCAS 01/02/03 as Tier-B live variants) — flagged here rather than silently dropped.
#   2. Fernando's/Lucas's state schema has no `next_kind` field (Helena's is the only agent that
#      uses that name) — their routing field is literally named `route`. Every Fernando/Lucas
#      golden's `expect` therefore lives under `fields` (`{"route": "notify"|"escalate", ...}`
#      / `{"route": "respond_member"|"escalate_human", ...}`), never `next_kind` (which would
#      silently no-op against a key that never exists in either TypedDict — `state.get(
#      "next_kind")` is always `None` for these two agents, so a naive reuse of
#      `mutate_expected_route`/a `next_kind`-keyed `expect` would be a SILENTLY VACUOUS check).
#      `_mutate_route_field` below is the local, `fields`-aware analog of `_harness.
#      mutate_expected_route` for exactly this reason.
#   3. Lucas's graph carries no raw free-text message field in `LucasState` at all (unlike
#      Helena) — the design's EVL-LUCAS-05 "clinical-sounding message" scenario sketch does not
#      map onto an actual state field. `EVL-LUCAS-05` instead proves the same GC guarantee the
#      design intends (motivo_categoria is never clinical) structurally, on an ordinary
#      escalate_human path — see that golden's own `description`.
#   4. `assert_no_leak(result.state, ...)` is the wrong assertion surface for a PL golden whose
#      canary is embedded in an INPUT field the graph never overwrites on its path (Helena's
#      `message_body`, Fernando's `intencao`): LangGraph's state merge preserves every untouched
#      input key verbatim, so the raw canary trivially "leaks" into the full graph state — this
#      is a harmless in-memory echo, never a real disclosure (the state never leaves the
#      process). The design's own §5 language ("CPF ABSENT from resumo_contexto / class token /
#      emitted process vars") already points at the CIBSEVEN-recorded engine-bound variables,
#      not the raw compiled-graph state — `EVL-HELENA-07`/`EVL-FERNANDO-05` therefore ship with
#      an EMPTY top-level `leak_canaries` (so the shared per-case tier-A test's default
#      `assert_no_leak(result.state, ...)` no-ops instead of false-failing) and get their own
#      dedicated test that inspects `cibseven.get_process_status(...)` directly, mirroring
#      `test_helena.py::test_cpf_bearing_field_value_never_reaches_engine_variables` /
#      `test_fernando.py::test_receive_invalid_intencao_fails_closed_without_leaking_value`.
# =============================================================================


# ---------------------------------------------------------------------------
# Helena — non-vacuousness proof for the 7 new goldens (02-08) landed by this wave. EVL-HELENA-01
# keeps its own dedicated mutation-check test from B0 (untouched, per the module docstring).
# ---------------------------------------------------------------------------

_HELENA_B1_ROUTE_MUTATION_IDS: frozenset[str] = frozenset(
    {
        "EVL-HELENA-02",
        "EVL-HELENA-03",
        "EVL-HELENA-04",
        "EVL-HELENA-05",
        "EVL-HELENA-06",
        "EVL-HELENA-08",
    }
)
_HELENA_B1_ROUTE_MUTATION_CASES = [c for c in HELENA_CASES if c["id"] in _HELENA_B1_ROUTE_MUTATION_IDS]


@pytest.mark.eval
@pytest.mark.parametrize("case", _HELENA_B1_ROUTE_MUTATION_CASES, ids=lambda c: c["id"])
async def test_helena_b1_evals_route_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for every RT-class Helena golden this wave adds:
    flipping `expect.next_kind` must make the harness fail — otherwise the golden would rubber-
    stamp whatever `classify()`/the routing conditional actually returns. Mirrors B0's
    `test_evl_helena_01_mutation_check_route_is_non_vacuous`, parametrized rather than
    one-function-per-id since all 6 share the exact same mutation shape."""
    await run_mutation_check(
        build,
        case,
        mutation=mutate_expected_route,
        extra_config=_helena_extra_config(),
    )


# ---------------------------------------------------------------------------
# Helena — CC-08 (2026-09-04): DMN-down / PHI-zone-blocked infra-failure goldens (EVL-HELENA-11/
# 12/13). Route mutation-check reuses the SAME `mutate_expected_route` shape as the B1 block
# above — kept as its own named set (not merged into `_HELENA_B1_ROUTE_MUTATION_IDS`) so each
# wave's own goldens stay traceable to the work package that added them.
# ---------------------------------------------------------------------------

_HELENA_CC08_MUTATION_IDS: frozenset[str] = frozenset({"EVL-HELENA-11", "EVL-HELENA-12", "EVL-HELENA-13"})
_HELENA_CC08_MUTATION_CASES = [c for c in HELENA_CASES if c["id"] in _HELENA_CC08_MUTATION_IDS]


@pytest.mark.eval
@pytest.mark.parametrize("case", _HELENA_CC08_MUTATION_CASES, ids=lambda c: c["id"])
async def test_helena_cc08_evals_route_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for the three CC-08 infra-failure goldens:
    flipping `expect.next_kind` must make the harness fail — otherwise these evals would rubber-
    stamp ANY route `classify()`/`escalate()` produces regardless of whether the DMN-down/
    PHI-zone-blocked path actually still escalates. Proves the goldens are watching the real
    fail-safe behaviour, not merely echoing it."""
    await run_mutation_check(
        build,
        case,
        mutation=mutate_expected_route,
        extra_config=_helena_extra_config(),
    )


@pytest.mark.eval
async def test_evl_helena_07_cpf_absent_from_engine_bound_variables() -> None:
    """PL: EVL-HELENA-07 — the synthetic CPF the beneficiary typed into `message_body` must
    never reach the SP-OP-ESCALATION-001 engine-bound process variables (`resumo_contexto`,
    `dossie_fernando`-equivalent fields) started via `start_process_idempotent`. See this
    module's ground-truth-correction note #4 above for why the check targets
    `cibseven.get_process_status(...)` rather than `result.state` (which harmlessly echoes the
    raw `message_body` input verbatim — never a real disclosure)."""
    case = next(c for c in HELENA_CASES if c["id"] == "EVL-HELENA-07")
    canary = "123.456.789-09"
    result = await run_case(build, case, extra_config=_helena_extra_config())
    business_key = result.state["escalation_business_key"]
    status = await result.cibseven.get_process_status(business_key)
    assert_no_leak(status.variables, [canary])


def _plant_canary_at(case: Mapping[str, Any], canary: str, index: int) -> dict[str, Any]:
    """Deep-copy `case` with `canary` appended to the `recorded_llm` response at `index`.

    `_harness.mutate_plant_canary` plants on the LAST response only. For a Helena escalate turn
    that is `_respond_llm`'s draft — which lands in `state["response_text"]` — so it exercises the
    STATE leak surface and nothing else. The response that reaches the ENGINE is `recorded_llm[1]`
    (`_resumo_contexto` -> `resumo_contexto`), which is why this local, index-addressed variant
    exists (mirrors `_mutate_route_field`'s "kept in this file" rationale)."""
    mutated = copy.deepcopy(dict(case))
    responses = list(mutated.get("recorded_llm") or [])
    if index >= len(responses):
        raise ValueError(f"case {case.get('id')!r} has no `recorded_llm[{index}]` to plant into")
    responses[index] = f"{responses[index]} {canary}"
    mutated["recorded_llm"] = responses
    return mutated


@pytest.mark.eval
async def test_evl_helena_07_mutation_check_canary_plant_is_non_vacuous() -> None:
    """Non-vacuousness proof for EVL-HELENA-07's ENGINE-VARIABLE assertion (INFO-B repair).

    THE DEFECT THIS REPLACES: the previous version planted the canary on the LAST scripted
    response via `mutate_plant_canary` and let `run_mutation_check` assert against
    `result.state`. That proved the STATE leak surface is live — it says nothing about the
    variable set `test_evl_helena_07_cpf_absent_from_engine_bound_variables` actually checks,
    whose input (`recorded_llm[1]`, the `_resumo_contexto` draft) was identifier-free BY
    CONSTRUCTION in that golden. The engine-variable eval was therefore VACUOUS: it could not
    have failed no matter what the graph did with the summary.

    THE REPAIR: plant the canary on `recorded_llm[1]` — the ONE scripted response that reaches
    `resumo_contexto` — and prove the value TRAVELLED to the engine-bound surface and was
    REDACTED there, rather than merely never arriving. The class token is what distinguishes the
    two: `[REDACTED_DIGITS]` can only be present if the CPF reached the scrub."""
    case = next(c for c in HELENA_CASES if c["id"] == "EVL-HELENA-07")
    canary = "123.456.789-09"

    result = await run_case(build, _plant_canary_at(case, canary, 1), extra_config=_helena_extra_config())

    status = await result.cibseven.get_process_status(result.state["escalation_business_key"])
    resumo = status.variables["resumo_contexto"]
    assert canary not in resumo, f"engine-bound resumo_contexto leaked the planted CPF: {resumo!r}"
    assert "[REDACTED_DIGITS]" in resumo, (
        "the planted CPF never reached `resumo_contexto` at all — this eval's engine-variable "
        f"assertion would be VACUOUS (got {resumo!r})"
    )
    assert_no_leak(status.variables, [canary])


@pytest.mark.eval
async def test_evl_helena_09_llm_drafted_identifiers_absent_from_engine_bound_variables() -> None:
    """PL: EVL-HELENA-09 (CC-06/HEL-05) — the LLM copies a synthetic CPF, e-mail and BR phone
    into the handoff summary it drafts, and NONE of them may reach the SP-OP-ESCALATION-001
    engine-bound process variables. Unlike EVL-HELENA-07 the identifiers are absent from
    `message_body`, so they can only appear downstream by way of the model's own output.

    Also asserts what SURVIVES: SP-OP-ESCALATION-001 §Variaveis requires `resumo_contexto`
    pseudonimizado, not empty — a scrub that deleted the summary would break the human handoff
    the contract exists to guarantee, and would still pass a canary-absent check alone."""
    case = next(c for c in HELENA_CASES if c["id"] == "EVL-HELENA-09")
    result = await run_case(build, case, extra_config=_helena_extra_config())

    status = await result.cibseven.get_process_status(result.state["escalation_business_key"])
    assert_no_leak(status.variables, case["leak_canaries"])

    resumo = status.variables["resumo_contexto"]
    assert "[REDACTED_DIGITS]" in resumo and "[REDACTED_EMAIL]" in resumo
    assert "[REDACTED_PHONE]" in resumo
    assert "Beneficiario solicitou atendente humano" in resumo
    # Correlation identifiers a handoff MUST carry are untouched by the free-text net.
    assert status.variables["beneficiario_pseudo_id"] == "PSEUDO-TESTE-009"
    assert status.variables["motivo_categoria"] == "solicitacao_humano"


# ---------------------------------------------------------------------------
# Shared mutation helper for Fernando/Lucas — their RT criterion lives at
# `expect["fields"]["route"]`, never `expect["next_kind"]` (ground-truth note #2 above). Kept in
# this file (not `_harness.py`, which B1 does not own) since it is specific to the two agents
# whose state field is literally named `route`.
# ---------------------------------------------------------------------------


def _mutate_route_field(case: Mapping[str, Any], alternates: tuple[str, ...]) -> dict[str, Any]:
    """Deep-copy `case` with `expect["fields"]["route"]` flipped to a DIFFERENT value from
    `alternates`. Mirrors `_harness.mutate_expected_route`'s shape exactly, just targeting
    `fields["route"]` instead of the top-level `next_kind` key Fernando/Lucas never populate."""
    mutated = copy.deepcopy(dict(case))
    expect = dict(mutated.get("expect") or {})
    fields = dict(expect.get("fields") or {})
    original = fields.get("route")
    wrong = next((r for r in alternates if r != original), None)
    if wrong is None:
        raise ValueError(f"case {case.get('id')!r} has no expect.fields.route to mutate")
    fields["route"] = wrong
    expect["fields"] = fields
    mutated["expect"] = expect
    return mutated


# ---------------------------------------------------------------------------
# Fernando — Tier A only (ground-truth note #1: no live-comparable classify() node exists).
# ---------------------------------------------------------------------------

FERNANDO_CASES = load_golden("fernando")

_FERNANDO_ROUTE_ALTERNATES: tuple[str, ...] = ("notify", "escalate")


def _fernando_extra_config() -> dict[str, Any]:
    """`FernandoGraph.build(config)`'s one seam beyond the four every agent shares."""
    return {"whatsapp": FakeWhatsAppSender()}


@pytest.mark.eval
@pytest.mark.parametrize("case", FERNANDO_CASES, ids=lambda c: c["id"])
async def test_fernando_eval_tier_a(case: dict[str, Any]) -> None:
    """Drive the compiled Fernando graph with the ReplayInferenceProvider; assert RT(fields)/ABS.

    Fernando's routing is 100% DMN + `intencao`-driven (ground-truth note #1) — every case's
    `expect` lives under `fields` (never `next_kind`, which this graph's state never sets)."""
    result = await run_case(build_fernando, case, extra_config=_fernando_extra_config())
    assert_expect(result.state, case["expect"])
    # EVAL-HARNESS-SENDER (NEW-07): `sender=` folds every WhatsApp text this turn actually sent
    # into the SAME ABS scan -- `state` alone never sees `send_escalation_ack`-shaped leaks.
    assert_no_leak(result.state, case.get("leak_canaries") or [], sender=result.whatsapp)


@pytest.mark.eval
@pytest.mark.parametrize("case", FERNANDO_CASES, ids=lambda c: c["id"])
async def test_fernando_eval_route_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for every Fernando golden: flipping
    `expect.fields.route` must make the harness fail."""
    await run_mutation_check(
        build_fernando,
        case,
        mutation=lambda c: _mutate_route_field(c, _FERNANDO_ROUTE_ALTERNATES),
        extra_config=_fernando_extra_config(),
    )


@pytest.mark.eval
async def test_evl_fernando_05_cpf_absent_from_engine_bound_variables() -> None:
    """PL: EVL-FERNANDO-05 — a synthetic-CPF-bearing (invalid) `intencao` value must reach
    escalate's engine-bound process variables ONLY as the bounded class token `ambiguidade` /
    `invalid_intencao`, never any fragment of the raw value (mirrors `test_fernando.py::
    test_receive_invalid_intencao_fails_closed_without_leaking_value` and Helena's T1.11
    cycle-2 CPF-leak regression). See ground-truth-correction note #4 above for why this check
    targets `cibseven.get_process_status(...)` rather than `result.state` (which harmlessly
    echoes the raw `intencao` input verbatim)."""
    case = next(c for c in FERNANDO_CASES if c["id"] == "EVL-FERNANDO-05")
    canary = "123.456.789-09"
    result = await run_case(build_fernando, case, extra_config=_fernando_extra_config())
    business_key = result.state["business_key"]
    status = await result.cibseven.get_process_status(business_key)
    assert_no_leak(status.variables, [canary])


@pytest.mark.eval
async def test_evl_fernando_05_mutation_check_canary_plant_is_non_vacuous() -> None:
    """Non-vacuousness proof for EVL-FERNANDO-05: planting the CPF canary onto the LAST (only)
    scripted LLM response (the escalate-path `_build_dossier` narrativa, which DOES flow into
    `state["dossier"]["narrativa"]`) must make the harness's own `assert_no_leak(state, ...)`
    fail."""
    case = next(c for c in FERNANDO_CASES if c["id"] == "EVL-FERNANDO-05")
    await run_mutation_check(
        build_fernando,
        case,
        mutation=lambda c: mutate_plant_canary(c, "123.456.789-09"),
        extra_config=_fernando_extra_config(),
    )


# ---------------------------------------------------------------------------
# Lucas — Tier A only (ground-truth note #1: no live-comparable classify() node exists).
# ---------------------------------------------------------------------------

LUCAS_CASES = load_golden("lucas")

_LUCAS_ROUTE_ALTERNATES: tuple[str, ...] = ("respond_member", "escalate_human")


def _lucas_extra_config() -> dict[str, Any]:
    """`LucasGraph.build(config)`'s one seam beyond the four every agent shares."""
    return {"whatsapp": FakeWhatsAppSender()}


@pytest.mark.eval
@pytest.mark.parametrize("case", LUCAS_CASES, ids=lambda c: c["id"])
async def test_lucas_eval_tier_a(case: dict[str, Any]) -> None:
    """Drive the compiled Lucas graph with the ReplayInferenceProvider; assert RT(fields)/ABS.

    Lucas's routing is 100% DMN + `intencao`/pre-resolved-fact-driven (ground-truth note #1) —
    every case's `expect` lives under `fields` (never `next_kind`, which this graph's state
    never sets)."""
    result = await run_case(build_lucas, case, extra_config=_lucas_extra_config())
    assert_expect(result.state, case["expect"])
    # EVAL-HARNESS-SENDER (NEW-07): `sender=` folds every WhatsApp text this turn actually sent
    # into the SAME ABS scan -- `state` alone never sees `send_escalation_ack`-shaped leaks.
    assert_no_leak(result.state, case.get("leak_canaries") or [], sender=result.whatsapp)


@pytest.mark.eval
@pytest.mark.parametrize("case", LUCAS_CASES, ids=lambda c: c["id"])
async def test_lucas_eval_route_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Non-vacuousness proof (T3.2 design §7.1) for every Lucas golden: flipping
    `expect.fields.route` must make the harness fail."""
    await run_mutation_check(
        build_lucas,
        case,
        mutation=lambda c: _mutate_route_field(c, _LUCAS_ROUTE_ALTERNATES),
        extra_config=_lucas_extra_config(),
    )
