"""Shared fixtures/doubles for `tests/evals/` (T3.2 wave B0 — the eval harness scaffold).

Owned exclusively by wave B0 (do not edit from a family-builder wave — see README.md's build-
wave table). Provides:

- `ReplayInferenceProvider` — the deterministic, injected inference seam every Tier-A
  (merge-blocking) eval drives the agent graph through. Structurally mirrors
  `tests/unit/agents/test_helena.py::_FakeInference` (in-order, pop-from-front, no LLM SDK, no
  network) so a golden case's `recorded_llm` list behaves exactly like the scripted
  `_FakeInference([...])` lists already used across `tests/unit/agents/`. Two additions beyond
  `_FakeInference`: (1) a configurable `phi_capable` flag so a case can deliberately exercise the
  PHI-zone routing fail-closed path (`PhiZoneRoutingError`) as a first-class FC-class eval
  instead of only being satisfied by accident; (2) a LOUD `ReplayExhaustedError` instead of a
  silent `""` fallback when a case's `recorded_llm` list is shorter than the number of LLM calls
  the turn actually makes — a silent empty-string return would make an under-specified golden
  look like it passed when it never actually exercised the scripted branch (the exact "vacuous
  eval" failure mode the design's mutation-check gate exists to catch).

  UN-SWALLOWABLE CONTRACT (EVAL-REPLAY-EXHAUSTION-SWALLOWED, R2 gap closure): raising
  `ReplayExhaustedError` at the exhausting `generate()` call is NOT enough on its own. Every
  production agent graph wraps its `_respond_llm`/`_resumo_contexto`/equivalent calls in a
  legitimate, unremovable `except Exception` fail-safe fallback ("never leave the beneficiary
  with nothing" — e.g. `HelenaGraph._respond_llm`, `FernandoGraph`/`LucasGraph`'s analogues, and
  best-effort variants across the other agents). `ReplayExhaustedError` is a plain `RuntimeError`
  (an `Exception`), so that fallback silently catches it and the turn completes NORMALLY with
  canned fallback text — a short golden whose `recorded_llm` under-counts the real call sequence
  can then pass vacuously if its assertions happen to be satisfied by the fallback path (this bit
  `EVL-HELENA-CLAREZA-03` before its fixture was corrected). The production `except Exception` is
  correct and MUST NOT be narrowed, removed, or edited to "fix" this.

  The fix lives at the HARNESS level instead: `ReplayInferenceProvider` records every exhaustion
  as an `ExhaustionEvent` on `self.exhausted_calls` the instant it happens — BEFORE raising — so
  the record survives regardless of what catches (or doesn't catch) the raised error. Every
  `run_case` invocation (`_harness.py`) inspects `exhausted_calls` AFTER the turn completes
  (success or failure) and raises `ReplayExhaustedError` itself if the list is non-empty,
  REGARDLESS of what the agent ultimately returned — this is what makes exhaustion un-swallowable
  at the eval level without touching `src/`'s fail-safe fallbacks.

  The exact SYMMETRIC contract also applies in the other direction: `recorded_llm` is documented
  (`README.md`) as "one entry per actual `inference.generate(...)` call the turn makes, not per
  'meaningful' call" — an EXACT count, not a ceiling. So a golden whose `recorded_llm` is LONGER
  than the calls the turn actually made leaves scripted responses unconsumed, which `run_case`
  also treats as a hard failure (`ReplayUnconsumedResponsesError`) rather than silently ignoring
  the leftover entries — an over-provisioned fixture is exactly the kind of "graph now makes
  fewer real LLM calls than the golden assumes" drift a golden-dataset promotion gate exists to
  catch (a call the graph used to make may have silently disappeared).
- `FakeWhatsAppSender` — a reusable outbound-sender double. `helena`/`fernando`/`lucas` each
  declare a structurally identical `WhatsAppSender` Protocol (`async def send(self, to_hash:
  str, text: str) -> dict[str, Any]`) — one fake here serves all three classifier-family agents.
- `load_golden(agent)` — the golden-dataset loader: reads every `tests/evals/golden/<agent>/
  *.json` case, validates the required schema keys are present, and returns them id-sorted.
  Fails LOUDLY (raises) on a missing directory / empty directory / malformed case — collection
  must never silently report "0 evals" when a golden file is actually broken (mirrors the CI
  guard's own "import error != no datasets yet" distinction, `ci.yml:530-537`).
- `live_key_skip` — optional entirely unconfigured local PHI evals skip explicitly.
  An explicit incompatible configuration fails. Required live mode never accepts
  absent configuration, skips, empty collection, or a body without its own completion.

"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest

from maezo.runtime.inference import PhiZoneRoutingError

from ._live import (
    LiveEvalError,
    LiveEvalInference,
    build_live_inference,
    live_configuration_absent,
    validate_live_configuration,
)

# ---------------------------------------------------------------------------
# ReplayInferenceProvider — Tier A (deterministic, no network, no key)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExhaustionEvent:
    """One `ReplayInferenceProvider.generate()` call that found `recorded_llm` already empty.

    Appended to `ReplayInferenceProvider.exhausted_calls` the instant it happens — BEFORE the
    corresponding `ReplayExhaustedError` is raised — so the event survives even when the caller
    (production agent code) wraps the call in a broad `except Exception` fail-safe fallback and
    returns fallback text instead of letting the error propagate. `run_case` (`_harness.py`)
    inspects this list AFTER the turn completes and raises regardless of what the agent
    returned; see the module docstring's "UN-SWALLOWABLE CONTRACT" section.
    """

    calls_made: int
    responses_provided: int
    prompt: str


class ReplayExhaustedError(RuntimeError):
    """Raised when a golden case's `recorded_llm` list runs out before the turn does.

    Fail-loud, never fail-silent: an eval author who under-counts the LLM calls a turn actually
    makes (e.g. forgets the `_resumo_contexto` + `_respond_llm` calls `escalate` makes in
    addition to `classify`) gets an immediate, unambiguous error naming the exhausting prompt —
    not a turn that quietly continues on an empty string and passes for the wrong reason.

    Raised from two call sites with the same shape: (1) `ReplayInferenceProvider.generate()`
    itself, at the moment of exhaustion (`detected_post_turn=False` — the default; this fires
    whenever nothing downstream catches it); (2) `run_case`'s post-turn check
    (`detected_post_turn=True`), when the exhaustion WAS caught internally by an agent's own
    fail-safe fallback and the turn otherwise returned normally — see `swallowed_state`.
    """

    def __init__(
        self,
        *,
        calls_made: int,
        responses_provided: int,
        prompt: str,
        detected_post_turn: bool = False,
        additional_events: int = 0,
        swallowed_state: dict[str, Any] | None = None,
    ) -> None:
        self.calls_made = calls_made
        self.responses_provided = responses_provided
        self.detected_post_turn = detected_post_turn
        self.additional_events = additional_events
        #: The graph's final returned state, when `run_case` still had one to attach (i.e. the
        #: agent's own fallback swallowed the exhaustion and the turn completed normally) — `None`
        #: when the exhaustion instead propagated out of `compiled.ainvoke(...)` unswallowed (no
        #: final state exists in that case). Lets a test PROVE the swallow actually happened (the
        #: fallback text is right here) rather than merely asserting on the exception type.
        self.swallowed_state = swallowed_state
        post_turn_note = ""
        if detected_post_turn:
            post_turn_note = (
                " This exhaustion was CAUGHT internally by the agent's own `except Exception` "
                "fail-safe fallback (a legitimate, unremovable production resilience pattern) "
                "and the turn returned NORMALLY with fallback text instead of propagating the "
                "error — tests/evals/_harness.py::run_case's post-turn check is what surfaced it "
                "here, unswallowed. Fix the golden's `recorded_llm`, never the agent's fallback "
                "or this eval's assertions."
            )
            if additional_events:
                post_turn_note += (
                    f" ({additional_events} further exhaustion event(s) also occurred this turn.)"
                )
        super().__init__(
            f"ReplayInferenceProvider exhausted: {calls_made} generate() call(s) made but only "
            f"{responses_provided} `recorded_llm` response(s) were provided. The golden case's "
            "`recorded_llm` list is too short for the turn it drives — add one more scripted "
            f"response per actual LLM call. Exhausting prompt (truncated): {prompt[:200]!r}." + post_turn_note
        )


class ReplayUnconsumedResponsesError(RuntimeError):
    """Raised by `run_case` when a golden case's `recorded_llm` list is LONGER than the number of
    `generate()` calls the turn actually made.

    The symmetric counterpart of `ReplayExhaustedError`: both directions violate the same
    documented contract (`README.md`'s golden-case schema — `recorded_llm` is "one entry per
    actual `inference.generate(...)` call the turn makes, not per 'meaningful' call", an EXACT
    count). Unconsumed entries mean the golden and the graph have drifted — e.g. the graph used
    to make one more LLM call on this path than it does today and nobody updated the fixture —
    which is exactly the kind of silent regression a golden-dataset promotion gate exists to
    catch. Never "fix" this by trimming the golden's excess entries without first checking WHY
    the call count changed; it may be a real graph regression, not a stale fixture.
    """

    def __init__(
        self, *, case_id: str, recorded_total: int, calls_made: int, unconsumed: Sequence[str]
    ) -> None:
        self.case_id = case_id
        self.recorded_total = recorded_total
        self.calls_made = calls_made
        self.unconsumed = list(unconsumed)
        preview = ", ".join(repr(r[:80]) for r in self.unconsumed[:3])
        more = f" (+{len(self.unconsumed) - 3} more)" if len(self.unconsumed) > 3 else ""
        super().__init__(
            f"golden case {case_id!r}: `recorded_llm` provided {recorded_total} response(s) but "
            f"the turn only made {calls_made} generate() call(s) — {len(self.unconsumed)} "
            f"scripted response(s) went unused: {preview}{more}. Trim `recorded_llm` to match "
            "the ACTUAL call count the graph makes for this path (or, if the graph now makes "
            "fewer real LLM calls than before, treat this as a regression signal to investigate, "
            "not a fixture bug to rubber-stamp away)."
        )


class ReplayInferenceProvider:
    """Deterministic, in-order fake inference seam for Tier-A evals (T3.2 B0).

    Structurally satisfies whatever an agent graph's `inference:` constructor parameter expects
    (every graph in `src/maezo/agents/*/graph.py` calls `self._llm.generate(prompt, phi=True)`
    duck-typed, never `isinstance`-checked against `maezo.runtime.inference.InferenceProvider`) —
    exactly like `_FakeInference` in the unit-test suite. Never imports the `anthropic` SDK,
    never makes a network call.
    """

    #: True for every agent's PHI-tagged classify/narrative call by default (module docstrings,
    #: ADR-0006/ADR-0009: every LLM call in this codebase passes `phi=True`). A case that wants
    #: to exercise the fail-closed PHI-routing path itself constructs
    #: `ReplayInferenceProvider(responses, phi_capable=False)`.
    is_mock: ClassVar[bool] = True

    def __init__(
        self,
        responses: Sequence[str],
        *,
        phi_capable: bool = True,
        model_id: str | None = None,
    ) -> None:
        self._responses: list[str] = list(responses)
        self.phi_capable = phi_capable
        self.model_id = model_id
        self.calls: list[tuple[str, bool, str | None]] = []
        #: Every exhaustion this instance has hit, in order — appended BEFORE the corresponding
        #: `ReplayExhaustedError` is raised, so the record survives a caller's `except Exception`
        #: fallback. `run_case` inspects this after the turn to make exhaustion un-swallowable
        #: (see module docstring's "UN-SWALLOWABLE CONTRACT").
        self.exhausted_calls: list[ExhaustionEvent] = []

    @property
    def remaining_responses(self) -> tuple[str, ...]:
        """Scripted `recorded_llm` entries not yet consumed by a `generate()` call.

        Read-only snapshot. Non-empty after a turn completes means the golden's `recorded_llm`
        over-provisioned responses the turn never asked for — `run_case` treats that as a hard
        failure too (`ReplayUnconsumedResponsesError`), never as harmless slack.
        """
        return tuple(self._responses)

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        # `task_kind` (AF-12, ADR-0009 §2) is the real
        # `InferenceProvider.generate()`'s 5th keyword
        # (`runtime/inference/__init__.py::InferenceProvider.generate`) --
        # purely observational for a mock/replay
        # provider (it only ever selects a MODEL TIER on a real provider), but it MUST be
        # ACCEPTED: before this parameter existed here, every caller that passes it (currently
        # only `LucasGraph._build_message`/`_build_dossier`/`_build_escalation_ack`) raised a
        # bare `TypeError: generate() got an unexpected keyword argument 'task_kind'` on EVERY
        # call -- silently swallowed by those same call sites' `except Exception` fail-safe
        # fallback, so `self.calls` never grew and every Lucas Tier-A eval was unknowingly
        # exercising ONLY the fallback text, never a real scripted `recorded_llm` response
        # (found + fixed by EVAL-REPLAY-EXHAUSTION-SWALLOWED via the new
        # `ReplayUnconsumedResponsesError` check in `_harness.py::run_case`, which is what a
        # signature mismatch here shows up as: `calls_made=0` with every `recorded_llm` entry
        # left unconsumed). Recorded on `self.calls` (not just accepted-and-dropped) so a test
        # can assert which AF-12 task kind a given call was tagged with, same as `phi`/`agent_id`.
        self.calls.append((prompt, phi, task_kind))
        if phi and not self.phi_capable:
            raise PhiZoneRoutingError(
                "PHI-tagged inference request cannot be served by ReplayInferenceProvider"
                "(phi_capable=False) — route to a human/incident, never fall back to a "
                "general-zone provider (ADR-0006/ADR-0017)."
            )
        if not self._responses:
            event = ExhaustionEvent(calls_made=len(self.calls), responses_provided=0, prompt=prompt)
            self.exhausted_calls.append(event)
            raise ReplayExhaustedError(
                calls_made=event.calls_made,
                responses_provided=event.responses_provided,
                prompt=event.prompt,
            )
        return self._responses.pop(0)

    def health_check(self) -> dict[str, str]:
        return {
            "status": "warning",
            "message": "ReplayInferenceProvider — scripted eval responses only, never a real model.",
        }


# ---------------------------------------------------------------------------
# Reusable transport/sender doubles (agent-agnostic; classifier-family agents share the
# structurally-identical `WhatsAppSender` Protocol — helena/graph.py, fernando/graph.py,
# lucas/graph.py).
# ---------------------------------------------------------------------------


class FakeWhatsAppSender:
    """Records every send; never actually delivers anything (test-only, never imported by src/)."""

    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self._fail = fail

    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        if self._fail:
            raise RuntimeError("fake transport down (eval fixture)")
        self.sent.append((to_hash, text))
        return {"ok": True}


# ---------------------------------------------------------------------------
# Golden-dataset loader
# ---------------------------------------------------------------------------

#: `tests/evals/golden/<agent>/EVL-<AGENT>-<NN>.json` — one file per golden case (§6 of the T3.2
#: design). Sorted by filename so `pytest --collect-only` output/parametrize ids are stable.
GOLDEN_ROOT = Path(__file__).parent / "golden"

#: Required top-level keys of a golden case (T3.2 design §6 schema). `leak_canaries`/`live` are
#: optional (absent == "no canary assertion" / "no Tier-B live variant").
_REQUIRED_CASE_KEYS: tuple[str, ...] = ("id", "agent", "class", "tier", "input", "recorded_llm", "expect")


def load_golden(agent: str, *, root: Path | None = None) -> list[dict[str, Any]]:
    """Load every golden case for `agent` from `tests/evals/golden/<agent>/*.json`.

    Fails LOUDLY (never returns an empty list silently) on: a missing agent directory, an empty
    directory, or a case missing a required schema key / declaring the wrong `agent` — an
    eval-authoring mistake here must surface as a collection-time error, never as "0 evals ran"
    (mirrors the CI guard's own exit-2-vs-4/5 distinction, `ci.yml:521-543`).
    """
    base = root or GOLDEN_ROOT
    agent_dir = base / agent
    if not agent_dir.is_dir():
        raise FileNotFoundError(
            f"no golden directory for agent {agent!r} at {agent_dir} — create "
            f"tests/evals/golden/{agent}/ with at least one EVL-*.json case (see README.md)."
        )
    cases: list[dict[str, Any]] = []
    for path in sorted(agent_dir.glob("*.json")):
        with path.open(encoding="utf-8") as fh:
            case = json.load(fh)
        missing = [k for k in _REQUIRED_CASE_KEYS if k not in case]
        if missing:
            raise ValueError(f"golden case {path} is missing required key(s): {missing}")
        if case["agent"] != agent:
            raise ValueError(
                f"golden case {path} declares agent={case['agent']!r}, expected {agent!r} "
                "(wrong directory, or copy-paste error from another agent's golden case)"
            )
        cases.append(case)
    if not cases:
        raise ValueError(
            f"no golden *.json cases found under {agent_dir} — an empty golden directory is "
            "treated as a broken build, not as 'no evals for this agent yet' (delete the "
            "directory entirely if that's genuinely the intent)."
        )
    return cases


# ---------------------------------------------------------------------------
# Tier B (live-LLM) skip gate — same credential names as the runtime liveness test.
# ---------------------------------------------------------------------------


live_key_skip = pytest.mark.skipif(
    live_configuration_absent(),
    reason="LIVE EVAL UNAVAILABLE: optional local run has no explicit PHI configuration",
)


_LIVE_OBSERVER = pytest.StashKey[LiveEvalInference]()


@pytest.fixture
def live_inference(request):
    """One observer per body; teardown checks survive graph Exception fallbacks."""
    live = build_live_inference()
    request.node.stash[_LIVE_OBSERVER] = live
    try:
        yield live
        live.verify()
    finally:
        live.close()


__all__ = [
    "GOLDEN_ROOT",
    "ExhaustionEvent",
    "FakeWhatsAppSender",
    "ReplayExhaustedError",
    "ReplayInferenceProvider",
    "ReplayUnconsumedResponsesError",
    "live_key_skip",
    "load_golden",
]


# ADR-0009: opt-in strict live evidence; ordinary keyless replay/local skips stay unchanged.
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-live-evals",
        action="store_true",
        default=False,
        help="Require configured PHI inference, nonzero live collection and successful completions.",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--require-live-evals"):
        config.pluginmanager.register(_RequiredLiveEvals(), "maezo-live-evals")


class _RequiredLiveEvals:
    def __init__(self) -> None:
        self.collected: set[str] = set()
        self.passed: set[str] = set()
        self.nonpass = False

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        try:
            validate_live_configuration()
        except LiveEvalError as exc:
            raise pytest.UsageError(str(exc)) from None

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_makereport(self, item, call):
        outcome = yield
        report = outcome.get_result()
        if report.when == "call" and report.passed:
            live = item.stash.get(_LIVE_OBSERVER, None)
            try:
                if type(live) is not LiveEvalInference or item.funcargs.get("live_inference") is not live:
                    raise LiveEvalError("LIVE EVAL FAILED: body has no completion observer")
                live.verify()
            except LiveEvalError as exc:
                report.outcome = "failed"
                report.longrepr = str(exc)

        # The generic runner redacts recognized sensitive values. PHI evals have
        # a stricter contract: no arbitrary assertion/narrative text is public.
        # Project every phase, including setup/teardown failures and skipped/xfail
        # reasons, before JUnit and runner report consumers see the TestReport.
        report.sections = []
        report.user_properties = []
        if hasattr(report, "wasxfail"):
            report.wasxfail = "LIVE EVAL INVALID: xfail is not completion evidence"
        if report.failed:
            report.longrepr = "LIVE EVAL FAILED: inspect private execution; no completion certified"
        elif report.skipped:
            report.longrepr = ("<live-eval>", 0, "LIVE EVAL INVALID: skipped execution")

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collected = {item.nodeid for item in session.items}
        if not self.collected or any(
            item.get_closest_marker("eval") is None or item.get_closest_marker("llm_live") is None
            for item in session.items
        ):
            raise pytest.UsageError("LIVE EVAL INVALID: require nonzero eval and llm_live selection")

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.failed or report.skipped or hasattr(report, "wasxfail"):
            self.nonpass = True
        if report.when == "call" and report.passed and not hasattr(report, "wasxfail"):
            self.passed.add(report.nodeid)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if session.config.option.collectonly:
            return
        if exitstatus == 0 and (not self.collected or self.nonpass or self.passed != self.collected):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        terminalreporter.write_line(
            f"LIVE EVAL: collected={len(self.collected)} passed={len(self.passed)}; "
            "collection-only is not live execution evidence"
        )
