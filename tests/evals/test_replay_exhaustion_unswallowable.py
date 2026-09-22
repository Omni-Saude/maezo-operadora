"""EVAL-REPLAY-EXHAUSTION-SWALLOWED (R2 gap closure) -- permanent proof that a golden case whose
`recorded_llm` under-counts an agent's real LLM-call sequence can no longer pass vacuously just
because the agent's own `except Exception` fail-safe fallback swallowed the resulting
`ReplayExhaustedError` internally.

Uses Helena (`src/maezo/agents/helena/graph.py`) as the documented real-agent case (task brief):
the `human_request` -> `escalate` path makes exactly THREE `inference.generate()` calls --
`classify` (1), `_resumo_contexto` (2), `_respond_llm` (3) -- and `_respond_llm`'s `except
Exception:` (graph.py, "fail-safe default: never leave the beneficiary with nothing") is a
genuine, unremovable production resilience pattern that this module never touches. `EVL-HELENA-07`
is the real shipped golden for this exact path (3 `recorded_llm` entries); the cases below are
built in-memory, deliberately mis-sized copies of that same shape -- no network, no live model,
fully deterministic (`ReplayInferenceProvider` only).

Three cases:
  1. `test_...one_entry_short_still_fails_even_though_agent_produced_a_fallback_answer` -- the
     NEGATIVE proof the task brief requires: `recorded_llm` is ONE entry short of what the
     `escalate` path needs. Pre-fix (i.e. checking ONLY `ReplayInferenceProvider.generate()`'s own
     immediate raise, the way this suite worked before EVAL-REPLAY-EXHAUSTION-SWALLOWED), that
     raise is caught by `_respond_llm`'s fallback and the turn returns NORMALLY with canned
     fallback text that still satisfies `assert_expect` -- a vacuous pass. Post-fix, `run_case`
     raises `ReplayExhaustedError` anyway, and the test also demonstrates directly (via
     `swallowed_state`, without going through `run_case` again) that a bare `assert_expect` on the
     swallowed state WOULD have passed, proving the harness-level check is load-bearing, not
     redundant.
  2. `test_...complete_golden_passes` -- the POSITIVE control: the same case with its full 3
     entries runs clean through `run_case`, and the response text actually reaches the real
     scripted value (not the fallback string), proving the negative case's failure is really about
     the missing entry and nothing else.
  3. `test_...unconsumed_recorded_llm_entry_is_not_silently_ignored` -- the symmetric direction
     named by the conftest docstring's design goal: a golden with ONE EXTRA `recorded_llm` entry
     beyond what the turn actually consumes must also fail loudly (`ReplayUnconsumedResponsesError`),
     never be silently tolerated as harmless slack.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.agents.helena.graph import build as build_helena

from ._harness import assert_expect, run_case
from .conftest import FakeWhatsAppSender, ReplayExhaustedError, ReplayUnconsumedResponsesError

pytestmark = pytest.mark.eval


def _helena_extra_config() -> dict[str, Any]:
    """`HelenaGraph.build(config)`'s one seam beyond the four every agent shares (mirrors
    `test_classifier_evals.py`/`test_helena_clarity_evals.py`)."""
    return {"whatsapp": FakeWhatsAppSender()}


#: The `human_request` -> `escalate` path, in-memory, same shape as the real shipped
#: `EVL-HELENA-07` (a synthetic-only fixture; `PSEUDO-TESTE-` id, no real PHI). THREE real
#: `generate()` calls this path makes: `classify`, `_resumo_contexto`, `_respond_llm`.
_CLASSIFY_JSON = (
    '{"intent": "human_request", "population": "none", "psychosocial_risk": false, '
    '"sintoma_codigo": null, "intensidade": "desconhecida"}'
)
_RESUMO_TEXT = "Beneficiario solicitou atendimento humano."
_RESPOND_TEXT = "Um atendente humano vai continuar seu atendimento em breve."

#: Verbatim from `HelenaGraph._respond_llm`'s `except EXTERNAL_DEPENDENCY_FAILURES` branch --
#: the constant that replaces the model's draft when the THIRD `generate()` call runs out of
#: scripted responses. Duplicated here (not imported) so this test does not silently stop proving
#: anything if that literal is ever edited in `src/` -- a drifted copy makes the assertion below
#: fail loudly instead of quietly checking the wrong string. It did exactly that on 21/09/2026
#: (second round), when the old fallback -- "Recebemos sua mensagem. Um profissional humano vai
#: continuar o atendimento em breve." -- was replaced: that text PROMISED a human and reached the
#: beneficiary through a `return` placed BEFORE the output fences, which in an `inform` turn (no
#: process, no queue, nobody calling) is the C1 defect with no model in the loop.
_HELENA_RESPOND_LLM_FALLBACK_TEXT = (
    "Recebemos sua mensagem. Nao consegui preparar a resposta agora por uma falha tecnica. "
    "Por favor, envie sua mensagem novamente em alguns minutos. Se voce estiver passando por "
    "uma emergencia, procure o servico de emergencia mais proximo."
)

#: What the beneficiary ACTUALLY reads on this path, and it is a TWO-STEP chain -- both steps in
#: production code. Step 1: `_respond_llm` degrades to the honest constant above. Step 2: this case
#: is `human_request -> escalate`, the start SUCCEEDS, and the TEXTO x FATO fence in `respond` sees
#: "a human took the case and the text does not say so" (F2), so it substitutes the handoff
#: constant. Same duplication discipline as above.
_HELENA_HANDOFF_SUBSTITUTE_TEXT = (
    "Recebemos sua mensagem e encaminhamos seu caso para a nossa equipe de saude. "
    "Um profissional vai dar continuidade ao seu atendimento. Se voce estiver passando por uma "
    "emergencia, procure o servico de emergencia mais proximo."
)

_CASE: dict[str, Any] = {
    "id": "EVL-TEST-REPLAY-EXHAUSTION-NEG",
    "agent": "helena",
    "class": "EU",
    "tier": ["A"],
    "description": (
        "In-memory only (not a shipped golden -- lives in this test module): same "
        "human_request -> escalate shape as EVL-HELENA-07, used to prove replay exhaustion "
        "cannot be silently swallowed by HelenaGraph._respond_llm's fail-safe fallback."
    ),
    "input": {
        "state": {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:evl-test-replay-exhaustion-neg",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "PSEUDO-TESTE-REPLAY-EXHAUSTION",
            "message_body": "Quero falar com um atendente humano",
        }
    },
    "recorded_llm": [_CLASSIFY_JSON, _RESUMO_TEXT, _RESPOND_TEXT],
    "expect": {
        "next_kind": "escalate",
        "fields": {"escalation_motivo": "solicitacao_humano"},
    },
    "leak_canaries": [],
}


async def test_replay_exhaustion_one_entry_short_fails_even_with_agent_fallback() -> None:
    """THE negative proof (task brief step 3): `recorded_llm` one entry short of the real
    3-call `escalate` path -- `_respond_llm`'s THIRD call is what exhausts. `_respond_llm`'s own
    `except Exception:` (graph.py) catches the resulting `ReplayExhaustedError` and returns its
    hardcoded fallback text; the turn otherwise completes NORMALLY with a CORRECT route
    (`next_kind="escalate"`, `escalation_motivo="solicitacao_humano"` -- both are decided by
    `classify()`, before the exhausting call even happens). `run_case` must still fail the case.
    """
    short_case = copy.deepcopy(_CASE)
    short_case["id"] = "EVL-TEST-REPLAY-EXHAUSTION-NEG-SHORT"
    short_case["recorded_llm"] = [_CLASSIFY_JSON, _RESUMO_TEXT]  # missing _respond_llm's entry

    with pytest.raises(ReplayExhaustedError) as excinfo:
        await run_case(build_helena, short_case, extra_config=_helena_extra_config())

    err = excinfo.value
    # It really was caught internally and the turn really did complete normally -- not some
    # unrelated crash that happens to also be a ReplayExhaustedError.
    assert err.detected_post_turn is True, "must be the run_case post-turn check, not the raw provider raise"
    assert err.swallowed_state is not None, "the turn must have returned a final state (fallback path taken)"
    assert err.swallowed_state["response_text"] == _HELENA_HANDOFF_SUBSTITUTE_TEXT, (
        "the swallowed state's response_text must be the end of the production degradation chain "
        "(_respond_llm's honest fallback, then respond's TEXTO x FATO substitution because a human "
        "WAS actioned and that constant does not mention the handoff) -- proves the exhaustion "
        "really was caught inside the graph, not by some other failure mode"
    )
    assert "profissional humano vai continuar" not in _HELENA_RESPOND_LLM_FALLBACK_TEXT, (
        "the fallback this test duplicates must never promise a human again: it is returned on a "
        "path where nothing was started (an `inform` turn), and it bypasses no fence anymore"
    )

    # THE SMOKING GUN: a bare assert_expect on that swallowed state -- i.e. exactly what this
    # suite's own test_helena_eval_tier_a would have checked before EVAL-REPLAY-EXHAUSTION-SWALLOWED
    # added run_case's post-turn check -- passes CLEANLY. This is the vacuous-pass bug: the route
    # was decided before the exhausting call, so nothing about a naive RT+SF assertion would ever
    # have caught the under-sized golden.
    assert_expect(err.swallowed_state, short_case["expect"])  # would NOT raise -- proves the vacuity


async def test_replay_exhaustion_complete_golden_passes() -> None:
    """POSITIVE control: the SAME case with its full 3 `recorded_llm` entries runs clean, and the
    real scripted response text (not the fallback) reaches the final state -- proves the negative
    case's failure is genuinely about the missing entry, nothing else about this fixture/path."""
    result = await run_case(build_helena, _CASE, extra_config=_helena_extra_config())
    assert_expect(result.state, _CASE["expect"])
    assert result.state["response_text"] == _RESPOND_TEXT
    assert result.state["response_text"] != _HELENA_RESPOND_LLM_FALLBACK_TEXT
    assert result.inference.exhausted_calls == []
    assert result.inference.remaining_responses == ()


async def test_replay_unconsumed_recorded_llm_entry_is_not_silently_ignored() -> None:
    """Symmetric direction (conftest.py docstring's design goal): ONE EXTRA `recorded_llm` entry
    beyond what the turn consumes must also fail loudly, not be silently tolerated."""
    long_case = copy.deepcopy(_CASE)
    long_case["id"] = "EVL-TEST-REPLAY-EXHAUSTION-NEG-LONG"
    long_case["recorded_llm"] = [_CLASSIFY_JSON, _RESUMO_TEXT, _RESPOND_TEXT, "never consumed"]

    with pytest.raises(ReplayUnconsumedResponsesError) as excinfo:
        await run_case(build_helena, long_case, extra_config=_helena_extra_config())

    err = excinfo.value
    assert err.calls_made == 3
    assert err.recorded_total == 4
    assert err.unconsumed == ["never consumed"]
