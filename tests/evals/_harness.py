"""The shared eval runner (T3.2 wave B0). Owned exclusively by wave B0.

Every family test module (`test_classifier_evals.py`, `test_dossier_adverse_evals.py`,
`test_dossier_admin_evals.py`) drives its golden cases through this module's `run_case` and
asserts the result with `assert_expect`/`assert_no_leak`/`score_live` — never hand-rolls graph
wiring or JSON parsing. Keeping ALL of that logic here (not per-family-file) is what makes the
44-eval matrix a genuine regression baseline rather than 44 independent, subtly-different
reimplementations of the same plumbing.

Pass-criterion types (T3.2 design §5/§6), each backed by one assertion helper below:
    RT  (exact route)             -> `assert_expect(state, {"next_kind": ...})`
    SF  (structured-field match)  -> `assert_expect(state, {"fields": {...}})`
    ABS (canary-absent)           -> `assert_no_leak(state, leak_canaries)`
    TH  (live threshold >= 0.9)   -> `score_live(...)` + `assert_live_score(...)` (Tier B only)
`assert_expect`'s `expect` mapping may carry `next_kind` and/or `fields` together, so a single
call covers RT+SF combined cases (e.g. EVL-HELENA-01: RT+SF).
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport
from tests.support.audit_fakes import FakeStartAuditSink

from .conftest import ReplayInferenceProvider, load_golden

# Re-exported so family test modules need only `from tests.evals._harness import ...`.
__all__ = [
    "RunResult",
    "assert_expect",
    "assert_live_score",
    "assert_no_leak",
    "load_golden",
    "mutate_expected_route",
    "mutate_plant_canary",
    "register_dmn_fixture",
    "run_case",
    "run_mutation_check",
    "score_live",
]


# ---------------------------------------------------------------------------
# run_case — build the compiled graph with injected fakes, drive one turn, return everything a
# case's assertions might need.
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """Everything a case's assertions might need after driving one turn.

    `state` is the final graph output (what `assert_expect`/`assert_no_leak` check against by
    default). The fakes are exposed too — a PL-class eval on a dossier agent, for instance, may
    need to inspect `cibseven`'s recorded start-process variables (the narrative/decision_basis
    live there, not in the returned state), mirroring how
    `tests/unit/agents/test_helena.py::test_cpf_bearing_field_value_never_reaches_engine_variables`
    inspects a recording CibSeven double directly.
    """

    state: dict[str, Any]
    inference: ReplayInferenceProvider
    dmn: FakeDmnTransport
    cibseven: FakeCibSevenTransport
    audit_sink: FakeStartAuditSink
    extra: dict[str, Any] = field(default_factory=dict)


def register_dmn_fixture(dmn: FakeDmnTransport, fixture: Mapping[str, Any] | None) -> None:
    """Register every `decision_key -> row(s)` pair from a golden case's `dmn_fixture`.

    A fixture value may be a single row (dict, the design doc's own example shape) or an
    already-list-wrapped set of rows — both are accepted so an eval author never has to remember
    which shape a particular decision key expects. Public (not `run_case`-only) because a Tier-B
    live variant typically drives a single node directly (e.g. `HelenaGraph.classify`) rather
    than the full compiled graph, but still needs the same DMN fixture wired onto its own
    `FakeDmnTransport` instance.
    """
    for decision_key, value in (fixture or {}).items():
        rows = value if isinstance(value, list) else [value]
        dmn.register(decision_key, rows)


async def run_case(
    build_fn: Callable[[dict[str, Any]], Any],
    case: Mapping[str, Any],
    *,
    extra_config: Mapping[str, Any] | None = None,
) -> RunResult:
    """Build `build_fn`'s agent graph with fakes seeded from `case`, invoke it, return the result.

    `build_fn` is an agent module's `build(config)` contract (`maezo.agents.<id>.graph.build`) —
    the SAME contract `AgentLoader`/the harness use in production, so an eval exercises the real
    build path, not a bespoke test-only wiring. `extra_config` supplies whatever additional
    keyword `build_fn` needs beyond the four seams every graph shares (`inference`/`dmn`/
    `cibseven`/`audit_sink`) — e.g. `{"whatsapp": FakeWhatsAppSender()}` for the classifier-family
    agents (helena/fernando/lucas).
    """
    inference = ReplayInferenceProvider(case["recorded_llm"])
    dmn = FakeDmnTransport()
    register_dmn_fixture(dmn, case.get("dmn_fixture"))
    cibseven = FakeCibSevenTransport()
    audit_sink = FakeStartAuditSink()

    config: dict[str, Any] = {
        "inference": inference,
        "dmn": dmn,
        "cibseven": cibseven,
        "audit_sink": audit_sink,
    }
    if extra_config:
        config.update(extra_config)

    graph = build_fn(config)
    compiled = graph.compile()
    input_state = dict(case["input"]["state"])
    result_state = await compiled.ainvoke(input_state)

    return RunResult(
        state=dict(result_state),
        inference=inference,
        dmn=dmn,
        cibseven=cibseven,
        audit_sink=audit_sink,
    )


# ---------------------------------------------------------------------------
# Pass-criterion assertions
# ---------------------------------------------------------------------------


def assert_expect(state: Mapping[str, Any], expect: Mapping[str, Any]) -> None:
    """RT + SF: assert `state["next_kind"]` (if given) and every `expect["fields"]` entry.

    A single golden `expect` block may carry both — `{"next_kind": "escalate", "fields":
    {"escalation_motivo": "red_flag_clinico"}}` is one RT check plus one SF check, matching the
    T3.2 design's `RT+SF` pass-criterion combination (e.g. EVL-HELENA-01).
    """
    if "next_kind" in expect:
        assert state.get("next_kind") == expect["next_kind"], (
            f"RT mismatch: expected next_kind={expect['next_kind']!r}, "
            f"got {state.get('next_kind')!r} (full state={state!r})"
        )
    for field_name, expected_value in (expect.get("fields") or {}).items():
        assert state.get(field_name) == expected_value, (
            f"SF mismatch: expected {field_name}={expected_value!r}, "
            f"got {state.get(field_name)!r} (full state={state!r})"
        )


def assert_no_leak(blob: Any, canaries: Sequence[str]) -> None:
    """ABS: assert none of `canaries` appear anywhere in `blob`'s serialized form.

    `blob` is typically a `RunResult.state` dict, but any JSON-serializable value works (e.g. a
    dossier agent's recorded CibSeven variables, `RunResult.cibseven.get_process_status(...)`'s
    result) — mirrors `tests/unit/agents/test_rafael_input_hardening.py`'s
    `_SLA_SENTINEL not in blob` pattern, generalized to a list of synthetic-PHI canaries.
    """
    if not canaries:
        return
    serialized = json.dumps(blob, ensure_ascii=False, default=str)
    for canary in canaries:
        assert canary not in serialized, f"ABS violation: leak canary {canary!r} found in emitted output"


def score_live(
    result_fields: Mapping[str, Any], baseline_fields: Mapping[str, Any], match: Sequence[str]
) -> float:
    """TH: fraction of `match` fields where the live run's value equals the golden baseline's.

    `baseline_fields` is normally `json.loads(case["recorded_llm"][0])` (the classify-call
    baseline the Tier-A replay would have returned) — Tier B re-runs the SAME node against a
    real LLM and diffs the result against that baseline, never against a hand-maintained second
    dataset. An empty `match` list scores 1.0 (nothing to check is vacuously satisfied, matching
    `assert_no_leak`'s empty-canaries no-op).
    """
    if not match:
        return 1.0
    hits = sum(1 for f in match if result_fields.get(f) == baseline_fields.get(f))
    return hits / len(match)


def assert_live_score(score: float, threshold: float) -> None:
    """TH: assert a `score_live` result meets the golden's `live.threshold` (design default 0.9)."""
    assert score >= threshold, f"TH violation: live score {score:.3f} below threshold {threshold:.3f}"


# ---------------------------------------------------------------------------
# Mutation-check helper (T3.2 design §7.1 — the non-vacuousness proof).
#
# A verifier (or a family builder proving their own golden isn't a rubber stamp) perturbs a
# golden's OWN pass criterion and asserts the SAME harness call now fails. An eval that still
# passes under its own deliberately-wrong expectation is vacuous: it would never catch a real
# prompt/graph/model regression, defeating the entire point of ADR-0009's promotion gate.
# ---------------------------------------------------------------------------

#: The three route literals every classifier-family agent's `next_kind`-shaped `expect` can take
#: (helena: inform/schedule/escalate; fernando/lucas use a 2-literal `Route` — `mutate_expected_route`
#: picks the first alternate that differs from the case's own value, so it still works for a
#: 2-literal route, it just never proposes the third).
_ROUTE_ALTERNATES: tuple[str, ...] = ("inform", "schedule", "escalate")


def mutate_expected_route(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy of `case` with `expect["next_kind"]` flipped to a DIFFERENT value.

    For an RT-class eval: proves `assert_expect` actually reads the graph's real route rather
    than trivially agreeing with whatever the golden says.
    """
    mutated = copy.deepcopy(dict(case))
    expect = dict(mutated.get("expect") or {})
    original = expect.get("next_kind")
    wrong = next((r for r in _ROUTE_ALTERNATES if r != original), None)
    if wrong is None:
        raise ValueError(f"case {case.get('id')!r} has no `expect.next_kind` to mutate")
    expect["next_kind"] = wrong
    mutated["expect"] = expect
    return mutated


def mutate_plant_canary(case: Mapping[str, Any], canary: str) -> dict[str, Any]:
    """Return a deep copy of `case` with `canary` appended to the last `recorded_llm` response.

    For a PL-class eval: simulates the LLM leaking the canary into its own free-text output
    (mirrors the live-proven CPF-leak regression `test_helena.py::
    test_cpf_bearing_field_value_never_reaches_engine_variables` guards against) and adds
    `canary` to `leak_canaries` so `assert_no_leak` is exercised against it. Requires the harness
    to actually route the mutated LLM text somewhere `assert_no_leak`'s serialized blob covers —
    if the mutated run still shows no leak, `run_mutation_check` raises (vacuous ABS eval).
    """
    mutated = copy.deepcopy(dict(case))
    responses = list(mutated.get("recorded_llm") or [])
    if not responses:
        raise ValueError(f"case {case.get('id')!r} has no `recorded_llm` entries to plant a canary into")
    responses[-1] = f"{responses[-1]} {canary}"
    mutated["recorded_llm"] = responses
    mutated["leak_canaries"] = [*list(mutated.get("leak_canaries") or []), canary]
    return mutated


async def run_mutation_check(
    build_fn: Callable[[dict[str, Any]], Any],
    case: Mapping[str, Any],
    *,
    mutation: Callable[[Mapping[str, Any]], dict[str, Any]],
    extra_config: Mapping[str, Any] | None = None,
) -> None:
    """Run a deliberately-corrupted `case` through the harness and assert it FAILS its own check.

    Raises `AssertionError` (build rejected, per the design's ratification checklist) if the
    mutated golden still passes — i.e. if `assert_expect`/`assert_no_leak` against the mutated
    `expect`/`leak_canaries` do NOT raise. Swallows the expected internal `AssertionError` from
    the (correctly failing) mutated check and returns normally — that's the passing case for
    THIS function (the eval is proven non-vacuous).
    """
    mutated = mutation(case)
    result = await run_case(build_fn, mutated, extra_config=extra_config)
    try:
        assert_expect(result.state, mutated.get("expect") or {})
        assert_no_leak(result.state, mutated.get("leak_canaries") or [])
    except AssertionError:
        return  # expected: the corrupted golden failed its own assertion -> eval is non-vacuous
    raise AssertionError(
        f"MUTATION CHECK FAILED for {mutated.get('id', '<unknown>')}: the deliberately-corrupted "
        "golden still PASSED its own assertions — this eval is vacuous (it would never catch a "
        "real prompt/graph/model regression) and the build must be rejected (T3.2 design §7.1)."
    )
