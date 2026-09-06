"""Clarity/legibility evals for Helena's beneficiary-facing triage wording (gap 10.3, WP-EVALS).

Scope note (this work package's brief): these evals judge the CLARITY OF THE WORDING Helena
emits to the beneficiary — `response_text`, the free text `_respond_llm` drafts and `respond`
sends over WhatsApp — never the clinical CONTENT of the SME-gated `triage_redflag_*` DMN tables
(`red_flag`/`conduta`/`prioridade`/`motivo` are DRAFT/verify content owned by a clinical
reviewer; this module never asserts on, judges, or fabricates it). The `dmn_fixture` in each
golden case below exists ONLY to drive Helena's routing to the path being clarity-checked.

Finding this closes: `docs/audits/maezo-deep-audit/reports/domain-10-accessibility.md:32`
("vocabulario de triagem nao e auditado para clareza linguistica ... sem eval de legibilidade
nos golden datasets") + `gateways/gvr-d10.md:17` (confirmed 8 Helena goldens, 0 clarity evals).

Three independent, deterministic, reproducible checks (`_harness.score_clarity`/
`assert_clarity` — see that module for the full contract; no LLM-as-judge, no constant score):
  1. sentence length — word count per sentence <= `clarity.max_words_per_sentence`;
  2. forbidden jargon — internal/engine vocabulary (DMN table names, raw `sintoma_codigo`
     values, `motivo_categoria` tokens, severity codes like `P1`) must never leak into the
     beneficiary-facing text verbatim;
  3. mandatory disclaimers — `response_prompt()`'s own instructions: an escalate handoff must
     name that a human continues and, when grave, that the beneficiary should seek emergency
     care if symptoms worsen before human contact; a schedule handoff must say a human will
     follow up.

Every case also carries a normal `expect` (RT/SF) block and is exercised by the shared
`test_helena_eval_tier_a` in `test_classifier_evals.py` too (both files call `load_golden
("helena")`, which returns every `*.json` under `golden/helena/` regardless of which module
added it) — this module adds the CLARITY-SPECIFIC assertion on top, plus its own
non-vacuousness proofs (T3.2 design §7.1).
"""

from __future__ import annotations

from typing import Any

import pytest

from maezo.agents.helena.graph import build

from ._harness import (
    ClarityReport,
    assert_clarity,
    assert_expect,
    assert_no_leak,
    load_golden,
    mutate_expected_route,
    mutate_extend_last_sentence,
    mutate_plant_canary,
    mutate_replace_last_response,
    run_case,
    run_mutation_check,
    score_clarity,
)
from .conftest import FakeWhatsAppSender

HELENA_CLAREZA_CASES = [c for c in load_golden("helena") if c["id"].startswith("EVL-HELENA-CLAREZA-")]


def _helena_extra_config() -> dict[str, Any]:
    """`HelenaGraph.build(config)`'s one seam beyond the four every agent shares."""
    return {"whatsapp": FakeWhatsAppSender()}


def _clarity_report(state: dict[str, Any], clarity_spec: dict[str, Any]) -> ClarityReport:
    field_name = clarity_spec.get("field", "response_text")
    text = state.get(field_name) or ""
    kwargs: dict[str, Any] = {
        "max_words_per_sentence": clarity_spec["max_words_per_sentence"],
        "forbidden_jargon": clarity_spec.get("forbidden_jargon") or (),
        "required_disclaimers": clarity_spec.get("required_disclaimers") or (),
    }
    if "min_words" in clarity_spec:
        # Optional override (REP-EVALS hardening) -- absent in all 3 shipped goldens, which rely
        # on `score_clarity`'s own default floor; kept out of `kwargs`'s literal so the primitive's
        # default is the single source of truth, not duplicated here.
        kwargs["min_words"] = clarity_spec["min_words"]
    return score_clarity(text, **kwargs)


# ---------------------------------------------------------------------------
# Tier A — deterministic replay, no network, no key. PR merge-blocking (mirrors every other
# Tier-A eval in this suite).
# ---------------------------------------------------------------------------


@pytest.mark.eval
@pytest.mark.parametrize("case", HELENA_CLAREZA_CASES, ids=lambda c: c["id"])
async def test_helena_clareza_eval_tier_a(case: dict[str, Any]) -> None:
    """Drive the compiled Helena graph; assert routing (RT/SF) AND the clarity of the wording
    it drafts for the beneficiary."""
    result = await run_case(build, case, extra_config=_helena_extra_config())
    assert_expect(result.state, case["expect"])
    # NEW-07 (Delta F1, VERIFY-EVAL-HARNESS-SENDER.md): this case sends via the sender wired by
    # `_helena_extra_config()` — `sender=result.whatsapp` folds every text Helena actually sent
    # into the same ABS scan, closing the same blind spot the original fix closed for
    # `test_classifier_evals.py::test_helena_eval_tier_a`.
    assert_no_leak(result.state, case.get("leak_canaries") or [], sender=result.whatsapp)
    assert_clarity(_clarity_report(result.state, case["clarity"]))


# ---------------------------------------------------------------------------
# Non-vacuousness proofs (T3.2 design §7.1). The RT half of every clarity golden is proven the
# same way as every other Helena golden (`mutate_expected_route`); the CL half gets its own
# proofs below, one per check kind, against the richest case (EVL-HELENA-CLAREZA-01, which
# carries both a jargon list AND two disclaimer groups) plus one each for the other two cases so
# no clarity golden in this file ships unproven.
# ---------------------------------------------------------------------------


@pytest.mark.eval
@pytest.mark.parametrize("case", HELENA_CLAREZA_CASES, ids=lambda c: c["id"])
async def test_helena_clareza_route_mutation_check_is_non_vacuous(case: dict[str, Any]) -> None:
    """Flipping `expect.next_kind` must make the harness fail — same RT non-vacuousness proof
    as every other Helena golden (mirrors `test_helena_b1_evals_route_mutation_check_is_non_vacuous`)."""
    await run_mutation_check(
        build,
        case,
        mutation=mutate_expected_route,
        extra_config=_helena_extra_config(),
    )


@pytest.mark.eval
async def test_evl_helena_clareza_01_mutation_check_jargon_plant_is_non_vacuous() -> None:
    """Planting a forbidden internal token onto the escalate handoff's drafted response must
    make `assert_clarity` fail — proves the jargon check is actually exercised, not vacuously
    green."""
    case = next(c for c in HELENA_CLAREZA_CASES if c["id"] == "EVL-HELENA-CLAREZA-01")
    mutated = mutate_plant_canary(case, "red_flag")
    result = await run_case(build, mutated, extra_config=_helena_extra_config())
    with pytest.raises(AssertionError, match="forbidden jargon"):
        assert_clarity(_clarity_report(result.state, mutated["clarity"]))


@pytest.mark.eval
async def test_evl_helena_clareza_01_mutation_check_long_sentence_is_non_vacuous() -> None:
    """Extending the escalate handoff's final sentence past `max_words_per_sentence` must make
    `assert_clarity` fail — proves the sentence-length check is actually exercised."""
    case = next(c for c in HELENA_CLAREZA_CASES if c["id"] == "EVL-HELENA-CLAREZA-01")
    mutated = mutate_extend_last_sentence(case, extra_words=30)
    result = await run_case(build, mutated, extra_config=_helena_extra_config())
    with pytest.raises(AssertionError, match="max-words-per-sentence"):
        assert_clarity(_clarity_report(result.state, mutated["clarity"]))


@pytest.mark.eval
async def test_evl_helena_clareza_01_mutation_check_missing_disclaimer_is_non_vacuous() -> None:
    """Replacing the escalate handoff's drafted response with a bland reply that omits both
    disclaimer groups must make `assert_clarity` fail — proves the disclaimer check is actually
    exercised, not a vacuous always-pass."""
    case = next(c for c in HELENA_CLAREZA_CASES if c["id"] == "EVL-HELENA-CLAREZA-01")
    mutated = mutate_replace_last_response(case, "Obrigada pela mensagem.")
    result = await run_case(build, mutated, extra_config=_helena_extra_config())
    with pytest.raises(AssertionError, match="missing mandatory disclaimer"):
        assert_clarity(_clarity_report(result.state, mutated["clarity"]))


@pytest.mark.eval
async def test_evl_helena_clareza_02_mutation_check_jargon_plant_is_non_vacuous() -> None:
    """Same jargon-plant proof as CLAREZA-01, against the inform-path golden (no disclaimer
    groups on this case, so jargon is the only clarity check it carries)."""
    case = next(c for c in HELENA_CLAREZA_CASES if c["id"] == "EVL-HELENA-CLAREZA-02")
    mutated = mutate_plant_canary(case, "dmn_table")
    result = await run_case(build, mutated, extra_config=_helena_extra_config())
    with pytest.raises(AssertionError, match="forbidden jargon"):
        assert_clarity(_clarity_report(result.state, mutated["clarity"]))


@pytest.mark.eval
async def test_evl_helena_clareza_03_mutation_check_missing_disclaimer_is_non_vacuous() -> None:
    """Same missing-disclaimer proof as CLAREZA-01, against the schedule-path golden."""
    case = next(c for c in HELENA_CLAREZA_CASES if c["id"] == "EVL-HELENA-CLAREZA-03")
    mutated = mutate_replace_last_response(case, "Tudo bem, obrigado.")
    result = await run_case(build, mutated, extra_config=_helena_extra_config())
    with pytest.raises(AssertionError, match="missing mandatory disclaimer"):
        assert_clarity(_clarity_report(result.state, mutated["clarity"]))
