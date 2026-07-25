"""Classifier-family evals — helena/fernando/lucas (T3.2 design §6/§7).

Wave B0 lands ONLY the reference case end to end: `EVL-HELENA-01` (proves collection ->
`make evals` green -> the `evals`/`evals-nightly` CI lanes unblock). Wave B1 EXTENDS this file
with Fernando's and Lucas's own `test_<agent>_eval_tier_a` / `test_<agent>_eval_tier_b_live`
functions (mirror the Helena ones below exactly — same shape, different `agent`/graph import/
`extra_config`) plus their own `golden/fernando/`, `golden/lucas/` cases. Do not edit the Helena
functions from B1 — add new functions instead, so the two waves never touch the same lines.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from maezo.agents.helena.graph import HelenaGraph, build
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
    assert_no_leak(result.state, case.get("leak_canaries") or [])


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
