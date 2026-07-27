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
- `FakeWhatsAppSender` — a reusable outbound-sender double. `helena`/`fernando`/`lucas` each
  declare a structurally identical `WhatsAppSender` Protocol (`async def send(self, to_hash:
  str, text: str) -> dict[str, Any]`) — one fake here serves all three classifier-family agents.
- `load_golden(agent)` — the golden-dataset loader: reads every `tests/evals/golden/<agent>/
  *.json` case, validates the required schema keys are present, and returns them id-sorted.
  Fails LOUDLY (raises) on a missing directory / empty directory / malformed case — collection
  must never silently report "0 evals" when a golden file is actually broken (mirrors the CI
  guard's own "import error != no datasets yet" distinction, `ci.yml:530-537`).
- `live_key_skip` — a `pytest.mark.skipif` mirroring `tests/unit/runtime/test_inference_live.py`
  (`_HAS_KEY` gate on `MAEZO_ANTHROPIC_API_KEY`/`ANTHROPIC_API_KEY`) for Tier-B (live-LLM,
  threshold-scored, nightly-only) eval variants. Reuses the existing `llm_live` pytest marker
  (already registered in `pyproject.toml`) — no new marker is registered for Tier B.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, ClassVar

import pytest

from maezo.runtime.inference import PhiZoneRoutingError

# ---------------------------------------------------------------------------
# ReplayInferenceProvider — Tier A (deterministic, no network, no key)
# ---------------------------------------------------------------------------


class ReplayExhaustedError(RuntimeError):
    """Raised when a golden case's `recorded_llm` list runs out before the turn does.

    Fail-loud, never fail-silent: an eval author who under-counts the LLM calls a turn actually
    makes (e.g. forgets the `_resumo_contexto` + `_respond_llm` calls `escalate` makes in
    addition to `classify`) gets an immediate, unambiguous error naming the exhausting prompt —
    not a turn that quietly continues on an empty string and passes for the wrong reason.
    """

    def __init__(self, *, calls_made: int, responses_provided: int, prompt: str) -> None:
        self.calls_made = calls_made
        self.responses_provided = responses_provided
        super().__init__(
            f"ReplayInferenceProvider exhausted: {calls_made} generate() call(s) made but only "
            f"{responses_provided} `recorded_llm` response(s) were provided. The golden case's "
            "`recorded_llm` list is too short for the turn it drives — add one more scripted "
            f"response per actual LLM call. Exhausting prompt (truncated): {prompt[:200]!r}"
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
        self.calls: list[tuple[str, bool]] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        if phi and not self.phi_capable:
            raise PhiZoneRoutingError(
                "PHI-tagged inference request cannot be served by ReplayInferenceProvider"
                "(phi_capable=False) — route to a human/incident, never fall back to a "
                "general-zone provider (ADR-0006/ADR-0017)."
            )
        if not self._responses:
            raise ReplayExhaustedError(
                calls_made=len(self.calls),
                responses_provided=0,
                prompt=prompt,
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
# Tier B (live-LLM) skip gate — mirrors tests/unit/runtime/test_inference_live.py exactly.
# ---------------------------------------------------------------------------

_HAS_LLM_KEY = bool(os.environ.get("MAEZO_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))

#: Apply to any Tier-B (live) eval test/parametrize case. Reuses the existing `llm_live` marker
#: (`pyproject.toml`) rather than registering a new one — Tier B is "the same live-Anthropic-call
#: liveness gate, applied to an eval case" structurally, not a new kind of skip.
live_key_skip = pytest.mark.skipif(
    not _HAS_LLM_KEY,
    reason=(
        "No MAEZO_ANTHROPIC_API_KEY / ANTHROPIC_API_KEY in environment — skipping Tier-B "
        "live-LLM eval variant. This is a loud, explicit skip, not a silent pass (mirrors "
        "tests/unit/runtime/test_inference_live.py): Tier-A (replay) evals are the PR "
        "merge-blocking gate and are unaffected; Tier-B is nightly-only, threshold-scored, "
        "non-blocking, and requires a real Anthropic key to ever execute."
    ),
)


__all__ = [
    "GOLDEN_ROOT",
    "FakeWhatsAppSender",
    "ReplayExhaustedError",
    "ReplayInferenceProvider",
    "live_key_skip",
    "load_golden",
]
